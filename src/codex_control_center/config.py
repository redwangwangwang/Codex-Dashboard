from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppConfig:
    home: Path
    database: Path
    codex_home: Path
    poll_seconds: float = 4.0

    @classmethod
    def from_env(cls) -> "AppConfig":
        home = Path(os.environ.get("CCC_HOME", "~/.codex-control-center")).expanduser()
        codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        home.mkdir(parents=True, exist_ok=True)
        return cls(
            home=home,
            database=home / "control-center.sqlite3",
            codex_home=codex_home,
            poll_seconds=max(1.0, float(os.environ.get("CCC_POLL_SECONDS", "4"))),
        )
