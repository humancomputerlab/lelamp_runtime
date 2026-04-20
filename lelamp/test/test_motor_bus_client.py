import os
import socket
import threading
import time
import unittest
import warnings
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi import FastAPI

from lelamp.motor_bus import client as client_mod
from lelamp.motor_bus import sentinel as sentinel_mod
from lelamp.motor_bus.server import build_app


class _FakeAnimation:
    def __init__(self) -> None:
        self.recordings = ["wake_up", "home_safe"]
        self.dispatched: list[tuple[str, Any]] = []
        self._done = threading.Event()
        self._done.set()

    def get_available_recordings(self) -> list[str]:
        return list(self.recordings)

    def dispatch(self, event_type: str, payload: Any) -> None:
        self.dispatched.append((event_type, payload))

    def begin_playback(self) -> None:
        self._done.clear()

    def end_playback(self) -> None:
        self._done.set()

    def wait_until_playback_complete(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout=timeout)


class _FakeRGB:
    def __init__(self) -> None:
        self.dispatched: list[tuple[str, Any]] = []
        self.cleared = False

    def dispatch(self, event_type: str, payload: Any) -> None:
        self.dispatched.append((event_type, payload))

    def clear(self) -> None:
        self.cleared = True


def _pick_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _LiveServer:
    """Spin up uvicorn in a daemon thread against a real FastAPI app."""

    def __init__(self, app: FastAPI) -> None:
        import uvicorn

        self.port = _pick_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            lifespan="off",
            access_log=False,
            ws="none",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "_LiveServer":
        self._thread.start()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if getattr(self._server, "started", False):
                return self
            time.sleep(0.05)
        raise RuntimeError("test server failed to start")

    def __exit__(self, *exc_info) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=3.0)


class ProxyAnimationServiceTests(unittest.TestCase):
    def test_dispatch_play_server_boot_does_not_emit_websockets_deprecation(self) -> None:
        animation = _FakeAnimation()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with _LiveServer(app) as srv:
                proxy = client_mod.ProxyAnimationService(srv.base_url)
                proxy.dispatch("play", "wake_up")

        self.assertEqual(animation.dispatched, [("play", "wake_up")])
        self.assertFalse(
            [
                warning
                for warning in caught
                if issubclass(warning.category, DeprecationWarning)
                and "websockets" in str(warning.message)
            ]
        )

    def test_dispatch_play_hits_server(self) -> None:
        animation = _FakeAnimation()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            proxy = client_mod.ProxyAnimationService(srv.base_url)
            proxy.dispatch("play", "wake_up")
            self.assertEqual(animation.dispatched, [("play", "wake_up")])

    def test_dispatch_startup_hits_server(self) -> None:
        animation = _FakeAnimation()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            proxy = client_mod.ProxyAnimationService(srv.base_url)
            proxy.dispatch("startup", "wake_up")
            self.assertEqual(animation.dispatched, [("startup", "wake_up")])

    def test_get_available_recordings(self) -> None:
        animation = _FakeAnimation()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            proxy = client_mod.ProxyAnimationService(srv.base_url)
            self.assertEqual(proxy.get_available_recordings(), ["wake_up", "home_safe"])

    def test_dispatch_unsupported_event_raises(self) -> None:
        proxy = client_mod.ProxyAnimationService("http://127.0.0.1:1")
        with self.assertRaises(client_mod.MotorBusClientError):
            proxy.dispatch("calibrate", "foo")

    def test_wait_until_playback_complete_observes_real_signal(self) -> None:
        animation = _FakeAnimation()
        animation.begin_playback()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            proxy = client_mod.ProxyAnimationService(srv.base_url)

            def _finish_soon() -> None:
                time.sleep(0.15)
                animation.end_playback()

            threading.Thread(target=_finish_soon, daemon=True).start()
            self.assertTrue(proxy.wait_until_playback_complete(timeout=2.0))

    def test_wait_until_playback_complete_returns_false_on_timeout(self) -> None:
        animation = _FakeAnimation()
        animation.begin_playback()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            proxy = client_mod.ProxyAnimationService(srv.base_url)
            self.assertFalse(proxy.wait_until_playback_complete(timeout=0.1))

    def test_wait_until_playback_complete_returns_false_when_server_gone(self) -> None:
        # No live server at that port → HTTP failure → returns False rather
        # than raising. Callers use the boolean to gate busy-lock release.
        proxy = client_mod.ProxyAnimationService("http://127.0.0.1:1")
        self.assertFalse(proxy.wait_until_playback_complete(timeout=0.1))


