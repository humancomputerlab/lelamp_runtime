import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lelamp.dashboard import runtime_bridge as runtime_bridge_mod
from lelamp.dashboard.runtime_bridge import DashboardRuntimeBridge


class FakeAnimationService:
    instances = []
    available_recordings = ["curious", "wake_up"]
    wait_result = True
    raise_on_start: Exception | None = None
    raise_on_dispatch: Exception | None = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.dispatched = []
        self.stopped = False
        FakeAnimationService.instances.append(self)

    def get_available_recordings(self) -> list[str]:
        return list(FakeAnimationService.available_recordings)

    def start(self) -> None:
        if FakeAnimationService.raise_on_start is not None:
            raise FakeAnimationService.raise_on_start
        self.started = True

    def dispatch(self, event_type: str, payload: str) -> None:
        if FakeAnimationService.raise_on_dispatch is not None:
            raise FakeAnimationService.raise_on_dispatch
        self.dispatched.append((event_type, payload))

    def wait_until_playback_complete(self, timeout: float | None = None) -> bool:
        return FakeAnimationService.wait_result

    def stop(self) -> None:
        self.stopped = True


class FakeRGBService:
    instances = []
    raise_on_handle_event: Exception | None = None
    raise_on_clear: Exception | None = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.actions = []
        FakeRGBService.instances.append(self)

    def handle_event(self, event_type: str, payload) -> None:
        if FakeRGBService.raise_on_handle_event is not None:
            raise FakeRGBService.raise_on_handle_event
        self.actions.append((event_type, payload))

    def clear(self) -> None:
        if FakeRGBService.raise_on_clear is not None:
            raise FakeRGBService.raise_on_clear
        self.actions.append(("clear", None))

    def stop(self) -> None:
        self.actions.append(("stop", None))


class ExplodingFactory:
    called = False

    def __new__(cls, **kwargs):
        cls.called = True
        raise RuntimeError("factory boom")


class ExplodingAnimationFactory:
    called = False

    def __new__(cls, **kwargs):
        cls.called = True
        raise RuntimeError("animation factory boom")


class DashboardRuntimeBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeAnimationService.instances = []
        FakeAnimationService.available_recordings = ["curious", "wake_up"]
        FakeAnimationService.wait_result = True
        FakeAnimationService.raise_on_start = None
        FakeAnimationService.raise_on_dispatch = None
        FakeRGBService.instances = []
        FakeRGBService.raise_on_handle_event = None
        FakeRGBService.raise_on_clear = None
        ExplodingFactory.called = False
        ExplodingAnimationFactory.called = False
        self._animation_proxy_patcher = patch.object(
            runtime_bridge_mod,
            "_build_animation_service_with_proxy",
            side_effect=lambda fallback_factory, **kwargs: fallback_factory(),
        )
        self._rgb_proxy_patcher = patch.object(
            runtime_bridge_mod,
            "_build_rgb_service_with_proxy",
            side_effect=lambda fallback_factory, **kwargs: fallback_factory(),
        )
        self._sentinel_patcher = patch.object(runtime_bridge_mod, "current_sentinel", return_value=None)
        self._animation_proxy_patcher.start()
        self._rgb_proxy_patcher.start()
        self._sentinel_patcher.start()
        self.addCleanup(self._animation_proxy_patcher.stop)
        self.addCleanup(self._rgb_proxy_patcher.stop)
        self.addCleanup(self._sentinel_patcher.stop)

    @staticmethod
    def _make_settings(enable_rgb: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            port="/dev/ttyACM0",
            lamp_id="lelamp",
            fps=30,
            interpolation_duration=3.0,
            startup_recording="wake_up",
            home_recording="home_safe",
            use_home_pose_relative=True,
            enable_rgb=enable_rgb,
            led_count=40,
            led_pin=12,
            led_freq_hz=800000,
            led_dma=10,
            led_brightness=255,
            led_invert=False,
            led_channel=0,
        )

    def test_play_uses_home_recording_as_idle_target(self) -> None:
        settings = self._make_settings()

        with patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
        ) as record_playback, patch.object(
            runtime_bridge_mod,
            "_elapsed_ms",
            return_value=2034,
        ):
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=SimpleNamespace(),
            )

            result = bridge.play("curious")

        service = FakeAnimationService.instances[-1]
        self.assertTrue(result.ok)
        self.assertEqual(service.kwargs["idle_recording"], "home_safe")
        self.assertEqual(service.kwargs["home_recording"], "home_safe")
        self.assertEqual(service.dispatched, [("play", "curious")])
        record_playback.assert_called_once_with(
            source="dashboard",
            initiator="dashboard",
            action="play",
            recording_name="curious",
            rgb=None,
            duration_ms=2034,
            ok=True,
            error=None,
        )

    def test_play_missing_recording_fast_fails_without_dispatch(self) -> None:
        settings = self._make_settings()
        FakeAnimationService.available_recordings = ["wake_up"]

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        result = bridge.play("curious")

        service = FakeAnimationService.instances[-1]
        self.assertFalse(result.ok)
        self.assertIn("not found", result.message.lower())
        self.assertEqual(result.detail, "curious")
        self.assertEqual(service.dispatched, [])
        self.assertFalse(service.started)
        self.assertFalse(service.stopped)

    def test_play_converts_service_exception_to_failed_result(self) -> None:
        settings = self._make_settings()
        FakeAnimationService.raise_on_start = RuntimeError("motor serial unavailable")

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        result = bridge.play("curious")

        self.assertFalse(result.ok)
        self.assertIn("failed", result.message.lower())
        self.assertIn("unavailable", result.detail)

    def test_play_converts_animation_factory_failure_to_failed_result(self) -> None:
        settings = self._make_settings()

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=ExplodingAnimationFactory,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        result = bridge.play("curious")

        self.assertFalse(result.ok)
        self.assertIn("failed", result.message.lower())
        self.assertIn("animation factory boom", result.detail)
        self.assertTrue(ExplodingAnimationFactory.called)

    def test_startup_via_motor_bus_waits_for_completion(self) -> None:
        # Proxy branch must not return success until wait_until_playback_complete
        # signals done, otherwise dashboard busy lock releases mid-choreography
        # and the next click shears the state machine.
        settings = self._make_settings()
        fake_sentinel = SimpleNamespace(
            pid=1, port=0, base_url="http://127.0.0.1:0", started_at_ms=0
        )

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        with patch.object(runtime_bridge_mod, "current_sentinel", return_value=fake_sentinel), patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
        ) as record_playback, patch.object(
            runtime_bridge_mod,
            "_elapsed_ms",
            return_value=2034,
        ):
            FakeAnimationService.wait_result = True
            result = bridge.startup()

        self.assertTrue(result.ok)
        service = FakeAnimationService.instances[-1]
        self.assertEqual(service.dispatched, [("startup", "wake_up")])
        record_playback.assert_called_once_with(
            source="dashboard",
            initiator="dashboard",
            action="startup",
            recording_name="wake_up",
            rgb=None,
            duration_ms=2034,
            ok=True,
            error=None,
        )

    def test_startup_via_motor_bus_reports_timeout_when_wait_returns_false(self) -> None:
        settings = self._make_settings()
        fake_sentinel = SimpleNamespace(
            pid=1, port=0, base_url="http://127.0.0.1:0", started_at_ms=0
        )

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        with patch.object(runtime_bridge_mod, "current_sentinel", return_value=fake_sentinel):
            FakeAnimationService.wait_result = False
            result = bridge.startup()

        self.assertFalse(result.ok)
        self.assertIn("timed out", result.message.lower())
        self.assertEqual(result.detail, "wake_up")

    def test_startup_hard_fails_when_motor_bus_ownership_is_uncertain(self) -> None:
        settings = self._make_settings()
        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        with patch.object(
            runtime_bridge_mod,
            "current_sentinel",
            side_effect=runtime_bridge_mod.MotorBusClientError("live sentinel exists but /health probe failed"),
        ):
            result = bridge.startup()

        self.assertFalse(result.ok)
        self.assertIn("uncertain", result.message.lower())
        self.assertIn("/health probe failed", result.detail)

    def test_startup_converts_remote_exception_to_failed_result(self) -> None:
        settings = self._make_settings()

        remote_module = SimpleNamespace(
            _handle_startup=lambda args: (_ for _ in ()).throw(RuntimeError("remote crashed")),
            DEFAULT_STARTUP_SETTLE_FRAMES=18,
            DEFAULT_STARTUP_HOLD_FRAMES=10,
            DEFAULT_STARTUP_FPS=15,
            DEFAULT_WAKE_FPS=30,
            DEFAULT_POST_WAKE_HOLD_SECONDS=0.8,
        )

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=remote_module,
        )

        result = bridge.startup()

        self.assertFalse(result.ok)
        self.assertIn("failed", result.message.lower())
        self.assertIn("remote crashed", result.detail)

    def test_startup_direct_path_records_playback_success(self) -> None:
        settings = self._make_settings()

        remote_module = SimpleNamespace(
            _handle_startup=lambda args: 0,
            DEFAULT_STARTUP_SETTLE_FRAMES=18,
            DEFAULT_STARTUP_HOLD_FRAMES=10,
            DEFAULT_STARTUP_FPS=15,
            DEFAULT_WAKE_FPS=30,
            DEFAULT_POST_WAKE_HOLD_SECONDS=0.8,
        )

        with patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
        ) as record_playback:
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=remote_module,
            )

            result = bridge.startup()

        self.assertTrue(result.ok)
        record_playback.assert_called_once_with(
            source="dashboard",
            initiator="dashboard",
            action="startup",
            recording_name="wake_up",
            rgb=None,
            duration_ms=None,
            ok=True,
            error=None,
        )

    def test_startup_direct_path_does_not_double_record_when_remote_handler_already_logged(self) -> None:
        settings = self._make_settings()
        playback_events = []

        def _remote_startup(args) -> int:
            del args
            runtime_bridge_mod.record_standalone_playback(
                source="dashboard",
                initiator="dashboard",
                action="startup",
                recording_name="wake_up",
                rgb=None,
                duration_ms=2034,
                ok=True,
                error=None,
            )
            return 0

        remote_module = SimpleNamespace(
            HANDLES_PLAYBACK_RECORDING=True,
            _handle_startup=_remote_startup,
            DEFAULT_STARTUP_SETTLE_FRAMES=18,
            DEFAULT_STARTUP_HOLD_FRAMES=10,
            DEFAULT_STARTUP_FPS=15,
            DEFAULT_WAKE_FPS=30,
            DEFAULT_POST_WAKE_HOLD_SECONDS=0.8,
        )

        with patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
            side_effect=lambda **kwargs: playback_events.append(kwargs),
        ):
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=remote_module,
            )
            result = bridge.startup()

        self.assertTrue(result.ok)
        self.assertEqual(playback_events, [
            {
                "source": "dashboard",
                "initiator": "dashboard",
                "action": "startup",
                "recording_name": "wake_up",
                "rgb": None,
                "duration_ms": 2034,
                "ok": True,
                "error": None,
            }
        ])

    def test_set_light_solid_dispatches_rgb_event_and_keeps_state(self) -> None:
        settings = self._make_settings()

        with patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
        ) as record_playback:
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=SimpleNamespace(),
            )

            result = bridge.set_light_solid((255, 170, 70))

        self.assertTrue(result.ok)
        self.assertEqual(FakeRGBService.instances[-1].actions, [("solid", (255, 170, 70))])
        record_playback.assert_called_once_with(
            source="dashboard",
            initiator="dashboard",
            action="light_solid",
            recording_name=None,
            rgb=(255, 170, 70),
            duration_ms=None,
            ok=True,
            error=None,
        )

    def test_set_light_solid_returns_failed_result_when_rgb_disabled(self) -> None:
        settings = self._make_settings(enable_rgb=False)

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=ExplodingFactory,
            remote_module=SimpleNamespace(),
        )

        result = bridge.set_light_solid((255, 170, 70))

        self.assertFalse(result.ok)
        self.assertIn("disabled", result.message.lower())
        self.assertFalse(ExplodingFactory.called)

    def test_clear_light_returns_failed_result_when_rgb_disabled(self) -> None:
        settings = self._make_settings(enable_rgb=False)

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=ExplodingFactory,
            remote_module=SimpleNamespace(),
        )

        result = bridge.clear_light()

        self.assertFalse(result.ok)
        self.assertIn("disabled", result.message.lower())
        self.assertFalse(ExplodingFactory.called)

    def test_clear_light_converts_rgb_service_exception_to_failed_result(self) -> None:
        settings = self._make_settings(enable_rgb=True)
        FakeRGBService.raise_on_clear = RuntimeError("device missing")

        with patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
        ) as record_playback:
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=SimpleNamespace(),
            )

            result = bridge.clear_light()

        self.assertFalse(result.ok)
        self.assertIn("failed", result.message.lower())
        self.assertIn("device missing", result.detail)
        record_playback.assert_called_once_with(
            source="dashboard",
            initiator="dashboard",
            action="light_clear",
            recording_name=None,
            rgb=None,
            duration_ms=None,
            ok=False,
            error="device missing",
        )

    def test_shutdown_pose_direct_path_records_playback_success(self) -> None:
        settings = self._make_settings()

        remote_module = SimpleNamespace(
            _handle_shutdown=lambda args: 0,
            DEFAULT_SHUTDOWN_PREPARE_FRACTION=0.22,
            DEFAULT_SHUTDOWN_PREPARE_FRAMES=10,
            DEFAULT_SHUTDOWN_SETTLE_FRAMES=16,
            DEFAULT_SHUTDOWN_HOLD_FRAMES=8,
            DEFAULT_SHUTDOWN_FPS=12,
            DEFAULT_SHUTDOWN_FINAL_HOLD_SECONDS=1.0,
            DEFAULT_RELEASE_PAUSE_SECONDS=0.8,
        )

        with patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
        ) as record_playback:
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=remote_module,
            )

            result = bridge.shutdown_pose()

        self.assertTrue(result.ok)
        record_playback.assert_called_once_with(
            source="dashboard",
            initiator="dashboard",
            action="shutdown_pose",
            recording_name="power_off",
            rgb=None,
            duration_ms=None,
            ok=True,
            error=None,
        )

    def test_shutdown_direct_path_does_not_double_record_when_remote_handler_already_logged(self) -> None:
        settings = self._make_settings()
        playback_events = []

        def _remote_shutdown(args) -> int:
            del args
            runtime_bridge_mod.record_standalone_playback(
                source="dashboard",
                initiator="dashboard",
                action="shutdown_pose",
                recording_name="power_off",
                rgb=None,
                duration_ms=1550,
                ok=True,
                error=None,
            )
            return 0

        remote_module = SimpleNamespace(
            HANDLES_PLAYBACK_RECORDING=True,
            _handle_shutdown=_remote_shutdown,
            DEFAULT_SHUTDOWN_PREPARE_FRACTION=0.22,
            DEFAULT_SHUTDOWN_PREPARE_FRAMES=10,
            DEFAULT_SHUTDOWN_SETTLE_FRAMES=16,
            DEFAULT_SHUTDOWN_HOLD_FRAMES=8,
            DEFAULT_SHUTDOWN_FPS=12,
            DEFAULT_SHUTDOWN_FINAL_HOLD_SECONDS=1.0,
            DEFAULT_RELEASE_PAUSE_SECONDS=0.8,
        )

        with patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
            side_effect=lambda **kwargs: playback_events.append(kwargs),
        ):
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=remote_module,
            )
            result = bridge.shutdown_pose()

        self.assertTrue(result.ok)
        self.assertEqual(playback_events, [
            {
                "source": "dashboard",
                "initiator": "dashboard",
                "action": "shutdown_pose",
                "recording_name": "power_off",
                "rgb": None,
                "duration_ms": 1550,
                "ok": True,
                "error": None,
            }
        ])

    def test_shutdown_pose_via_motor_bus_records_shutdown_pose_action(self) -> None:
        settings = self._make_settings()
        FakeAnimationService.available_recordings = ["curious", "wake_up", "power_off"]
        fake_sentinel = SimpleNamespace(
            pid=1, port=0, base_url="http://127.0.0.1:0", started_at_ms=0
        )

        with patch.object(runtime_bridge_mod, "current_sentinel", return_value=fake_sentinel), patch.object(
            runtime_bridge_mod,
            "record_standalone_playback",
        ) as record_playback, patch.object(
            runtime_bridge_mod,
            "_elapsed_ms",
            return_value=1550,
        ):
            bridge = DashboardRuntimeBridge(
                settings,
                animation_factory=FakeAnimationService,
                rgb_factory=FakeRGBService,
                remote_module=SimpleNamespace(),
            )

            result = bridge.shutdown_pose()

        self.assertTrue(result.ok)
        record_playback.assert_called_once_with(
            source="dashboard",
            initiator="dashboard",
            action="shutdown_pose",
            recording_name="power_off",
            rgb=None,
            duration_ms=1550,
            ok=True,
            error=None,
        )


if __name__ == "__main__":
    unittest.main()
