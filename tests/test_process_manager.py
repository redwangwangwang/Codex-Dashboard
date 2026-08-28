from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from codex_dashboard.db import Database
from codex_dashboard.engine import ProjectionEngine
from codex_dashboard.process_manager import ProcessManager
from tests.helpers import make_config


class ProcessManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = self.root / "fake-codex"
        self.db = None

    def tearDown(self) -> None:
        self.temp.cleanup()

    def manager(self, script: str) -> ProcessManager:
        self.fake.write_text("#!/usr/bin/env python3\n" + script, encoding="utf-8")
        self.fake.chmod(0o755)
        config = make_config(self.root, codex_bin=str(self.fake))
        self.db = Database(config.database_path)
        engine = ProjectionEngine(self.db, config)
        return ProcessManager(config, self.db, engine)

    def wait_for(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("timed out waiting for managed process state")

    def test_normal_process_exit_is_idle(self) -> None:
        manager = self.manager(
            "import json, time\n"
            "print(json.dumps({'type':'turn_started'}), flush=True)\n"
            "print(json.dumps({'type':'agent_message','message':'done'}), flush=True)\n"
            "print(json.dumps({'type':'turn_completed'}), flush=True)\n"
        )
        session_id = manager.start("Do work", self.root)
        assert self.db is not None
        self.wait_for(lambda: (self.db.get_session(session_id, event_limit=0) or {}).get("pid") is None)
        session = self.db.get_session(session_id)
        assert session is not None
        self.assertEqual(session["status"], "IDLE")
        self.assertNotEqual(session["status"], "COMPLETED")

    @unittest.skipUnless(os.name == "posix", "POSIX process signals required")
    def test_pause_continue_and_cancel_only_owned_process(self) -> None:
        manager = self.manager(
            "import json, time\n"
            "print(json.dumps({'type':'turn_started'}), flush=True)\n"
            "time.sleep(20)\n"
        )
        session_id = manager.start("Wait", self.root)
        assert self.db is not None
        self.wait_for(lambda: (self.db.get_session(session_id, event_limit=0) or {}).get("status") == "RUNNING")
        manager.pause(session_id)
        self.assertEqual(self.db.get_session(session_id, event_limit=0)["status"], "PAUSED")
        manager.continue_process(session_id)
        self.assertEqual(self.db.get_session(session_id, event_limit=0)["status"], "RUNNING")
        manager.cancel(session_id, timeout=1)
        self.wait_for(lambda: (self.db.get_session(session_id, event_limit=0) or {}).get("status") == "CANCELLED")

    def test_external_session_has_no_destructive_capabilities(self) -> None:
        manager = self.manager("pass\n")
        assert self.db is not None
        self.db.ensure_session("external", source="rollout", status="RUNNING", managed=False)
        session = self.db.get_session("external", event_limit=0)
        assert session is not None
        capabilities = manager.capabilities(session)
        self.assertFalse(capabilities["pause"])
        self.assertFalse(capabilities["cancel"])


if __name__ == "__main__":
    unittest.main()
