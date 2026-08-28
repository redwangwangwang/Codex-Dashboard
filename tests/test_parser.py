from __future__ import annotations

import json
import unittest

from codex_dashboard.parser import parse_json_line


class ParserTests(unittest.TestCase):
    def test_legacy_rollout_session_meta(self) -> None:
        thread_id = "11111111-1111-4111-8111-111111111111"
        raw = {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": thread_id, "cwd": "/tmp/repo", "model_provider": "openai"},
        }
        event = parse_json_line(json.dumps(raw), origin="rollout.jsonl", ordinal=0)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.session_id, thread_id)
        self.assertEqual(event.kind, "session.meta")
        self.assertEqual(event.payload["cwd"], "/tmp/repo")

    def test_event_msg_unwraps_user_message(self) -> None:
        thread_id = "22222222-2222-4222-8222-222222222222"
        raw = {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "thread_id": thread_id, "message": "Build the dashboard"},
        }
        event = parse_json_line(json.dumps(raw), origin="rollout.jsonl", ordinal=1)
        assert event is not None
        self.assertEqual(event.kind, "message.user")
        self.assertEqual(event.actor, "user")
        self.assertEqual(event.text, "Build the dashboard")

    def test_exec_command_completion_normalizes_output(self) -> None:
        thread_id = "33333333-3333-4333-8333-333333333333"
        raw = {
            "type": "exec_command_end",
            "thread_id": thread_id,
            "call_id": "call-1",
            "command": ["python", "-m", "unittest"],
            "stdout": "OK",
            "stderr": "",
            "exit_code": 0,
        }
        event = parse_json_line(json.dumps(raw), origin="exec.jsonl", ordinal=2)
        assert event is not None
        self.assertEqual(event.kind, "command.completed")
        self.assertEqual(event.payload["command"], "python -m unittest")
        self.assertEqual(event.payload["stdout"], "OK")

    def test_plan_steps_are_canonical(self) -> None:
        raw = {
            "type": "plan_update",
            "thread_id": "44444444-4444-4444-8444-444444444444",
            "steps": [
                {"step": "Inspect", "status": "done", "weight": 2},
                {"step": "Implement", "status": "in_progress"},
            ],
        }
        event = parse_json_line(json.dumps(raw), origin="exec.jsonl", ordinal=3)
        assert event is not None
        self.assertEqual(event.kind, "plan.updated")
        self.assertEqual(event.payload["steps"][0]["status"], "completed")
        self.assertEqual(event.payload["steps"][1]["status"], "in_progress")

    def test_invalid_json_is_ignored(self) -> None:
        self.assertIsNone(parse_json_line("{not json", origin="broken", ordinal=0))


if __name__ == "__main__":
    unittest.main()
