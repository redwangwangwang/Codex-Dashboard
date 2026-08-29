from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from .models import HostInput

_SECRET_KEY = re.compile(
    r"(^|_)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|secret|private[_-]?key|cookie)($|_)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PRIVATE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL)
_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)=([^\s;&]+)")


def redact(value: Any) -> Any:
    """Recursively redact credentials while preserving numeric telemetry."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)) and not isinstance(item, (int, float, bool)):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _PRIVATE.sub("[REDACTED PRIVATE KEY]", value)
        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
        return value
    return value


def validate_local_identity(path: str | None) -> str | None:
    if not path:
        return None
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        raise ValueError("identity_file must resolve to an absolute local path")
    return str(expanded)


def ssh_base_argv(host: HostInput, *, connect_timeout: int = 8) -> list[str]:
    """Build argv without a shell; remote command is supplied as the last arg."""
    identity = validate_local_identity(host.identity_file)
    argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={max(2, min(connect_timeout, 30))}",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2",
        "-o", "LogLevel=ERROR",
        "-o", "StrictHostKeyChecking=accept-new" if host.enroll_host_key else "StrictHostKeyChecking=yes",
        "-p", str(host.port),
    ]
    if identity:
        argv += ["-i", identity, "-o", "IdentitiesOnly=yes"]
    if host.jump_host:
        argv += ["-J", host.jump_host]
    argv.append(f"{host.username}@{host.hostname}")
    return argv


def remote_shell(command: str) -> str:
    return "sh -lc " + shlex.quote(command)


def safe_tag_list(value: Any, *, max_items: int = 16, max_length: int = 48) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = " ".join(item.strip().split())[:max_length]
        if item and item not in result:
            result.append(item)
        if len(result) >= max_items:
            break
    return result
