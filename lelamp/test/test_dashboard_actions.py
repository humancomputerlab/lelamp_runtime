import threading
import unittest
from types import SimpleNamespace

from lelamp.dashboard.actions import build_light_actions, build_motion_actions
from lelamp.dashboard.actions.executor import DashboardActionExecutor
from lelamp.dashboard.runtime_bridge import DashboardActionResult
from lelamp.dashboard.state_store import DashboardStateStore


class DashboardActionExecutorTests(unittest.TestCase):
    def test_submit_tracks_current_action_and_busy_state_until_completion(self) -> None:
        store = DashboardStateStore()
        started = threading.Event()
        gate = threading.Event()
        executor = DashboardActionExecutor(store)

        def startup() -> DashboardActionResult:
            started.set()
            gate.wait(timeout=1.0)
            return DashboardActionResult(True, "done")

        receipt = executor.submit(
            "startup",
            startup,
            section="motion",
            success_patch={"status": "idle"},
        )

        self.assertTrue(receipt.ok)
        self.assertTrue(started.wait(timeout=1.0))
        self.assertTrue(executor.is_busy())
        self.assertEqual(executor.current_action(), "startup")

        gate.set()
        self.assertTrue(executor.wait_for_idle(timeout=1.0))
        self.assertFalse(executor.is_busy())
        self.assertIsNone(executor.current_action())

    def test_submit_rejects_overlapping_actions_while_worker_is_running(self) -> None:
        store = DashboardStateStore()
        started = threading.Event()
        gate = threading.Event()

        def slow_action() -> DashboardActionResult:
            started.set()
            gate.wait(timeout=1.0)
            return DashboardActionResult(True, "done")

        executor = DashboardActionExecutor(store)

        first = executor.submit(
            "startup",
            slow_action,
            section="motion",
            success_patch={"status": "idle"},
        )
        self.assertTrue(started.wait(timeout=1.0))

        second = executor.submit(
            "shutdown_pose",
            slow_action,
            section="motion",
            success_patch={"status": "idle"},
        )

        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.error, "busy")

        gate.set()
        self.assertTrue(executor.wait_for_idle(timeout=1.0))

    def test_submit_updates_store_during_and_after_successful_action(self) -> None:
        store = DashboardStateStore()
        started = threading.Event()
        gate = threading.Event()
        executor = DashboardActionExecutor(store)

        def clear_light() -> DashboardActionResult:
            started.set()
            gate.wait(timeout=1.0)
            return DashboardActionResult(True, "cleared")

        receipt = executor.submit(
            "clear_light",
            clear_light,
            section="light",
            success_patch={"status": "off", "color": None},
        )
        self.assertTrue(started.wait(timeout=1.0))

        running_snapshot = store.snapshot()

        self.assertTrue(receipt.ok)
        self.assertEqual(running_snapshot["system"]["status"], "running")
        self.assertEqual(running_snapshot["system"]["active_action"], "clear_light")
        self.assertEqual(running_snapshot["light"]["status"], "running")
        self.assertIsNone(running_snapshot["light"]["last_result"])

        gate.set()
        self.assertTrue(executor.wait_for_idle(timeout=1.0))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["system"]["status"], "ready")
        self.assertIsNone(snapshot["system"]["active_action"])
        self.assertEqual(snapshot["light"]["status"], "off")
        self.assertIsNone(snapshot["light"]["color"])
        self.assertEqual(snapshot["light"]["last_result"], "cleared")

    def test_submit_success_preserves_existing_degraded_system_status(self) -> None:
        store = DashboardStateStore()
        store.patch(
            "audio",
            {
                "status": "warning",
                "output_device": None,
                "volume_percent": None,
                "last_result": "amixer unavailable",
            },
        )
        executor = DashboardActionExecutor(store)

        receipt = executor.submit(
            "stop",
            lambda: DashboardActionResult(True, "home"),
            section="motion",
            success_patch={"status": "idle"},
        )

        self.assertTrue(receipt.ok)
        self.assertTrue(executor.wait_for_idle(timeout=1.0))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["system"]["status"], "warning")
        self.assertEqual(snapshot["audio"]["status"], "warning")
        self.assertEqual(snapshot["motion"]["status"], "idle")

    def test_submit_records_error_when_action_returns_failed_result(self) -> None:
        store = DashboardStateStore()
        executor = DashboardActionExecutor(store)

        receipt = executor.submit(
            "shutdown_pose",
            lambda: DashboardActionResult(False, "motor unavailable"),
            section="motion",
            success_patch={"status": "idle"},
        )

        self.assertTrue(receipt.ok)
        self.assertTrue(executor.wait_for_idle(timeout=1.0))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["system"]["status"], "error")
        self.assertIsNone(snapshot["system"]["active_action"])
        self.assertEqual(snapshot["motion"]["status"], "error")
        self.assertEqual(snapshot["motion"]["last_result"], "motor unavailable")
        self.assertEqual(snapshot["errors"][0]["code"], "action.shutdown_pose")
        self.assertEqual(snapshot["errors"][0]["source"], "motion")

    def test_submit_records_error_when_callback_raises_exception(self) -> None:
        store = DashboardStateStore()
        executor = DashboardActionExecutor(store)

        receipt = executor.submit(
            "startup",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            section="motion",
            success_patch={"status": "idle"},
        )

        self.assertTrue(receipt.ok)
        self.assertTrue(executor.wait_for_idle(timeout=1.0))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["system"]["status"], "error")
        self.assertEqual(snapshot["motion"]["status"], "error")
        self.assertEqual(snapshot["motion"]["last_result"], "boom")
        self.assertEqual(snapshot["errors"][0]["code"], "action.startup")
        self.assertTrue(snapshot["errors"][0]["active"])

    def test_submit_clears_action_error_after_successful_retry(self) -> None:
        store = DashboardStateStore()
        executor = DashboardActionExecutor(store)

        first = executor.submit(
            "shutdown_pose",
            lambda: DashboardActionResult(False, "motor unavailable"),
            section="motion",
            success_patch={"status": "idle"},
        )
        self.assertTrue(first.ok)
        self.assertTrue(executor.wait_for_idle(timeout=1.0))

        second = executor.submit(
            "shutdown_pose",
            lambda: DashboardActionResult(True, "recovered"),
            section="motion",
            success_patch={"status": "idle"},
        )
        self.assertTrue(second.ok)
        self.assertTrue(executor.wait_for_idle(timeout=1.0))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["system"]["status"], "ready")
        self.assertEqual(snapshot["motion"]["status"], "idle")
        self.assertEqual(snapshot["motion"]["last_result"], "recovered")
        self.assertEqual(snapshot["errors"][0]["code"], "action.shutdown_pose")
        self.assertFalse(snapshot["errors"][0]["active"])


