from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from codex_dashboard.collector import Collector
from codex_dashboard.db import Database
from codex_dashboard.engine import ProjectionEngine
from tests.helpers import make_config


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_config(self.root, git_refresh_seconds=9999)
        self.db = Database(self.config.database_path)
        self.engine = ProjectionEngine(self.db, self.config)
        self.collector = Collector(self.config, self.db, self.engine)
        self.session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.rollout_dir = self.config.codex_home / "sessions" / "2026" / "08" / "29"
        self.rollout_dir.mkdir(parents=True)
        self.rollout = self.rollout_dir / f"rollout-2026-08-29T00-00-00-{self.session_id}.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _line(self, type_name: str, payload: dict | None = None) -> str:
        return json.dumps({"timestamp": "2026-08-29T00:00:00Z", "type": type_name, "payload": payload or {}}, ensure_ascii=False)

    def test_incremental_scan_is_idempotent(self) -> None:
        self.rollout.write_text(
            self._line("session_meta", {"id": self.session_id, "cwd": str(self.root), "title": "Collector test"}) + "\n" +
            self._line("event_msg", {"type": "turn_started", "thread_id": self.session_id, "turn_id": "t1"}) + "\n",
            encoding="utf-8",
        )
        self.assertTrue(self.collector.scan_file(self.rollout))
        first = self.db.get_session(self.session_id)
        assert first is not None
        self.assertEqual(len(first["events"]), 2)
        self.assertFalse(self.collector.scan_file(self.rollout))
        second = self.db.get_session(self.session_id)
        assert second is not None
        self.assertEqual(len(second["events"]), 2)

    def test_partial_line_is_held_until_newline(self) -> None:
        first = self._line("session_meta", {"id": self.session_id, "title": "Partial"}) + "\n"
        second = self._line("event_msg", {"type": "agent_message", "thread_id": self.session_id, "message": "complete line"})
        split = len(second) // 2
        self.rollout.write_text(first + second[:split], encoding="utf-8")
        self.collector.scan_file(self.rollout)
        before = self.db.get_session(self.session_id)
        assert before is not None
        self.assertEqual(len(before["events"]), 1)
        with self.rollout.open("a", encoding="utf-8") as handle:
            handle.write(second[split:] + "\n")
        self.collector.scan_file(self.rollout)
        after = self.db.get_session(self.session_id)
        assert after is not None
        self.assertEqual(len(after["events"]), 2)
        self.assertEqual(after["events"][0]["text"], "complete line")

    def test_truncate_and_rewrite_is_detected_by_head_hash(self) -> None:
        self.rollout.write_text(self._line("session_meta", {"id": self.session_id, "title": "Old"}) + "\n", encoding="utf-8")
        self.collector.scan_file(self.rollout)
        self.rollout.write_text(
            self._line("session_meta", {"id": self.session_id, "title": "New and longer title"}) + "\n" +
            self._line("event_msg", {"type": "user_message", "thread_id": self.session_id, "message": "rewritten"}) + "\n",
            encoding="utf-8",
        )
        self.collector.scan_file(self.rollout)
        session = self.db.get_session(self.session_id)
        assert session is not None
        self.assertGreaterEqual(len(session["events"]), 3)
        self.assertEqual(session["title"], "New and longer title")

    def test_state_database_discovers_thread_metadata_read_only(self) -> None:
        state_path = self.config.codex_home / "state_5.sqlite"
        with sqlite3.connect(state_path) as connection:
            connection.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,cwd TEXT,model TEXT,updated_at TEXT)")
            connection.execute(
                "INSERT INTO threads VALUES(?,?,?,?,?)",
                (self.session_id, "State DB task", str(self.root), "gpt-test", "2026-08-29T00:00:00Z"),
            )
        self.assertTrue(self.collector.discover_state_database())
        session = self.db.get_session(self.session_id)
        assert session is not None
        self.assertEqual(session["title"], "State DB task")
        self.assertEqual(session["model"], "gpt-test")


if __name__ == "__main__":
    unittest.main()
