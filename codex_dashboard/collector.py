from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .db import Database
from .engine import ProjectionEngine
from .git_inspector import GitInspector
from .parser import ParsedEvent, fallback_session_from_path, parse_json_line
from .util import compact_text, json_loads, parse_time, stable_hash, utcnow


class Collector:
    """Discover Codex sessions and tail rollout JSONL without loading history wholesale."""

    def __init__(
        self,
        config: Config,
        database: Database,
        engine: ProjectionEngine,
        *,
        on_change: Callable[[], None] | None = None,
    ):
        self.config = config
        self.db = database
        self.engine = engine
        self.git = GitInspector(config)
        self.on_change = on_change or (lambda: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_git_refresh = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="codex-dashboard-collector", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                changed = self.scan_once()
                if changed:
                    self.on_change()
            except Exception as exc:  # collector failure must not take down the UI
                self.db.audit("collector.scan", result="error", detail={"error": repr(exc)})
            self._stop.wait(self.config.poll_interval)

    def scan_once(self) -> bool:
        changed = self.discover_state_database()
        for path in self.rollout_paths():
            try:
                if self.scan_file(path):
                    changed = True
            except (OSError, PermissionError) as exc:
                self.db.audit("collector.file", result="error", detail={"path": str(path), "error": repr(exc)})
        now = time.monotonic()
        if now - self._last_git_refresh >= self.config.git_refresh_seconds:
            if self.refresh_git_evidence():
                changed = True
            self._last_git_refresh = now
        self.engine.reconcile()
        return changed

    def rollout_paths(self) -> list[Path]:
        roots = [
            self.config.codex_home / "sessions",
            self.config.codex_home / "archived_sessions",
            self.config.runs_dir,
        ]
        paths: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            try:
                paths.extend(path for path in root.rglob("*.jsonl") if path.is_file())
            except OSError:
                continue
        # Newest first improves perceived startup, while offsets keep subsequent scans cheap.
        try:
            paths.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
        except OSError:
            paths.sort(reverse=True)
        return paths[:20_000]

    def state_database_paths(self) -> list[Path]:
        candidates = [
            self.config.codex_home / "state_5.sqlite",
            self.config.codex_home / "state.sqlite",
            self.config.codex_home / "state.db",
        ]
        return [path for path in candidates if path.is_file()]

    def discover_state_database(self) -> bool:
        changed = False
        for path in self.state_database_paths():
            try:
                uri = f"file:{path.as_posix()}?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=2) as connection:
                    connection.row_factory = sqlite3.Row
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    table = next((name for name in ("threads", "sessions", "conversations") if name in tables), None)
                    if not table:
                        continue
                    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                    id_column = next((name for name in ("id", "thread_id", "session_id") if name in columns), None)
                    if not id_column:
                        continue
                    wanted = [name for name in (
                        id_column, "title", "name", "cwd", "working_directory", "model", "model_provider",
                        "created_at", "updated_at", "archived", "source", "rollout_path",
                    ) if name in columns]
                    order = "updated_at DESC" if "updated_at" in columns else f"{id_column} DESC"
                    query = f"SELECT {','.join(dict.fromkeys(wanted))} FROM {table} ORDER BY {order} LIMIT 10000"
                    for row in connection.execute(query):
                        item = dict(row)
                        session_id = str(item.get(id_column) or "")
                        if not session_id:
                            continue
                        timestamp = parse_time(item.get("updated_at") or item.get("created_at"))
                        source_id = stable_hash("state-db", path, session_id, item.get("updated_at"), item)
                        event = ParsedEvent(
                            session_id=session_id,
                            source_id=source_id,
                            timestamp=timestamp,
                            kind="session.meta",
                            payload={
                                "thread_id": session_id,
                                "title": item.get("title") or item.get("name") or "",
                                "cwd": item.get("cwd") or item.get("working_directory") or "",
                                "model": item.get("model") or item.get("model_provider") or "",
                                "source": item.get("source") or "codex-state",
                                "state_database": str(path),
                                "rollout_path": item.get("rollout_path") or "",
                            },
                            raw={"state_database": str(path), "row": item},
                        )
                        if self.engine.ingest(event):
                            changed = True
                        if item.get("archived"):
                            self.db.update_session(session_id, archived=True)
            except (sqlite3.Error, OSError) as exc:
                self.db.audit("collector.state_db", result="error", detail={"path": str(path), "error": repr(exc)})
        return changed

    @staticmethod
    def _head_hash(path: Path) -> str:
        with path.open("rb") as handle:
            return hashlib.sha256(handle.read(4096)).hexdigest()

    def scan_file(self, path: Path) -> bool:
        stat = path.stat()
        inode = f"{getattr(stat, 'st_dev', 0)}:{getattr(stat, 'st_ino', 0)}"
        head_hash = self._head_hash(path)
        saved = self.db.get_offset(str(path))
        archived = "archived" in {part.lower() for part in path.parts}
        fallback = fallback_session_from_path(path)

        reset = not saved
        if saved:
            reset = (
                saved.get("inode") != inode
                or stat.st_size < int(saved.get("offset") or 0)
                or (saved.get("head_hash") and saved.get("head_hash") != head_hash)
            )
        if reset:
            offset = 0
            partial = b""
        else:
            offset = int(saved.get("offset") or 0)
            partial = bytes(saved.get("partial") or b"")

        changed = False
        if reset and stat.st_size > self.config.initial_head_bytes + self.config.initial_tail_bytes:
            with path.open("rb") as handle:
                head = handle.read(self.config.initial_head_bytes)
                tail_start = max(len(head), stat.st_size - self.config.initial_tail_bytes)
                handle.seek(tail_start)
                tail = handle.read(self.config.initial_tail_bytes)
            changed |= self._consume_complete_lines(path, head, fallback, prefix="head", start_offset=0, discard_first=False, archived=archived)
            # Tail starts in the middle of an arbitrary line. Discard that fragment, but retain an unfinished final line.
            first_newline = tail.find(b"\n")
            if first_newline >= 0:
                tail = tail[first_newline + 1 :]
                tail_start += first_newline + 1
            complete, partial = self._split_complete(tail)
            changed |= self._consume_complete_lines(path, complete, fallback, prefix="tail", start_offset=tail_start, discard_first=False, archived=archived)
            offset = stat.st_size
        else:
            remaining = max(0, stat.st_size - offset)
            read_size = min(remaining, self.config.max_incremental_bytes)
            if read_size:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read(read_size)
                combined = partial + chunk
                combined_start = max(0, offset - len(partial))
                complete, partial = self._split_complete(combined)
                changed |= self._consume_complete_lines(path, complete, fallback, prefix="line", start_offset=combined_start, discard_first=False, archived=archived)
                offset += len(chunk)

        self.db.set_offset(
            str(path), inode=inode, offset=offset, size=stat.st_size, mtime_ns=stat.st_mtime_ns,
            partial=partial[-self.config.max_event_bytes :], head_hash=head_hash,
        )
        return changed

    @staticmethod
    def _split_complete(data: bytes) -> tuple[bytes, bytes]:
        if not data:
            return b"", b""
        last = data.rfind(b"\n")
        if last < 0:
            return b"", data
        return data[: last + 1], data[last + 1 :]

    def _consume_complete_lines(
        self,
        path: Path,
        data: bytes,
        fallback: str,
        *,
        prefix: str,
        start_offset: int,
        discard_first: bool,
        archived: bool,
    ) -> bool:
        if not data:
            return False
        changed = False
        position = start_offset
        lines = data.splitlines(keepends=True)
        if discard_first and lines:
            position += len(lines.pop(0))
        for line in lines:
            payload = line.rstrip(b"\r\n")
            line_position = position
            position += len(line)
            if not payload or len(payload) > self.config.max_event_bytes:
                if len(payload) > self.config.max_event_bytes:
                    self.db.audit("collector.oversize_event", result="ignored", detail={"path": str(path), "bytes": len(payload)})
                continue
            event = parse_json_line(
                payload,
                origin=str(path),
                ordinal=f"{prefix}:{line_position}",
                fallback_session_id=fallback,
            )
            if not event:
                self.db.audit("collector.invalid_json", result="ignored", detail={"path": str(path), "offset": line_position})
                continue
            if self.engine.ingest(event):
                changed = True
                if archived:
                    self.db.update_session(event.session_id, archived=True)
        return changed

    def refresh_git_evidence(self) -> bool:
        changed = False
        for session in self.db.list_sessions(limit=5000):
            cwd = session.get("cwd")
            if not cwd:
                continue
            try:
                snapshot = self.git.inspect(cwd)
            except (OSError, ValueError):
                continue
            if not snapshot.get("is_git"):
                continue
            self.db.update_session(
                session["id"],
                repo_root=snapshot.get("root", ""),
                metadata={**(session.get("metadata") or {}), "git": {
                    "branch": snapshot.get("branch", ""), "head": snapshot.get("head", ""),
                }},
            )
            for item in snapshot.get("changes", []):
                key = stable_hash("git-status", snapshot.get("head"), item.get("status"), item.get("path"))
                before = self.db.get_session(session["id"], event_limit=0)
                count_before = len((before or {}).get("files", []))
                self.db.add_file_change(
                    session["id"], key, str(item["path"]), action=str(item.get("action") or "modified"),
                    timestamp=session.get("updated_at") or utcnow(),
                )
                after = self.db.get_session(session["id"], event_limit=0)
                if len((after or {}).get("files", [])) > count_before:
                    changed = True
        return changed
