from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .db import Database
from .parser import ParsedEvent, detect_test_result
from .util import clamp, compact_text, first_present, stable_hash, to_epoch, utcnow

TERMINAL_STATES = {"COMPLETED", "CANCELLED", "FAILED"}
ACTIVE_STATES = {"RUNNING", "WAITING_INPUT", "WAITING_APPROVAL", "PAUSED", "BLOCKED"}


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _duration_ms(payload: dict[str, Any]) -> int | None:
    direct = first_present(payload, ("duration_ms", "duration.millis", "elapsed_ms"))
    if direct is not None:
        return _int(direct)
    duration = payload.get("duration")
    if isinstance(duration, dict):
        try:
            return int(float(duration.get("secs", 0)) * 1000 + float(duration.get("nanos", 0)) / 1_000_000)
        except (TypeError, ValueError):
            return None
    return None


def _status_from_payload(payload: dict[str, Any], *, default: str) -> str:
    status = str(first_present(payload, ("status", "state"), default)).lower()
    if status in {"completed", "success", "succeeded", "done", "passed"}:
        return "COMPLETED"
    if status in {"failed", "error", "errored"}:
        return "FAILED"
    if status in {"cancelled", "canceled", "aborted"}:
        return "CANCELLED"
    if status in {"paused", "suspended"}:
        return "PAUSED"
    return default


