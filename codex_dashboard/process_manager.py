from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database
from .engine import ProjectionEngine, TERMINAL_STATES
from .parser import ParsedEvent, parse_json_line
from .util import compact_text, stable_hash, utcnow


@dataclass(slots=True)
class ManagedProcess:
    session_id: str
    thread_id: str
    process: subprocess.Popen[bytes]
    log_path: Path
    command: list[str]
    cancelled: bool = False
    paused: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


class ProcessManager:
    """Own the lifecycle only for Codex processes launched by this dashboard."""

    def __init__(self, config: Config, database: Database, engine: ProjectionEngine, *, on_change: callable | None = None):
        self.config = config
        self.db = database
        self.engine = engine
        self.on_change = on_change or (lambda: None)
        self._lock = threading.RLock()
        self._processes: dict[str, ManagedProcess] = {}

    @property
    def available(self) -> bool:
        return shutil.which(self.config.codex_bin) is not None

    def capabilities(self, session: dict[str, Any]) -> dict[str, bool]:
        session_id = str(session.get("id") or "")
        with self._lock:
            owned = self._processes.get(session_id)
        alive = bool(owned and owned.process.poll() is None)
        posix = os.name == "posix"
        thread_id = str(session.get("thread_id") or session_id)
        terminal = session.get("status") in TERMINAL_STATES
        return {
            "open": bool(thread_id),
            "instruct": self.available and bool(thread_id) and not terminal,
            "resume": self.available and bool(thread_id) and not alive and not terminal,
            "pause": bool(alive and posix and not owned.paused),
            "continue": bool(alive and posix and owned.paused),
            "cancel": bool(alive),
            "mark_complete": not terminal,
        }

    def start(self, prompt: str, cwd: str | Path, *, title: str = "", model: str = "", resume_thread_id: str | None = None) -> str:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        if not self.available:
            raise RuntimeError(f"Codex executable not found: {self.config.codex_bin}")
        working_directory = Path(cwd).expanduser().resolve()
        if not working_directory.is_dir():
            raise ValueError(f"working directory does not exist: {working_directory}")

        local_id = str(uuid.uuid4())
        now = utcnow()
        self.db.ensure_session(
            local_id,
            thread_id=resume_thread_id or local_id,
            title=title or prompt.splitlines()[0][:120],
            cwd=str(working_directory),
            model=model,
            source="managed",
            status="RUNNING",
            managed=True,
            started_at=now,
            last_event_at=now,
        )
        command = [self.config.codex_bin, "exec", "--json", "--skip-git-repo-check"]
        if model:
            command.extend(["--model", model])
        if resume_thread_id:
            command.extend(["resume", resume_thread_id, prompt])
        else:
            command.append(prompt)

        log_path = self.config.runs_dir / f"managed-{local_id}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        creation_flags = 0
        start_new_session = os.name == "posix"
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=working_directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=start_new_session,
                creationflags=creation_flags,
            )
        except Exception as exc:
            self.db.update_session(local_id, status="FAILED", phase="Failed to start", error=repr(exc), finished_at=utcnow())
            self.db.audit("process.start", local_id, result="error", detail={"error": repr(exc), "command": command})
            raise

        managed = ManagedProcess(
            session_id=local_id,
            thread_id=resume_thread_id or local_id,
            process=process,
            log_path=log_path,
            command=command,
        )
        with self._lock:
            self._processes[local_id] = managed
        self.engine.managed_process_started(local_id, process.pid, now)
        self.db.audit("task.start", local_id, detail={"cwd": str(working_directory), "resume_thread_id": resume_thread_id})
        threading.Thread(target=self._read_and_wait, args=(managed,), name=f"codex-managed-{local_id[:8]}", daemon=True).start()
        self.on_change()
        return local_id

    def _read_and_wait(self, managed: ManagedProcess) -> None:
        stream = managed.process.stdout
        ordinal = 0
        try:
            with managed.log_path.open("ab", buffering=0) as log:
                if stream is not None:
                    for raw_line in iter(stream.readline, b""):
                        if not raw_line:
                            break
                        ordinal += 1
                        log.write(raw_line if raw_line.endswith(b"\n") else raw_line + b"\n")
                        event = parse_json_line(
                            raw_line,
                            origin=str(managed.log_path),
                            ordinal=ordinal,
                            fallback_session_id=managed.session_id,
                        )
                        if event is None:
                            self._record_unstructured_output(managed, raw_line, ordinal)
                            continue
                        self._bind_real_thread(managed, event)
                        self.engine.ingest(event)
                        self.on_change()
        except Exception as exc:
            self.db.audit("process.output", managed.session_id, result="error", detail={"error": repr(exc)})
        finally:
            return_code = managed.process.wait()
            with managed.lock:
                cancelled = managed.cancelled
                final_id = managed.session_id
            self.engine.managed_process_exited(final_id, return_code, cancelled=cancelled)
            with self._lock:
                for key, item in list(self._processes.items()):
                    if item is managed:
                        self._processes.pop(key, None)
            self.on_change()

    def _record_unstructured_output(self, managed: ManagedProcess, raw_line: bytes, ordinal: int) -> None:
        text = compact_text(raw_line.decode("utf-8", "replace"), 20_000)
        if not text:
            return
        event = ParsedEvent(
            session_id=managed.session_id,
            source_id=stable_hash(managed.log_path, "stderr", ordinal, raw_line),
            timestamp=utcnow(),
            kind="process.output",
            actor="system",
            text=text,
            payload={"text": text},
            raw={"text": text},
        )
        self.engine.ingest(event)

    def _bind_real_thread(self, managed: ManagedProcess, event: ParsedEvent) -> None:
        with managed.lock:
            if event.session_id == managed.session_id or managed.thread_id == event.session_id:
                managed.thread_id = event.session_id
                return
            old_id = managed.session_id
            real_id = event.session_id
            old = self.db.get_session(old_id, event_limit=0) or {}
            self.db.ensure_session(
                real_id,
                thread_id=real_id,
                title=old.get("title", ""),
                cwd=old.get("cwd", ""),
                model=old.get("model", ""),
                source="managed",
                status="RUNNING",
                managed=True,
                started_at=old.get("started_at"),
                last_event_at=event.timestamp,
            )
            self.db.update_session(real_id, pid=managed.process.pid, managed=True, status="RUNNING", phase="Codex connected")
            self.db.update_session(
                old_id,
                status="CANCELLED",
                archived=True,
                pid=None,
                phase="Replaced by Codex thread",
                summary=f"Managed launch continued as session {real_id}",
                finished_at=event.timestamp,
            )
            self.db.audit("session.bind", real_id, detail={"local_session_id": old_id})
            with self._lock:
                self._processes.pop(old_id, None)
                self._processes[real_id] = managed
            managed.session_id = real_id
            managed.thread_id = real_id

    def _owned(self, session_id: str) -> ManagedProcess:
        with self._lock:
            managed = self._processes.get(session_id)
        if not managed or managed.process.poll() is not None:
            raise RuntimeError("this session does not have a live dashboard-owned process")
        return managed

    def pause(self, session_id: str) -> None:
        if os.name != "posix":
            raise RuntimeError("pause is only supported on POSIX systems")
        managed = self._owned(session_id)
        with managed.lock:
            os.killpg(managed.process.pid, signal.SIGSTOP)
            managed.paused = True
        self.db.update_session(session_id, status="PAUSED", phase="Paused by user")
        self.db.audit("process.pause", session_id, detail={"pid": managed.process.pid})
        self.on_change()

    def continue_process(self, session_id: str) -> None:
        if os.name != "posix":
            raise RuntimeError("continue is only supported on POSIX systems")
        managed = self._owned(session_id)
        with managed.lock:
            os.killpg(managed.process.pid, signal.SIGCONT)
            managed.paused = False
        self.db.update_session(session_id, status="RUNNING", phase="Continued by user")
        self.db.audit("process.continue", session_id, detail={"pid": managed.process.pid})
        self.on_change()

    def cancel(self, session_id: str, timeout: float = 5.0) -> None:
        managed = self._owned(session_id)
        with managed.lock:
            managed.cancelled = True
            if managed.paused and os.name == "posix":
                os.killpg(managed.process.pid, signal.SIGCONT)
                managed.paused = False
            if os.name == "posix":
                os.killpg(managed.process.pid, signal.SIGTERM)
            else:
                managed.process.terminate()
        try:
            managed.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(managed.process.pid, signal.SIGKILL)
            else:
                managed.process.kill()
        self.db.audit("process.cancel", session_id, detail={"pid": managed.process.pid})
        self.on_change()

    def send_instruction(self, session_id: str, message: str) -> str:
        message = message.strip()
        if not message:
            raise ValueError("instruction is required")
        session = self.db.get_session(session_id, event_limit=0)
        if not session:
            raise KeyError(session_id)
        thread_id = str(session.get("thread_id") or session_id)
        with self._lock:
            managed = self._processes.get(session_id)
        if managed and managed.process.poll() is None:
            # Recent Codex versions support queueing a turn into an active session.
            result = subprocess.run(
                [self.config.codex_bin, "queue", thread_id, message],
                cwd=session.get("cwd") or None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            self.db.audit(
                "instruction.queue", session_id, result="ok" if result.returncode == 0 else "error",
                detail={"return_code": result.returncode, "stderr": compact_text(result.stderr, 4000)},
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Codex queue command failed")
            self.on_change()
            return session_id
        return self.start(
            message,
            session.get("cwd") or Path.cwd(),
            title=session.get("title") or "Resume Codex session",
            model=session.get("model") or "",
            resume_thread_id=thread_id,
        )

    def mark_complete(self, session_id: str, summary: str = "") -> None:
        session = self.db.get_session(session_id, event_limit=0)
        if not session:
            raise KeyError(session_id)
        if session.get("status") in TERMINAL_STATES:
            return
        now = utcnow()
        event = ParsedEvent(
            session_id=session_id,
            source_id=stable_hash("dashboard", "complete", session_id, now),
            timestamp=now,
            kind="session.completed",
            actor="user",
            text=summary.strip(),
            payload={"summary": summary.strip(), "explicit": True, "source": "dashboard"},
            raw={"type": "dashboard.complete", "summary": summary.strip()},
        )
        self.engine.ingest(event)
        self.db.audit("task.complete", session_id, detail={"summary": summary.strip()})
        self.on_change()

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._processes)
        for session_id in ids:
            try:
                self.cancel(session_id, timeout=2.0)
            except Exception:
                pass
