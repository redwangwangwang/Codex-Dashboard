from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from codex_dashboard.server import DashboardHTTPServer
from codex_dashboard.service import DashboardService
from tests.helpers import make_config


class HTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_config(self.root)
        self.service = DashboardService(self.config)
        self.server = DashboardHTTPServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.service.stop()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body).encode() if body is not None else None
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
            request_headers["Content-Length"] = str(len(payload))
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        result = json.loads(raw) if raw and "application/json" in (response.getheader("Content-Type") or "") else raw
        connection.close()
        return response.status, result, response

    def test_health_and_static_app(self) -> None:
        status, payload, response = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        status, html, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Codex Control Center", html)

    def test_create_edit_and_read_idle_task(self) -> None:
        status, task, _ = self.request("POST", "/api/tasks", {
            "prompt": "Build a test dashboard",
            "title": "HTTP task",
            "cwd": str(self.root),
            "start": False,
        })
        self.assertEqual(status, 201)
        self.assertEqual(task["status"], "RUNNING")  # draft user input is evidence that work is ready
        task_id = task["id"]
        status, updated, _ = self.request("PATCH", f"/api/tasks/{task_id}", {"title": "Renamed"})
        self.assertEqual(status, 200)
        self.assertEqual(updated["title"], "Renamed")
        status, detail, _ = self.request("GET", f"/api/tasks/{task_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["id"], task_id)
        self.assertFalse(detail["progress_known"])

    def test_demo_and_overview(self) -> None:
        status, result, _ = self.request("POST", "/api/demo", {})
        self.assertEqual(status, 201)
        self.assertEqual(result["count"], 4)
        status, overview, _ = self.request("GET", "/api/overview")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(overview["total"], 4)
        self.assertGreaterEqual(overview["need_attention"], 1)

    def test_settings_validation(self) -> None:
        status, settings, _ = self.request("PUT", "/api/settings", {"stale_seconds": 120, "poll_interval": 0.5})
        self.assertEqual(status, 200)
        self.assertEqual(settings["stale_seconds"], 120)
        status, payload, _ = self.request("PUT", "/api/settings", {"poll_interval": 0})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_sse_starts_with_refresh_event(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/api/events")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("text/event-stream", response.getheader("Content-Type"))
        first = response.fp.readline().decode("utf-8")
        self.assertEqual(first.strip(), "event: refresh")
        connection.close()


class AuthenticationTests(unittest.TestCase):
    def test_non_loopback_configuration_requires_bearer_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory), host="0.0.0.0", token="secret-token")
            service = DashboardService(config)
            server = DashboardHTTPServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 401)
                connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/health", headers={"Authorization": "Bearer secret-token"})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)
                service.stop()


if __name__ == "__main__":
    unittest.main()
