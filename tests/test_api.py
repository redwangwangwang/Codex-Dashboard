from pathlib import Path

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