class ActionBuilderTests(unittest.TestCase):
    def test_motion_play_action_uses_executor_with_expected_success_patch(self) -> None:
        executor = _RecordingExecutor()
        bridge = SimpleNamespace(
            settings=SimpleNamespace(home_recording="home_safe"),
            play=lambda name: DashboardActionResult(True, f"played {name}"),
            startup=lambda: DashboardActionResult(True, "started"),
            shutdown_pose=lambda: DashboardActionResult(True, "shutdown"),
            stop=lambda: DashboardActionResult(True, "stopped"),
        )

        actions = build_motion_actions(executor, bridge)
        receipt = actions["play"]("curious")

        self.assertTrue(receipt.ok)
        self.assertEqual(executor.calls[-1]["action_id"], "play:curious")
        self.assertEqual(executor.calls[-1]["section"], "motion")
        self.assertEqual(
            executor.calls[-1]["success_patch"],
            {
                "status": "idle",
                "current_recording": "home_safe",
                "last_completed_recording": "curious",
            },
        )

    def test_light_solid_action_uses_executor_with_expected_success_patch(self) -> None:
        executor = _RecordingExecutor()
        bridge = SimpleNamespace(
            set_light_solid=lambda rgb: DashboardActionResult(True, f"set {rgb}"),
            clear_light=lambda: DashboardActionResult(True, "cleared"),
        )

        actions = build_light_actions(executor, bridge)
        receipt = actions["solid"](255, 170, 70)

        self.assertTrue(receipt.ok)
        self.assertEqual(executor.calls[-1]["action_id"], "light:solid")
        self.assertEqual(executor.calls[-1]["section"], "light")
        self.assertEqual(
            executor.calls[-1]["success_patch"],
            {
                "status": "solid",
                "color": {"red": 255, "green": 170, "blue": 70},
            },
        )


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def submit(self, action_id, callback, *, section, success_patch):
        self.calls.append(
            {
                "action_id": action_id,
                "section": section,
                "success_patch": success_patch,
                "result": callback(),
            }
        )
        return SimpleNamespace(ok=True)


if __name__ == "__main__":
    unittest.main()
