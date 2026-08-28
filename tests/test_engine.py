from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codex_dashboard.db import Database
from codex_dashboard.engine import ProjectionEngine
from tests.helpers import event, make_config


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_config(self.root, stale_seconds=10, command_hung_seconds=10)
        self.db = Database(self.config.database_path)
        self.engine = ProjectionEngine(self.db, self.config)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_progress_is_unknown_without_plan(self) -> None:
        self.engine.ingest(event("s1", "session.meta", {"title": "No plan"}))
        session = self.db.get_session("s1")
        assert session is not None
        self.assertFalse(session["progress_known"])
        self.assertIsNone(session["progress"])

    def test_replan_is_versioned_and_progress_can_decrease(self) -> None:
        self.engine.ingest(event("s2", "session.meta", {"title": "Replan"}))
        self.engine.ingest(event("s2", "plan.updated", {"steps": [
            {"text": "A", "status": "completed", "weight": 1},
            {"text": "B", "status": "completed", "weight": 1},
        ]}, source="plan-one"))
        first = self.db.get_session("s2")
        assert first is not None
        self.assertEqual(first["progress"], 100.0)
        self.engine.ingest(event("s2", "plan.updated", {"steps": [
            {"text": "A", "status": "completed", "weight": 1},
            {"text": "B", "status": "pending", "weight": 1},
            {"text": "C", "status": "pending", "weight": 1},
            {"text": "D", "status": "pending", "weight": 1},
        ]}, source="plan-two"))
        second = self.db.get_session("s2")
        assert second is not None
        self.assertEqual(second["progress"], 25.0)
        self.assertEqual(second["plan_version"], 2)
        self.assertEqual(len(second["plans"]), 2)

    def test_input_required_is_critical_and_user_message_resolves_it(self) -> None:
        self.engine.ingest(event("s3", "input.required", {"question": "Which region?"}, text="Which region?"))
        waiting = self.db.get_session("s3")
        assert waiting is not None
        self.assertEqual(waiting["status"], "WAITING_INPUT")
        self.assertEqual(waiting["alerts"][0]["severity"], "CRITICAL")
        self.engine.ingest(event("s3", "message.user", {"message": "us-east-1"}, text="us-east-1"))
        resumed = self.db.get_session("s3")
        assert resumed is not None
        self.assertEqual(resumed["status"], "RUNNING")
        self.assertFalse(any(alert["state"] == "OPEN" for alert in resumed["alerts"]))

    def test_turn_completion_is_idle_not_completed(self) -> None:
        self.engine.ingest(event("s4", "turn.started", {}))
        self.engine.ingest(event("s4", "turn.completed", {}))
        session = self.db.get_session("s4")
        assert session is not None
        self.assertEqual(session["status"], "IDLE")

    def test_process_exit_zero_is_idle_not_completed(self) -> None:
        self.engine.ingest(event("s5", "turn.started", {}))
        self.engine.managed_process_exited("s5", 0)
        session = self.db.get_session("s5")
        assert session is not None
        self.assertEqual(session["status"], "IDLE")

    def test_third_consecutive_test_failure_escalates(self) -> None:
        for index in range(3):
            self.engine.ingest(event("s6", "command.completed", {
                "call_id": f"test-{index}",
                "command": "pytest tests/test_api.py",
                "exit_code": 1,
                "stderr": "1 failed, 8 passed",
            }, source=f"failure-{index}"))
        session = self.db.get_session("s6")
        assert session is not None
        repeated = [alert for alert in session["alerts"] if alert["alert_key"] == "repeated-test-failure"]
        self.assertTrue(repeated)
        self.assertEqual(repeated[0]["severity"], "CRITICAL")

    def test_recent_command_output_prevents_hung_alert(self) -> None:
        now = datetime.now(timezone.utc)
        started = (now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
        output = (now - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
        self.engine.ingest(event("s7", "command.started", {"call_id": "long", "command": "build", "timestamp": started}))
        self.engine.ingest(event("s7", "command.output", {"call_id": "long", "stdout": "still working", "timestamp": output}, source="output"))
        self.engine.reconcile(now.isoformat().replace("+00:00", "Z"))
        self.assertFalse(any(alert["alert_key"] == "command-hung:long" for alert in self.db.open_alerts("s7")))


if __name__ == "__main__":
    unittest.main()
