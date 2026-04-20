"""FastAPI loopback server running inside the agent process.

Exposes the long-lived ``AnimationService`` and ``RGBService`` that the agent
already owns. Dashboard and CLI use ``motor_bus.client`` to reach here.

The server runs on ``127.0.0.1`` only. Requests from other hosts are not
accepted (bind address is loopback-only). There is no auth by design: the
surface is equivalent to what any other process on the same machine could do
by opening ``/dev/ttyACM0`` directly.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from contextlib import closing
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .sentinel import SentinelInfo, remove_sentinel, write_sentinel

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770


class PlayRequest(BaseModel):
    recording_name: str


class FramesRequest(BaseModel):
    frames: list[dict[str, float]]


class SolidRequest(BaseModel):
    red: int = Field(ge=0, le=255)
    green: int = Field(ge=0, le=255)
    blue: int = Field(ge=0, le=255)


class PaintRequest(BaseModel):
    colors: list[list[int]]


class WaitCompleteRequest(BaseModel):
    # dashboard's default play timeout is 120s; cap at 180s so a misbehaving
    # client can't pin a uvicorn worker indefinitely.
    timeout: float = Field(default=120.0, ge=0.0, le=180.0)


AnimationServiceError = Callable[[], Optional[str]]


def build_app(
    *,
    animation_service,
    get_animation_service_error: AnimationServiceError,
    rgb_service,
    led_count: int,
) -> FastAPI:
    app = FastAPI(title="LeLamp Motor Bus", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        motion_error = get_animation_service_error()
        return {
            # ``ok`` means "server process is reachable and speaking motor bus
            # protocol". It does NOT promise every domain is usable; clients
            # that need motor/rgb must look at ``motor_ok`` / ``rgb_ok``.
            "ok": True,
            "motor_ok": motion_error is None,
            "rgb_ok": rgb_service is not None,
            "animation_error": motion_error,
            # Retained for backwards compat with the H0 client; superseded by
            # ``rgb_ok``.
            "rgb_available": rgb_service is not None,
            "led_count": led_count,
            "pid": os.getpid(),
        }

    @app.get("/motor/recordings")
    def list_recordings() -> dict[str, Any]:
        try:
            return {"recordings": animation_service.get_available_recordings()}
        except Exception as exc:
            raise HTTPException(500, f"failed to list recordings: {exc}")

    def _arm_playback_gate() -> None:
        # Pre-clears the completion event BEFORE queuing the dispatch so that a
        # subsequent /motor/wait_complete observes the in-flight playback
        # instead of the previous (still-set) done signal. Without this, the
        # queued event is only processed on the next event_loop tick, leaving
        # a racy window where wait_until_playback_complete returns True
        # immediately after a fresh dispatch.
        gate = getattr(animation_service, "_playback_done", None)
        if gate is not None:
            gate.clear()

    @app.post("/motor/play")
    def play(req: PlayRequest) -> dict[str, Any]:
        err = get_animation_service_error()
        if err is not None:
            raise HTTPException(503, f"motion unavailable: {err}")
        try:
            _arm_playback_gate()
            animation_service.dispatch("play", req.recording_name)
        except Exception as exc:
            raise HTTPException(500, f"dispatch failed: {exc}")
        return {"status": "dispatched", "recording": req.recording_name}

    @app.post("/motor/startup")
    def startup(req: PlayRequest) -> dict[str, Any]:
        err = get_animation_service_error()
        if err is not None:
            raise HTTPException(503, f"motion unavailable: {err}")
        try:
            _arm_playback_gate()
            animation_service.dispatch("startup", req.recording_name)
        except Exception as exc:
            raise HTTPException(500, f"dispatch failed: {exc}")
        return {"status": "dispatched", "recording": req.recording_name}

    @app.post("/motor/frames")
    def frames(req: FramesRequest) -> dict[str, Any]:
        err = get_animation_service_error()
        if err is not None:
            raise HTTPException(503, f"motion unavailable: {err}")
        try:
            _arm_playback_gate()
            animation_service.dispatch("frames", req.frames)
        except Exception as exc:
            raise HTTPException(500, f"dispatch failed: {exc}")
        return {"status": "dispatched", "frame_count": len(req.frames)}

    @app.post("/motor/wait_complete")
    def wait_complete(req: WaitCompleteRequest) -> dict[str, Any]:
        # Bridges ``AnimationService.wait_until_playback_complete`` over HTTP
        # so proxy clients can observe real completion rather than returning
        # True immediately. The call is blocking; FastAPI runs sync def in a
        # thread pool so uvicorn's event loop stays responsive.
        err = get_animation_service_error()
        if err is not None:
            raise HTTPException(503, f"motion unavailable: {err}")
        try:
            done = animation_service.wait_until_playback_complete(timeout=req.timeout)
        except Exception as exc:
            raise HTTPException(500, f"wait_complete failed: {exc}")
        return {"done": bool(done), "timeout": req.timeout}

    @app.post("/rgb/solid")
    def rgb_solid(req: SolidRequest) -> dict[str, Any]:
        if rgb_service is None:
            raise HTTPException(503, "RGB disabled")
        try:
            rgb_service.dispatch("solid", (req.red, req.green, req.blue))
        except Exception as exc:
            raise HTTPException(500, f"solid failed: {exc}")
        return {"status": "dispatched", "rgb": [req.red, req.green, req.blue]}

    @app.post("/rgb/paint")
    def rgb_paint(req: PaintRequest) -> dict[str, Any]:
        if rgb_service is None:
            raise HTTPException(503, "RGB disabled")
        validated: list[tuple[int, int, int]] = []
        for idx, color in enumerate(req.colors):
            if len(color) != 3 or not all(isinstance(v, int) and 0 <= v <= 255 for v in color):
                raise HTTPException(400, f"invalid color at index {idx}: {color}")
            validated.append((color[0], color[1], color[2]))
        try:
            rgb_service.dispatch("paint", validated)
        except Exception as exc:
            raise HTTPException(500, f"paint failed: {exc}")
        return {"status": "dispatched", "count": len(validated)}

    @app.post("/rgb/clear")
    def rgb_clear() -> dict[str, Any]:
        if rgb_service is None:
            raise HTTPException(503, "RGB disabled")
        try:
            rgb_service.clear()
        except Exception as exc:
            raise HTTPException(500, f"clear failed: {exc}")
        return {"status": "cleared"}

    return app


def _port_is_free(host: str, port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


class MotorBusServer:
    """Lifecycle wrapper: boots uvicorn in a daemon thread, manages sentinel."""

    def __init__(
        self,
        *,
        animation_service,
        get_animation_service_error: AnimationServiceError,
        rgb_service,
        led_count: int,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.host = host
        self.port = port
        self._app = build_app(
            animation_service=animation_service,
            get_animation_service_error=get_animation_service_error,
            rgb_service=rgb_service,
            led_count=led_count,
        )
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(
        self,
        ready_timeout: float = 3.0,
        *,
        bind_retry_total_s: float = 12.0,
        bind_retry_interval_s: float = 0.5,
    ) -> None:
        if self._thread is not None:
            raise RuntimeError("MotorBusServer already started")

        # uvicorn is imported lazily so tooling that doesn't need the server
        # (e.g. unit tests that build app() directly) can avoid the import.
        import uvicorn

        # Brief bind retry: when systemd restarts the agent quickly, the prior
        # listener's TCP socket may still be in TIME_WAIT. Without a retry the
        # whole arbiter stays dark for the lifetime of the new agent, forcing
        # dashboard / CLI back into direct hardware contention. Probing every
        # ~0.5s for up to bind_retry_total_s gives the kernel enough time to
        # release the port.
        deadline = time.time() + max(0.0, bind_retry_total_s)
        first = True
        while True:
            if _port_is_free(self.host, self.port):
                break
            if time.time() >= deadline:
                logger.warning(
                    "motor bus port %s:%s is busy; server not started (waited %.1fs)",
                    self.host,
                    self.port,
                    bind_retry_total_s,
                )
                return
            if first:
                logger.info(
                    "motor bus port %s:%s busy; retrying bind for up to %.1fs",
                    self.host,
                    self.port,
                    bind_retry_total_s,
                )
                first = False
            time.sleep(max(0.05, bind_retry_interval_s))

        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level="warning",
            lifespan="off",
            access_log=False,
            loop="asyncio",
            ws="none",
        )
        self._server = uvicorn.Server(config)

        def _run() -> None:
            try:
                self._server.run()
            except Exception:
                logger.exception("motor bus uvicorn crashed")
            finally:
                self._ready.clear()

        self._thread = threading.Thread(target=_run, name="lelamp-motor-bus", daemon=True)
        self._thread.start()

        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if getattr(self._server, "started", False):
                self._ready.set()
                break
            time.sleep(0.05)

        if not self._ready.is_set():
            logger.warning("motor bus server did not report ready within %.1fs", ready_timeout)
            return

        write_sentinel(
            SentinelInfo(
                pid=os.getpid(),
                port=self.port,
                base_url=f"http://{self.host}:{self.port}",
                started_at_ms=int(time.time() * 1000),
            )
        )
        logger.info("motor bus server started on %s:%s", self.host, self.port)

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def stop(self, timeout: float = 3.0) -> None:
        remove_sentinel()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._ready.clear()
