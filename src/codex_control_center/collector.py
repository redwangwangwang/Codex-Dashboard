from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .db import Database, utcnow
from .models import HostInput
from .security import redact, remote_shell, safe_tag_list, ssh_base_argv

Publish = Callable[[dict[str, Any]], Awaitable[None]]


def _find(obj: Any, *keys: str) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj[key] is not None:
                return obj[key]
        for value in obj.values():
            found = _find(value, *keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find(value, *keys)
            if found is not None:
                return found
    return None


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _percent(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 100.0))
    if isinstance(value, dict):
        return _percent(value.get("used_percent") or value.get("percent"))
    return None


def event_type(obj: dict[str, Any]) -> str:
    return str(obj.get("type") or obj.get("method") or _find(obj, "event_type", "kind") or "unknown")[:160]


def event_time(obj: dict[str, Any]) -> str:
    value = obj.get("timestamp") or obj.get("time") or _find(obj, "created_at")
    return str(value) if value else utcnow()


def source_session_id(obj: dict[str, Any], path: str) -> str:
    value = _find(obj, "thread_id", "threadId", "session_id", "sessionId")
    if value:
        return str(value)
    match = re.search(r"([0-9a-f]{8}(?:-[0-9a-f-]{8,})?)", Path(path).name, re.I)
    return match.group(1) if match else hashlib.sha256(path.encode()).hexdigest()[:24]


def lifecycle_for(kind: str, obj: dict[str, Any], current: str) -> str:
    low = kind.lower()
    status = str(_find(obj, "status", "state") or "").lower()
    if any(word in low for word in ("failed", "error")) or status in {"failed", "error"}:
        return "failed"
    if any(word in low for word in ("completed", "turn/completed", "task_complete")) or status in {"completed", "done"}:
        return "completed"
    if "interrupt" in low or status in {"cancelled", "canceled", "interrupted"}:
        return "interrupted"
    if any(word in low for word in ("started", "delta", "command", "tool")) or status in {"running", "inprogress", "in_progress"}:
        return "running"
    return current or "unknown"


def project_event(db: Database, host_id: str, path: str, offset: int, obj: dict[str, Any]) -> str | None:
    obj = redact(obj)
    sid = source_session_id(obj, path)
    key = f"{host_id}:{sid}"
    kind = event_type(obj)
    when = event_time(obj)
    if not db.insert_event(host_id=host_id, session_key=key, source_session_id=sid, event_time=when, event_type=kind, payload=obj, source_path=path, source_offset=offset):
        return None
    current = db.get_session_raw(key) or {}
    title = current.get("title") or str(_find(obj, "title", "goal", "prompt", "user_message") or "Untitled Codex session")[:300]
    cwd = _find(obj, "cwd", "working_directory", "workingDirectory") or current.get("cwd")
    model = _find(obj, "model", "model_name") or current.get("model")
    usage = _find(obj, "usage", "token_usage", "token_count")
    usage = usage if isinstance(usage, dict) else obj
    input_tokens = _number(_find(usage, "input_tokens", "inputTokens"))
    cached = _number(_find(usage, "cached_input_tokens", "cachedInputTokens"))
    output = _number(_find(usage, "output_tokens", "outputTokens"))
    reasoning = _number(_find(usage, "reasoning_output_tokens", "reasoning_tokens", "reasoningTokens"))
    total = _number(_find(usage, "total_tokens", "totalTokens"))
    context_window = _number(_find(obj, "context_window", "model_context_window", "contextWindow")) or current.get("context_window")
    used_context = _number(_find(obj, "context_tokens", "context_used", "last_token_usage"))
    context_percent = None
    if context_window and used_context is not None:
        context_percent = round(100.0 * used_context / context_window, 2)
    elif current.get("context_percent") is not None:
        context_percent = current["context_percent"]
    rate_limits = _find(obj, "rate_limits", "rateLimits")
    primary = _percent(rate_limits.get("primary")) if isinstance(rate_limits, dict) else None
    secondary = _percent(rate_limits.get("secondary")) if isinstance(rate_limits, dict) else None
    tags = safe_tag_list(_find(obj, "tags")) or current.get("tags", [])
    capabilities = safe_tag_list(_find(obj, "capabilities"), max_items=32, max_length=80) or current.get("capabilities", [])
    interaction = current.get("interaction", "none")
    low = kind.lower()
    if "approval" in low:
        interaction = "approval_required"
    elif "question" in low or "input_required" in low:
        interaction = "input_required"
    elif "completed" in low:
        interaction = "none"
    now = utcnow()
    db.upsert_session({
        "key": key, "host_id": host_id, "source_session_id": sid, "title": title,
        "cwd": str(cwd) if cwd else current.get("cwd"), "repository": current.get("repository"), "branch": current.get("branch"),
        "head": current.get("head"), "dirty": current.get("dirty"), "ahead": current.get("ahead"), "behind": current.get("behind"),
        "conflict": current.get("conflict", False), "lifecycle": lifecycle_for(kind, obj, current.get("lifecycle", "unknown")),
        "stage": current.get("stage", "unknown"), "interaction": interaction, "model": str(model) if model else None, "tags_json": tags,
        "input_tokens": input_tokens if input_tokens is not None else current.get("input_tokens"),
        "cached_input_tokens": cached if cached is not None else current.get("cached_input_tokens"),
        "output_tokens": output if output is not None else current.get("output_tokens"),
        "reasoning_tokens": reasoning if reasoning is not None else current.get("reasoning_tokens"),
        "total_tokens": total if total is not None else current.get("total_tokens"), "context_window": context_window,
        "context_percent": context_percent, "rate_primary_percent": primary if primary is not None else current.get("rate_primary_percent"),
        "rate_secondary_percent": secondary if secondary is not None else current.get("rate_secondary_percent"),
        "capabilities_json": capabilities, "last_event_at": when, "updated_at": now,
    })
    return key


