from pathlib import Path

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
