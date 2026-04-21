"""Unit tests for the manual fallback button (DEMO_PLAN §1.2).

These exercise ``lelamp.dashboard.actions.intervene`` directly without
FastAPI / TestClient, so they run even in environments without the full
web stack installed. The HTTP-surface tests (route wiring, status codes)
live in ``test_dashboard_api.py`` and require ``fastapi`` + ``httpx``.
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from lelamp.dashboard.actions.intervene import (
    BREATH,
    MOTION,
    BreathSlot,
    INTERVENE_STYLES,
    available_styles,
    build_intervene_action,
    style_metadata,
)
from lelamp.dashboard.runtime_bridge import DashboardActionResult


class FakeExecutor:
    """Synchronous executor stand-in that records submit() args."""

    def __init__(self, *, busy: bool = False) -> None:
        self.busy = busy
        self.active: str | None = None
        self.submitted: list[tuple[str, object, str]] = []

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
                message="busy",
                error="busy",
                active_action=self.active,
            )
        self.submitted.append((action_id, callback, section))
        self.active = action_id
        return SimpleNamespace(
            ok=True,
            action_id=action_id,
            state="running",
            message=f"{action_id} started",
            error=None,
            active_action=action_id,
        )


class FakeBridge:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(home_recording="home_safe")
        self.played: list[str] = []
        self._fail_on: set[str] = set()

    def set_play_failure(self, name: str) -> None:
        self._fail_on.add(name)

    def play(self, name: str) -> DashboardActionResult:
        self.played.append(name)
        if name in self._fail_on:
            return DashboardActionResult(False, f"{name} failed")
        return DashboardActionResult(True, f"played {name}")


class FakeRgbService:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self._lock = threading.Lock()

    def handle_event(self, event_type, payload) -> None:
        with self._lock:
            self.events.append((event_type, payload))


# ─── Registry sanity ──────────────────────────────────────────


class StyleRegistryTests(unittest.TestCase):
    def test_expected_styles_present(self) -> None:
        # Order matters — the dashboard UI lays out buttons in this order.
        self.assertEqual(
            available_styles(),
            ["shy", "headshake", "sad_nod", "breath_moderate", "breath_mild"],
        )

    def test_style_metadata_types(self) -> None:
        meta = {m["style"]: m for m in style_metadata()}
        self.assertEqual(meta["shy"]["type"], MOTION)
        self.assertEqual(meta["sad_nod"]["recordings"], ["sad", "nod"])
        self.assertEqual(meta["breath_moderate"]["type"], BREATH)
        self.assertAlmostEqual(meta["breath_moderate"]["duration_sec"], 90.0)
        self.assertAlmostEqual(meta["breath_mild"]["duration_sec"], 60.0)

    def test_breath_spec_is_frozen(self) -> None:
        # The spec wrapper IS frozen — prevents someone from swapping the
        # plan on the shared registry mid-demo.
        spec = INTERVENE_STYLES["breath_moderate"]
        with self.assertRaises(Exception):
            spec.plan = None  # type: ignore[misc]


# ─── Motion dispatch ──────────────────────────────────────────


class MotionDispatchTests(unittest.TestCase):
    def test_shy_submits_single_play(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        slot = BreathSlot(lambda: FakeRgbService())
        intervene = build_intervene_action(executor, bridge, slot)

        receipt = intervene("shy")
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.action_id, "intervene:shy")
        self.assertEqual(len(executor.submitted), 1)

        # The executor's worker thread would invoke the callback. Run it
        # synchronously here to assert the bridge saw the right call.
        _, cb, section = executor.submitted[0]
        self.assertEqual(section, "motion")
        result = cb()
        self.assertTrue(result.ok)
        self.assertEqual(bridge.played, ["shy"])

    def test_sad_nod_chains_two_plays_in_order(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        slot = BreathSlot(lambda: FakeRgbService())
        intervene = build_intervene_action(executor, bridge, slot)

        intervene("sad_nod")
        _, cb, _ = executor.submitted[0]
        cb()
        self.assertEqual(bridge.played, ["sad", "nod"])

    def test_sad_nod_short_circuits_on_failure(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        bridge.set_play_failure("sad")
        slot = BreathSlot(lambda: FakeRgbService())
        intervene = build_intervene_action(executor, bridge, slot)

        intervene("sad_nod")
        _, cb, _ = executor.submitted[0]
        result = cb()
        self.assertFalse(result.ok)
        self.assertEqual(bridge.played, ["sad"])  # did not attempt "nod"

    def test_motion_returns_busy_receipt_when_executor_busy(self) -> None:
        executor = FakeExecutor(busy=True)
        executor.active = "play:curious"
        bridge = FakeBridge()
        slot = BreathSlot(lambda: FakeRgbService())
        intervene = build_intervene_action(executor, bridge, slot)

        receipt = intervene("headshake")
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.error, "busy")

    def test_unknown_style_raises_key_error(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        slot = BreathSlot(lambda: FakeRgbService())
        intervene = build_intervene_action(executor, bridge, slot)
        with self.assertRaises(KeyError):
            intervene("nope")


# ─── Breath dispatch ──────────────────────────────────────────


def _wait_for_orchestrator_to_stop(orch, timeout: float = 3.0) -> None:
    orch.stop()
    orch.wait_until_done(timeout=timeout)


class BreathDispatchTests(unittest.TestCase):
    def test_breath_moderate_starts_orchestrator(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        service = FakeRgbService()
        slot = BreathSlot(lambda: service)
        intervene = build_intervene_action(executor, bridge, slot)

        receipt = intervene("breath_moderate")
        try:
            self.assertTrue(receipt.ok)
            self.assertEqual(receipt.action_id, "intervene:breath_moderate")
            orch = slot.current()
            self.assertIsNotNone(orch)
            self.assertTrue(slot.is_running() or orch.stats.dispatches >= 0)
            # Give it a moment to dispatch at least one tick, then stop.
            time.sleep(0.25)
        finally:
            _wait_for_orchestrator_to_stop(slot.current())
        # At least one "solid" event should have reached the fake service.
        self.assertTrue(
            any(evt == "solid" for evt, _ in service.events),
            f"expected a solid event, got {service.events[:3]}",
        )

    def test_breath_preempts_previous_breath(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        service = FakeRgbService()
        slot = BreathSlot(lambda: service)
        intervene = build_intervene_action(executor, bridge, slot)

        try:
            intervene("breath_moderate")
            first = slot.current()
            self.assertIsNotNone(first)
            time.sleep(0.1)  # let first orchestrator get rolling

            intervene("breath_mild")
            second = slot.current()
            self.assertIsNotNone(second)
            self.assertIsNot(first, second)

            # First orchestrator must have been stopped by the preemption.
            first.wait_until_done(timeout=2.0)
            self.assertFalse(first.running)
        finally:
            current = slot.current()
            if current is not None:
                _wait_for_orchestrator_to_stop(current)

    def test_motion_click_preempts_running_breath(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        service = FakeRgbService()
        slot = BreathSlot(lambda: service)
        intervene = build_intervene_action(executor, bridge, slot)

        intervene("breath_mild")
        running = slot.current()
        self.assertIsNotNone(running)
        time.sleep(0.1)
        # A motion intervene should stop the breath first, then submit
        # the motion. The listener does the same thing.
        intervene("shy")
        running.wait_until_done(timeout=2.0)
        self.assertFalse(running.running)

    def test_rgb_factory_failure_surfaces_as_error_receipt(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()

        def _boom():
            raise RuntimeError("sentinel missing")

        slot = BreathSlot(_boom)
        intervene = build_intervene_action(executor, bridge, slot)

        receipt = intervene("breath_moderate")
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.error, "breath_start_failed")
        self.assertIn("sentinel missing", receipt.message)

    def test_slot_stop_is_idempotent(self) -> None:
        executor = FakeExecutor()
        bridge = FakeBridge()
        service = FakeRgbService()
        slot = BreathSlot(lambda: service)
        intervene = build_intervene_action(executor, bridge, slot)

        # No orchestrator yet — stop() should report nothing stopped.
        self.assertFalse(slot.stop())

        intervene("breath_mild")
        time.sleep(0.1)
        self.assertTrue(slot.stop())
        current = slot.current()
        if current is not None:
            current.wait_until_done(timeout=2.0)
        # Second stop after shutdown reports nothing to stop.
        self.assertFalse(slot.stop())


if __name__ == "__main__":
    unittest.main()