class ProxyRGBServiceTests(unittest.TestCase):
    def test_solid_and_paint_and_clear(self) -> None:
        rgb = _FakeRGB()
        app = build_app(
            animation_service=_FakeAnimation(),
            get_animation_service_error=lambda: None,
            rgb_service=rgb,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            proxy = client_mod.ProxyRGBService(srv.base_url)
            proxy.handle_event("solid", (10, 20, 30))
            proxy.handle_event("paint", [(1, 2, 3), (4, 5, 6)])
            proxy.clear()
            self.assertEqual(
                rgb.dispatched,
                [("solid", (10, 20, 30)), ("paint", [(1, 2, 3), (4, 5, 6)])],
            )
            self.assertTrue(rgb.cleared)


class FallbackRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_path = Path(
            os.environ.get("TMPDIR", "/tmp")
        ) / f"lelamp-motor-bus-client-test-{os.getpid()}.json"
        self._env_patcher = mock.patch.dict(
            os.environ, {"LELAMP_MOTOR_BUS_SENTINEL": str(self.tmp_path)}
        )
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)

        def _cleanup() -> None:
            try:
                self.tmp_path.unlink()
            except FileNotFoundError:
                pass

        self.addCleanup(_cleanup)

    def test_build_animation_service_uses_fallback_when_no_sentinel(self) -> None:
        sentinel_mod.remove_sentinel()
        fallback_called = {"n": 0}

        def _factory():
            fallback_called["n"] += 1
            return "fallback"

        result = client_mod.build_animation_service(_factory)
        self.assertEqual(result, "fallback")
        self.assertEqual(fallback_called["n"], 1)

    def test_build_animation_service_raises_when_live_sentinel_probe_fails(self) -> None:
        # Sentinel points at a closed port. That means another process still
        # advertises ownership, so we must fail closed instead of silently
        # falling back to direct hardware access.
        free_port = _pick_free_port()
        sentinel_mod.write_sentinel(
            sentinel_mod.SentinelInfo(
                pid=os.getpid(),
                port=free_port,
                base_url=f"http://127.0.0.1:{free_port}",
                started_at_ms=1,
            )
        )
        called = {"n": 0}

        def _factory():
            called["n"] += 1
            return "fallback"

        with self.assertRaises(client_mod.MotorBusClientError):
            client_mod.build_animation_service(_factory, probe_timeout=0.5)
        self.assertEqual(called["n"], 0)

    def test_build_rgb_service_raises_when_live_sentinel_probe_fails(self) -> None:
        free_port = _pick_free_port()
        sentinel_mod.write_sentinel(
            sentinel_mod.SentinelInfo(
                pid=os.getpid(),
                port=free_port,
                base_url=f"http://127.0.0.1:{free_port}",
                started_at_ms=1,
            )
        )
        called = {"n": 0}

        def _factory():
            called["n"] += 1
            return "fallback"

        with self.assertRaises(client_mod.MotorBusClientError):
            client_mod.build_rgb_service(_factory, probe_timeout=0.5)
        self.assertEqual(called["n"], 0)

    def test_build_animation_service_returns_proxy_when_server_alive(self) -> None:
        animation = _FakeAnimation()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            sentinel_mod.write_sentinel(
                sentinel_mod.SentinelInfo(
                    pid=os.getpid(),
                    port=srv.port,
                    base_url=srv.base_url,
                    started_at_ms=1,
                )
            )

            def _factory():
                raise AssertionError("fallback should not fire when proxy works")

            result = client_mod.build_animation_service(_factory)
            self.assertIsInstance(result, client_mod.ProxyAnimationService)
            result.dispatch("play", "wake_up")
            self.assertEqual(animation.dispatched, [("play", "wake_up")])

    def test_build_animation_service_falls_back_when_motor_not_ok(self) -> None:
        # Agent process is alive but its AnimationService never started
        # successfully. The server exposes motor_ok=False; client must fall
        # through to direct hardware so the self-recovery path stays alive.
        animation = _FakeAnimation()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: "/dev/ttyACM0 busy",
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            sentinel_mod.write_sentinel(
                sentinel_mod.SentinelInfo(
                    pid=os.getpid(),
                    port=srv.port,
                    base_url=srv.base_url,
                    started_at_ms=1,
                )
            )
            called = {"n": 0}

            def _factory():
                called["n"] += 1
                return "direct_fallback"

            result = client_mod.build_animation_service(_factory)
            self.assertEqual(result, "direct_fallback")
            self.assertEqual(called["n"], 1)

    def test_build_rgb_service_falls_back_when_rgb_not_ok(self) -> None:
        # Agent is alive, motor is fine, but RGB is disabled → rgb probe
        # should not route through proxy (would only ever return 503).
        animation = _FakeAnimation()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: None,
            rgb_service=None,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            sentinel_mod.write_sentinel(
                sentinel_mod.SentinelInfo(
                    pid=os.getpid(),
                    port=srv.port,
                    base_url=srv.base_url,
                    started_at_ms=1,
                )
            )
            called = {"n": 0}

            def _factory():
                called["n"] += 1
                return "direct_fallback"

            result = client_mod.build_rgb_service(_factory)
            self.assertEqual(result, "direct_fallback")
            self.assertEqual(called["n"], 1)

    def test_build_rgb_service_uses_proxy_when_motor_failed_but_rgb_ok(self) -> None:
        # motor_ok=False should not prevent RGB traffic from routing via
        # the proxy; domains are independent.
        animation = _FakeAnimation()
        rgb = _FakeRGB()
        app = build_app(
            animation_service=animation,
            get_animation_service_error=lambda: "motor busy",
            rgb_service=rgb,
            led_count=40,
        )
        with _LiveServer(app) as srv:
            sentinel_mod.write_sentinel(
                sentinel_mod.SentinelInfo(
                    pid=os.getpid(),
                    port=srv.port,
                    base_url=srv.base_url,
                    started_at_ms=1,
                )
            )

            def _factory():
                raise AssertionError("should route via rgb proxy when rgb_ok is True")

            result = client_mod.build_rgb_service(_factory)
            self.assertIsInstance(result, client_mod.ProxyRGBService)
            result.handle_event("solid", (1, 2, 3))
            self.assertEqual(rgb.dispatched, [("solid", (1, 2, 3))])

    def test_current_sentinel_rejects_unknown_require(self) -> None:
        with self.assertRaises(ValueError):
            client_mod.current_sentinel(require="bogus")

    def test_current_sentinel_legacy_server_field_fallback(self) -> None:
        # Simulate a server that only returns the old ``ok`` + legacy field
        # set. current_sentinel should still work by falling back to
        # ``animation_error is None`` / ``rgb_available``.
        class _LegacyHealthServer(_LiveServer):
            pass

        app = FastAPI()

        @app.get("/health")
        def legacy_health() -> dict[str, Any]:
            return {
                "ok": True,
                "animation_error": None,
                "rgb_available": True,
                "led_count": 40,
                "pid": os.getpid(),
            }

        with _LegacyHealthServer(app) as srv:
            sentinel_mod.write_sentinel(
                sentinel_mod.SentinelInfo(
                    pid=os.getpid(),
                    port=srv.port,
                    base_url=srv.base_url,
                    started_at_ms=1,
                )
            )
            self.assertIsNotNone(client_mod.current_sentinel(require=client_mod.REQUIRE_MOTOR))
            self.assertIsNotNone(client_mod.current_sentinel(require=client_mod.REQUIRE_RGB))


if __name__ == "__main__":
    unittest.main()
