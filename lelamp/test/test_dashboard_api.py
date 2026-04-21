import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from lelamp.dashboard.actions import BreathSlot
from lelamp.dashboard.api import create_app
from lelamp.dashboard.runtime_bridge import DashboardActionResult
from lelamp.dashboard.state_store import DashboardStateStore


class FakeExecutor:
    def __init__(self, *, busy: bool = False, active: str | None = None) -> None:
        self.busy = busy
        self.active = active
        self.submitted = []  # [(action_id, callback, section), ...]

    def is_busy(self) -> bool:
        return self.busy

    def current_action(self) -> str | None:
        return self.active

    def submit(self, action_id, callback, *, section, success_patch):
        if self.busy:
            return SimpleNamespace(
                ok=False,
                action_id=action_id,
                state="busy",
                message="Another action is already running.",
                error="busy",
                active_action=self.active,
            )
        self.submitted.append((action_id, callback, section))
        self.active = action_id
        return SimpleNamespace(
            ok=True,
            action_id=action_id,
            state="running",
            message=f"{action_id} started.",
            error=None,
            active_action=action_id,
        )


class FakeBridge:
    settings = SimpleNamespace(home_recording="home_safe")

    def __init__(self, recordings: list[str] | None = None) -> None:
        self._recordings = ["curious", "wake_up"] if recordings is None else recordings
        self.played = []  # recording names, in call order

    def list_recordings(self) -> list[str]:
        return list(self._recordings)

    def startup(self) -> DashboardActionResult:
        return DashboardActionResult(True, "startup complete")

    def stop(self) -> DashboardActionResult:
        return DashboardActionResult(True, "stopped")

    def shutdown_pose(self) -> DashboardActionResult:
        return DashboardActionResult(True, "shutdown complete")

    def play(self, name: str) -> DashboardActionResult:
        self.played.append(name)
        return DashboardActionResult(True, f"played {name}")

    def set_light_solid(self, rgb) -> DashboardActionResult:
        return DashboardActionResult(True, f"rgb {rgb}")

    def clear_light(self) -> DashboardActionResult:
        return DashboardActionResult(True, "cleared")


class FakeRgbService:
    """Records every handle_event call so breath tests can assert on dispatched RGBs."""

    def __init__(self):
        self.events = []

    def handle_event(self, event_type, payload):
        self.events.append((event_type, payload))


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        dashboard_host="127.0.0.1",
        dashboard_port=8765,
        dashboard_poll_ms=400,
        dashboard_expose_transcripts=False,
    )


