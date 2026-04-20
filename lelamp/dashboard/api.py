"""FastAPI app for the local LeLamp dashboard."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lelamp.dashboard.actions import (
    DashboardActionExecutor,
    build_light_actions,
    build_motion_actions,
)
from lelamp.dashboard.runtime_bridge import DashboardRuntimeBridge
from lelamp.dashboard.samplers import DashboardSamplerLoop
from lelamp.dashboard.state_store import DashboardStateStore
from lelamp.runtime_config import load_runtime_settings


WEB_DIR = Path(__file__).resolve().parent / "web"

_ACTION_META = {
    "startup": {
        "label": "启动灯",
        "running_label": "启动中",
        "section": "motion",
    },
    "play": {
        "label": "播放动作",
        "running_label": "动作中",
        "disabled_label": "暂无动作",
        "section": "motion",
        "requires_recordings": True,
    },
    "stop": {
        "label": "回到待机",
        "running_label": "回位中",
        "section": "motion",
    },
    "shutdown_pose": {
        "label": "进入休息",
        "running_label": "休息中",
        "section": "motion",
    },
    "light_solid": {
        "label": "暖黄灯光",
        "running_label": "点亮中",
        "section": "light",
    },
    "light_clear": {
        "label": "关闭灯光",
        "running_label": "关闭中",
        "section": "light",
    },
}


def _state_snapshot_for_api(snapshot: dict[str, object], *, expose_transcripts: bool) -> dict[str, object]:
    payload = dict(snapshot)
    voice = payload.get("voice")
    if not expose_transcripts and isinstance(voice, dict):
        voice_payload = dict(voice)
        voice_payload["last_asr_text"] = None
        voice_payload["last_reply_text"] = None
        payload["voice"] = voice_payload
    return payload


def _receipt_response(receipt: Any) -> JSONResponse:
    status_code = 202
    if not receipt.ok and getattr(receipt, "error", None) == "busy":
        status_code = 409
    elif not receipt.ok:
        status_code = 500
    return JSONResponse(status_code=status_code, content=dict(vars(receipt)))


def _catalog_action_key(active_action: str | None) -> str | None:
    if active_action is None:
        return None
    if active_action.startswith("play:"):
        return "play"
    if active_action == "light:solid":
        return "light_solid"
    if active_action == "light:clear":
        return "light_clear"
    return active_action


def _running_label(action_key: str, active_action: str | None) -> str:
    meta = _ACTION_META[action_key]
    running_label = meta["running_label"]
    if action_key == "play" and active_action and ":" in active_action:
        return running_label.format(name=active_action.split(":", 1)[1])
    return running_label


def _action_catalog(
    snapshot: dict[str, object],
    recordings: list[str],
    *,
    busy: bool,
    active_action: str | None,
) -> dict[str, dict[str, object]]:
    active_key = _catalog_action_key(active_action)
    motion_status = snapshot.get("motion", {}).get("status", "unknown")
    light_status = snapshot.get("light", {}).get("status", "unknown")
    catalog: dict[str, dict[str, object]] = {}

    for action_key, meta in _ACTION_META.items():
        section_status = motion_status if meta["section"] == "motion" else light_status

        if busy:
            if action_key == active_key:
                catalog[action_key] = {
                    "enabled": False,
                    "state": "running",
                    "label": _running_label(action_key, active_action),
                }
            else:
                catalog[action_key] = {
                    "enabled": False,
                    "state": "disabled",
                    "label": "执行中",
                }
            continue

        if meta.get("requires_recordings") and not recordings:
            catalog[action_key] = {
                "enabled": False,
                "state": "disabled",
                "label": meta["disabled_label"],
            }
            continue

        if section_status == "error":
            catalog[action_key] = {
                "enabled": True,
                "state": "error",
                "label": "动作异常" if meta["section"] == "motion" else "灯光异常",
            }
            continue

        catalog[action_key] = {
            "enabled": True,
            "state": "enabled",
            "label": meta["label"],
        }

    return catalog


def create_app(
    *,
    settings=None,
    store=None,
    bridge=None,
    executor=None,
    enable_background: bool = True,
) -> FastAPI:
    settings = settings or load_runtime_settings()
    store = store or DashboardStateStore()
    bridge = bridge or DashboardRuntimeBridge(settings)
    executor = executor or DashboardActionExecutor(store)
    sampler = (
        DashboardSamplerLoop(store, settings, bridge, executor)
        if enable_background
        else None
    )

    motion_actions = build_motion_actions(executor, bridge)
    light_actions = build_light_actions(executor, bridge)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        if sampler is not None:
            sampler.start()
        try:
            yield
        finally:
            if sampler is not None:
                sampler.stop()

    app = FastAPI(title="LeLamp Dashboard", lifespan=_lifespan)
    app.mount("/static", StaticFiles(directory=WEB_DIR, check_dir=False), name="static")

    @app.get("/")
    def index() -> FileResponse:
        index_path = WEB_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=404, detail="Dashboard UI not built yet.")
        return FileResponse(index_path)

    @app.get("/api/state")
    def get_state() -> dict[str, object]:
        return _state_snapshot_for_api(
            store.snapshot(),
            expose_transcripts=getattr(settings, "dashboard_expose_transcripts", False),
        )

    @app.get("/api/actions")
    def get_actions() -> dict[str, object]:
        busy = executor.is_busy()
        active_action = executor.current_action()
        snapshot = store.snapshot()
        try:
            recordings = bridge.list_recordings()
        except Exception:
            recordings = list(snapshot.get("motion", {}).get("available_recordings", []))

        return {
            "busy": busy,
            "active_action": active_action,
            "recordings": recordings,
            "poll_ms": settings.dashboard_poll_ms,
            "config": {
                "dashboard_host": settings.dashboard_host,
                "dashboard_port": settings.dashboard_port,
                "poll_ms": settings.dashboard_poll_ms,
            },
            "actions": _action_catalog(
                snapshot,
                recordings,
                busy=busy,
                active_action=active_action,
            ),
        }

    @app.post("/api/actions/startup")
    def post_startup() -> JSONResponse:
        return _receipt_response(motion_actions["startup"]())

    @app.post("/api/actions/play")
    def post_play(payload: dict[str, str]) -> JSONResponse:
        name = payload.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="Missing recording name.")
        return _receipt_response(motion_actions["play"](name))

    @app.post("/api/actions/shutdown_pose")
    def post_shutdown_pose() -> JSONResponse:
        return _receipt_response(motion_actions["shutdown_pose"]())

    @app.post("/api/actions/stop")
    def post_stop() -> JSONResponse:
        return _receipt_response(motion_actions["stop"]())

    @app.post("/api/lights/solid")
    def post_solid(payload: dict[str, int]) -> JSONResponse:
        required = ("red", "green", "blue")
        if any(channel not in payload for channel in required):
            raise HTTPException(status_code=400, detail="Missing RGB channel value.")
        if any(
            not isinstance(payload[channel], int) or isinstance(payload[channel], bool)
            for channel in required
        ):
            raise HTTPException(status_code=400, detail="RGB values must be integers.")
        if any(payload[channel] < 0 or payload[channel] > 255 for channel in required):
            raise HTTPException(status_code=400, detail="RGB values must be between 0 and 255.")
        return _receipt_response(
            light_actions["solid"](payload["red"], payload["green"], payload["blue"])
        )

    @app.post("/api/lights/clear")
    def post_clear() -> JSONResponse:
        return _receipt_response(light_actions["clear"]())

    return app


if __name__ == "__main__":
    runtime_settings = load_runtime_settings()
    uvicorn.run(
        "lelamp.dashboard.api:create_app",
        factory=True,
        host=runtime_settings.dashboard_host,
        port=runtime_settings.dashboard_port,
    )
