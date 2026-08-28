from __future__ import annotations

from pathlib import Path
from typing import Any

from codex_dashboard.config import Config
from codex_dashboard.parser import ParsedEvent
from codex_dashboard.util import stable_hash, utcnow


def make_config(root: Path, **overrides: Any) -> Config:
    values = {
        "codex_home": root / "codex",
        "data_dir": root / "dashboard",
        "host": "127.0.0.1",
        "port": 8765,
        "poll_interval": 0.1,
        "stale_seconds": 60,
        "command_hung_seconds": 60,
        "git_refresh_seconds": 60,
        "codex_bin": "codex",
    }
    values.update(overrides)
    config = Config(**values)
    config.prepare()
    config.codex_home.mkdir(parents=True, exist_ok=True)
    return config


def event(session_id: str, kind: str, payload: dict[str, Any] | None = None, *, text: str = "", source: str = "test") -> ParsedEvent:
    payload = payload or {}
    timestamp = payload.get("timestamp") or utcnow()
    return ParsedEvent(
        session_id=session_id,
        source_id=stable_hash(source, session_id, kind, payload, text),
        timestamp=timestamp,
        kind=kind,
        actor="user" if kind == "message.user" else "assistant" if kind == "message.agent" else "system",
        text=text,
        payload=payload,
        raw={"type": kind, "payload": payload},
    )