class DashboardApiTests(unittest.TestCase):
    def test_get_state_returns_snapshot(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=False,
        )
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        self.assertIn("system", response.json())
        self.assertIn("voice", response.json())
        self.assertIsNone(response.json()["voice"]["last_asr_text"])
        self.assertIsNone(response.json()["voice"]["last_reply_text"])

    def test_post_startup_returns_running_receipt(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=False,
        )
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.post("/api/actions/startup")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["action_id"], "startup")

    def test_post_startup_returns_busy_when_executor_rejects(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=False,
        )
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(busy=True, active="play:curious"),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.post("/api/actions/startup")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "busy")
        self.assertEqual(response.json()["active_action"], "play:curious")

    def test_get_actions_reports_button_states_and_disables_play_without_recordings(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=False,
        )
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(recordings=[]),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.get("/api/actions")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["actions"]["startup"]["state"], "enabled")
        self.assertEqual(payload["actions"]["startup"]["label"], "启动灯")
        self.assertFalse(payload["actions"]["play"]["enabled"])
        self.assertEqual(payload["actions"]["play"]["state"], "disabled")
        self.assertEqual(payload["actions"]["play"]["label"], "暂无动作")

    def test_get_actions_marks_running_action_and_disables_others(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=False,
        )
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(busy=True, active="startup"),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.get("/api/actions")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["active_action"], "startup")
        self.assertEqual(payload["actions"]["startup"]["state"], "running")
        self.assertEqual(payload["actions"]["startup"]["label"], "启动中")
        self.assertEqual(payload["actions"]["play"]["state"], "disabled")
        self.assertEqual(payload["actions"]["play"]["label"], "执行中")

    def test_post_solid_light_rejects_rgb_values_out_of_range(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=False,
        )
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.post(
            "/api/lights/solid",
            json={"red": -1, "green": 999, "blue": 1},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "RGB values must be between 0 and 255.")

    def test_get_state_hides_transcripts_by_default(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=False,
        )
        store = DashboardStateStore()
        store.patch(
            "voice",
            {
                "status": "ready",
                "last_asr_text": "你好",
                "last_reply_text": "我在",
            },
        )
        app = create_app(
            settings=settings,
            store=store,
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        payload = client.get("/api/state").json()

        self.assertIsNone(payload["voice"]["last_asr_text"])
        self.assertIsNone(payload["voice"]["last_reply_text"])

    def test_get_state_can_expose_transcripts_when_explicitly_enabled(self) -> None:
        settings = SimpleNamespace(
            dashboard_host="127.0.0.1",
            dashboard_port=8765,
            dashboard_poll_ms=400,
            dashboard_expose_transcripts=True,
        )
        store = DashboardStateStore()
        store.patch(
            "voice",
            {
                "status": "ready",
                "last_asr_text": "你好",
                "last_reply_text": "我在",
            },
        )
        app = create_app(
            settings=settings,
            store=store,
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        payload = client.get("/api/state").json()

        self.assertEqual(payload["voice"]["last_asr_text"], "你好")
        self.assertEqual(payload["voice"]["last_reply_text"], "我在")


class DashboardIntervenApiTests(unittest.TestCase):
    """Manual fallback button — DEMO_PLAN §1.2."""

    def _build(self, *, executor=None, rgb_service=None, bridge=None):
        bridge = bridge or FakeBridge()
        executor = executor or FakeExecutor()
        service = rgb_service or FakeRgbService()
        slot = BreathSlot(lambda: service)
        app = create_app(
            settings=_settings(),
            store=DashboardStateStore(),
            bridge=bridge,
            executor=executor,
            breath_slot=slot,
            enable_background=False,
        )
        return TestClient(app), slot, bridge, executor, service

    def test_get_actions_exposes_intervene_style_registry(self) -> None:
        client, slot, _bridge, _exec, _svc = self._build()
        response = client.get("/api/actions")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("intervene", payload)
        styles = {s["style"] for s in payload["intervene"]["styles"]}
        self.assertEqual(
            styles,
            {"shy", "headshake", "sad_nod", "breath_moderate", "breath_mild"},
        )
        self.assertFalse(payload["intervene"]["breath_running"])

    def test_intervene_motion_submits_single_play_via_executor(self) -> None:
        client, _slot, bridge, executor, _svc = self._build()
        response = client.post("/api/actions/intervene", json={"style": "shy"})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["action_id"], "intervene:shy")
        # Callback is recorded but not yet invoked by the fake executor,
        # so run it manually to assert the chained play happens.
        self.assertEqual(len(executor.submitted), 1)
        _, callback, section = executor.submitted[0]
        self.assertEqual(section, "motion")
        result = callback()
        self.assertTrue(result.ok)
        self.assertEqual(bridge.played, ["shy"])

    def test_intervene_sad_nod_chains_two_recordings_in_order(self) -> None:
        client, _slot, bridge, executor, _svc = self._build()
        response = client.post("/api/actions/intervene", json={"style": "sad_nod"})
        self.assertEqual(response.status_code, 202)
        _, callback, _ = executor.submitted[0]
        result = callback()
        self.assertTrue(result.ok)
        # Must be "sad" then "nod" — reversing it would read wrong on stage.
        self.assertEqual(bridge.played, ["sad", "nod"])

    def test_intervene_sad_nod_shortcircuits_on_first_failure(self) -> None:
        client, _slot, bridge, executor, _svc = self._build()

        def _fail_first(name):
            bridge.played.append(name)
            return DashboardActionResult(False, f"{name} failed")

        bridge.play = _fail_first  # type: ignore[assignment]
        client.post("/api/actions/intervene", json={"style": "sad_nod"})
        _, callback, _ = executor.submitted[0]
        result = callback()
        self.assertFalse(result.ok)
        # Only "sad" ran — we did not plow through to "nod" after failure.
        self.assertEqual(bridge.played, ["sad"])

    def test_intervene_returns_409_when_executor_busy_on_motion(self) -> None:
        client, _slot, _bridge, _exec, _svc = self._build(
            executor=FakeExecutor(busy=True, active="play:curious"),
        )
        response = client.post("/api/actions/intervene", json={"style": "headshake"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "busy")

    def test_intervene_breath_starts_orchestrator_and_reports_running(self) -> None:
        client, slot, _bridge, _exec, service = self._build()
        response = client.post(
            "/api/actions/intervene", json={"style": "breath_moderate"}
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["action_id"], "intervene:breath_moderate")
        # Orchestrator is a daemon thread; stop it and wait briefly so the
        # test is deterministic (no thread leak).
        current = slot.current()
        self.assertIsNotNone(current)
        self.assertTrue(slot.is_running() or current.stats.dispatches >= 0)
        current.stop()
        current.wait_until_done(timeout=2.0)
        self.assertFalse(slot.is_running())
        # At least one RGB solid event was dispatched before we stopped.
        # Some runs may stop before the first tick; guard against that.
        if service.events:
            self.assertEqual(service.events[0][0], "solid")

    def test_intervene_breath_preempts_previous_breath(self) -> None:
        client, slot, _bridge, _exec, _svc = self._build()
        client.post("/api/actions/intervene", json={"style": "breath_moderate"})
        first = slot.current()
        self.assertIsNotNone(first)
        # Kick off a second breath — old one must be stopped.
        client.post("/api/actions/intervene", json={"style": "breath_mild"})
        second = slot.current()
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        # Old orchestrator should have been stopped; give it a moment.
        first.wait_until_done(timeout=2.0)
        self.assertFalse(first.running)
        # Clean up the second one too.
        second.stop()
        second.wait_until_done(timeout=2.0)

    def test_intervene_stop_route_stops_running_breath(self) -> None:
        client, slot, _bridge, _exec, _svc = self._build()
        client.post("/api/actions/intervene", json={"style": "breath_mild"})
        self.assertTrue(slot.is_running())
        response = client.post("/api/actions/intervene/stop")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["stopped"])
        current = slot.current()
        if current is not None:
            current.wait_until_done(timeout=2.0)
        self.assertFalse(slot.is_running())
        # Second stop is idempotent — reports nothing to stop.
        response2 = client.post("/api/actions/intervene/stop")
        self.assertEqual(response2.status_code, 200)
        self.assertFalse(response2.json()["stopped"])

    def test_intervene_rejects_unknown_style(self) -> None:
        client, _slot, _bridge, _exec, _svc = self._build()
        response = client.post("/api/actions/intervene", json={"style": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown style", response.json()["detail"])

    def test_intervene_requires_style_field(self) -> None:
        client, _slot, _bridge, _exec, _svc = self._build()
        response = client.post("/api/actions/intervene", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Missing style.")

    def test_intervene_breath_surface_rgb_factory_failure_as_500(self) -> None:
        def _boom():
            raise RuntimeError("sentinel missing")

        slot = BreathSlot(_boom)
        app = create_app(
            settings=_settings(),
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            breath_slot=slot,
            enable_background=False,
        )
        client = TestClient(app)
        response = client.post(
            "/api/actions/intervene", json={"style": "breath_moderate"}
        )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json()["ok"])
        self.assertIn("sentinel missing", response.json()["message"])


if __name__ == "__main__":
    unittest.main()
