from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: Any, default: str | None = None) -> str:
    if value is None:
        return default or utcnow()
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    text = str(value).strip()
    if not text:
        return default or utcnow()
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        return default or utcnow()


def to_epoch(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_loads(value: str | bytes | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, UnicodeDecodeError):
        return default


def stable_hash(*parts: Any, length: int = 40) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, bytes):
            payload = part
        elif isinstance(part, (dict, list, tuple)):
            payload = json_dumps(part).encode("utf-8", "replace")
        else:
            payload = str(part).encode("utf-8", "replace")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()[:length]


def first_present(mapping: dict[str, Any], paths: Iterable[str], default: Any = None) -> Any:
    for path in paths:
        current: Any = mapping
        ok = True
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                ok = False
                break
            current = current[key]
        if ok and current is not None:
            return current
    return default


def extract_uuid(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        match = _UUID_RE.search(str(value))
        if match:
            return match.group(0).lower()
    return None


def compact_text(value: Any, limit: int = 20_000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                chunks.append(str(first_present(item, ("text", "input_text", "output_text", "content"), "")))
        text = "\n".join(filter(None, chunks))
    elif isinstance(value, dict):
        text = str(first_present(value, ("text", "message", "content", "output"), json_dumps(value)))
    else:
        text = str(value)
    text = text.replace("\x00", "").strip()
    if len(text) > limit:
        return text[:limit] + f"\n… <truncated {len(text) - limit} chars>"
    return text


def command_text(command: Any) -> str:
    if isinstance(command, str):
        return command
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if isinstance(command, dict):
        return command_text(first_present(command, ("command", "cmd", "argv"), command))
    return compact_text(command)


def safe_resolve(path: str | Path, roots: Iterable[Path]) -> Path:
    resolved = Path(path).expanduser().resolve()
    allowed = [root.expanduser().resolve() for root in roots]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise ValueError(f"path is outside allowed roots: {resolved}")
    return resolved


def is_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))
