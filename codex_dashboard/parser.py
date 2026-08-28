from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import command_text, compact_text, extract_uuid, first_present, parse_time, stable_hash


@dataclass(slots=True)
class ParsedEvent:
    session_id: str
    source_id: str
    timestamp: str
    kind: str
    actor: str = "system"
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "actor": self.actor,
            "text": self.text,
            "payload": self.payload,
            "raw": self.raw,
        }


_TYPE_ALIASES = {
    "session_meta": "session.meta",
    "thread_started": "session.started",
    "thread_started_event": "session.started",
    "turn_started": "turn.started",
    "turn_start": "turn.started",
    "turn_complete": "turn.completed",
    "turn_completed": "turn.completed",
    "turn_failed": "turn.failed",
    "agent_message": "message.agent",
    "assistant_message": "message.agent",
    "user_message": "message.user",
    "exec_command_begin": "command.started",
    "exec_command_start": "command.started",
    "command_execution_started": "command.started",
    "exec_command_output": "command.output",
    "command_execution_output": "command.output",
    "exec_command_end": "command.completed",
    "command_execution_completed": "command.completed",
    "mcp_tool_call_begin": "tool.started",
    "mcp_tool_call_start": "tool.started",
    "tool_call_started": "tool.started",
    "mcp_tool_call_end": "tool.completed",
    "tool_call_completed": "tool.completed",
    "file_change": "file.changed",
    "file_change_started": "file.changed",
    "file_change_completed": "file.changed",
    "request_user_input": "input.required",
    "user_input_requested": "input.required",
    "approval_request": "approval.required",
    "exec_approval_request": "approval.required",
    "apply_patch_approval_request": "approval.required",
    "plan_update": "plan.updated",
    "update_plan": "plan.updated",
    "error": "error",
    "stream_error": "error",
    "task_complete": "session.completed",
    "session_complete": "session.completed",
    "session_completed": "session.completed",
    "item_started": "item.started",
    "item_completed": "item.completed",
    "reasoning": "reasoning",
}


