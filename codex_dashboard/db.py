from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from .util import json_dumps, json_loads, utcnow

_SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  thread_id TEXT,
  title TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL DEFAULT '',
  repo_root TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'rollout',
  status TEXT NOT NULL DEFAULT 'DISCOVERED',
  phase TEXT NOT NULL DEFAULT '',
  progress REAL,
  progress_known INTEGER NOT NULL DEFAULT 0,
  plan_version INTEGER NOT NULL DEFAULT 0,
  managed INTEGER NOT NULL DEFAULT 0,
  pid INTEGER,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  last_event_at TEXT,
  finished_at TEXT,
  summary TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  attention_reason TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_thread ON sessions(thread_id);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL UNIQUE,
  timestamp TEXT NOT NULL,
  kind TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system',
  text TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, timestamp DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, timestamp DESC);

CREATE TABLE IF NOT EXISTS commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  call_id TEXT NOT NULL,
  command TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'RUNNING',
  started_at TEXT,
  ended_at TEXT,
  exit_code INTEGER,
  stdout TEXT NOT NULL DEFAULT '',
  stderr TEXT NOT NULL DEFAULT '',
  last_output_at TEXT,
  duration_ms INTEGER,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(session_id, call_id)
);
CREATE INDEX IF NOT EXISTS idx_commands_session ON commands(session_id, started_at DESC);

CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  call_id TEXT NOT NULL,
  tool TEXT NOT NULL DEFAULT '',
  arguments_json TEXT NOT NULL DEFAULT '{}',
  result_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'RUNNING',
  started_at TEXT,
  ended_at TEXT,
  duration_ms INTEGER,
  UNIQUE(session_id, call_id)
);
CREATE INDEX IF NOT EXISTS idx_tools_session ON tool_calls(session_id, started_at DESC);

