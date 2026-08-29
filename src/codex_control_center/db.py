from __future__ import annotations

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
