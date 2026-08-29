from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .collector import Collector
from .config import AppConfig
from .db import Database
from .models import ActionInput, HostInput, Settings


class EventBus:
    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in tuple(self.clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try: queue.get_nowait()
                except asyncio.QueueEmpty: pass
                try: queue.put_nowait({"type": "resync.required"})
                except asyncio.QueueFull: pass

    async def stream(self):
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.clients.add(queue)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 20)
                    yield "event: update\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.clients.discard(queue)


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig.from_env()
    db = Database(config.database)
    bus = EventBus()
    collector = Collector(db, config.codex_home, bus.publish)
    task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal task
        task = asyncio.create_task(collector.loop(config.poll_seconds), name="codex-control-center-collector")
        yield
        collector.stop()
        if task:
            task.cancel()
            try: await task
            except (asyncio.CancelledError, Exception): pass

    app = FastAPI(title="Codex Control Center", version="0.2.0", lifespan=lifespan)
    app.state.db = db
    app.state.collector = collector
    app.state.bus = bus

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": "0.2.0", "hosts": len(db.list_hosts())}

    @app.get("/api/settings", response_model=Settings)
    def get_settings() -> Settings:
        return db.get_settings()

    @app.put("/api/settings", response_model=Settings)
    async def put_settings(value: Settings) -> Settings:
        result = db.put_settings(value)
        await bus.publish({"type": "settings.changed", "locale": value.locale})
        return result

    @app.get("/api/hosts")
    def hosts() -> list[dict[str, Any]]:
        return db.list_hosts()

    @app.post("/api/hosts", status_code=201)
    async def add_host(value: HostInput) -> dict[str, Any]:
        result = db.create_host(value)
        await bus.publish({"type": "host.changed", "host_id": result["id"]})
        return result

    @app.put("/api/hosts/{host_id}")
    async def update_host(host_id: str, value: HostInput) -> dict[str, Any]:
        result = db.update_host(host_id, value)
        if not result:
            raise HTTPException(404, "host not found")
        await bus.publish({"type": "host.changed", "host_id": host_id})
        return result

    @app.delete("/api/hosts/{host_id}", status_code=204)
    async def delete_host(host_id: str, confirm: bool = Query(False)) -> None:
        host = db.get_host(host_id)
        if not host:
            raise HTTPException(404, "host not found")
        if not confirm:
            raise HTTPException(409, "destructive operation requires confirm=true")
        if host.get("managed_app_server_pid"):
            try: await collector.stop_app_server(host_id)
            except Exception: pass
        if not db.delete_host(host_id):
            raise HTTPException(404, "host not found")
        await bus.publish({"type": "host.deleted", "host_id": host_id})

    @app.post("/api/hosts/{host_id}/test")
    async def test_host(host_id: str) -> dict[str, Any]:
        try: return await collector.test_host(host_id)
        except KeyError: raise HTTPException(404, "host not found")
        except Exception as exc: raise HTTPException(502, str(exc))

    @app.post("/api/hosts/{host_id}/sync")
    async def sync_host(host_id: str) -> dict[str, Any]:
        if not db.get_host(host_id):
            raise HTTPException(404, "host not found")
        count = await collector.sync_host(host_id)
        collector.evaluate_attention()
        return {"ok": True, "events": count}

    @app.post("/api/hosts/{host_id}/app-server/start")
    async def start_app_server(host_id: str) -> dict[str, Any]:
        try: return await collector.start_app_server(host_id)
        except KeyError: raise HTTPException(404, "host not found")
        except Exception as exc: raise HTTPException(502, str(exc))

    @app.post("/api/hosts/{host_id}/app-server/stop")
    async def stop_app_server(host_id: str, body: ActionInput) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, "stop requires confirm=true")
        try: return await collector.stop_app_server(host_id)
        except KeyError: raise HTTPException(404, "host not found")
        except Exception as exc: raise HTTPException(502, str(exc))

    @app.get("/api/sessions")
    def sessions(
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), q: str = Query("", max_length=200),
        host_id: str | None = None, lifecycle: str | None = None, tag: str | None = None,
    ) -> dict[str, Any]:
        items, total = db.list_sessions(limit=limit, offset=offset, query=q, host_id=host_id, lifecycle=lifecycle, tag=tag)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/session/{session_key:path}")
    def session(session_key: str) -> dict[str, Any]:
        item = db.get_session_raw(session_key)
        if not item: raise HTTPException(404, "session not found")
        item["events"] = db.events(session_key, limit=80)
        return item

    @app.get("/api/sessions/{session_key:path}/events")
    def events(session_key: str, limit: int = Query(200, ge=1, le=1000), before_id: int | None = None) -> list[dict[str, Any]]:
        if not db.get_session_raw(session_key): raise HTTPException(404, "session not found")
        return db.events(session_key, limit=limit, before_id=before_id)

    @app.post("/api/sessions/{session_key:path}/actions/{action}")
    def session_action(session_key: str, action: str, body: ActionInput) -> dict[str, Any]:
        item = db.get_session_raw(session_key)
        if not item: raise HTTPException(404, "session not found")
        if action not in item.get("capabilities", []):
            db.audit("operator", f"session.{action}", session_key, "denied", {"reason": "capability unavailable"})
            raise HTTPException(409, "connected Codex adapter did not advertise this capability")
        if action in {"interrupt", "reject"} and not body.confirm:
            raise HTTPException(409, "action requires confirm=true")
        db.audit("operator", f"session.{action}", session_key, "queued", {"text": body.text})
        return {"accepted": True, "status": "queued", "note": "adapter dispatch is capability-gated"}

    @app.get("/api/attention")
    def attention(status: str = Query("open"), limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
        collector.evaluate_attention()
        return db.alerts(status=status, limit=limit)

    @app.post("/api/attention/{alert_id}/resolve")
    async def resolve(alert_id: str, resolution: str = Query("resolved", pattern="^(resolved|false_positive|silenced)$")) -> dict[str, Any]:
        if not db.resolve_alert(alert_id, resolution): raise HTTPException(404, "alert not found")
        await bus.publish({"type": "attention.changed", "alert_id": alert_id})
        return {"ok": True}

    @app.get("/api/stream")
    async def stream(request: Request):
        async def generator():
            async for chunk in bus.stream():
                if await request.is_disconnected(): break
                yield chunk
        return StreamingResponse(generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static / "index.html")

    return app


app = create_app()