def _normalize_type(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return _TYPE_ALIASES.get(text, text.replace("_", "."))


def _unwrap(raw: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Return canonical type candidate, payload, and outer timestamp.

    Codex has shipped several rollout envelopes. This deliberately recognizes shape rather
    than a single version, and preserves the complete raw object for future re-projection.
    """
    timestamp = first_present(raw, ("timestamp", "created_at", "time"), "")
    current: Any = raw
    type_parts: list[str] = []
    for _ in range(5):
        if not isinstance(current, dict):
            break
        current_type = current.get("type") or current.get("event") or current.get("kind")
        if current_type:
            type_parts.append(str(current_type))
        if isinstance(current.get("item"), dict):
            current = current["item"]
            continue
        if isinstance(current.get("payload"), dict) and str(current_type or "").lower() in {
            "event_msg", "response_item", "rollout_item", "event", "item"
        }:
            current = current["payload"]
            continue
        break
    payload = current if isinstance(current, dict) else {"value": current}
    candidate = payload.get("type") or payload.get("event") or payload.get("kind")
    if not candidate:
        candidate = type_parts[-1] if type_parts else "unknown"
    return str(candidate), payload, str(timestamp or payload.get("timestamp") or "")


def _message_text(payload: dict[str, Any]) -> str:
    value = first_present(payload, ("message", "text", "content", "output", "last_agent_message"), "")
    return compact_text(value)


def _session_id(raw: dict[str, Any], payload: dict[str, Any], fallback: str | None) -> str:
    explicit = first_present(
        payload,
        ("thread_id", "session_id", "conversation_id", "thread.id", "session.id", "meta.id", "meta.session_id", "id"),
    )
    outer = first_present(raw, ("thread_id", "session_id", "conversation_id", "id"))
    return extract_uuid(explicit, outer, fallback) or str(explicit or outer or fallback or stable_hash(raw, length=24))


def _response_item_kind(payload: dict[str, Any], current_kind: str) -> str:
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    item_type = _normalize_type(item.get("type") or item.get("kind"))
    status = str(item.get("status") or "").lower()
    if item_type in {"command.execution", "command_execution", "local.shell.call", "shell.command"}:
        return "command.completed" if status in {"completed", "failed", "cancelled"} else "command.started"
    if item_type in {"mcp.tool.call", "tool.call", "function.call"}:
        return "tool.completed" if status in {"completed", "failed", "cancelled"} else "tool.started"
    if item_type in {"file.change", "apply.patch", "patch"}:
        return "file.changed"
    if item_type in {"agent.message", "assistant.message", "message"}:
        role = str(item.get("role") or "assistant").lower()
        return "message.user" if role == "user" else "message.agent"
    if item_type != "unknown":
        return item_type
    return current_kind


def parse_record(
    raw: dict[str, Any],
    *,
    origin: str,
    ordinal: int | str,
    fallback_session_id: str | None = None,
) -> ParsedEvent:
    type_value, payload, outer_timestamp = _unwrap(raw)
    kind = _normalize_type(type_value)
    if kind in {"response.item", "item.started", "item.completed"}:
        kind = _response_item_kind(payload, kind)
    session_id = _session_id(raw, payload, fallback_session_id)
    timestamp = parse_time(first_present(payload, ("timestamp", "created_at", "started_at", "completed_at"), outer_timestamp))
    source_id = stable_hash(origin, ordinal, raw)

    role = str(first_present(payload, ("role", "actor"), "system")).lower()
    if kind == "message.agent":
        role = "assistant"
    elif kind == "message.user":
        role = "user"
    text = _message_text(payload)

    normalized = dict(payload)
    item = payload.get("item") if isinstance(payload.get("item"), dict) else None
    if item:
        normalized = {**payload, **item, "item": item}

    if kind.startswith("command."):
        normalized["call_id"] = str(first_present(normalized, ("call_id", "id", "item_id", "turn_id"), source_id))
        normalized["command"] = command_text(first_present(normalized, ("command", "cmd", "argv", "parsed_cmd"), ""))
        normalized["cwd"] = str(first_present(normalized, ("cwd", "working_directory"), ""))
        normalized["stdout"] = compact_text(first_present(normalized, ("stdout", "output", "aggregated_output", "formatted_output"), ""))
        normalized["stderr"] = compact_text(first_present(normalized, ("stderr", "error"), ""))
        if text == "":
            text = normalized["command"] or normalized["stdout"] or normalized["stderr"]
    elif kind.startswith("tool."):
        normalized["call_id"] = str(first_present(normalized, ("call_id", "id", "item_id"), source_id))
        normalized["tool"] = str(first_present(normalized, ("tool", "name", "server", "function.name"), "tool"))
        normalized["arguments"] = first_present(normalized, ("arguments", "input", "params"), {})
        normalized["result"] = compact_text(first_present(normalized, ("result", "output", "content", "error"), ""))
    elif kind == "session.meta":
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
        normalized.update({
            "thread_id": str(first_present(meta, ("id", "session_id", "thread_id"), session_id)),
            "cwd": str(first_present(meta, ("cwd", "working_directory"), "")),
            "model": str(first_present(meta, ("model", "model_provider"), "")),
            "source": str(first_present(meta, ("source", "originator"), "rollout")),
            "title": compact_text(first_present(meta, ("title", "name"), ""), 500),
        })
    elif kind == "file.changed":
        normalized["changes"] = extract_file_changes(normalized)
    elif kind == "plan.updated":
        normalized["steps"] = extract_plan_steps(normalized)
    elif kind in {"input.required", "approval.required"}:
        text = text or compact_text(first_present(normalized, ("question", "reason", "message"), "Action required"))

    return ParsedEvent(
        session_id=session_id,
        source_id=source_id,
        timestamp=timestamp,
        kind=kind,
        actor=role,
        text=text,
        payload=normalized,
        raw=raw,
    )


def parse_json_line(
    line: str | bytes,
    *,
    origin: str,
    ordinal: int | str,
    fallback_session_id: str | None = None,
) -> ParsedEvent | None:
    try:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        raw = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        raw = {"type": "raw", "value": raw}
    return parse_record(raw, origin=origin, ordinal=ordinal, fallback_session_id=fallback_session_id)


def fallback_session_from_path(path: str | Path) -> str:
    found = extract_uuid(str(path))
    return found or "file-" + stable_hash(Path(path).as_posix(), length=24)


def extract_file_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = first_present(payload, ("changes", "files", "file_changes", "patch.changes"), [])
    if isinstance(candidates, dict):
        candidates = [candidates]
    result: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, str):
                result.append({"path": item, "action": "modified"})
                continue
            if not isinstance(item, dict):
                continue
            path = first_present(item, ("path", "file", "filename", "new_path", "old_path"), "")
            if not path:
                continue
            result.append({
                "path": str(path),
                "action": str(first_present(item, ("action", "kind", "status"), "modified")),
                "patch": compact_text(first_present(item, ("patch", "diff"), ""), 200_000),
                "additions": first_present(item, ("additions", "lines_added")),
                "deletions": first_present(item, ("deletions", "lines_deleted")),
            })
    else:
        path = first_present(payload, ("path", "file", "filename"), "")
        if path:
            result.append({"path": str(path), "action": "modified", "patch": compact_text(payload.get("patch", ""), 200_000)})
    return result


def extract_plan_steps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_steps = first_present(payload, ("steps", "plan", "items", "explanation.plan"), [])
    if isinstance(raw_steps, dict):
        raw_steps = raw_steps.get("steps") or raw_steps.get("items") or []
    if not isinstance(raw_steps, list):
        return []
    result: list[dict[str, Any]] = []
    for index, step in enumerate(raw_steps):
        if isinstance(step, str):
            result.append({"id": str(index + 1), "text": step, "status": "pending", "weight": 1.0})
            continue
        if not isinstance(step, dict):
            continue
        status = str(first_present(step, ("status", "state"), "pending")).lower()
        if status in {"complete", "completed", "done", "success", "passed"}:
            status = "completed"
        elif status in {"active", "in_progress", "running", "doing"}:
            status = "in_progress"
        else:
            status = "pending"
        try:
            weight = max(0.0, float(step.get("weight", 1.0)))
        except (TypeError, ValueError):
            weight = 1.0
        result.append({
            "id": str(first_present(step, ("id", "step_id"), index + 1)),
            "text": compact_text(first_present(step, ("text", "step", "description", "title"), f"Step {index + 1}"), 2000),
            "status": status,
            "weight": weight,
        })
    return result


def detect_test_result(command: str, stdout: str, stderr: str, exit_code: int | None) -> dict[str, Any] | None:
    lower = command.lower()
    frameworks = {
        "pytest": ("pytest", "pytest"),
        "unittest": ("unittest", "unittest"),
        "npm test": ("npm", "javascript"),
        "pnpm test": ("pnpm", "javascript"),
        "yarn test": ("yarn", "javascript"),
        "cargo test": ("cargo", "rust"),
        "go test": ("go", "go"),
        "dotnet test": ("dotnet", ".net"),
        "mvn test": ("maven", "java"),
        "gradle test": ("gradle", "java"),
    }
    framework = ""
    for marker, (_, label) in frameworks.items():
        if marker in lower:
            framework = label
            break
    if not framework:
        return None
    output = (stdout + "\n" + stderr).strip()
    passed = failed = skipped = None
    patterns = [
        (r"(\d+)\s+passed", "passed"),
        (r"(\d+)\s+failed", "failed"),
        (r"(\d+)\s+skipped", "skipped"),
        (r"tests?\s+(\d+)", "tests"),
        (r"failures?\s+(\d+)", "failures"),
    ]
    values: dict[str, int] = {}
    for pattern, name in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            values[name] = int(match.group(1))
    passed = values.get("passed")
    failed = values.get("failed", values.get("failures"))
    skipped = values.get("skipped")
    if passed is None and values.get("tests") is not None and failed is not None:
        passed = max(0, values["tests"] - failed)
    status = "PASSED" if exit_code == 0 else "FAILED" if exit_code is not None else "UNKNOWN"
    return {"framework": framework, "status": status, "passed": passed, "failed": failed, "skipped": skipped, "output": compact_text(output, 100_000)}
