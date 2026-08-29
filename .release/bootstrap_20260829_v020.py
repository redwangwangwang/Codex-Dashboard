from __future__ import annotations

"""Guarded source materializer for Codex Dashboard v0.2.0.

This file is used only when the normal Git push channel cannot publish the already
verified source tree.  The workflow checks for a complete v0.2.0 tree first and
runs this fallback only when the repository is otherwise incomplete.
"""

from pathlib import Path

FILES: dict[str, str] = {}

FILES["pyproject.toml"] = r'''[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "codex-control-center"
version = "0.2.0"
description = "Local-first, evidence-first dashboard for local and SSH-hosted Codex sessions"
readme = "README.md"
requires-python = ">=3.11"
license = {text = "MIT"}
authors = [{name = "Codex Dashboard contributors"}]
dependencies = [
  "fastapi>=0.115,<1",
  "pydantic>=2.8,<3",
  "uvicorn>=0.30,<1",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]

[project.scripts]
codex-control-center = "codex_control_center.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}
include-package-data = true

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
codex_control_center = ["static/*"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]
'''

FILES["README.md"] = r'''# Codex Control Center

A local-first, evidence-first web dashboard for observing and managing Codex CLI
sessions across this computer and multiple SSH hosts.

## v0.2.0 highlights

- Runtime language selection: Simplified Chinese, English, Japanese, and Korean.
- SSH host profiles using an identity file or ssh-agent; remote passwords are not
  accepted or stored.
- Strict host-key checking by default, optional jump hosts, incremental remote
  rollout ingestion, connection health, reconnect backoff, and managed remote
  app-server lifecycle.
- Host-scoped session identity so equal Codex thread IDs on different servers do
  not collide.
- Local and remote repository, branch, HEAD, dirty/ahead/behind, conflict, diff,
  process, disk, context, token, and rate-limit evidence.
- Need Attention inbox, browser notifications, concurrent-worktree warnings,
  tags, search, filters, and bounded pagination.
- Conservative status: when Codex does not expose a denominator or capability,
  the UI shows Unknown/Unavailable instead of inventing progress or success.

## Install and run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
codex-control-center --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`.

The default database is `~/.codex-control-center/control-center.sqlite3`.
Override paths with:

```bash
export CCC_HOME=/path/to/control-center-data
export CODEX_HOME=/path/to/.codex
```

## SSH model

Settings → Remote hosts supports host, user, port, identity file, jump host,
Codex home, and allowed workspace roots.  The collector executes bounded,
read-only probes and incrementally reads rollout JSONL files.  It never stores a
remote password.  Unknown or changed host keys fail closed unless the operator
explicitly enables first-use enrollment for that host.

Observation does not imply control.  Start/stop of a dashboard-owned remote
app-server is available separately; session actions remain disabled unless the
adapter has advertised that capability.

## API

- `GET/PUT /api/settings`
- `GET/POST /api/hosts`
- `PUT/DELETE /api/hosts/{host_id}`
- `POST /api/hosts/{host_id}/test`
- `POST /api/hosts/{host_id}/sync`
- `POST /api/hosts/{host_id}/app-server/start`
- `POST /api/hosts/{host_id}/app-server/stop`
- `GET /api/sessions`
- `GET /api/sessions/{session_key}`
- `GET /api/sessions/{session_key}/events`
- `GET /api/attention`
- `POST /api/attention/{alert_id}/resolve`
- `GET /api/stream` (SSE)

See `docs/COMMUNITY_RESEARCH_2026-08.md` and
`docs/SSH_REMOTE_SECURITY.md` for product and trust-boundary details.
'''

FILES["LICENSE"] = r'''MIT License

Copyright (c) 2026 Codex Dashboard contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

FILES["CHANGELOG.md"] = r'''# Changelog

## 0.2.0 — 2026-08-29

- Added runtime-selectable multilingual UI.
- Added secure SSH host profiles and incremental remote session collection.
- Added host-scoped session identity and dashboard-owned app-server lifecycle.
- Added token/context/rate-limit and storage health projections.
- Added browser notifications, tags, bounded pagination, and worktree collision
  attention items.
- Hardened secret redaction so numeric token telemetry remains observable while
  credential-shaped fields are removed.
'''

FILES["src/codex_control_center/__init__.py"] = r'''"""Codex Control Center."""

__version__ = "0.2.0"
'''

FILES["src/codex_control_center/config.py"] = r'''from __future__ import annotations

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
'''

FILES["src/codex_control_center/models.py"] = r'''from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Locale = Literal["en-US", "zh-CN", "ja-JP", "ko-KR"]


class NotificationSettings(BaseModel):
    enabled: bool = False
    completed: bool = True
    attention: bool = True
    failure: bool = True
    remote_offline: bool = True
    context_pressure: bool = True
    while_focused: bool = False


class Settings(BaseModel):
    locale: Locale = "en-US"
    page_size: int = Field(default=50, ge=10, le=200)
    context_warning_percent: int = Field(default=85, ge=50, le=99)
    rate_limit_warning_percent: int = Field(default=85, ge=50, le=99)
    stale_minutes: int = Field(default=20, ge=2, le=1440)
    history_warning_mb: int = Field(default=2048, ge=64, le=1_000_000)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)


class HostInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    hostname: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=80)
    port: int = Field(default=22, ge=1, le=65535)
    identity_file: str | None = Field(default=None, max_length=1024)
    jump_host: str | None = Field(default=None, max_length=255)
    codex_home: str = Field(default="~/.codex", max_length=1024)
    workspace_roots: list[str] = Field(default_factory=list, max_length=32)
    enabled: bool = True
    enroll_host_key: bool = False
    poll_seconds: int = Field(default=8, ge=2, le=3600)
    manage_app_server: bool = False
    password: str | None = Field(default=None, exclude=True)

    @field_validator("name", "hostname", "username")
    @classmethod
    def no_control_chars(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("invalid control characters")
        return value

    @field_validator("workspace_roots")
    @classmethod
    def normalize_roots(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            value = value.strip()
            if value and value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def reject_passwords(self) -> "HostInput":
        if self.password:
            raise ValueError("password authentication is not supported; use ssh-agent or an identity file")
        return self


class Host(HostInput):
    id: str
    status: Literal["unknown", "online", "offline", "degraded"] = "unknown"
    last_seen_at: str | None = None
    last_error: str | None = None
    managed_app_server_pid: int | None = None
    created_at: str
    updated_at: str


class Session(BaseModel):
    key: str
    host_id: str
    source_session_id: str
    title: str
    cwd: str | None = None
    repository: str | None = None
    branch: str | None = None
    head: str | None = None
    dirty: bool | None = None
    ahead: int | None = None
    behind: int | None = None
    conflict: bool = False
    lifecycle: str = "unknown"
    stage: str = "unknown"
    interaction: str = "none"
    model: str | None = None
    tags: list[str] = Field(default_factory=list)
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    context_window: int | None = None
    context_percent: float | None = None
    rate_primary_percent: float | None = None
    rate_secondary_percent: float | None = None
    capabilities: list[str] = Field(default_factory=list)
    last_event_at: str
    updated_at: str


class Page(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class ActionInput(BaseModel):
    text: str | None = Field(default=None, max_length=20_000)
    confirm: bool = False
'''

FILES["src/codex_control_center/security.py"] = r'''from __future__ import annotations

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
'''

FILES["src/codex_control_center/db.py"] = r'''from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import HostInput, Settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hosts (
  id TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'unknown',
  last_seen_at TEXT,
  last_error TEXT,
  managed_app_server_pid INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cursors (
  host_id TEXT NOT NULL,
  path TEXT NOT NULL,
  offset INTEGER NOT NULL DEFAULT 0,
  partial BLOB NOT NULL DEFAULT X'',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (host_id, path)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  host_id TEXT NOT NULL,
  session_key TEXT NOT NULL,
  source_session_id TEXT NOT NULL,
  event_time TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source_path TEXT,
  source_offset INTEGER,
  UNIQUE(host_id, source_path, source_offset)
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_key, id DESC);

CREATE TABLE IF NOT EXISTS sessions (
  key TEXT PRIMARY KEY,
  host_id TEXT NOT NULL,
  source_session_id TEXT NOT NULL,
  title TEXT NOT NULL,
  cwd TEXT,
  repository TEXT,
  branch TEXT,
  head TEXT,
  dirty INTEGER,
  ahead INTEGER,
  behind INTEGER,
  conflict INTEGER NOT NULL DEFAULT 0,
  lifecycle TEXT NOT NULL DEFAULT 'unknown',
  stage TEXT NOT NULL DEFAULT 'unknown',
  interaction TEXT NOT NULL DEFAULT 'none',
  model TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  input_tokens INTEGER,
  cached_input_tokens INTEGER,
  output_tokens INTEGER,
  reasoning_tokens INTEGER,
  total_tokens INTEGER,
  context_window INTEGER,
  context_percent REAL,
  rate_primary_percent REAL,
  rate_secondary_percent REAL,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  last_event_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_host ON sessions(host_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_state ON sessions(lifecycle, updated_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  session_key TEXT,
  host_id TEXT,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  occurrences INTEGER NOT NULL DEFAULT 1,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status, severity, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL,
  result TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as db:
            db.executescript(SCHEMA)
            row = db.execute("SELECT 1 FROM settings WHERE id=1").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO settings(id,value_json,updated_at) VALUES(1,?,?)",
                    (Settings().model_dump_json(), utcnow()),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

    def get_settings(self) -> Settings:
        with self.connect() as db:
            row = db.execute("SELECT value_json FROM settings WHERE id=1").fetchone()
        return Settings.model_validate_json(row[0])

    def put_settings(self, settings: Settings) -> Settings:
        with self.connect() as db:
            db.execute("UPDATE settings SET value_json=?,updated_at=? WHERE id=1", (settings.model_dump_json(), utcnow()))
        self.audit("operator", "settings.update", "settings", "ok", settings.model_dump())
        return settings

    def list_hosts(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM hosts ORDER BY created_at").fetchall()
        result = []
        for row in rows:
            item = json.loads(row["value_json"])
            item.update({k: row[k] for k in ("id", "status", "last_seen_at", "last_error", "managed_app_server_pid", "created_at", "updated_at")})
            result.append(item)
        return result

    def get_host(self, host_id: str) -> dict[str, Any] | None:
        return next((h for h in self.list_hosts() if h["id"] == host_id), None)

    def create_host(self, value: HostInput) -> dict[str, Any]:
        now = utcnow()
        host_id = str(uuid.uuid4())
        payload = value.model_dump(exclude={"password"})
        with self.connect() as db:
            db.execute(
                "INSERT INTO hosts(id,value_json,created_at,updated_at) VALUES(?,?,?,?)",
                (host_id, json.dumps(payload), now, now),
            )
        self.audit("operator", "host.create", host_id, "ok", {"name": value.name, "hostname": value.hostname})
        return self.get_host(host_id) or {}

    def update_host(self, host_id: str, value: HostInput) -> dict[str, Any] | None:
        with self.connect() as db:
            cur = db.execute(
                "UPDATE hosts SET value_json=?,updated_at=? WHERE id=?",
                (json.dumps(value.model_dump(exclude={"password"})), utcnow(), host_id),
            )
        if not cur.rowcount:
            return None
        self.audit("operator", "host.update", host_id, "ok", {"name": value.name})
        return self.get_host(host_id)

    def delete_host(self, host_id: str) -> bool:
        with self.connect() as db:
            cur = db.execute("DELETE FROM hosts WHERE id=?", (host_id,))
        if cur.rowcount:
            self.audit("operator", "host.delete", host_id, "ok", {})
        return bool(cur.rowcount)

    def set_host_health(self, host_id: str, status: str, error: str | None = None, *, pid: int | None = None) -> None:
        now = utcnow()
        with self.connect() as db:
            if status == "online":
                db.execute(
                    "UPDATE hosts SET status=?,last_seen_at=?,last_error=NULL,managed_app_server_pid=COALESCE(?,managed_app_server_pid),updated_at=? WHERE id=?",
                    (status, now, pid, now, host_id),
                )
            else:
                db.execute("UPDATE hosts SET status=?,last_error=?,updated_at=? WHERE id=?", (status, (error or "")[:2000], now, host_id))

    def cursor(self, host_id: str, path: str) -> tuple[int, bytes]:
        with self.connect() as db:
            row = db.execute("SELECT offset,partial FROM cursors WHERE host_id=? AND path=?", (host_id, path)).fetchone()
        return (int(row[0]), bytes(row[1])) if row else (0, b"")

    def set_cursor(self, host_id: str, path: str, offset: int, partial: bytes) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO cursors(host_id,path,offset,partial,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(host_id,path) DO UPDATE SET offset=excluded.offset,partial=excluded.partial,updated_at=excluded.updated_at",
                (host_id, path, offset, partial[-1_000_000:], utcnow()),
            )

    def insert_event(self, *, host_id: str, session_key: str, source_session_id: str, event_time: str, event_type: str, payload: dict[str, Any], source_path: str, source_offset: int) -> bool:
        with self.connect() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO events(host_id,session_key,source_session_id,event_time,event_type,payload_json,source_path,source_offset) VALUES(?,?,?,?,?,?,?,?)",
                (host_id, session_key, source_session_id, event_time, event_type, json.dumps(payload, ensure_ascii=False), source_path, source_offset),
            )
        return bool(cur.rowcount)

    def upsert_session(self, item: dict[str, Any]) -> None:
        columns = [
            "key", "host_id", "source_session_id", "title", "cwd", "repository", "branch", "head", "dirty", "ahead", "behind", "conflict",
            "lifecycle", "stage", "interaction", "model", "tags_json", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
            "total_tokens", "context_window", "context_percent", "rate_primary_percent", "rate_secondary_percent", "capabilities_json", "last_event_at", "updated_at",
        ]
        values = []
        for name in columns:
            value = item.get(name)
            if name in {"tags_json", "capabilities_json"} and not isinstance(value, str):
                value = json.dumps(value or [])
            if name in {"dirty", "conflict"} and value is not None:
                value = int(bool(value))
            values.append(value)
        updates = ",".join(f"{name}=excluded.{name}" for name in columns if name not in {"key", "host_id", "source_session_id"})
        sql = f"INSERT INTO sessions({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT(key) DO UPDATE SET {updates}"
        with self.connect() as db:
            db.execute(sql, values)

    def get_session_raw(self, key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE key=?", (key,)).fetchone()
        return self._session_row(row) if row else None

    @staticmethod
    def _session_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["dirty"] = None if item["dirty"] is None else bool(item["dirty"])
        item["conflict"] = bool(item["conflict"])
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
        item["capabilities"] = json.loads(item.pop("capabilities_json") or "[]")
        return item

    def list_sessions(self, *, limit: int, offset: int, query: str = "", host_id: str | None = None, lifecycle: str | None = None, tag: str | None = None) -> tuple[list[dict[str, Any]], int]:
        clauses, params = [], []
        if query:
            clauses.append("(title LIKE ? OR cwd LIKE ? OR repository LIKE ? OR branch LIKE ? OR tags_json LIKE ?)")
            needle = f"%{query}%"
            params.extend([needle] * 5)
        if host_id:
            clauses.append("host_id=?"); params.append(host_id)
        if lifecycle:
            clauses.append("lifecycle=?"); params.append(lifecycle)
        if tag:
            clauses.append("tags_json LIKE ?"); params.append(f'%"{tag}"%')
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            total = int(db.execute("SELECT COUNT(*) FROM sessions" + where, params).fetchone()[0])
            rows = db.execute("SELECT * FROM sessions" + where + " ORDER BY updated_at DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return [self._session_row(row) for row in rows], total

    def events(self, key: str, *, limit: int = 200, before_id: int | None = None) -> list[dict[str, Any]]:
        clause, params = "session_key=?", [key]
        if before_id is not None:
            clause += " AND id<?"; params.append(before_id)
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM events WHERE {clause} ORDER BY id DESC LIMIT ?", params).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def raise_alert(self, *, fingerprint: str, session_key: str | None, host_id: str | None, kind: str, severity: str, title: str, detail: str, evidence: dict[str, Any]) -> None:
        now = utcnow()
        with self.connect() as db:
            db.execute(
                "INSERT INTO alerts(id,fingerprint,session_key,host_id,kind,severity,title,detail,evidence_json,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET status='open',severity=excluded.severity,title=excluded.title,detail=excluded.detail,evidence_json=excluded.evidence_json,last_seen_at=excluded.last_seen_at,occurrences=alerts.occurrences+1,resolved_at=NULL",
                (str(uuid.uuid4()), fingerprint, session_key, host_id, kind, severity, title, detail, json.dumps(evidence), now, now),
            )

    def alerts(self, *, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM alerts WHERE status=? ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,last_seen_at DESC LIMIT ?", (status, limit)).fetchall()
        return [{**dict(row), "evidence": json.loads(row["evidence_json"])} for row in rows]

    def resolve_alert(self, alert_id: str, resolution: str) -> bool:
        now = utcnow()
        with self.connect() as db:
            cur = db.execute("UPDATE alerts SET status=?,resolved_at=? WHERE id=?", (resolution, now, alert_id))
        if cur.rowcount:
            self.audit("operator", "attention.resolve", alert_id, "ok", {"resolution": resolution})
        return bool(cur.rowcount)

    def audit(self, actor: str, action: str, target: str, result: str, detail: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO audit(time,actor,action,target,result,detail_json) VALUES(?,?,?,?,?,?)", (utcnow(), actor, action, target, result, json.dumps(detail)))
'''

FILES["src/codex_control_center/collector.py"] = r'''from __future__ import annotations

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
'''

FILES["src/codex_control_center/api.py"] = r'''from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .collector import Collector
from .config import AppConfig
from .db import Database
from .models import ActionInput, HostInput, Settings


class EventBus:
    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in tuple(self.clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try: queue.get_nowait()
                except asyncio.QueueEmpty: pass
                try: queue.put_nowait({"type": "resync.required"})
                except asyncio.QueueFull: pass

    async def stream(self):
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.clients.add(queue)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 20)
                    yield "event: update\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.clients.discard(queue)


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig.from_env()
    db = Database(config.database)
    bus = EventBus()
    collector = Collector(db, config.codex_home, bus.publish)
    task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal task
        task = asyncio.create_task(collector.loop(config.poll_seconds), name="codex-control-center-collector")
        yield
        collector.stop()
        if task:
            task.cancel()
            try: await task
            except (asyncio.CancelledError, Exception): pass

    app = FastAPI(title="Codex Control Center", version="0.2.0", lifespan=lifespan)
    app.state.db = db
    app.state.collector = collector
    app.state.bus = bus

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": "0.2.0", "hosts": len(db.list_hosts())}

    @app.get("/api/settings", response_model=Settings)
    def get_settings() -> Settings:
        return db.get_settings()

    @app.put("/api/settings", response_model=Settings)
    async def put_settings(value: Settings) -> Settings:
        result = db.put_settings(value)
        await bus.publish({"type": "settings.changed", "locale": value.locale})
        return result

    @app.get("/api/hosts")
    def hosts() -> list[dict[str, Any]]:
        return db.list_hosts()

    @app.post("/api/hosts", status_code=201)
    async def add_host(value: HostInput) -> dict[str, Any]:
        result = db.create_host(value)
        await bus.publish({"type": "host.changed", "host_id": result["id"]})
        return result

    @app.put("/api/hosts/{host_id}")
    async def update_host(host_id: str, value: HostInput) -> dict[str, Any]:
        result = db.update_host(host_id, value)
        if not result:
            raise HTTPException(404, "host not found")
        await bus.publish({"type": "host.changed", "host_id": host_id})
        return result

    @app.delete("/api/hosts/{host_id}", status_code=204)
    async def delete_host(host_id: str, confirm: bool = Query(False)) -> None:
        host = db.get_host(host_id)
        if not host:
            raise HTTPException(404, "host not found")
        if not confirm:
            raise HTTPException(409, "destructive operation requires confirm=true")
        if host.get("managed_app_server_pid"):
            try: await collector.stop_app_server(host_id)
            except Exception: pass
        if not db.delete_host(host_id):
            raise HTTPException(404, "host not found")
        await bus.publish({"type": "host.deleted", "host_id": host_id})

    @app.post("/api/hosts/{host_id}/test")
    async def test_host(host_id: str) -> dict[str, Any]:
        try: return await collector.test_host(host_id)
        except KeyError: raise HTTPException(404, "host not found")
        except Exception as exc: raise HTTPException(502, str(exc))

    @app.post("/api/hosts/{host_id}/sync")
    async def sync_host(host_id: str) -> dict[str, Any]:
        if not db.get_host(host_id):
            raise HTTPException(404, "host not found")
        count = await collector.sync_host(host_id)
        collector.evaluate_attention()
        return {"ok": True, "events": count}

    @app.post("/api/hosts/{host_id}/app-server/start")
    async def start_app_server(host_id: str) -> dict[str, Any]:
        try: return await collector.start_app_server(host_id)
        except KeyError: raise HTTPException(404, "host not found")
        except Exception as exc: raise HTTPException(502, str(exc))

    @app.post("/api/hosts/{host_id}/app-server/stop")
    async def stop_app_server(host_id: str, body: ActionInput) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, "stop requires confirm=true")
        try: return await collector.stop_app_server(host_id)
        except KeyError: raise HTTPException(404, "host not found")
        except Exception as exc: raise HTTPException(502, str(exc))

    @app.get("/api/sessions")
    def sessions(
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), q: str = Query("", max_length=200),
        host_id: str | None = None, lifecycle: str | None = None, tag: str | None = None,
    ) -> dict[str, Any]:
        items, total = db.list_sessions(limit=limit, offset=offset, query=q, host_id=host_id, lifecycle=lifecycle, tag=tag)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/sessions/{session_key:path}")
    def session(session_key: str) -> dict[str, Any]:
        item = db.get_session_raw(session_key)
        if not item: raise HTTPException(404, "session not found")
        item["events"] = db.events(session_key, limit=80)
        return item

    @app.get("/api/sessions/{session_key:path}/events")
    def events(session_key: str, limit: int = Query(200, ge=1, le=1000), before_id: int | None = None) -> list[dict[str, Any]]:
        if not db.get_session_raw(session_key): raise HTTPException(404, "session not found")
        return db.events(session_key, limit=limit, before_id=before_id)

    @app.post("/api/sessions/{session_key:path}/actions/{action}")
    def session_action(session_key: str, action: str, body: ActionInput) -> dict[str, Any]:
        item = db.get_session_raw(session_key)
        if not item: raise HTTPException(404, "session not found")
        if action not in item.get("capabilities", []):
            db.audit("operator", f"session.{action}", session_key, "denied", {"reason": "capability unavailable"})
            raise HTTPException(409, "connected Codex adapter did not advertise this capability")
        if action in {"interrupt", "reject"} and not body.confirm:
            raise HTTPException(409, "action requires confirm=true")
        db.audit("operator", f"session.{action}", session_key, "queued", {"text": body.text})
        return {"accepted": True, "status": "queued", "note": "adapter dispatch is capability-gated"}

    @app.get("/api/attention")
    def attention(status: str = Query("open"), limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
        collector.evaluate_attention()
        return db.alerts(status=status, limit=limit)

    @app.post("/api/attention/{alert_id}/resolve")
    async def resolve(alert_id: str, resolution: str = Query("resolved", pattern="^(resolved|false_positive|silenced)$")) -> dict[str, Any]:
        if not db.resolve_alert(alert_id, resolution): raise HTTPException(404, "alert not found")
        await bus.publish({"type": "attention.changed", "alert_id": alert_id})
        return {"ok": True}

    @app.get("/api/stream")
    async def stream(request: Request):
        async def generator():
            async for chunk in bus.stream():
                if await request.is_disconnected(): break
                yield chunk
        return StreamingResponse(generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static / "index.html")

    return app


app = create_app()
'''

FILES["src/codex_control_center/cli.py"] = r'''from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex Control Center")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("codex_control_center.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
'''

FILES["src/codex_control_center/static/index.html"] = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Codex Control Center</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <header>
    <div><strong>Codex Control Center</strong><span class="version">v0.2.0</span></div>
    <nav>
      <button data-view="sessions" class="active"></button>
      <button data-view="attention"></button>
      <button data-view="hosts"></button>
      <button data-view="settings"></button>
    </nav>
  </header>
  <main>
    <section id="sessions" class="view active">
      <div class="toolbar">
        <input id="search" type="search" autocomplete="off">
        <select id="host-filter"><option value=""></option></select>
        <select id="state-filter"><option value=""></option><option value="running">running</option><option value="completed">completed</option><option value="failed">failed</option></select>
        <button id="refresh"></button>
      </div>
      <div id="summary" class="summary"></div>
      <div class="table-wrap"><table><thead><tr><th data-i18n="title"></th><th data-i18n="host"></th><th data-i18n="state"></th><th data-i18n="repository"></th><th data-i18n="context"></th><th data-i18n="updated"></th></tr></thead><tbody id="session-rows"></tbody></table></div>
      <div class="pager"><button id="prev">‹</button><span id="page-info"></span><button id="next">›</button></div>
    </section>

    <section id="attention" class="view"><div id="attention-list" class="cards"></div></section>

    <section id="hosts" class="view">
      <div class="section-head"><h2 data-i18n="remoteHosts"></h2><button id="add-host" data-i18n="addHost"></button></div>
      <div id="host-list" class="cards"></div>
    </section>

    <section id="settings" class="view">
      <form id="settings-form" class="panel form-grid">
        <label><span data-i18n="language"></span><select id="locale"><option value="en-US">English</option><option value="zh-CN">简体中文</option><option value="ja-JP">日本語</option><option value="ko-KR">한국어</option></select></label>
        <label><span data-i18n="pageSize"></span><input id="page-size" type="number" min="10" max="200"></label>
        <label><span data-i18n="contextThreshold"></span><input id="context-warning" type="number" min="50" max="99"></label>
        <label><span data-i18n="rateThreshold"></span><input id="rate-warning" type="number" min="50" max="99"></label>
        <label class="check"><input id="notifications" type="checkbox"><span data-i18n="browserNotifications"></span></label>
        <button type="submit" data-i18n="save"></button>
      </form>
    </section>
  </main>

  <dialog id="host-dialog">
    <form id="host-form" method="dialog" class="form-grid">
      <input id="host-id" type="hidden">
      <h2 data-i18n="sshHost"></h2>
      <label><span data-i18n="name"></span><input id="host-name" required maxlength="80"></label>
      <label><span data-i18n="hostname"></span><input id="hostname" required></label>
      <label><span data-i18n="username"></span><input id="username" required></label>
      <label><span data-i18n="port"></span><input id="port" type="number" value="22" min="1" max="65535"></label>
      <label><span data-i18n="identityFile"></span><input id="identity-file" placeholder="~/.ssh/id_ed25519"></label>
      <label><span data-i18n="jumpHost"></span><input id="jump-host" placeholder="bastion.example.com"></label>
      <label><span>CODEX_HOME</span><input id="codex-home" value="~/.codex"></label>
      <label><span data-i18n="workspaceRoots"></span><textarea id="workspace-roots" rows="3"></textarea></label>
      <label class="check"><input id="host-enabled" type="checkbox" checked><span data-i18n="enabled"></span></label>
      <label class="check"><input id="enroll-key" type="checkbox"><span data-i18n="enrollHostKey"></span></label>
      <div class="dialog-actions"><button type="button" id="cancel-host" data-i18n="cancel"></button><button value="save" data-i18n="save"></button></div>
    </form>
  </dialog>

  <dialog id="detail-dialog"><div id="detail"></div><button id="close-detail" data-i18n="close"></button></dialog>
  <div id="toast"></div>
  <script src="/static/app.js" defer></script>
</body>
</html>
'''

FILES["src/codex_control_center/static/styles.css"] = r''':root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:#17202a;background:#f4f6f8;color-scheme:light}*{box-sizing:border-box}body{margin:0}header{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.8rem 1.2rem;background:#111827;color:white;box-shadow:0 2px 10px #0002}.version{margin-left:.6rem;color:#94a3b8;font-size:.8rem}nav{display:flex;gap:.35rem}button,input,select,textarea{font:inherit}button{border:1px solid #cbd5e1;border-radius:.55rem;background:white;padding:.5rem .75rem;cursor:pointer}header button{color:#cbd5e1;background:transparent;border-color:#334155}header button.active{color:white;background:#334155}main{max-width:1440px;margin:auto;padding:1rem}.view{display:none}.view.active{display:block}.toolbar,.section-head,.pager{display:flex;align-items:center;gap:.6rem;margin-bottom:1rem}.toolbar input{min-width:18rem;flex:1}.toolbar input,.toolbar select,.form-grid input,.form-grid select,.form-grid textarea{border:1px solid #cbd5e1;border-radius:.55rem;padding:.55rem;background:white}.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.7rem;margin-bottom:1rem}.metric,.panel,.card{background:white;border:1px solid #e2e8f0;border-radius:.8rem;padding:1rem;box-shadow:0 1px 3px #0000000d}.metric strong{display:block;font-size:1.4rem}.table-wrap{overflow:auto;background:white;border:1px solid #e2e8f0;border-radius:.8rem}table{width:100%;border-collapse:collapse;min-width:900px}th,td{text-align:left;padding:.7rem;border-bottom:1px solid #edf2f7;vertical-align:top}tbody tr{cursor:pointer}tbody tr:hover{background:#f8fafc}.pill{display:inline-block;border-radius:999px;padding:.18rem .5rem;background:#e2e8f0;font-size:.78rem;margin:.1rem}.pill.running{background:#dcfce7;color:#166534}.pill.failed,.pill.critical{background:#fee2e2;color:#991b1b}.pill.completed{background:#dbeafe;color:#1e40af}.pill.high{background:#ffedd5;color:#9a3412}.cards{display:grid;gap:.75rem}.card-head{display:flex;justify-content:space-between;gap:1rem}.muted{color:#64748b;font-size:.88rem}.danger{color:#b91c1c}.actions{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.7rem}.form-grid{display:grid;gap:.8rem;max-width:720px}.form-grid label{display:grid;gap:.3rem}.form-grid .check{display:flex;align-items:center;gap:.5rem}.form-grid .check input{width:auto}.dialog-actions{display:flex;justify-content:flex-end;gap:.5rem}dialog{border:0;border-radius:1rem;box-shadow:0 20px 60px #0005;max-width:min(760px,92vw);max-height:90vh;overflow:auto}dialog::backdrop{background:#0f172a88}.pager{justify-content:center;margin-top:1rem}#toast{position:fixed;right:1rem;bottom:1rem;background:#111827;color:white;padding:.8rem 1rem;border-radius:.6rem;opacity:0;transform:translateY(1rem);transition:.2s;pointer-events:none}#toast.show{opacity:1;transform:none}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0f172a;color:#e2e8f0;padding:.8rem;border-radius:.6rem;max-height:22rem;overflow:auto}@media(max-width:760px){header{align-items:flex-start;flex-direction:column}nav{width:100%;overflow:auto}.toolbar{align-items:stretch;flex-direction:column}.toolbar input{min-width:0}.summary{grid-template-columns:repeat(2,minmax(0,1fr))}main{padding:.7rem}}
'''

FILES["src/codex_control_center/static/app.js"] = r'''const I18N={
"en-US":{sessions:"Sessions",attention:"Need Attention",hosts:"Remote hosts",settings:"Settings",search:"Search title, repo, branch or tag",allHosts:"All hosts",allStates:"All states",refresh:"Refresh",title:"Title",host:"Host",state:"State",repository:"Repository",context:"Context",updated:"Updated",remoteHosts:"Remote SSH hosts",addHost:"Add host",language:"Language",pageSize:"Rows per page",contextThreshold:"Context warning (%)",rateThreshold:"Rate-limit warning (%)",browserNotifications:"Browser notifications",save:"Save",sshHost:"SSH host",name:"Name",hostname:"Hostname",username:"Username",port:"Port",identityFile:"Identity file (optional; otherwise ssh-agent)",jumpHost:"Jump host (optional)",workspaceRoots:"Allowed workspace roots (one per line)",enabled:"Enabled",enrollHostKey:"Enroll an unknown host key on first connection",cancel:"Cancel",close:"Close",test:"Test",sync:"Sync",startServer:"Start app-server",stopServer:"Stop app-server",delete:"Delete",resolve:"Resolve",falsePositive:"False positive",unknown:"Unknown",noSessions:"No sessions matched",noAttention:"No open attention items",online:"Online",offline:"Offline",degraded:"Degraded",confirmDelete:"Delete this SSH host? Dashboard-owned app-server will be stopped.",confirmStop:"Stop the dashboard-owned remote app-server?",saved:"Saved",failed:"Operation failed",total:"Total",running:"Running",completed:"Completed",needsInput:"Needs input",events:"Events",tokens:"Tokens",branch:"Branch",worktree:"Worktree"},
"zh-CN":{sessions:"会话",attention:"待处理",hosts:"远程主机",settings:"设置",search:"搜索标题、仓库、分支或标签",allHosts:"全部主机",allStates:"全部状态",refresh:"刷新",title:"标题",host:"主机",state:"状态",repository:"仓库",context:"上下文",updated:"更新时间",remoteHosts:"SSH 远程主机",addHost:"添加主机",language:"界面语言",pageSize:"每页行数",contextThreshold:"上下文告警阈值（%）",rateThreshold:"限流告警阈值（%）",browserNotifications:"浏览器通知",save:"保存",sshHost:"SSH 主机",name:"名称",hostname:"主机名",username:"用户名",port:"端口",identityFile:"私钥路径（可选；否则使用 ssh-agent）",jumpHost:"跳板机（可选）",workspaceRoots:"允许的工作区根目录（每行一个）",enabled:"启用",enrollHostKey:"首次连接时登记未知主机密钥",cancel:"取消",close:"关闭",test:"测试连接",sync:"同步",startServer:"启动 app-server",stopServer:"停止 app-server",delete:"删除",resolve:"处理",falsePositive:"误报",unknown:"未知",noSessions:"没有匹配的会话",noAttention:"当前没有待处理事项",online:"在线",offline:"离线",degraded:"降级",confirmDelete:"删除此 SSH 主机？控制中心拥有的 app-server 将被停止。",confirmStop:"停止控制中心拥有的远程 app-server？",saved:"已保存",failed:"操作失败",total:"总计",running:"运行中",completed:"已完成",needsInput:"需要输入",events:"事件",tokens:"Token",branch:"分支",worktree:"工作树"},
"ja-JP":{sessions:"セッション",attention:"要対応",hosts:"リモートホスト",settings:"設定",search:"タイトル、リポジトリ、ブランチ、タグを検索",allHosts:"すべてのホスト",allStates:"すべての状態",refresh:"更新",title:"タイトル",host:"ホスト",state:"状態",repository:"リポジトリ",context:"コンテキスト",updated:"更新",remoteHosts:"SSH リモートホスト",addHost:"ホストを追加",language:"表示言語",pageSize:"1ページの行数",contextThreshold:"コンテキスト警告（%）",rateThreshold:"レート制限警告（%）",browserNotifications:"ブラウザー通知",save:"保存",sshHost:"SSH ホスト",name:"名前",hostname:"ホスト名",username:"ユーザー名",port:"ポート",identityFile:"秘密鍵（任意。未指定時は ssh-agent）",jumpHost:"踏み台ホスト（任意）",workspaceRoots:"許可するワークスペース（1行1件）",enabled:"有効",enrollHostKey:"初回接続時に未知のホスト鍵を登録",cancel:"キャンセル",close:"閉じる",test:"テスト",sync:"同期",startServer:"app-server を起動",stopServer:"app-server を停止",delete:"削除",resolve:"解決",falsePositive:"誤検知",unknown:"不明",noSessions:"一致するセッションはありません",noAttention:"未処理項目はありません",online:"オンライン",offline:"オフライン",degraded:"縮退",confirmDelete:"この SSH ホストを削除しますか？",confirmStop:"管理対象 app-server を停止しますか？",saved:"保存しました",failed:"操作に失敗しました",total:"合計",running:"実行中",completed:"完了",needsInput:"入力待ち",events:"イベント",tokens:"トークン",branch:"ブランチ",worktree:"ワークツリー"},
"ko-KR":{sessions:"세션",attention:"확인 필요",hosts:"원격 호스트",settings:"설정",search:"제목, 저장소, 브랜치 또는 태그 검색",allHosts:"모든 호스트",allStates:"모든 상태",refresh:"새로고침",title:"제목",host:"호스트",state:"상태",repository:"저장소",context:"컨텍스트",updated:"업데이트",remoteHosts:"SSH 원격 호스트",addHost:"호스트 추가",language:"표시 언어",pageSize:"페이지당 행",contextThreshold:"컨텍스트 경고 (%)",rateThreshold:"속도 제한 경고 (%)",browserNotifications:"브라우저 알림",save:"저장",sshHost:"SSH 호스트",name:"이름",hostname:"호스트명",username:"사용자명",port:"포트",identityFile:"개인 키 경로(선택, 미지정 시 ssh-agent)",jumpHost:"점프 호스트(선택)",workspaceRoots:"허용 작업공간 루트(한 줄에 하나)",enabled:"사용",enrollHostKey:"첫 연결에서 알 수 없는 호스트 키 등록",cancel:"취소",close:"닫기",test:"테스트",sync:"동기화",startServer:"app-server 시작",stopServer:"app-server 중지",delete:"삭제",resolve:"해결",falsePositive:"오탐",unknown:"알 수 없음",noSessions:"일치하는 세션이 없습니다",noAttention:"열린 확인 항목이 없습니다",online:"온라인",offline:"오프라인",degraded:"저하",confirmDelete:"이 SSH 호스트를 삭제할까요?",confirmStop:"관리 중인 app-server를 중지할까요?",saved:"저장됨",failed:"작업 실패",total:"전체",running:"실행 중",completed:"완료",needsInput:"입력 필요",events:"이벤트",tokens:"토큰",branch:"브랜치",worktree:"워크트리"}}
;
const state={settings:null,hosts:[],sessions:[],alerts:[],offset:0,total:0,view:"sessions"};
const $=s=>document.querySelector(s); const $$=s=>[...document.querySelectorAll(s)];
function t(k){const l=state.settings?.locale||localStorage.getItem("ccc-locale")||"en-US";return I18N[l]?.[k]??I18N["en-US"][k]??k}
async function api(url,options={}){const r=await fetch(url,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});if(!r.ok){let m=await r.text();try{m=JSON.parse(m).detail||m}catch{}throw new Error(m||`${r.status}`)}return r.status===204?null:r.json()}
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function translate(){document.documentElement.lang=(state.settings?.locale||"en-US").slice(0,2);$$(`[data-i18n]`).forEach(el=>el.textContent=t(el.dataset.i18n));$("#search").placeholder=t("search");$("#host-filter option").textContent=t("allHosts");$("#state-filter option").textContent=t("allStates");$$(`header button`).forEach((b,i)=>b.textContent=[t("sessions"),t("attention"),t("hosts"),t("settings")][i])}
function toast(msg){const el=$("#toast");el.textContent=msg;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2400)}
function relative(value){if(!value)return t("unknown");const s=Math.round((Date.now()-Date.parse(value))/1000);if(s<60)return `${s}s`;if(s<3600)return `${Math.round(s/60)}m`;if(s<86400)return `${Math.round(s/3600)}h`;return `${Math.round(s/86400)}d`}
async function loadSettings(){state.settings=await api("/api/settings");localStorage.setItem("ccc-locale",state.settings.locale);$("#locale").value=state.settings.locale;$("#page-size").value=state.settings.page_size;$("#context-warning").value=state.settings.context_warning_percent;$("#rate-warning").value=state.settings.rate_limit_warning_percent;$("#notifications").checked=state.settings.notifications.enabled;translate()}
async function loadHosts(){state.hosts=await api("/api/hosts");const filter=$("#host-filter");const selected=filter.value;filter.innerHTML=`<option value="">${esc(t("allHosts"))}</option><option value="local">local</option>`+state.hosts.map(h=>`<option value="${esc(h.id)}">${esc(h.name)}</option>`).join("");filter.value=selected;renderHosts()}
function renderHosts(){const root=$("#host-list");root.innerHTML=state.hosts.length?state.hosts.map(h=>`<article class="card"><div class="card-head"><div><strong>${esc(h.name)}</strong><div class="muted">${esc(h.username)}@${esc(h.hostname)}:${h.port}</div></div><span class="pill ${esc(h.status)}">${esc(t(h.status)||h.status)}</span></div><div class="muted">${h.last_error?`<span class="danger">${esc(h.last_error)}</span>`:`${esc(t("updated"))}: ${esc(relative(h.last_seen_at))}`}</div><div class="actions"><button data-host-action="test" data-id="${h.id}">${t("test")}</button><button data-host-action="sync" data-id="${h.id}">${t("sync")}</button><button data-host-action="start" data-id="${h.id}">${t("startServer")}</button><button data-host-action="stop" data-id="${h.id}">${t("stopServer")}</button><button data-host-action="edit" data-id="${h.id}">${t("settings")}</button><button data-host-action="delete" data-id="${h.id}">${t("delete")}</button></div></article>`).join(""):`<div class="panel muted">${esc(t("noSessions"))}</div>`}
async function loadSessions(){const p=new URLSearchParams({limit:state.settings?.page_size||50,offset:state.offset,q:$("#search").value,host_id:$("#host-filter").value,lifecycle:$("#state-filter").value});const data=await api(`/api/sessions?${p}`);state.sessions=data.items;state.total=data.total;renderSessions()}
function renderSessions(){const hosts=Object.fromEntries(state.hosts.map(h=>[h.id,h.name]));hosts.local="local";const rows=$("#session-rows");rows.innerHTML=state.sessions.length?state.sessions.map(s=>`<tr data-key="${esc(s.key)}"><td><strong>${esc(s.title)}</strong><div>${(s.tags||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join("")}</div></td><td>${esc(hosts[s.host_id]||s.host_id)}</td><td><span class="pill ${esc(s.lifecycle)}">${esc(s.lifecycle||t("unknown"))}</span>${s.interaction!=="none"?`<span class="pill high">${esc(t("needsInput"))}</span>`:""}</td><td>${esc(s.repository||s.cwd||t("unknown"))}<div class="muted">${esc(s.branch||"")} ${esc(s.head||"")}${s.dirty?" • dirty":""}${s.conflict?" • conflict":""}</div></td><td>${s.context_percent==null?t("unknown"):`${Number(s.context_percent).toFixed(1)}%`}<div class="muted">${s.total_tokens==null?"":`${Number(s.total_tokens).toLocaleString()} ${t("tokens")}`}</div></td><td>${esc(relative(s.updated_at))}</td></tr>`).join(""):`<tr><td colspan="6" class="muted">${esc(t("noSessions"))}</td></tr>`;const running=state.sessions.filter(x=>x.lifecycle==="running").length,done=state.sessions.filter(x=>x.lifecycle==="completed").length,input=state.sessions.filter(x=>x.interaction!=="none").length;$("#summary").innerHTML=[[t("total"),state.total],[t("running"),running],[t("completed"),done],[t("needsInput"),input]].map(x=>`<div class="metric"><strong>${x[1]}</strong>${esc(x[0])}</div>`).join("");const size=state.settings?.page_size||50;$("#page-info").textContent=`${state.total?state.offset+1:0}–${Math.min(state.offset+size,state.total)} / ${state.total}`;$("#prev").disabled=state.offset===0;$("#next").disabled=state.offset+size>=state.total}
async function loadAttention(){state.alerts=await api("/api/attention");const root=$("#attention-list");root.innerHTML=state.alerts.length?state.alerts.map(a=>`<article class="card"><div class="card-head"><div><span class="pill ${esc(a.severity)}">${esc(a.severity)}</span> <strong>${esc(a.title)}</strong></div><span class="muted">×${a.occurrences}</span></div><p>${esc(a.detail)}</p><pre>${esc(JSON.stringify(a.evidence,null,2))}</pre><div class="actions"><button data-alert="resolved" data-id="${a.id}">${t("resolve")}</button><button data-alert="false_positive" data-id="${a.id}">${t("falsePositive")}</button></div></article>`).join(""):`<div class="panel muted">${esc(t("noAttention"))}</div>`;maybeNotify()}
async function showSession(key){const s=await api(`/api/sessions/${encodeURIComponent(key)}`);$("#detail").innerHTML=`<h2>${esc(s.title)}</h2><p><span class="pill ${esc(s.lifecycle)}">${esc(s.lifecycle)}</span></p><dl><dt>${t("host")}</dt><dd>${esc(s.host_id)}</dd><dt>${t("worktree")}</dt><dd>${esc(s.cwd||t("unknown"))}</dd><dt>${t("branch")}</dt><dd>${esc(s.branch||t("unknown"))}</dd><dt>${t("tokens")}</dt><dd>${esc(s.total_tokens??t("unknown"))}</dd></dl><h3>${t("events")}</h3>${(s.events||[]).map(e=>`<details><summary>${esc(e.event_type)} · ${esc(e.event_time)}</summary><pre>${esc(JSON.stringify(e.payload,null,2))}</pre></details>`).join("")}`;$("#detail-dialog").showModal()}
function openHost(h={}){$("#host-id").value=h.id||"";$("#host-name").value=h.name||"";$("#hostname").value=h.hostname||"";$("#username").value=h.username||"";$("#port").value=h.port||22;$("#identity-file").value=h.identity_file||"";$("#jump-host").value=h.jump_host||"";$("#codex-home").value=h.codex_home||"~/.codex";$("#workspace-roots").value=(h.workspace_roots||[]).join("\n");$("#host-enabled").checked=h.enabled??true;$("#enroll-key").checked=h.enroll_host_key??false;if(!$("#host-dialog").open)$("#host-dialog").showModal()}
async function saveHost(){const id=$("#host-id").value;const body={name:$("#host-name").value,hostname:$("#hostname").value,username:$("#username").value,port:Number($("#port").value),identity_file:$("#identity-file").value||null,jump_host:$("#jump-host").value||null,codex_home:$("#codex-home").value||"~/.codex",workspace_roots:$("#workspace-roots").value.split("\n").map(x=>x.trim()).filter(Boolean),enabled:$("#host-enabled").checked,enroll_host_key:$("#enroll-key").checked,poll_seconds:8,manage_app_server:false};await api(id?`/api/hosts/${id}`:"/api/hosts",{method:id?"PUT":"POST",body:JSON.stringify(body)});toast(t("saved"));await loadHosts()}
async function hostAction(action,id){const h=state.hosts.find(x=>x.id===id);if(action==="edit")return openHost(h);if(action==="delete"){if(!confirm(t("confirmDelete")))return;await api(`/api/hosts/${id}?confirm=true`,{method:"DELETE"})}else if(action==="stop"){if(!confirm(t("confirmStop")))return;await api(`/api/hosts/${id}/app-server/stop`,{method:"POST",body:JSON.stringify({confirm:true})})}else{const path=action==="start"?"app-server/start":action;await api(`/api/hosts/${id}/${path}`,{method:"POST",body:"{}"})}toast(t("saved"));await Promise.all([loadHosts(),loadSessions(),loadAttention()])}
function maybeNotify(){if(!state.settings?.notifications?.enabled||!("Notification"in window)||Notification.permission!=="granted")return;if(!state.settings.notifications.while_focused&&document.visibilityState==="visible")return;for(const a of state.alerts.slice(0,3)){const k=`ccc-notified-${a.id}-${a.last_seen_at}`;if(!sessionStorage.getItem(k)){new Notification(a.title,{body:a.detail,tag:a.fingerprint});sessionStorage.setItem(k,"1")}}}
function connect(){let wait=1000;const start=()=>{const es=new EventSource("/api/stream");es.addEventListener("update",async()=>{wait=1000;await Promise.all([loadSessions(),loadAttention(),loadHosts()])});es.onopen=()=>wait=1000;es.onerror=()=>{es.close();setTimeout(start,wait);wait=Math.min(wait*2,30000)}};start()}
$$(`header button`).forEach(b=>b.onclick=()=>{$$(`header button,.view`).forEach(x=>x.classList.remove("active"));b.classList.add("active");state.view=b.dataset.view;$(`#${state.view}`).classList.add("active");if(state.view==="attention")loadAttention();if(state.view==="hosts")loadHosts()});$("#refresh").onclick=()=>Promise.all([loadSessions(),loadAttention(),loadHosts()]);let timer;$("#search").oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{state.offset=0;loadSessions()},250)};$("#host-filter").onchange=$("#state-filter").onchange=()=>{state.offset=0;loadSessions()};$("#prev").onclick=()=>{state.offset=Math.max(0,state.offset-(state.settings?.page_size||50));loadSessions()};$("#next").onclick=()=>{state.offset+=state.settings?.page_size||50;loadSessions()};$("#session-rows").onclick=e=>{const row=e.target.closest("tr[data-key]");if(row)showSession(row.dataset.key)};$("#close-detail").onclick=()=>$("#detail-dialog").close();$("#add-host").onclick=()=>openHost();$("#cancel-host").onclick=()=>$("#host-dialog").close();$("#host-form").onsubmit=async e=>{e.preventDefault();try{await saveHost();$("#host-dialog").close()}catch(err){toast(`${t("failed")}: ${err.message}`)}};$("#host-list").onclick=e=>{const b=e.target.closest("[data-host-action]");if(b)hostAction(b.dataset.hostAction,b.dataset.id).catch(err=>toast(`${t("failed")}: ${err.message}`))};$("#attention-list").onclick=async e=>{const b=e.target.closest("[data-alert]");if(!b)return;await api(`/api/attention/${b.dataset.id}/resolve?resolution=${b.dataset.alert}`,{method:"POST",body:"{}"});loadAttention()};$("#settings-form").onsubmit=async e=>{e.preventDefault();const s=structuredClone(state.settings);s.locale=$("#locale").value;s.page_size=Number($("#page-size").value);s.context_warning_percent=Number($("#context-warning").value);s.rate_limit_warning_percent=Number($("#rate-warning").value);s.notifications.enabled=$("#notifications").checked;if(s.notifications.enabled&&Notification.permission==="default")await Notification.requestPermission();state.settings=await api("/api/settings",{method:"PUT",body:JSON.stringify(s)});localStorage.setItem("ccc-locale",s.locale);translate();toast(t("saved"));loadSessions()};
(async()=>{try{await loadSettings();await loadHosts();await Promise.all([loadSessions(),loadAttention()]);connect()}catch(err){toast(`${t("failed")}: ${err.message}`)}})();
'''

FILES["tests/test_security.py"] = r'''from codex_control_center.models import HostInput
from codex_control_center.security import redact, safe_tag_list, ssh_base_argv


def test_redaction_preserves_numeric_usage():
    value = redact({"input_tokens": 120, "access_token": "secret-value", "nested": {"api_key": "abc"}})
    assert value["input_tokens"] == 120
    assert value["access_token"] == "[REDACTED]"
    assert value["nested"]["api_key"] == "[REDACTED]"


def test_host_rejects_password():
    try:
        HostInput(name="x", hostname="example.com", username="u", password="nope")
    except ValueError as exc:
        assert "password" in str(exc)
    else:
        raise AssertionError("password was accepted")


def test_ssh_is_batch_and_strict_by_default():
    host = HostInput(name="x", hostname="example.com", username="u")
    argv = ssh_base_argv(host)
    joined = " ".join(argv)
    assert "BatchMode=yes" in joined
    assert "StrictHostKeyChecking=yes" in joined
    assert argv[-1] == "u@example.com"


def test_tags_are_bounded_and_unique():
    assert safe_tag_list([" a ", "a", "b"], max_items=2) == ["a", "b"]
'''

FILES["tests/test_projection.py"] = r'''from pathlib import Path

from codex_control_center.collector import project_event
from codex_control_center.db import Database


def test_host_scoped_session_identity(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    event = {"type": "turn/completed", "thread_id": "same-id", "timestamp": "2026-08-29T00:00:00Z", "usage": {"input_tokens": 5}}
    assert project_event(db, "host-a", "a.jsonl", 0, event) == "host-a:same-id"
    assert project_event(db, "host-b", "b.jsonl", 0, event) == "host-b:same-id"
    assert db.get_session_raw("host-a:same-id")["input_tokens"] == 5
    assert db.get_session_raw("host-b:same-id")["input_tokens"] == 5


def test_unknown_context_is_not_invented(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    event = {"type": "turn/started", "thread_id": "t", "usage": {"input_tokens": 10}}
    project_event(db, "local", "x", 0, event)
    assert db.get_session_raw("local:t")["context_percent"] is None


def test_context_percent_when_denominator_exists(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    event = {"type": "token_count", "thread_id": "t", "context_tokens": 80, "context_window": 100}
    project_event(db, "local", "x", 0, event)
    assert db.get_session_raw("local:t")["context_percent"] == 80.0
'''

FILES["tests/test_api.py"] = r'''from pathlib import Path

from fastapi.testclient import TestClient

from codex_control_center.api import create_app
from codex_control_center.config import AppConfig


def client(tmp_path: Path) -> TestClient:
    cfg = AppConfig(tmp_path, tmp_path / "db.sqlite3", tmp_path / ".codex", 999)
    return TestClient(create_app(cfg))


def test_health_and_settings(tmp_path: Path):
    with client(tmp_path) as c:
        assert c.get("/api/health").json()["version"] == "0.2.0"
        settings = c.get("/api/settings").json()
        settings["locale"] = "zh-CN"
        assert c.put("/api/settings", json=settings).json()["locale"] == "zh-CN"


def test_host_crud_never_accepts_password(tmp_path: Path):
    with client(tmp_path) as c:
        payload = {"name": "dev", "hostname": "dev.example", "username": "codex", "password": "secret"}
        assert c.post("/api/hosts", json=payload).status_code == 422
        payload.pop("password")
        created = c.post("/api/hosts", json=payload)
        assert created.status_code == 201
        host = created.json()
        assert "password" not in host
        assert c.delete(f"/api/hosts/{host['id']}").status_code == 409
        assert c.delete(f"/api/hosts/{host['id']}?confirm=true").status_code == 204


def test_session_pagination(tmp_path: Path):
    with client(tmp_path) as c:
        db = c.app.state.db
        for i in range(5):
            db.upsert_session({"key": f"local:{i}", "host_id": "local", "source_session_id": str(i), "title": f"Task {i}", "lifecycle": "running", "stage": "unknown", "interaction": "none", "tags_json": [], "capabilities_json": [], "conflict": False, "last_event_at": "2026-08-29T00:00:00Z", "updated_at": f"2026-08-29T00:00:0{i}Z"})
        data = c.get("/api/sessions?limit=2&offset=2").json()
        assert data["total"] == 5
        assert len(data["items"]) == 2


def test_capability_gating(tmp_path: Path):
    with client(tmp_path) as c:
        db = c.app.state.db
        db.upsert_session({"key": "local:t", "host_id": "local", "source_session_id": "t", "title": "Task", "lifecycle": "running", "stage": "unknown", "interaction": "none", "tags_json": [], "capabilities_json": [], "conflict": False, "last_event_at": "2026-08-29T00:00:00Z", "updated_at": "2026-08-29T00:00:00Z"})
        assert c.post("/api/sessions/local:t/actions/interrupt", json={"confirm": True}).status_code == 409
'''

FILES["docs/SSH_REMOTE_SECURITY.md"] = r'''# SSH remote security model

Each SSH host is a separate trust, identity, cursor, and failure domain.

- Authentication uses an OpenSSH identity file or ssh-agent. Passwords are
  rejected by the validation model and never stored.
- Strict host-key checking is enabled by default. First-use enrollment requires
  an explicit per-host setting; changed keys still fail closed.
- Local SSH execution uses an argv array, BatchMode, bounded connection and
  command timeouts, keepalives, and validated host fields.
- Remote shell text is generated only by fixed collectors and quoted paths. The
  HTTP API never accepts an arbitrary remote command.
- Workspace roots can constrain repository probes.
- Incremental cursors prevent repeatedly copying complete history files.
- Session keys include host identity.
- Only app-server processes using the dashboard's own PID file are stopped.
  Existing TUI sessions and unrelated processes are not killed.
- All stored payloads pass through credential redaction before immutable event
  insertion.

Bind the HTTP service to loopback. For another device, use an authenticated VPN
or reverse proxy; do not expose an authentication-disabled instance publicly.
'''

FILES["docs/COMMUNITY_RESEARCH_2026-08.md"] = r'''# Community research: Codex session management (August 2026)

The release review sampled current `openai/codex` issues, app-server protocol
material, and adjacent community dashboards. Requests were grouped into operator
problems instead of copied as an unbounded feature list.

## Implemented in v0.2.0

1. **Remote development and fleet visibility.** Requests such as
   `openai/codex#10450` and `#9224` describe SSH/cloud development and remote
   control. v0.2.0 adds key/agent-based SSH hosts, strict host verification,
   jump-host support, incremental remote histories, host-scoped IDs, health,
   managed app-server lifecycle, and repository/process/disk evidence.
2. **Context, quota, and storage pressure.** `#23794`, `#14593`, `#28879`, and
   `#28224` motivate visible context/rate usage and bounded storage. The
   dashboard projects counters only when present, computes percentages only
   with a denominator, monitors pressure, and uses incremental bounded reads.
3. **Central attention and approvals.** `#2998` and multi-session operator tools
   motivate a single queue. The dashboard combines approval/input, failure,
   conflict, stale, host outage, context/rate, and worktree collision signals.
4. **Completion/background notification.** `#3962` motivates opt-in browser
   notifications for completion and other actionable transitions.
5. **History discoverability.** `#12564` motivates titles; operators also need
   tags, host-aware search, filters, archive, and pagination at fleet scale.
6. **Parallel safety and recovery evidence.** `#11626` and `#9203` motivate
   safer iteration. The dashboard exposes dirty/conflict/diff evidence and
   destructive confirmations, but does not fake a provenance-safe workspace
   rewind.
7. **Localization.** Four runtime-selectable languages are shipped with English
   fallback and no restart requirement.

## Deliberate boundaries

LSP installation (`#8745`), model quota/context policy (`#19464`, `#30364`),
native IDE plugins (`#4313`), Codex plan semantics (`#2101`, `#28969`),
subagent routing (`#2604`, `#31814`), and a chat-plus-workspace `/rewind`
(`#11626`) require Codex, model-provider, or IDE support. The dashboard observes
and capability-gates these surfaces instead of displaying controls that only
change dashboard state.

## Adjacent projects

- `ArnabCodes/codex-dash` is a lightweight local TUI and synced-snapshot board.
- `jstuart0/agentpulse` combines broad multi-agent observation/orchestration and
  an operator inbox.

This project keeps a narrower evidence contract: Codex-native events,
orthogonal states, conservative Unknown semantics, SSH trust boundaries,
repository/worktree collision evidence, and immutable local audit history.

## Source index

- https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md
- https://github.com/openai/codex/issues/10450
- https://github.com/openai/codex/issues/9224
- https://github.com/openai/codex/issues/23794
- https://github.com/openai/codex/issues/3962
- https://github.com/openai/codex/issues/12564
- https://github.com/openai/codex/issues/2998
- https://github.com/openai/codex/issues/11626
- https://github.com/openai/codex/issues/28224
- https://github.com/ArnabCodes/codex-dash
- https://github.com/jstuart0/agentpulse
'''

for relative, content in FILES.items():
    path = Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

print(f"Materialized {len(FILES)} source files")
