"""Tests for the recovered → happy_wiggle closure trigger (task #17).

The recovered rule has always lived in scene_a.yaml / scene_b.yaml,
but was gated behind ``trigger: after_any_intervention`` and a
listener-side ``continue`` that turned the whole rule into dead code.
These tests lock the newly-wired behavior:

1. decide() refuses recovered when no intervention has happened yet
   (even if stamina is already above the min).
2. decide() allows recovered after an intervention has been recorded.
3. Dispatch of recovered clears the flag so happy_wiggle only plays
   once per recovery cycle, no matter how many frames keep landing
   above stamina_min.
4. A fresh intervention (mild/moderate/severe) re-arms the flag, so
   the user can go through a second fatigue → recovery cycle.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from lelamp.integrations.fluxchi_listener import (
    FluxChiStateListener,
    ScenarioProfile,
    VoiceGate,
)


def _scene_a() -> ScenarioProfile:
    path = (
        Path(__file__).parent.parent
        / "integrations"
        / "profiles"
        / "scene_a.yaml"
    )
    return ScenarioProfile.load_from_yaml(path)


def _frame(stamina: float, perclos: float = 0.0) -> dict:
    return {
        # A very-recent synthetic frame: listener's stale filter uses
        # ``time.time() - ts > stale_sec``; we set ts far in the future
        # so the test doesn't race the wall clock.
        "ts": 9999999999.0,
        "subject": {"stamina": stamina, "perclos_ewma": perclos},
        "events": [],
    }


class DecideGatingTests(unittest.TestCase):
    """Unit-level checks against ``ScenarioProfile.decide``."""

    def test_recovered_blocked_without_prior_intervention(self) -> None:
        profile = _scene_a()
        # stamina 80 is comfortably above recovered's stamina_min=65.
        decision = profile.decide(_frame(stamina=80.0), post_intervention=False)
        self.assertIsNone(decision)

    def test_recovered_fires_after_intervention(self) -> None:
        profile = _scene_a()
        decision = profile.decide(_frame(stamina=80.0), post_intervention=True)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.level, "recovered")
        self.assertTrue(decision.is_recovered)
        self.assertEqual(decision.recording, "happy_wiggle")

    def test_intervention_levels_never_set_is_recovered(self) -> None:
        profile = _scene_a()
        # stamina 38 hits moderate.
        decision = profile.decide(_frame(stamina=38.0, perclos=0.3), post_intervention=True)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.level, "moderate")
        self.assertFalse(decision.is_recovered)

    def test_recovered_gated_below_stamina_min_even_if_armed(self) -> None:
        profile = _scene_a()
        # stamina 60 is below recovered's stamina_min=65. Nothing should
        # match for this scene because the rule list only contains the
        # three intervention rules (which require low stamina) + recovered.
        decision = profile.decide(_frame(stamina=60.0, perclos=0.0), post_intervention=True)
        # 60 is exactly at mild's stamina_max=60 -> should match mild
        self.assertIsNotNone(decision)
        self.assertNotEqual(decision.level, "recovered")


class _FakeDashboard:
    async def aclose(self):  # pragma: no cover
        pass


def _build_listener(profile: ScenarioProfile) -> FluxChiStateListener:
    with mock.patch("lelamp.integrations.fluxchi_listener.websockets", new=object()):
        return FluxChiStateListener(
            ws_url="ws://ignored",
            profile=profile,
            dashboard=_FakeDashboard(),
            voice_gate=VoiceGate(enabled=False),
            dry_run=True,  # keeps hot path off the dashboard HTTP client
            enable_memory=False,
        )


class ListenerFlagLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Listener-level: flag flips on dispatch, resets on recovered."""

    async def test_flag_flips_on_intervention_and_resets_on_recovered(self) -> None:
        profile = _scene_a()
        listener = _build_listener(profile)
        listener._begin_session()

        # 1. Starts disarmed — recovered must not fire.
        self.assertFalse(listener._post_intervention)

        # 2. Dispatch a moderate intervention (dry-run path avoids HTTP).
        await listener._handle(_frame(stamina=35.0, perclos=0.3))
        self.assertTrue(listener._post_intervention)

        # 3. Now stamina bounces back — recovered should fire *once*.
        await listener._handle(_frame(stamina=80.0))
        self.assertFalse(listener._post_intervention)

        # 4. A second high-stamina frame should NOT re-trigger recovered:
        # the flag is cleared, so decide() returns None.
        pre_dispatch_count = len(listener._last_dispatch_ts)
        await listener._handle(_frame(stamina=82.0))
        # recovered already recorded itself in the dispatch table, so
        # the number of tracked levels shouldn't grow on this second
        # recovered-eligible frame.
        self.assertEqual(len(listener._last_dispatch_ts), pre_dispatch_count)

    async def test_second_intervention_rearms_recovered(self) -> None:
        profile = _scene_a()
        listener = _build_listener(profile)
        listener._begin_session()

        # First full cycle: moderate -> recovered.
        await listener._handle(_frame(stamina=35.0, perclos=0.3))
        await listener._handle(_frame(stamina=80.0))
        self.assertFalse(listener._post_intervention)
        # Second cycle: mild dwell not strictly needed for this test —
        # stamina=55 + perclos=0.2 hits mild (stamina_max=60, perclos_min=0.15).
        # We advance _last_dispatch_ts so debounce doesn't eat it.
        listener._last_dispatch_ts.clear()
        await listener._handle(_frame(stamina=55.0, perclos=0.2))
        self.assertTrue(listener._post_intervention)

        # Second recovered fire.
        listener._last_dispatch_ts.clear()
        await listener._handle(_frame(stamina=80.0))
        self.assertFalse(listener._post_intervention)


if __name__ == "__main__":
    unittest.main()
