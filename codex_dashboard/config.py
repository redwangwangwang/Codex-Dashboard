from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, replace
from pathlib import Path


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float = 0.1) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class Config:
    codex_home: Path
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    token: str | None = None
    poll_interval: float = 2.0
    stale_seconds: int = 900
    command_hung_seconds: int = 600
    git_refresh_seconds: int = 15
    codex_bin: str = "codex"
    initial_head_bytes: int = 64 * 1024
    initial_tail_bytes: int = 2 * 1024 * 1024
    max_incremental_bytes: int = 8 * 1024 * 1024
    max_event_bytes: int = 2 * 1024 * 1024
    max_diff_bytes: int = 1024 * 1024

    @classmethod
    def from_env(cls, **overrides: object) -> "Config":
        codex_home = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()
        data_dir = Path(os.getenv("CODEX_DASHBOARD_DATA", "~/.codex-dashboard")).expanduser()
        config = cls(
            codex_home=codex_home,
            data_dir=data_dir,
            host=os.getenv("CODEX_DASHBOARD_HOST", "127.0.0.1"),
            port=_env_int("CODEX_DASHBOARD_PORT", 8765),
            token=os.getenv("CODEX_DASHBOARD_TOKEN") or None,
            poll_interval=_env_float("CODEX_DASHBOARD_POLL", 2.0),
            stale_seconds=_env_int("CODEX_DASHBOARD_STALE_SECONDS", 900),
            command_hung_seconds=_env_int("CODEX_DASHBOARD_COMMAND_HUNG_SECONDS", 600),
            git_refresh_seconds=_env_int("CODEX_DASHBOARD_GIT_REFRESH_SECONDS", 15),
            codex_bin=os.getenv("CODEX_BIN", "codex"),
        )
        clean = {key: value for key, value in overrides.items() if value is not None}
        if clean:
            config = replace(config, **clean)
        config.validate()
        return config

    @property
    def database_path(self) -> Path:
        return self.data_dir / "dashboard.sqlite"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def requires_token(self) -> bool:
        host = self.host.strip().lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return False
        try:
            return not ipaddress.ip_address(host).is_loopback
        except ValueError:
            return True

    def validate(self) -> None:
        if not (1 <= self.port <= 65535):
            raise ValueError("port must be between 1 and 65535")
        if self.requires_token and not self.token:
            raise ValueError("CODEX_DASHBOARD_TOKEN is required when binding outside loopback")
        if self.max_event_bytes < 1024:
            raise ValueError("max_event_bytes is too small")

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
