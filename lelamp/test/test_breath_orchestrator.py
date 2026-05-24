"""Unit tests for breath_orchestrator and Scene B profile loading.

Run: uv run python -m pytest lelamp/test/test_breath_orchestrator.py -v
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from lelamp.breath_orchestrator import BreathOrchestrator, BreathPlan, kelvin_to_rgb


class KelvinToRgbTests(unittest.TestCase):
    def test_warm_anchor_is_amber(self):
        r, g, b = kelvin_to_rgb(2000)
        self.assertEqual(r, 255, "2000K red at ceiling")
        self.assertGreater(r, b, "2000K red > blue (warm)")
        self.assertLess(b, 50, "2000K blue ~ 0")

    def test_daylight_anchor_is_white(self):
        r, g, b = kelvin_to_rgb(6500)
        self.assertGreater(min(r, g, b), 230, "6500K near full white")

    def test_blue_increases_with_kelvin(self):
        b2000 = kelvin_to_rgb(2000)[2]
        b3000 = kelvin_to_rgb(3000)[2]
        b6500 = kelvin_to_rgb(6500)[2]
        self.assertLess(b2000, b3000)
        self.assertLess(b3000, b6500)

    def test_clamps_to_range(self):
        # Should not raise at extremes
        kelvin_to_rgb(500)
        kelvin_to_rgb(100000)


class BreathPlanValidationTests(unittest.TestCase):
    def test_rejects_zero_duration(self):
        with self.assertRaises(ValueError):
            BreathPlan(duration_sec=0)

    def test_rejects_peak_below_base(self):
        with self.assertRaises(ValueError):
            BreathPlan(intensity_peak=0.2, intensity_base=0.5)

    def test_rejects_zero_tick_hz(self):
        with self.assertRaises(ValueError):
            BreathPlan(tick_hz=0)


class BreathOrchestratorRunTests(unittest.TestCase):
    def test_short_run_emits_expected_ticks(self):
        collected = []
        plan = BreathPlan(duration_sec=1.0, bpm_start=12.0, bpm_end=4.0,
                          kelvin_start=3000.0, kelvin_end=2400.0,
                          intensity_base=0.3, intensity_peak=1.0,
                          tick_hz=10.0, name="utest")
        # fade_out_sec=0 on this test to isolate main-loop tick count
        # from the fade tail phase (tested separately below)
        orch = BreathOrchestrator(plan, lambda e, p: collected.append((e, p)),
                                  stop_clears=True, dedupe=False, fade_out_sec=0)
        orch.start()
        self.assertTrue(orch.wait_until_done(timeout=3.0))
        self.assertTrue(orch.stats.completed)
        # ~10 ticks in 1s @ 10Hz, with some tolerance
        self.assertGreaterEqual(orch.stats.ticks, 7)
        self.assertLessEqual(orch.stats.ticks, 14)
        self.assertEqual(orch.stats.errors, 0)
        # Last event is the clear (stop_clears=True on natural completion)
        self.assertEqual(collected[-1], ("solid", (0, 0, 0)))

    def test_stop_preempts_and_clears(self):
        collected = []
        plan = BreathPlan(duration_sec=10.0, tick_hz=10.0, name="utest_stop")
        orch = BreathOrchestrator(plan, lambda e, p: collected.append((e, p)),
                                  stop_clears=True, dedupe=False)
        orch.start()
        time.sleep(0.3)
        orch.stop()
        orch.wait_until_done(timeout=1.0)
        self.assertFalse(orch.stats.completed)
        self.assertEqual(collected[-1], ("solid", (0, 0, 0)))

    def test_handler_errors_do_not_crash(self):
        calls = [0]
        def flaky(e, p):
            calls[0] += 1
            if calls[0] % 3 == 0:
                raise RuntimeError("simulated")
        plan = BreathPlan(duration_sec=0.6, tick_hz=10.0, name="utest_flaky")
        orch = BreathOrchestrator(plan, flaky, stop_clears=False, dedupe=False)
        orch.start()
        self.assertTrue(orch.wait_until_done(timeout=3.0))
        self.assertTrue(orch.stats.completed)
        self.assertGreater(orch.stats.errors, 0)

    def test_stop_is_idempotent_after_natural_completion(self):
        collected = []
        plan = BreathPlan(duration_sec=0.5, tick_hz=5.0, name="utest_idempotent_stop")
        orch = BreathOrchestrator(
            plan,
            lambda e, p: collected.append((e, p)),
            stop_clears=True,
            dedupe=False,
        )
        orch.start()
        self.assertTrue(orch.wait_until_done(timeout=3.0))
        self.assertTrue(orch.stats.completed)

        events_after_completion = list(collected)
        self.assertEqual(events_after_completion[-1], ("solid", (0, 0, 0)))

        orch.stop()

        self.assertEqual(
            collected,
            events_after_completion,
            "stop() after natural completion should not emit an extra clear",
        )

    def test_should_continue_interrupts_mid_run(self):
        """voice_gate / user override 模拟：返回非 None 字符串即中断。"""
        collected = []
        gate_state = {"blocked_after_tick": 5, "tick": 0}

        def should_continue():
            gate_state["tick"] += 1
            if gate_state["tick"] > gate_state["blocked_after_tick"]:
                return "voice_user_speaking"
            return None

        plan = BreathPlan(duration_sec=5.0, tick_hz=10.0, name="utest_interrupt",
                          intensity_base=0.3, intensity_peak=1.0)
        orch = BreathOrchestrator(
            plan, lambda e, p: collected.append((e, p)),
            stop_clears=True, dedupe=False,
            should_continue=should_continue,
            fade_out_sec=2.0, interrupt_fade_sec=0.3,
        )
        orch.start()
        self.assertTrue(orch.wait_until_done(timeout=4.0))
        self.assertFalse(orch.stats.completed, "interrupt should mark completed=False")
        self.assertEqual(orch.stats.interrupt_reason, "voice_user_speaking")
        self.assertLess(orch.stats.ticks, 30, "interrupted quickly, not a full 5s run")
        # Last frame is the clear; several intermediate frames descend in intensity
        self.assertEqual(collected[-1], ("solid", (0, 0, 0)))
        self.assertGreater(orch.stats.fade_ticks, 0, "fade tail ran")

    def test_fade_tail_descends_monotonically_on_natural_completion(self):
        """自然完成 → fade_out_sec 秒 warm amber 渐隐到 (0,0,0)，每步 intensity 单调递减。"""
        collected = []
        plan = BreathPlan(duration_sec=0.8, tick_hz=10.0, name="utest_fade",
                          intensity_base=0.8, intensity_peak=1.0)
        orch = BreathOrchestrator(
            plan, lambda e, p: collected.append((e, p)),
            stop_clears=True, dedupe=False,
            fade_out_sec=1.0, interrupt_fade_sec=0.3,
        )
        orch.start()
        self.assertTrue(orch.wait_until_done(timeout=4.0))
        self.assertTrue(orch.stats.completed)
        self.assertGreaterEqual(orch.stats.fade_ticks, 3, "natural fade emits multiple frames")

        # Last frame is always (0,0,0); the N frames before it should be warm and
        # monotonically decreasing in the R channel (intensity drops)
        rgbs = [p for _, p in collected]
        self.assertEqual(rgbs[-1], (0, 0, 0))
        # Pull the final 4 non-clear frames — they are part of the fade tail
        tail = [p for p in rgbs[:-1] if p != (0, 0, 0)][-4:]
        reds = [p[0] for p in tail]
        # Fade phase should monotonically descend R (since kelvin is pinned to end value,
        # and intensity multiplier is linearly decreasing)
        for i in range(len(reds) - 1):
            self.assertGreaterEqual(
                reds[i], reds[i + 1],
                f"fade tail R non-monotonic: {reds} at index {i}"
            )

    def test_stop_after_fade_is_still_idempotent(self):
        """natural completion → fade → clear → 再调 stop() 不应该再 emit 任何帧。"""
        collected = []
        plan = BreathPlan(duration_sec=0.4, tick_hz=10.0, name="utest_idempotent_fade",
                          intensity_base=0.5, intensity_peak=1.0)
        orch = BreathOrchestrator(
            plan, lambda e, p: collected.append((e, p)),
            stop_clears=True, dedupe=False,
            fade_out_sec=0.3,
        )
        orch.start()
        self.assertTrue(orch.wait_until_done(timeout=3.0))
        self.assertTrue(orch.stats.completed)
        snapshot = list(collected)
        orch.stop()
        self.assertEqual(collected, snapshot,
                         "stop() after fade completed should not emit an extra frame")

    def test_warm_spectrum_property(self):
        """At 2400-3000K, every non-clear frame should satisfy R >= G >= B."""
        frames = []
        plan = BreathPlan(duration_sec=1.0, bpm_start=12.0, bpm_end=4.0,
                          kelvin_start=3000.0, kelvin_end=2400.0,
                          tick_hz=10.0, name="utest_warm")
        orch = BreathOrchestrator(plan, lambda e, p: frames.append(p),
                                  stop_clears=False, dedupe=False)
        orch.start()
        orch.wait_until_done(timeout=3.0)
        self.assertGreater(len(frames), 0)
        for r, g, b in frames:
            self.assertGreaterEqual(r, g, f"R>=G at rgb=({r},{g},{b})")
            self.assertGreaterEqual(g, b, f"G>=B at rgb=({r},{g},{b})")


class SceneProfileTests(unittest.TestCase):
    """Make sure action.type: breath parses into a BreathPlan on Scene B, and
    that Scene A (motion-only) still loads with action_type = motion."""

    def setUp(self):
        # Import lazily so this module stays importable even if PyYAML is missing
        from lelamp.integrations.fluxchi_listener import (
            ScenarioProfile, ACTION_TYPE_MOTION, ACTION_TYPE_BREATH,
        )
        self.ScenarioProfile = ScenarioProfile
        self.ACTION_TYPE_MOTION = ACTION_TYPE_MOTION
        self.ACTION_TYPE_BREATH = ACTION_TYPE_BREATH
        self.profiles_dir = (
            Path(__file__).resolve().parent.parent / "integrations" / "profiles"
        )

    def test_scene_a_all_motion(self):
        p = self.ScenarioProfile.load_from_yaml(self.profiles_dir / "scene_a.yaml")
        self.assertEqual(len(p.levels), 4)
        for lv in p.levels:
            self.assertEqual(lv.action_type, self.ACTION_TYPE_MOTION)
            self.assertIsNone(lv.breath)

    def test_scene_b_mild_and_moderate_are_breath(self):
        p = self.ScenarioProfile.load_from_yaml(self.profiles_dir / "scene_b.yaml")
        by_name = {lv.name: lv for lv in p.levels}
        self.assertEqual(by_name["moderate"].action_type, self.ACTION_TYPE_BREATH)
        self.assertIsNotNone(by_name["moderate"].breath)
        self.assertEqual(by_name["moderate"].breath.duration_sec, 90.0)
        self.assertEqual(by_name["moderate"].breath.bpm_start, 12.0)
        self.assertEqual(by_name["moderate"].breath.bpm_end, 4.0)
        self.assertEqual(by_name["moderate"].breath.kelvin_start, 3000.0)
        self.assertEqual(by_name["moderate"].breath.kelvin_end, 2400.0)

        self.assertEqual(by_name["mild"].action_type, self.ACTION_TYPE_BREATH)
        self.assertEqual(by_name["mild"].breath.duration_sec, 60.0)

    def test_scene_b_severe_stays_discrete(self):
        p = self.ScenarioProfile.load_from_yaml(self.profiles_dir / "scene_b.yaml")
        by_name = {lv.name: lv for lv in p.levels}
        self.assertEqual(by_name["severe"].action_type, self.ACTION_TYPE_MOTION)
        self.assertEqual(by_name["severe"].recording, "sad")
        self.assertEqual(by_name["severe"].followup_recording, "nod")

    def test_decide_routes_moderate_to_breath(self):
        p = self.ScenarioProfile.load_from_yaml(self.profiles_dir / "scene_b.yaml")
        frame = {
            "ts": time.time(),
            "subject": {"stamina": 35.0, "perclos_ewma": 0.3},
            "events": [],
        }
        d = p.decide(frame)
        self.assertIsNotNone(d)
        self.assertEqual(d.level, "moderate")
        self.assertEqual(d.action_type, self.ACTION_TYPE_BREATH)
        self.assertIsNotNone(d.breath)
        self.assertIsNone(d.recording)

    def test_decide_routes_microsleep_to_severe_motion(self):
        p = self.ScenarioProfile.load_from_yaml(self.profiles_dir / "scene_b.yaml")
        frame = {
            "ts": time.time(),
            "subject": {"stamina": 20.0, "perclos_ewma": 0.4},
            "events": [{"kind": "microsleep_detected"}],
        }
        d = p.decide(frame)
        self.assertIsNotNone(d)
        self.assertEqual(d.level, "severe")
        self.assertEqual(d.action_type, self.ACTION_TYPE_MOTION)
        self.assertEqual(d.recording, "sad")

    def test_decide_no_trigger_when_fresh(self):
        p = self.ScenarioProfile.load_from_yaml(self.profiles_dir / "scene_b.yaml")
        frame = {
            "ts": time.time(),
            "subject": {"stamina": 80.0, "perclos_ewma": 0.05},
            "events": [],
        }
        self.assertIsNone(p.decide(frame))


if __name__ == "__main__":
    unittest.main()