CREATE TABLE IF NOT EXISTS file_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_key TEXT NOT NULL,
  path TEXT NOT NULL,
  action TEXT NOT NULL DEFAULT 'modified',
  additions INTEGER,
  deletions INTEGER,
  patch TEXT NOT NULL DEFAULT '',
  timestamp TEXT NOT NULL,
  UNIQUE(session_id, event_key, path)
);
CREATE INDEX IF NOT EXISTS idx_files_session ON file_changes(session_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS test_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  run_key TEXT NOT NULL,
  command TEXT NOT NULL DEFAULT '',
  framework TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  passed INTEGER,
  failed INTEGER,
  skipped INTEGER,
  duration_ms INTEGER,
  output TEXT NOT NULL DEFAULT '',
  timestamp TEXT NOT NULL,
  UNIQUE(session_id, run_key)
);
CREATE INDEX IF NOT EXISTS idx_tests_session ON test_runs(session_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  explanation TEXT NOT NULL DEFAULT '',
  progress REAL,
  steps_json TEXT NOT NULL,
  UNIQUE(session_id, version)
);
CREATE INDEX IF NOT EXISTS idx_plans_session ON plans(session_id, version DESC);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  alert_key TEXT NOT NULL,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'OPEN',
  count INTEGER NOT NULL DEFAULT 1,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  acknowledged_at TEXT,
  resolved_at TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(session_id, alert_key)
);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(state, severity, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_session ON alerts(session_id, state, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  timestamp TEXT NOT NULL,
  action TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'dashboard',
  result TEXT NOT NULL DEFAULT 'ok',
  detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS offsets (
  path TEXT PRIMARY KEY,
  inode TEXT NOT NULL DEFAULT '',
  offset INTEGER NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 0,
  mtime_ns INTEGER NOT NULL DEFAULT 0,
  partial BLOB NOT NULL DEFAULT X'',
  head_hash TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

_JSON_COLUMNS = {
    "metadata_json": "metadata",
    "payload_json": "payload",
    "raw_json": "raw",
    "arguments_json": "arguments",
    "steps_json": "steps",
    "evidence_json": "evidence",
    "detail_json": "detail",
}


class Database:
    """Small SQLite event store.

    Connections are intentionally short lived. Collectors and HTTP request threads never
    share a cursor; WAL mode lets readers continue while one writer updates projections.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self.initialize()

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5, check_same_thread=False)
        else:
            connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self) -> None:
        with self._write_lock, self.connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO settings(key,value_json,updated_at) VALUES('revision','0',?)",
                (utcnow(),),
            )

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for column, target in _JSON_COLUMNS.items():
            if column in result:
                result[target] = json_loads(result.pop(column), {})
        for key in ("managed", "progress_known", "archived"):
            if key in result:
                result[key] = bool(result[key])
        return result

    def bump_revision(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT value_json FROM settings WHERE key='revision'").fetchone()
        current = int(json_loads(row[0], 0)) if row else 0
        value = current + 1
        connection.execute(
            "INSERT INTO settings(key,value_json,updated_at) VALUES('revision',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            (json_dumps(value), utcnow()),
        )
        return value

    def revision(self) -> int:
        with self.connect(readonly=True) as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key='revision'").fetchone()
            return int(json_loads(row[0], 0)) if row else 0

    def set_setting(self, key: str, value: Any) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (key, json_dumps(value), utcnow()),
            )
            self.bump_revision(connection)

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect(readonly=True) as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
            return json_loads(row[0], default) if row else default

    def settings(self) -> dict[str, Any]:
        with self.connect(readonly=True) as connection:
            return {row["key"]: json_loads(row["value_json"]) for row in connection.execute("SELECT * FROM settings")}

    def ensure_session(self, session_id: str, **fields: Any) -> None:
        now = fields.get("updated_at") or utcnow()
        metadata = fields.pop("metadata", {})
        values = {
            "thread_id": fields.get("thread_id") or session_id,
            "title": fields.get("title") or "",
            "cwd": fields.get("cwd") or "",
            "repo_root": fields.get("repo_root") or "",
            "model": fields.get("model") or "",
            "source": fields.get("source") or "rollout",
            "status": fields.get("status") or "DISCOVERED",
            "managed": 1 if fields.get("managed") else 0,
            "started_at": fields.get("started_at"),
            "updated_at": now,
            "last_event_at": fields.get("last_event_at"),
            "metadata_json": json_dumps(metadata),
        }
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO sessions(
                  id,thread_id,title,cwd,repo_root,model,source,status,managed,
                  started_at,updated_at,last_event_at,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  thread_id=COALESCE(NULLIF(excluded.thread_id,''),sessions.thread_id),
                  title=CASE WHEN excluded.title='' THEN sessions.title ELSE excluded.title END,
                  cwd=CASE WHEN excluded.cwd='' THEN sessions.cwd ELSE excluded.cwd END,
                  repo_root=CASE WHEN excluded.repo_root='' THEN sessions.repo_root ELSE excluded.repo_root END,
                  model=CASE WHEN excluded.model='' THEN sessions.model ELSE excluded.model END,
                  source=CASE WHEN sessions.source='managed' THEN sessions.source ELSE excluded.source END,
                  managed=MAX(sessions.managed,excluded.managed),
                  started_at=COALESCE(sessions.started_at,excluded.started_at),
                  updated_at=MAX(sessions.updated_at,excluded.updated_at),
                  last_event_at=COALESCE(excluded.last_event_at,sessions.last_event_at)
                """,
                (session_id, *values.values()),
            )
            self.bump_revision(connection)

    def update_session(self, session_id: str, **fields: Any) -> None:
        allowed = {
            "thread_id", "title", "cwd", "repo_root", "model", "source", "status", "phase",
            "progress", "progress_known", "plan_version", "managed", "pid", "started_at", "updated_at",
            "last_event_at", "finished_at", "summary", "error", "attention_reason", "archived",
            "metadata_json",
        }
        normalized: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "metadata":
                normalized["metadata_json"] = json_dumps(value)
            elif key in allowed:
                if key in {"progress_known", "managed", "archived"}:
                    value = 1 if value else 0
                normalized[key] = value
        normalized.setdefault("updated_at", utcnow())
        if not normalized:
            return
        assignments = ",".join(f"{key}=?" for key in normalized)
        with self.transaction() as connection:
            connection.execute(f"UPDATE sessions SET {assignments} WHERE id=?", (*normalized.values(), session_id))
            self.bump_revision(connection)

    def insert_event(self, event: Mapping[str, Any]) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO events(
                  session_id,source_id,timestamp,kind,actor,text,payload_json,raw_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    event["session_id"], event["source_id"], event["timestamp"], event["kind"],
                    event.get("actor", "system"), event.get("text", ""),
                    json_dumps(event.get("payload", {})), json_dumps(event.get("raw", {})),
                ),
            )
            inserted = cursor.rowcount > 0
            if inserted:
                connection.execute(
                    "UPDATE sessions SET last_event_at=?,updated_at=MAX(updated_at,?) WHERE id=?",
                    (event["timestamp"], event["timestamp"], event["session_id"]),
                )
                self.bump_revision(connection)
            return inserted

    def event_exists(self, source_id: str) -> bool:
        with self.connect(readonly=True) as connection:
            return connection.execute("SELECT 1 FROM events WHERE source_id=?", (source_id,)).fetchone() is not None

    def upsert_command(self, session_id: str, call_id: str, **fields: Any) -> None:
        defaults = {
            "command": "", "cwd": "", "status": "RUNNING", "started_at": None, "ended_at": None,
            "exit_code": None, "stdout": "", "stderr": "", "last_output_at": None,
            "duration_ms": None, "metadata_json": json_dumps(fields.pop("metadata", {})),
        }
        defaults.update({k: v for k, v in fields.items() if k in defaults})
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO commands(
                  session_id,call_id,command,cwd,status,started_at,ended_at,exit_code,stdout,stderr,
                  last_output_at,duration_ms,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id,call_id) DO UPDATE SET
                  command=CASE WHEN excluded.command='' THEN commands.command ELSE excluded.command END,
                  cwd=CASE WHEN excluded.cwd='' THEN commands.cwd ELSE excluded.cwd END,
                  status=excluded.status,
                  started_at=COALESCE(commands.started_at,excluded.started_at),
                  ended_at=COALESCE(excluded.ended_at,commands.ended_at),
                  exit_code=COALESCE(excluded.exit_code,commands.exit_code),
                  stdout=CASE WHEN excluded.stdout='' THEN commands.stdout ELSE excluded.stdout END,
                  stderr=CASE WHEN excluded.stderr='' THEN commands.stderr ELSE excluded.stderr END,
                  last_output_at=COALESCE(excluded.last_output_at,commands.last_output_at),
                  duration_ms=COALESCE(excluded.duration_ms,commands.duration_ms),
                  metadata_json=CASE WHEN excluded.metadata_json='{}' THEN commands.metadata_json ELSE excluded.metadata_json END
                """,
                (session_id, call_id, *defaults.values()),
            )
            self.bump_revision(connection)

    def upsert_tool(self, session_id: str, call_id: str, **fields: Any) -> None:
        values = {
            "tool": fields.get("tool", ""), "arguments_json": json_dumps(fields.get("arguments", {})),
            "result_text": fields.get("result_text", ""), "status": fields.get("status", "RUNNING"),
            "started_at": fields.get("started_at"), "ended_at": fields.get("ended_at"),
            "duration_ms": fields.get("duration_ms"),
        }
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO tool_calls(session_id,call_id,tool,arguments_json,result_text,status,started_at,ended_at,duration_ms)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id,call_id) DO UPDATE SET
                  tool=CASE WHEN excluded.tool='' THEN tool_calls.tool ELSE excluded.tool END,
                  arguments_json=CASE WHEN excluded.arguments_json='{}' THEN tool_calls.arguments_json ELSE excluded.arguments_json END,
                  result_text=CASE WHEN excluded.result_text='' THEN tool_calls.result_text ELSE excluded.result_text END,
                  status=excluded.status,started_at=COALESCE(tool_calls.started_at,excluded.started_at),
                  ended_at=COALESCE(excluded.ended_at,tool_calls.ended_at),duration_ms=COALESCE(excluded.duration_ms,tool_calls.duration_ms)
                """,
                (session_id, call_id, *values.values()),
            )
            self.bump_revision(connection)

    def add_file_change(self, session_id: str, event_key: str, path: str, **fields: Any) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO file_changes(session_id,event_key,path,action,additions,deletions,patch,timestamp)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(session_id,event_key,path) DO UPDATE SET
                action=excluded.action,additions=COALESCE(excluded.additions,file_changes.additions),
                deletions=COALESCE(excluded.deletions,file_changes.deletions),
                patch=CASE WHEN excluded.patch='' THEN file_changes.patch ELSE excluded.patch END,timestamp=excluded.timestamp""",
                (session_id, event_key, path, fields.get("action", "modified"), fields.get("additions"),
                 fields.get("deletions"), fields.get("patch", ""), fields.get("timestamp", utcnow())),
            )
            self.bump_revision(connection)

    def add_test_run(self, session_id: str, run_key: str, **fields: Any) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO test_runs(session_id,run_key,command,framework,status,passed,failed,skipped,duration_ms,output,timestamp)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,run_key) DO UPDATE SET
                status=excluded.status,passed=excluded.passed,failed=excluded.failed,skipped=excluded.skipped,
                duration_ms=excluded.duration_ms,output=excluded.output,timestamp=excluded.timestamp""",
                (session_id, run_key, fields.get("command", ""), fields.get("framework", ""), fields.get("status", "UNKNOWN"),
                 fields.get("passed"), fields.get("failed"), fields.get("skipped"), fields.get("duration_ms"),
                 fields.get("output", ""), fields.get("timestamp", utcnow())),
            )
            self.bump_revision(connection)

    def add_plan(self, session_id: str, version: int, timestamp: str, steps: list[dict[str, Any]], explanation: str, progress: float | None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO plans(session_id,version,timestamp,explanation,progress,steps_json) VALUES(?,?,?,?,?,?)",
                (session_id, version, timestamp, explanation, progress, json_dumps(steps)),
            )
            connection.execute(
                "UPDATE sessions SET plan_version=?,progress=?,progress_known=?,updated_at=? WHERE id=?",
                (version, progress, 1 if progress is not None else 0, timestamp, session_id),
            )
            self.bump_revision(connection)

    def upsert_alert(self, session_id: str, alert_key: str, severity: str, title: str, message: str = "", evidence: Any = None, timestamp: str | None = None) -> None:
        timestamp = timestamp or utcnow()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO alerts(session_id,alert_key,severity,title,message,state,count,first_seen_at,last_seen_at,evidence_json)
                VALUES(?,?,?,?,?,'OPEN',1,?,?,?) ON CONFLICT(session_id,alert_key) DO UPDATE SET
                severity=excluded.severity,title=excluded.title,message=excluded.message,state='OPEN',
                count=alerts.count+1,last_seen_at=excluded.last_seen_at,resolved_at=NULL,evidence_json=excluded.evidence_json""",
                (session_id, alert_key, severity, title, message, timestamp, timestamp, json_dumps(evidence or {})),
            )
            connection.execute("UPDATE sessions SET attention_reason=?,updated_at=? WHERE id=?", (title, timestamp, session_id))
            self.bump_revision(connection)

    def resolve_alert(self, session_id: str, alert_key: str, timestamp: str | None = None) -> None:
        timestamp = timestamp or utcnow()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE alerts SET state='RESOLVED',resolved_at=?,last_seen_at=? WHERE session_id=? AND alert_key=? AND state!='RESOLVED'",
                (timestamp, timestamp, session_id, alert_key),
            )
            row = connection.execute("SELECT title FROM alerts WHERE session_id=? AND state='OPEN' ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,last_seen_at DESC LIMIT 1", (session_id,)).fetchone()
            connection.execute("UPDATE sessions SET attention_reason=? WHERE id=?", (row[0] if row else "", session_id))
            self.bump_revision(connection)

    def acknowledge_alert(self, alert_id: int) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE alerts SET state='ACKNOWLEDGED',acknowledged_at=? WHERE id=?", (utcnow(), alert_id))
            self.bump_revision(connection)

    def audit(self, action: str, session_id: str | None = None, *, actor: str = "dashboard", result: str = "ok", detail: Any = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_log(session_id,timestamp,action,actor,result,detail_json) VALUES(?,?,?,?,?,?)",
                (session_id, utcnow(), action, actor, result, json_dumps(detail or {})),
            )
            self.bump_revision(connection)

    def get_offset(self, path: str) -> dict[str, Any] | None:
        with self.connect(readonly=True) as connection:
            row = connection.execute("SELECT * FROM offsets WHERE path=?", (path,)).fetchone()
            return dict(row) if row else None

    def set_offset(self, path: str, *, inode: str, offset: int, size: int, mtime_ns: int, partial: bytes, head_hash: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO offsets(path,inode,offset,size,mtime_ns,partial,head_hash,updated_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET inode=excluded.inode,offset=excluded.offset,size=excluded.size,
                mtime_ns=excluded.mtime_ns,partial=excluded.partial,head_hash=excluded.head_hash,updated_at=excluded.updated_at""",
                (path, inode, offset, size, mtime_ns, partial, head_hash, utcnow()),
            )

    def get_session(self, session_id: str, *, event_limit: int = 300) -> dict[str, Any] | None:
        with self.connect(readonly=True) as connection:
            session = self._decode(connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
            if not session:
                return None
            session["events"] = [self._decode(row) for row in connection.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY timestamp DESC,id DESC LIMIT ?", (session_id, event_limit)
            )]
            for table, key in (("commands", "commands"), ("tool_calls", "tools"), ("file_changes", "files"),
                               ("test_runs", "tests"), ("plans", "plans"), ("alerts", "alerts"), ("audit_log", "audit")):
                order = "version DESC" if table == "plans" else ("last_seen_at DESC" if table == "alerts" else ("timestamp DESC" if table in {"file_changes", "test_runs", "audit_log"} else "id DESC"))
                session[key] = [self._decode(row) for row in connection.execute(
                    f"SELECT * FROM {table} WHERE session_id=? ORDER BY {order} LIMIT 500", (session_id,)
                )]
            return session

    def list_sessions(self, *, status: str | None = None, attention: bool = False, completed: bool | None = None, query: str = "", limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            statuses = [item.strip().upper() for item in status.split(",") if item.strip()]
            clauses.append(f"s.status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if completed is True:
            clauses.append("s.status='COMPLETED'")
        elif completed is False:
            clauses.append("s.status!='COMPLETED'")
        if attention:
            clauses.append("EXISTS(SELECT 1 FROM alerts a2 WHERE a2.session_id=s.id AND a2.state='OPEN')")
        if query:
            clauses.append("(s.title LIKE ? OR s.cwd LIKE ? OR s.id LIKE ? OR s.summary LIKE ?)")
            pattern = f"%{query}%"
            params.extend([pattern] * 4)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""SELECT s.*,
          (SELECT COUNT(*) FROM alerts a WHERE a.session_id=s.id AND a.state='OPEN') AS open_alerts,
          (SELECT MAX(CASE a.severity WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'WARNING' THEN 2 ELSE 1 END) FROM alerts a WHERE a.session_id=s.id AND a.state='OPEN') AS severity_rank,
          (SELECT COUNT(*) FROM file_changes f WHERE f.session_id=s.id) AS changed_files,
          (SELECT status FROM test_runs t WHERE t.session_id=s.id ORDER BY timestamp DESC,id DESC LIMIT 1) AS latest_test_status
          FROM sessions s{where}
          ORDER BY COALESCE(severity_rank,0) DESC,s.updated_at DESC LIMIT ? OFFSET ?"""
        params.extend([max(1, min(limit, 2000)), max(0, offset)])
        with self.connect(readonly=True) as connection:
            return [self._decode(row) for row in connection.execute(sql, params)]

    def overview(self) -> dict[str, Any]:
        with self.connect(readonly=True) as connection:
            status_counts = {row["status"]: row["count"] for row in connection.execute("SELECT status,COUNT(*) count FROM sessions GROUP BY status")}
            alerts = [self._decode(row) for row in connection.execute(
                """SELECT a.*,s.title,s.cwd,s.status session_status FROM alerts a JOIN sessions s ON s.id=a.session_id
                WHERE a.state='OPEN' ORDER BY CASE a.severity WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'WARNING' THEN 2 ELSE 1 END DESC,a.last_seen_at DESC LIMIT 50"""
            )]
            recent = [self._decode(row) for row in connection.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 12")]
            return {
                "revision": self.revision(),
                "total": sum(status_counts.values()),
                "status_counts": status_counts,
                "need_attention": len(alerts),
                "alerts": alerts,
                "recent": recent,
            }

    def running_commands(self) -> list[dict[str, Any]]:
        with self.connect(readonly=True) as connection:
            return [self._decode(row) for row in connection.execute("SELECT * FROM commands WHERE status='RUNNING'")]

    def open_alerts(self, session_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alerts WHERE state='OPEN'"
        params: tuple[Any, ...] = ()
        if session_id:
            sql += " AND session_id=?"
            params = (session_id,)
        sql += " ORDER BY last_seen_at DESC"
        with self.connect(readonly=True) as connection:
            return [self._decode(row) for row in connection.execute(sql, params)]

    def delete_all(self) -> None:
        with self.transaction() as connection:
            for table in ("audit_log", "alerts", "plans", "test_runs", "file_changes", "tool_calls", "commands", "events", "sessions", "offsets"):
                connection.execute(f"DELETE FROM {table}")
            self.bump_revision(connection)