class Collector:
    def __init__(self, db: Database, codex_home: Path, publish: Publish):
        self.db = db
        self.codex_home = codex_home
        self.publish = publish
        self._stop = asyncio.Event()
        self._locks: dict[str, asyncio.Lock] = {}

    async def loop(self, interval: float) -> None:
        while not self._stop.is_set():
            try:
                await self.sync_local()
                await asyncio.gather(*(self.sync_host(host["id"]) for host in self.db.list_hosts() if host.get("enabled")), return_exceptions=True)
                self.evaluate_attention()
            except Exception as exc:
                await self.publish({"type": "collector.error", "detail": str(exc)[:1000]})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def sync_local(self) -> int:
        root = self.codex_home / "sessions"
        if not root.exists():
            return 0
        count = 0
        for path in sorted(root.rglob("*.jsonl"))[-2000:]:
            count += await self._ingest_local_file("local", path)
        if count:
            await self.publish({"type": "sessions.changed", "host_id": "local", "events": count})
        return count

    async def _ingest_local_file(self, host_id: str, path: Path) -> int:
        offset, partial = self.db.cursor(host_id, str(path))
        try:
            size = path.stat().st_size
            if size < offset:
                offset, partial = 0, b""
            with path.open("rb") as fh:
                fh.seek(offset)
                data = fh.read(8_000_000)
        except OSError:
            return 0
        return self._consume(host_id, str(path), offset, partial, data)

    def _consume(self, host_id: str, path: str, offset: int, partial: bytes, data: bytes) -> int:
        combined = partial + data
        parts = combined.split(b"\n")
        tail = parts.pop() if combined and not combined.endswith(b"\n") else b""
        count = 0
        running = offset - len(partial)
        for raw in parts:
            line_offset = max(0, running)
            running += len(raw) + 1
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(obj, dict) and project_event(self.db, host_id, path, line_offset, obj):
                count += 1
        self.db.set_cursor(host_id, path, offset + len(data), tail)
        return count

    async def _ssh(self, host: dict[str, Any], command: str, *, timeout: int = 20, binary: bool = False) -> bytes | str:
        model = HostInput.model_validate(host)
        argv = ssh_base_argv(model) + [remote_shell(command)]
        process = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill(); await process.communicate()
            raise RuntimeError("SSH command timed out")
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace")[-1500:] or f"ssh exit {process.returncode}")
        return stdout if binary else stdout.decode(errors="replace")

    async def test_host(self, host_id: str) -> dict[str, Any]:
        host = self.db.get_host(host_id)
        if not host:
            raise KeyError(host_id)
        output = await self._ssh(host, "printf 'ccc-ok\\n'; uname -s; command -v codex || true", timeout=15)
        self.db.set_host_health(host_id, "online")
        return {"ok": True, "output": str(output).splitlines()[:4]}

    async def sync_host(self, host_id: str) -> int:
        lock = self._locks.setdefault(host_id, asyncio.Lock())
        if lock.locked():
            return 0
        async with lock:
            host = self.db.get_host(host_id)
            if not host or not host.get("enabled"):
                return 0
            try:
                root = host.get("codex_home") or "~/.codex"
                script = (
                    "import json,pathlib,os; "
                    f"r=pathlib.Path(os.path.expanduser({root!r}))/ 'sessions'; "
                    "print(json.dumps([{'path':str(p),'size':p.stat().st_size,'mtime':p.stat().st_mtime} for p in r.rglob('*.jsonl')][-2000:]))"
                )
                listing = await self._ssh(host, "python3 -c " + shlex.quote(script), timeout=25)
                files = json.loads(str(listing) or "[]")
                total = 0
                for entry in files:
                    path, size = str(entry["path"]), int(entry["size"])
                    offset, partial = self.db.cursor(host_id, path)
                    if size < offset:
                        offset, partial = 0, b""
                    if size == offset:
                        continue
                    amount = min(size - offset, 8_000_000)
                    reader = (
                        "import sys; "
                        f"f=open({path!r},'rb'); f.seek({offset}); sys.stdout.buffer.write(f.read({amount}))"
                    )
                    data = await self._ssh(host, "python3 -c " + shlex.quote(reader), timeout=35, binary=True)
                    assert isinstance(data, bytes)
                    total += self._consume(host_id, path, offset, partial, data)
                self.db.set_host_health(host_id, "online")
                await self._probe_remote_git(host)
                if total:
                    await self.publish({"type": "sessions.changed", "host_id": host_id, "events": total})
                return total
            except Exception as exc:
                self.db.set_host_health(host_id, "offline", str(exc))
                self.db.raise_alert(fingerprint=f"host-offline:{host_id}", session_key=None, host_id=host_id, kind="remote_offline", severity="high", title="Remote host unavailable", detail=str(exc)[:1000], evidence={"host": host.get("name")})
                await self.publish({"type": "host.changed", "host_id": host_id, "status": "offline"})
                return 0

    async def _probe_remote_git(self, host: dict[str, Any]) -> None:
        sessions, _ = self.db.list_sessions(limit=200, offset=0, host_id=host["id"])
        for session in sessions:
            cwd = session.get("cwd")
            if not cwd or not self._allowed_root(host, cwd):
                continue
            command = " && ".join([
                f"cd -- {shlex.quote(cwd)}",
                "root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0",
                "branch=$(git branch --show-current 2>/dev/null || true)",
                "head=$(git rev-parse --short=12 HEAD 2>/dev/null || true)",
                "dirty=0; test -z \"$(git status --porcelain 2>/dev/null)\" || dirty=1",
                "conflict=0; test -z \"$(git diff --name-only --diff-filter=U 2>/dev/null)\" || conflict=1",
                "ab=$(git rev-list --left-right --count '@{upstream}...HEAD' 2>/dev/null || printf '0 0')",
                "printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$root\" \"$branch\" \"$head\" \"$dirty\" \"$conflict\" \"$ab\"",
            ])
            try:
                output = str(await self._ssh(host, command, timeout=15)).strip().split("\t")
                if len(output) < 6:
                    continue
                behind, ahead = (output[5].split() + ["0", "0"])[:2]
                raw = self.db.get_session_raw(session["key"]) or session
                raw.update({
                    "repository": output[0] or None, "branch": output[1] or None, "head": output[2] or None,
                    "dirty": output[3] == "1", "conflict": output[4] == "1", "behind": int(behind), "ahead": int(ahead),
                    "tags_json": raw.get("tags", []), "capabilities_json": raw.get("capabilities", []), "updated_at": utcnow(),
                })
                self.db.upsert_session(raw)
            except Exception:
                continue

    @staticmethod
    def _allowed_root(host: dict[str, Any], cwd: str) -> bool:
        roots = host.get("workspace_roots") or []
        if not roots:
            return True
        return any(cwd == root or cwd.startswith(root.rstrip("/") + "/") for root in roots)

    async def start_app_server(self, host_id: str) -> dict[str, Any]:
        host = self.db.get_host(host_id)
        if not host:
            raise KeyError(host_id)
        home = host.get("codex_home") or "~/.codex"
        control = f"{home}/control-center"
        command = (
            f"mkdir -p {shlex.quote(control)}; "
            f"if test -s {shlex.quote(control + '/app-server.pid')} && kill -0 $(cat {shlex.quote(control + '/app-server.pid')}) 2>/dev/null; then cat {shlex.quote(control + '/app-server.pid')}; exit 0; fi; "
            f"nohup codex app-server --listen unix://{shlex.quote(control + '/app-server.sock')} >{shlex.quote(control + '/app-server.log')} 2>&1 < /dev/null & "
            f"pid=$!; printf '%s' \"$pid\" >{shlex.quote(control + '/app-server.pid')}; printf '%s\\n' \"$pid\""
        )
        output = str(await self._ssh(host, command, timeout=20)).strip().splitlines()
        pid = int(output[-1])
        self.db.set_host_health(host_id, "online", pid=pid)
        self.db.audit("operator", "app_server.start", host_id, "ok", {"pid": pid})
        return {"ok": True, "pid": pid}

    async def stop_app_server(self, host_id: str) -> dict[str, Any]:
        host = self.db.get_host(host_id)
        if not host:
            raise KeyError(host_id)
        home = host.get("codex_home") or "~/.codex"
        pidfile = f"{home}/control-center/app-server.pid"
        command = f"if test -s {shlex.quote(pidfile)}; then pid=$(cat {shlex.quote(pidfile)}); kill \"$pid\" 2>/dev/null || true; rm -f {shlex.quote(pidfile)}; fi"
        await self._ssh(host, command, timeout=15)
        with self.db.connect() as db:
            db.execute("UPDATE hosts SET managed_app_server_pid=NULL,updated_at=? WHERE id=?", (utcnow(), host_id))
        self.db.audit("operator", "app_server.stop", host_id, "ok", {})
        return {"ok": True}

    def evaluate_attention(self) -> None:
        settings = self.db.get_settings()
        sessions, _ = self.db.list_sessions(limit=10_000, offset=0)
        active_by_worktree: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in sessions:
            key = item["key"]
            if item.get("interaction") in {"approval_required", "input_required"}:
                self.db.raise_alert(fingerprint=f"interaction:{key}:{item['interaction']}", session_key=key, host_id=item["host_id"], kind=item["interaction"], severity="high", title="Session needs operator input", detail=item["interaction"], evidence={"session": item["title"]})
            if item.get("conflict"):
                self.db.raise_alert(fingerprint=f"git-conflict:{key}", session_key=key, host_id=item["host_id"], kind="git_conflict", severity="critical", title="Git conflict detected", detail=item.get("repository") or item.get("cwd") or "", evidence={"branch": item.get("branch"), "head": item.get("head")})
            cp = item.get("context_percent")
            if cp is not None and cp >= settings.context_warning_percent:
                self.db.raise_alert(fingerprint=f"context:{key}", session_key=key, host_id=item["host_id"], kind="context_pressure", severity="medium", title="Context pressure is high", detail=f"{cp:.1f}%", evidence={"context_percent": cp, "context_window": item.get("context_window")})
            rp = max(item.get("rate_primary_percent") or 0, item.get("rate_secondary_percent") or 0)
            if rp >= settings.rate_limit_warning_percent:
                self.db.raise_alert(fingerprint=f"rate:{key}", session_key=key, host_id=item["host_id"], kind="rate_limit_pressure", severity="medium", title="Rate-limit pressure is high", detail=f"{rp:.1f}%", evidence={"primary": item.get("rate_primary_percent"), "secondary": item.get("rate_secondary_percent")})
            if item.get("lifecycle") == "running" and item.get("cwd"):
                active_by_worktree.setdefault((item["host_id"], item["cwd"]), []).append(item)
        for (host_id, cwd), group in active_by_worktree.items():
            if len(group) > 1 and any(item.get("dirty") for item in group):
                ids = sorted(item["key"] for item in group)
                self.db.raise_alert(fingerprint="worktree-collision:" + hashlib.sha256("|".join(ids).encode()).hexdigest()[:24], session_key=None, host_id=host_id, kind="worktree_collision", severity="critical", title="Concurrent sessions are writing the same worktree", detail=cwd, evidence={"sessions": ids, "cwd": cwd})
