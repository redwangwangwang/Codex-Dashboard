from __future__ import annotations

import hmac
import json
import mimetypes
import os
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import Config
from .service import DashboardService
from .util import json_dumps

_STATIC_ROOT = Path(__file__).with_name("static")
_MAX_BODY = 2 * 1024 * 1024


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: DashboardService):
        self.service = service
        super().__init__(address, DashboardRequestHandler)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("CODEX_DASHBOARD_QUIET") != "1":
            super().log_message(format, *args)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, status: int, payload: Any) -> None:
        data = json_dumps(payload).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_error_json(self, status: int, code: str, message: str, detail: Any = None) -> None:
        payload: dict[str, Any] = {"error": {"code": code, "message": message}}
        if detail is not None:
            payload["error"]["detail"] = detail
        self._send_json(status, payload)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > _MAX_BODY:
            raise ValueError("request body is too large")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _authorized(self, parsed: Any) -> bool:
        config = self.server.service.config
        if not config.token and not config.requires_token:
            return True
        expected = config.token or ""
        header = self.headers.get("Authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
        supplied = supplied or self.headers.get("X-API-Token", "")
        if not supplied:
            supplied = parse_qs(parsed.query).get("token", [""])[0]
        return bool(expected and hmac.compare_digest(supplied, expected))

    @staticmethod
    def _params(parsed: Any) -> dict[str, str]:
        return {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}

    def do_OPTIONS(self) -> None:  # intentionally no permissive cross-origin CORS
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers()
        self.send_header("Allow", "GET, HEAD, POST, PATCH, PUT, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and not self._authorized(parsed):
            self._send_error_json(HTTPStatus.UNAUTHORIZED, "unauthorized", "A valid dashboard token is required.")
            return
        try:
            if parsed.path == "/api/health":
                self._send_json(HTTPStatus.OK, self.server.service.health())
            elif parsed.path == "/api/doctor":
                self._send_json(HTTPStatus.OK, self.server.service.doctor())
            elif parsed.path == "/api/overview":
                self._send_json(HTTPStatus.OK, self.server.service.overview())
            elif parsed.path in {"/api/tasks", "/api/sessions"}:
                self._send_json(HTTPStatus.OK, {"items": self.server.service.list_tasks(self._params(parsed))})
            elif parsed.path == "/api/settings":
                self._send_json(HTTPStatus.OK, self.server.service.get_settings())
            elif parsed.path == "/api/events":
                self._send_sse(parsed)
            elif parsed.path.startswith("/api/tasks/") or parsed.path.startswith("/api/sessions/"):
                self._get_task_route(parsed)
            else:
                self._serve_static(parsed.path)
        except KeyError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Task not found: {exc.args[0]}")
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except BrokenPipeError:
            return
        except Exception as exc:
            self.server.service.db.audit("http.get", result="error", detail={"path": parsed.path, "error": repr(exc)})
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "The request could not be completed.")

    def _get_task_route(self, parsed: Any) -> None:
        parts = [unquote(item) for item in parsed.path.split("/") if item]
        if len(parts) < 3:
            raise KeyError("")
        session_id = parts[2]
        if len(parts) == 4 and parts[3] == "diff":
            params = self._params(parsed)
            payload = self.server.service.task_diff(
                session_id,
                path=params.get("path") or None,
                staged=params.get("staged", "").lower() in {"1", "true", "yes"},
            )
            self._send_json(HTTPStatus.OK, payload)
            return
        self._send_json(HTTPStatus.OK, self.server.service.get_task(session_id))

    def do_POST(self) -> None:
        self._mutate("POST")

    def do_PATCH(self) -> None:
        self._mutate("PATCH")

    def do_PUT(self) -> None:
        self._mutate("PUT")

    def _mutate(self, method: str) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._send_error_json(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "Writes are only accepted by the API.")
            return
        if not self._authorized(parsed):
            self._send_error_json(HTTPStatus.UNAUTHORIZED, "unauthorized", "A valid dashboard token is required.")
            return
        try:
            payload = self._read_json()
            if method == "POST" and parsed.path in {"/api/tasks", "/api/sessions"}:
                self._send_json(HTTPStatus.CREATED, self.server.service.create_task(payload))
                return
            if method == "POST" and parsed.path == "/api/demo":
                self._send_json(HTTPStatus.CREATED, self.server.service.seed_demo(reset=bool(payload.get("reset"))))
                return
            if method == "PUT" and parsed.path == "/api/settings":
                self._send_json(HTTPStatus.OK, self.server.service.update_settings(payload))
                return
            if parsed.path.startswith("/api/tasks/") or parsed.path.startswith("/api/sessions/"):
                parts = [unquote(item) for item in parsed.path.split("/") if item]
                if len(parts) < 3:
                    raise ValueError("missing task id")
                session_id = parts[2]
                if method == "PATCH" and len(parts) == 3:
                    self._send_json(HTTPStatus.OK, self.server.service.update_task(session_id, payload))
                    return
                if method == "POST" and len(parts) == 5 and parts[3] == "actions":
                    self._send_json(HTTPStatus.OK, self.server.service.action(session_id, parts[4], payload))
                    return
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "API route not found.")
        except KeyError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", f"Task not found: {exc.args[0]}")
        except (ValueError, RuntimeError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except Exception as exc:
            self.server.service.db.audit("http.write", result="error", detail={"path": parsed.path, "error": repr(exc)})
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "The request could not be completed.")

    def _send_sse(self, parsed: Any) -> None:
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        notifier = self.server.service.notifier
        cursor = notifier.value
        initial = json_dumps({"revision": self.server.service.db.revision(), "reason": "connected"})
        self.wfile.write(f"event: refresh\ndata: {initial}\n\n".encode("utf-8"))
        self.wfile.flush()
        deadline = time.monotonic() + 30 * 60
        while time.monotonic() < deadline:
            next_cursor = notifier.wait(cursor, timeout=15)
            if next_cursor > cursor:
                cursor = next_cursor
                data = json_dumps({"revision": self.server.service.db.revision(), "reason": "changed"})
                message = f"event: refresh\nid: {cursor}\ndata: {data}\n\n"
            else:
                message = ": keep-alive\n\n"
            self.wfile.write(message.encode("utf-8"))
            self.wfile.flush()

    def _serve_static(self, url_path: str) -> None:
        relative = url_path.lstrip("/") or "index.html"
        candidate = (_STATIC_ROOT / relative).resolve()
        root = _STATIC_ROOT.resolve()
        if candidate != root and root not in candidate.parents:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Static asset not found.")
            return
        if not candidate.is_file():
            candidate = root / "index.html"
        if not candidate.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "Dashboard assets are missing.")
            return
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def serve(config: Config, *, ready: threading.Event | None = None) -> None:
    service = DashboardService(config)
    service.start()
    server = DashboardHTTPServer((config.host, config.port), service)
    if ready:
        ready.set()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.shutdown()
        server.server_close()
        service.stop()