class ProjectionEngine:
    """Project immutable events into query-oriented state.

    Every decision is deterministic and may be rebuilt from the events table. The engine is
    deliberately conservative: a turn finishing means the agent is idle; only explicit task
    completion is shown as COMPLETED. Progress is absent until a structured plan exists.
    """

    def __init__(self, database: Database, config: Config):
        self.db = database
        self.config = config

    def ingest(self, event: ParsedEvent) -> bool:
        payload = event.payload
        self.db.ensure_session(
            event.session_id,
            thread_id=str(payload.get("thread_id") or event.session_id),
            source=str(payload.get("source") or "rollout"),
            updated_at=event.timestamp,
            last_event_at=event.timestamp,
        )
        if not self.db.insert_event(event.as_dict()):
            return False

        session = self.db.get_session(event.session_id, event_limit=1) or {}
        kind = event.kind

        if kind in {"session.meta", "session.started"}:
            self._project_session_metadata(event, session)
        elif kind == "turn.started":
            self._set_status(event.session_id, "RUNNING", event.timestamp, phase="Working")
            self._resolve_waiting_alerts(event.session_id, event.timestamp)
        elif kind == "turn.completed":
            current = (self.db.get_session(event.session_id, event_limit=0) or {}).get("status")
            if current not in TERMINAL_STATES:
                self._set_status(event.session_id, "IDLE", event.timestamp, phase="Turn complete")
        elif kind == "turn.failed":
            error = event.text or compact_text(first_present(payload, ("error", "message"), "Turn failed"))
            self._set_status(event.session_id, "FAILED", event.timestamp, phase="Failed", error=error)
            self.db.upsert_alert(event.session_id, "turn-failed", "HIGH", "Turn failed", error, payload, event.timestamp)
        elif kind == "session.completed":
            summary = event.text or compact_text(first_present(payload, ("summary", "message", "last_agent_message"), ""))
            self._set_status(event.session_id, "COMPLETED", event.timestamp, phase="Completed", summary=summary, finished_at=event.timestamp)
            self._resolve_all_operational_alerts(event.session_id, event.timestamp)
        elif kind == "message.user":
            self._project_user_message(event, session)
        elif kind == "message.agent":
            self._project_agent_message(event)
        elif kind == "input.required":
            self._set_status(event.session_id, "WAITING_INPUT", event.timestamp, phase="Waiting for input")
            self.db.upsert_alert(
                event.session_id, "input-required", "CRITICAL", "Input required",
                event.text or "Codex is waiting for information before it can continue.", payload, event.timestamp,
            )
        elif kind == "approval.required":
            self._set_status(event.session_id, "WAITING_APPROVAL", event.timestamp, phase="Waiting for approval")
            self.db.upsert_alert(
                event.session_id, "approval-required", "CRITICAL", "Approval required",
                event.text or "Codex requested approval for a protected action.", payload, event.timestamp,
            )
        elif kind.startswith("command."):
            self._project_command(event)
        elif kind.startswith("tool."):
            self._project_tool(event)
        elif kind == "file.changed":
            self._project_files(event)
        elif kind == "plan.updated":
            self._project_plan(event)
        elif kind == "error" or kind.endswith(".error"):
            message = event.text or compact_text(first_present(payload, ("error", "message", "detail"), "Unknown Codex error"))
            self.db.update_session(event.session_id, error=message, phase="Error", updated_at=event.timestamp)
            self.db.upsert_alert(event.session_id, "runtime-error", "HIGH", "Runtime error", message, payload, event.timestamp)
        elif kind == "reasoning":
            self.db.update_session(event.session_id, phase="Reasoning", updated_at=event.timestamp)

        self._refresh_attention(event.session_id)
        return True

    def _project_session_metadata(self, event: ParsedEvent, previous: dict[str, Any]) -> None:
        payload = event.payload
        title = compact_text(payload.get("title", ""), 500)
        metadata = dict(previous.get("metadata") or {})
        metadata.update({k: v for k, v in payload.items() if k in {"originator", "cli_version", "model_provider", "forked_from_id", "git"}})
        status = previous.get("status") or "DISCOVERED"
        if status == "DISCOVERED" and event.kind == "session.started":
            status = "RUNNING"
        self.db.update_session(
            event.session_id,
            thread_id=str(payload.get("thread_id") or previous.get("thread_id") or event.session_id),
            title=title or previous.get("title", ""),
            cwd=str(payload.get("cwd") or previous.get("cwd") or ""),
            model=str(payload.get("model") or previous.get("model") or ""),
            source=str(payload.get("source") or previous.get("source") or "rollout"),
            status=status,
            phase="Started" if event.kind == "session.started" else previous.get("phase", ""),
            started_at=previous.get("started_at") or event.timestamp,
            metadata=metadata,
            updated_at=event.timestamp,
        )

    def _project_user_message(self, event: ParsedEvent, previous: dict[str, Any]) -> None:
        fields: dict[str, Any] = {"updated_at": event.timestamp}
        if not previous.get("title") and event.text:
            first_line = event.text.splitlines()[0].strip()
            fields["title"] = (first_line[:117] + "…") if len(first_line) > 120 else first_line
        current = previous.get("status")
        if current in {"WAITING_INPUT", "WAITING_APPROVAL", "BLOCKED", "IDLE", "DISCOVERED"}:
            fields.update(status="RUNNING", phase="Input received")
        self.db.update_session(event.session_id, **fields)
        self._resolve_waiting_alerts(event.session_id, event.timestamp)

    def _project_agent_message(self, event: ParsedEvent) -> None:
        fields: dict[str, Any] = {"updated_at": event.timestamp, "phase": "Responding"}
        if event.text:
            fields["summary"] = event.text[-10_000:]
        self.db.update_session(event.session_id, **fields)

    def _project_command(self, event: ParsedEvent) -> None:
        payload = event.payload
        call_id = str(payload.get("call_id") or event.source_id)
        command = str(payload.get("command") or "")
        cwd = str(payload.get("cwd") or "")
        if event.kind == "command.started":
            self.db.upsert_command(
                event.session_id, call_id, command=command, cwd=cwd, status="RUNNING",
                started_at=event.timestamp, last_output_at=event.timestamp, metadata=payload,
            )
            self._set_status(event.session_id, "RUNNING", event.timestamp, phase="Running command")
            self.db.resolve_alert(event.session_id, f"command-failed:{call_id}", event.timestamp)
            return
        if event.kind == "command.output":
            current = self._find_command(event.session_id, call_id)
            stdout = compact_text(payload.get("stdout") or payload.get("output") or event.text, 200_000)
            self.db.upsert_command(
                event.session_id, call_id, command=command, cwd=cwd,
                status=(current or {}).get("status", "RUNNING"),
                started_at=(current or {}).get("started_at") or event.timestamp,
                stdout=stdout or (current or {}).get("stdout", ""),
                stderr=compact_text(payload.get("stderr", ""), 200_000) or (current or {}).get("stderr", ""),
                last_output_at=event.timestamp,
            )
            self.db.resolve_alert(event.session_id, f"command-hung:{call_id}", event.timestamp)
            return

        exit_code = _int(first_present(payload, ("exit_code", "code", "return_code")))
        status = _status_from_payload(payload, default="COMPLETED" if exit_code in {None, 0} else "FAILED")
        stdout = compact_text(payload.get("stdout") or payload.get("aggregated_output") or payload.get("output") or "", 200_000)
        stderr = compact_text(payload.get("stderr") or payload.get("error") or "", 200_000)
        current = self._find_command(event.session_id, call_id)
        self.db.upsert_command(
            event.session_id, call_id, command=command, cwd=cwd, status=status,
            started_at=(current or {}).get("started_at") or event.timestamp,
            ended_at=event.timestamp, exit_code=exit_code, stdout=stdout, stderr=stderr,
            last_output_at=event.timestamp, duration_ms=_duration_ms(payload), metadata=payload,
        )
        self.db.resolve_alert(event.session_id, f"command-hung:{call_id}", event.timestamp)
        if status == "FAILED" or (exit_code is not None and exit_code != 0):
            message = stderr or stdout or f"Command exited with code {exit_code}"
            self.db.upsert_alert(
                event.session_id, f"command-failed:{call_id}", "HIGH", "Command failed",
                f"{command or 'Command'}: {message[:4000]}", {"call_id": call_id, "exit_code": exit_code}, event.timestamp,
            )
            self.db.update_session(event.session_id, phase="Command failed", updated_at=event.timestamp)
        else:
            self.db.resolve_alert(event.session_id, f"command-failed:{call_id}", event.timestamp)
            current_session = self.db.get_session(event.session_id, event_limit=0) or {}
            if current_session.get("status") not in TERMINAL_STATES | {"WAITING_INPUT", "WAITING_APPROVAL"}:
                self.db.update_session(event.session_id, status="RUNNING", phase="Command complete", updated_at=event.timestamp)

        test = detect_test_result(command, stdout, stderr, exit_code)
        if test:
            run_key = stable_hash(event.source_id, "test")
            self.db.add_test_run(
                event.session_id, run_key, command=command, duration_ms=_duration_ms(payload),
                timestamp=event.timestamp, **test,
            )
            self._update_test_alert(event.session_id, event.timestamp)

    def _project_tool(self, event: ParsedEvent) -> None:
        payload = event.payload
        call_id = str(payload.get("call_id") or event.source_id)
        if event.kind == "tool.started":
            self.db.upsert_tool(
                event.session_id, call_id, tool=str(payload.get("tool") or "tool"),
                arguments=payload.get("arguments") or {}, status="RUNNING", started_at=event.timestamp,
            )
            self._set_status(event.session_id, "RUNNING", event.timestamp, phase=f"Using {payload.get('tool') or 'tool'}")
            return
        raw_status = str(first_present(payload, ("status", "state"), "completed")).lower()
        status = "FAILED" if raw_status in {"failed", "error"} or bool(payload.get("error")) else "COMPLETED"
        self.db.upsert_tool(
            event.session_id, call_id, tool=str(payload.get("tool") or "tool"),
            arguments=payload.get("arguments") or {}, result_text=str(payload.get("result") or event.text or ""),
            status=status, ended_at=event.timestamp, duration_ms=_duration_ms(payload),
        )
        if status == "FAILED":
            self.db.upsert_alert(
                event.session_id, f"tool-failed:{call_id}", "HIGH", "Tool call failed",
                compact_text(payload.get("error") or payload.get("result") or event.text, 4000), payload, event.timestamp,
            )
        else:
            self.db.resolve_alert(event.session_id, f"tool-failed:{call_id}", event.timestamp)

    def _project_files(self, event: ParsedEvent) -> None:
        for change in event.payload.get("changes") or []:
            path = str(change.get("path") or "")
            if not path:
                continue
            self.db.add_file_change(
                event.session_id, event.source_id, path,
                action=str(change.get("action") or "modified"), additions=_int(change.get("additions")),
                deletions=_int(change.get("deletions")), patch=str(change.get("patch") or ""), timestamp=event.timestamp,
            )
        self.db.update_session(event.session_id, phase="Editing files", updated_at=event.timestamp)

    def _project_plan(self, event: ParsedEvent) -> None:
        steps = event.payload.get("steps") or []
        session = self.db.get_session(event.session_id, event_limit=0) or {}
        version = int(session.get("plan_version") or 0) + 1
        total_weight = sum(max(0.0, float(step.get("weight", 1.0))) for step in steps)
        done_weight = sum(
            max(0.0, float(step.get("weight", 1.0)))
            for step in steps if step.get("status") == "completed"
        )
        progress = round(clamp(100.0 * done_weight / total_weight), 1) if steps and total_weight > 0 else None
        explanation = compact_text(first_present(event.payload, ("explanation", "message", "text"), event.text), 5000)
        self.db.add_plan(event.session_id, version, event.timestamp, steps, explanation, progress)
        self.db.update_session(event.session_id, phase="Plan updated", updated_at=event.timestamp)

    def _find_command(self, session_id: str, call_id: str) -> dict[str, Any] | None:
        session = self.db.get_session(session_id, event_limit=0)
        if not session:
            return None
        return next((item for item in session.get("commands", []) if item.get("call_id") == call_id), None)

    def _update_test_alert(self, session_id: str, timestamp: str) -> None:
        session = self.db.get_session(session_id, event_limit=0) or {}
        tests = session.get("tests", [])
        consecutive = 0
        for test in tests:
            if test.get("status") == "FAILED":
                consecutive += 1
            else:
                break
        if consecutive >= 3:
            self.db.upsert_alert(
                session_id, "repeated-test-failure", "CRITICAL", "Tests failed repeatedly",
                f"The latest {consecutive} detected test runs failed.", {"consecutive_failures": consecutive}, timestamp,
            )
        elif consecutive:
            severity = "HIGH" if consecutive == 2 else "WARNING"
            self.db.upsert_alert(
                session_id, "test-failure", severity, "Tests are failing",
                f"{consecutive} consecutive detected test run(s) failed.", {"consecutive_failures": consecutive}, timestamp,
            )
            self.db.resolve_alert(session_id, "repeated-test-failure", timestamp)
        else:
            self.db.resolve_alert(session_id, "test-failure", timestamp)
            self.db.resolve_alert(session_id, "repeated-test-failure", timestamp)

    def _set_status(self, session_id: str, status: str, timestamp: str, **fields: Any) -> None:
        current = self.db.get_session(session_id, event_limit=0) or {}
        if current.get("status") in {"COMPLETED", "CANCELLED"} and status not in {"COMPLETED", "CANCELLED"}:
            return
        self.db.update_session(session_id, status=status, updated_at=timestamp, **fields)

    def _resolve_waiting_alerts(self, session_id: str, timestamp: str) -> None:
        self.db.resolve_alert(session_id, "input-required", timestamp)
        self.db.resolve_alert(session_id, "approval-required", timestamp)
        self.db.resolve_alert(session_id, "stale", timestamp)

    def _resolve_all_operational_alerts(self, session_id: str, timestamp: str) -> None:
        for alert in self.db.open_alerts(session_id):
            if alert.get("alert_key") not in {"runtime-error", "turn-failed"}:
                self.db.resolve_alert(session_id, str(alert["alert_key"]), timestamp)

    def _refresh_attention(self, session_id: str) -> None:
        alerts = self.db.open_alerts(session_id)
        rank = {"CRITICAL": 4, "HIGH": 3, "WARNING": 2, "INFO": 1}
        alerts.sort(key=lambda item: (rank.get(str(item.get("severity")), 0), item.get("last_seen_at", "")), reverse=True)
        self.db.update_session(session_id, attention_reason=alerts[0]["title"] if alerts else "")

    def reconcile(self, now: str | None = None) -> None:
        now = now or utcnow()
        current_epoch = to_epoch(now)
        for session in self.db.list_sessions(limit=5000):
            status = str(session.get("status") or "DISCOVERED")
            if status in TERMINAL_STATES:
                self.db.resolve_alert(session["id"], "stale", now)
                continue
            last_event = to_epoch(session.get("last_event_at") or session.get("updated_at"))
            age = current_epoch - last_event if last_event else 0
            if status in {"RUNNING", "DISCOVERED"} and age >= self.config.stale_seconds:
                self.db.upsert_alert(
                    session["id"], "stale", "WARNING", "No recent activity",
                    f"No new evidence has been observed for {int(age)} seconds.", {"age_seconds": int(age)}, now,
                )
                if status == "RUNNING":
                    self.db.update_session(session["id"], status="BLOCKED", phase="No recent activity", updated_at=now)
            elif age < self.config.stale_seconds:
                self.db.resolve_alert(session["id"], "stale", now)

        for command in self.db.running_commands():
            last_output = to_epoch(command.get("last_output_at") or command.get("started_at"))
            age = current_epoch - last_output if last_output else 0
            key = f"command-hung:{command['call_id']}"
            if age >= self.config.command_hung_seconds:
                self.db.upsert_alert(
                    command["session_id"], key, "HIGH", "Command may be hung",
                    f"No output from `{command.get('command') or 'command'}` for {int(age)} seconds.",
                    {"call_id": command["call_id"], "age_seconds": int(age)}, now,
                )
            else:
                self.db.resolve_alert(command["session_id"], key, now)

    def managed_process_started(self, session_id: str, pid: int, timestamp: str | None = None) -> None:
        timestamp = timestamp or utcnow()
        self.db.update_session(
            session_id, managed=True, pid=pid, status="RUNNING", phase="Starting Codex",
            started_at=timestamp, last_event_at=timestamp, updated_at=timestamp,
        )
        self.db.audit("process.start", session_id, detail={"pid": pid})

    def managed_process_exited(self, session_id: str, return_code: int, *, cancelled: bool = False, timestamp: str | None = None) -> None:
        timestamp = timestamp or utcnow()
        session = self.db.get_session(session_id, event_limit=0) or {}
        if cancelled:
            self.db.update_session(session_id, pid=None, status="CANCELLED", phase="Cancelled", finished_at=timestamp, updated_at=timestamp)
        elif return_code == 0:
            # Process success only proves the CLI invocation ended normally; it does not prove the user's task is done.
            status = "COMPLETED" if session.get("status") == "COMPLETED" else "IDLE"
            self.db.update_session(session_id, pid=None, status=status, phase="Codex process exited", updated_at=timestamp)
        else:
            message = f"Managed Codex process exited with code {return_code}."
            self.db.update_session(session_id, pid=None, status="FAILED", phase="Process failed", error=message, finished_at=timestamp, updated_at=timestamp)
            self.db.upsert_alert(session_id, "process-exit", "HIGH", "Codex process exited unexpectedly", message, {"return_code": return_code}, timestamp)
        self.db.audit("process.exit", session_id, result="ok" if return_code == 0 else "error", detail={"return_code": return_code, "cancelled": cancelled})
