from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .collector import Collector
from .config import Config
from .db import Database
from .engine import ProjectionEngine, TERMINAL_STATES
from .git_inspector import GitInspector
from .parser import ParsedEvent
from .process_manager import ProcessManager
from .util import stable_hash, utcnow


class RevisionNotifier:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._value = 0

    def notify(self) -> int:
        with self._condition:
            self._value += 1
            self._condition.notify_all()
            return self._value

    def wait(self, previous: int, timeout: float = 15.0) -> int:
        with self._condition:
            if self._value <= previous:
                self._condition.wait(timeout)
            return self._value

    @property
    def value(self) -> int:
        with self._condition:
            return self._value


class DashboardService:
    def __init__(self, config: Config):
        config.prepare()
        self.config = config
        self.db = Database(config.database_path)
        self.notifier = RevisionNotifier()
        self.engine = ProjectionEngine(self.db, config)
        self.collector = Collector(config, self.db, self.engine, on_change=self.notifier.notify)
        self.processes = ProcessManager(config, self.db, self.engine, on_change=self.notifier.notify)
        self.git = GitInspector(config)
        self._started = False
        self._apply_persisted_settings()

    def _apply_persisted_settings(self) -> None:
        saved = self.db.get_setting("runtime", {}) or {}
        allowed = {"poll_interval", "stale_seconds", "command_hung_seconds", "git_refresh_seconds"}
        updates = {key: saved[key] for key in allowed if key in saved}
        if not updates:
            return
        candidate = replace(self.config, **updates)
        candidate.validate()
        self.config = candidate
        self.engine.config = candidate
        self.collector.config = candidate
        self.collector.git.config = candidate
        self.processes.config = candidate
        self.git.config = candidate

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.collector.start()

    def stop(self) -> None:
        if not self._started:
            return
        self.collector.stop()
        self.processes.stop_all()
        self._started = False

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": "1.0.0",
            "revision": self.db.revision(),
            "codex_available": self.processes.available,
            "codex_home": str(self.config.codex_home),
            "database": str(self.config.database_path),
        }

    def doctor(self) -> dict[str, Any]:
        state_databases = [str(path) for path in self.collector.state_database_paths()]
        rollout_count = len(self.collector.rollout_paths())
        checks = {
            "codex_binary": {"ok": self.processes.available, "value": shutil.which(self.config.codex_bin) or "not found"},
            "codex_home": {"ok": self.config.codex_home.exists(), "value": str(self.config.codex_home)},
            "state_database": {"ok": bool(state_databases), "value": state_databases},
            "rollouts": {"ok": rollout_count > 0, "value": rollout_count},
            "database": {"ok": self.config.database_path.exists(), "value": str(self.config.database_path)},
        }
        return {"ok": all(item["ok"] for key, item in checks.items() if key != "state_database"), "checks": checks}

    def overview(self) -> dict[str, Any]:
        result = self.db.overview()
        result["collector"] = {
            "poll_interval": self.config.poll_interval,
            "codex_home": str(self.config.codex_home),
            "codex_available": self.processes.available,
        }
        return result

    def list_tasks(self, params: dict[str, str]) -> list[dict[str, Any]]:
        completed_raw = params.get("completed")
        completed = None if completed_raw is None else completed_raw.lower() in {"1", "true", "yes"}
        tasks = self.db.list_sessions(
            status=params.get("status"),
            attention=params.get("attention", "").lower() in {"1", "true", "yes"},
            completed=completed,
            query=params.get("q", ""),
            limit=int(params.get("limit", "500")),
            offset=int(params.get("offset", "0")),
        )
        for task in tasks:
            task["capabilities"] = self.processes.capabilities(task)
        return tasks

    def get_task(self, session_id: str) -> dict[str, Any]:
        task = self.db.get_session(session_id)
        if not task:
            raise KeyError(session_id)
        task["capabilities"] = self.processes.capabilities(task)
        task["progress_label"] = f"{task['progress']:.0f}%" if task.get("progress_known") and task.get("progress") is not None else "Unknown"
        return task

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or "").strip()
        cwd = str(payload.get("cwd") or Path.cwd())
        title = str(payload.get("title") or "").strip()
        model = str(payload.get("model") or "").strip()
        should_start = bool(payload.get("start", True))
        if should_start:
            session_id = self.processes.start(prompt, cwd, title=title, model=model)
        else:
            session_id = str(uuid.uuid4())
            now = utcnow()
            self.db.ensure_session(
                session_id,
                title=title or (prompt.splitlines()[0][:120] if prompt else "New Codex task"),
                cwd=str(Path(cwd).expanduser().resolve()),
                model=model,
                source="dashboard",
                status="IDLE",
                managed=True,
                started_at=now,
                last_event_at=now,
            )
            if prompt:
                event = ParsedEvent(
                    session_id=session_id,
                    source_id=stable_hash("dashboard", "draft", session_id, prompt),
                    timestamp=now,
                    kind="message.user",
                    actor="user",
                    text=prompt,
                    payload={"message": prompt, "draft": True},
                    raw={"type": "dashboard.draft", "message": prompt},
                )
                self.engine.ingest(event)
            self.db.audit("task.create", session_id, detail={"start": False})
            self.notifier.notify()
        return self.get_task(session_id)

    def update_task(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.db.get_session(session_id, event_limit=0)
        if not current:
            raise KeyError(session_id)
        allowed = {"title", "summary", "archived"}
        updates = {key: payload[key] for key in allowed if key in payload}
        if "title" in updates:
            updates["title"] = str(updates["title"]).strip()[:500]
        if "summary" in updates:
            updates["summary"] = str(updates["summary"])[:20_000]
        self.db.update_session(session_id, **updates)
        self.db.audit("task.update", session_id, detail={"fields": sorted(updates)})
        self.notifier.notify()
        return self.get_task(session_id)

    def action(self, session_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = self.db.get_session(session_id, event_limit=0)
        if not task:
            raise KeyError(session_id)
        normalized = action.replace("-", "_").lower()
        if normalized == "pause":
            self.processes.pause(session_id)
        elif normalized in {"continue", "resume_process"}:
            self.processes.continue_process(session_id)
        elif normalized == "cancel":
            self.processes.cancel(session_id)
        elif normalized in {"instruct", "instruction", "resume"}:
            new_id = self.processes.send_instruction(session_id, str(payload.get("message") or payload.get("prompt") or ""))
            return self.get_task(new_id)
        elif normalized in {"complete", "mark_complete"}:
            self.processes.mark_complete(session_id, str(payload.get("summary") or ""))
        elif normalized == "acknowledge":
            alert_id = int(payload.get("alert_id"))
            self.db.acknowledge_alert(alert_id)
            self.db.audit("alert.acknowledge", session_id, detail={"alert_id": alert_id})
            self.notifier.notify()
        elif normalized == "scan":
            self.collector.scan_once()
            self.notifier.notify()
        else:
            raise ValueError(f"unknown action: {action}")
        return self.get_task(session_id)

    def task_diff(self, session_id: str, *, path: str | None = None, staged: bool = False) -> dict[str, Any]:
        task = self.db.get_session(session_id, event_limit=0)
        if not task:
            raise KeyError(session_id)
        cwd = task.get("repo_root") or task.get("cwd")
        return {"session_id": session_id, "path": path, "staged": staged, "diff": self.git.diff(cwd, path=path, staged=staged) if cwd else ""}

    def get_settings(self) -> dict[str, Any]:
        return {
            "codex_home": str(self.config.codex_home),
            "data_dir": str(self.config.data_dir),
            "host": self.config.host,
            "port": self.config.port,
            "poll_interval": self.config.poll_interval,
            "stale_seconds": self.config.stale_seconds,
            "command_hung_seconds": self.config.command_hung_seconds,
            "git_refresh_seconds": self.config.git_refresh_seconds,
            "codex_bin": self.config.codex_bin,
            "token_configured": bool(self.config.token),
            "token_required": self.config.requires_token,
        }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed_types: dict[str, type] = {
            "poll_interval": float,
            "stale_seconds": int,
            "command_hung_seconds": int,
            "git_refresh_seconds": int,
        }
        updates: dict[str, Any] = {}
        for key, cast in allowed_types.items():
            if key in payload:
                try:
                    updates[key] = cast(payload[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid {key}") from exc
        candidate = replace(self.config, **updates)
        candidate.validate()
        self.db.set_setting("runtime", updates)
        self.config = candidate
        self.engine.config = candidate
        self.collector.config = candidate
        self.collector.git.config = candidate
        self.processes.config = candidate
        self.git.config = candidate
        self.db.audit("settings.update", detail=updates)
        self.notifier.notify()
        return self.get_settings()

    def seed_demo(self, *, reset: bool = False) -> dict[str, Any]:
        if reset:
            self.db.delete_all()
        now = utcnow()
        demos = [
            {
                "id": "demo-running", "title": "Implement OAuth callback flow", "cwd": "/workspace/acme-web",
                "events": [
                    ("session.meta", {"title": "Implement OAuth callback flow", "cwd": "/workspace/acme-web", "model": "gpt-5-codex"}, ""),
                    ("message.user", {"message": "Implement OAuth callback flow and tests"}, "Implement OAuth callback flow and tests"),
                    ("plan.updated", {"steps": [
                        {"text": "Inspect auth routes", "status": "completed", "weight": 1},
                        {"text": "Implement callback", "status": "in_progress", "weight": 2},
                        {"text": "Add integration tests", "status": "pending", "weight": 1},
                    ]}, ""),
                    ("turn.started", {}, ""),
                    ("command.started", {"call_id": "demo-cmd", "command": "python -m pytest tests/test_auth.py"}, ""),
                ],
            },
            {
                "id": "demo-approval", "title": "Upgrade production database schema", "cwd": "/workspace/payments",
                "events": [
                    ("session.meta", {"title": "Upgrade production database schema", "cwd": "/workspace/payments"}, ""),
                    ("turn.started", {}, ""),
                    ("approval.required", {"reason": "Apply migration to protected database"}, "Apply migration to protected database"),
                ],
            },
            {
                "id": "demo-tests", "title": "Repair flaky checkout tests", "cwd": "/workspace/store",
                "events": [
                    ("session.meta", {"title": "Repair flaky checkout tests", "cwd": "/workspace/store"}, ""),
                    ("turn.started", {}, ""),
                    *[("command.completed", {"call_id": f"test-{index}", "command": "pytest tests/test_checkout.py", "exit_code": 1, "stderr": "1 failed, 8 passed"}, "") for index in range(1, 4)],
                ],
            },
            {
                "id": "demo-complete", "title": "Add export endpoint", "cwd": "/workspace/api",
                "events": [
                    ("session.meta", {"title": "Add export endpoint", "cwd": "/workspace/api"}, ""),
                    ("plan.updated", {"steps": [{"text": "Implementation", "status": "completed"}, {"text": "Tests", "status": "completed"}]}, ""),
                    ("session.completed", {"summary": "Export endpoint and tests are complete."}, "Export endpoint and tests are complete."),
                ],
            },
        ]
        created: list[str] = []
        for demo in demos:
            session_id = demo["id"]
            for index, (kind, payload, text) in enumerate(demo["events"]):
                event = ParsedEvent(
                    session_id=session_id,
                    source_id=stable_hash("demo", session_id, index, payload),
                    timestamp=now,
                    kind=kind,
                    actor="assistant" if kind == "message.agent" else "system",
                    text=text,
                    payload={**payload, "source": "demo"},
                    raw={"type": kind, "payload": payload},
                )
                self.engine.ingest(event)
            created.append(session_id)
        self.db.audit("demo.seed", detail={"sessions": created, "reset": reset})
        self.notifier.notify()
        return {"created": created, "count": len(created)}
