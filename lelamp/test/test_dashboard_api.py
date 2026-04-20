import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from lelamp.dashboard.api import create_app
from lelamp.dashboard.runtime_bridge import DashboardActionResult
from lelamp.dashboard.state_store import DashboardStateStore


class FakeExecutor:
    def __init__(self, *, busy: bool = False, active: str | None = None) -> None:
        self.busy = busy
        self.active = active

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

    def list_recordings(self) -> list[str]:
        return list(self._recordings)

    def startup(self) -> DashboardActionResult:
        return DashboardActionResult(True, "startup complete")

    def stop(self) -> DashboardActionResult:
        return DashboardActionResult(True, "stopped")

    def shutdown_pose(self) -> DashboardActionResult:
        return DashboardActionResult(True, "shutdown complete")

    def play(self, name: str) -> DashboardActionResult:
        return DashboardActionResult(True, f"played {name}")

    def set_light_solid(self, rgb) -> DashboardActionResult:
        return DashboardActionResult(True, f"rgb {rgb}")

    def clear_light(self) -> DashboardActionResult:
        return DashboardActionResult(True, "cleared")


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


if __name__ == "__main__":
    unittest.main()
