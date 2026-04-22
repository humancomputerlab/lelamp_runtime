"""Tests for the listener's global session cooldown / budget (task #18).

The listener already has per-level debounce. Task #18 adds a coarser
sliding-window cap across *all* non-severe interventions in a session,
so the lamp does not nag repeatedly during a long low-stamina stretch.

These tests lock three behaviors:

1. Successful dispatches are recorded in ``_dispatch_history``.
2. Once the window is full, the next mild/moderate dispatch is dropped.
3. Severe still bypasses the budget because it is safety-critical.
"""

from __future__ import annotations

import unittest
from unittest import mock

from lelamp.integrations.fluxchi_listener import (
    ACTION_TYPE_MOTION,
    FluxChiStateListener,
    LevelRule,
    ScenarioProfile,
    VoiceGate,
)


def _profile_with_budget() -> ScenarioProfile:
    return ScenarioProfile(
        name="budget_profile",
        debounce_same_level_sec=0.0,
        level_upgrade_immediate=True,
        voice_gate_enabled=False,
        global_budget_max=1,
        global_budget_window_sec=300.0,
        levels=[
            LevelRule(
                name="severe",
                stamina_max=25.0,
                action_type=ACTION_TYPE_MOTION,
                recording="sad",
                urgency_rank=3,
            ),
            LevelRule(
                name="moderate",
                stamina_max=40.0,
                perclos_ewma_min=0.25,
                action_type=ACTION_TYPE_MOTION,
                recording="headshake",
                urgency_rank=2,
            ),
            LevelRule(
                name="mild",
                stamina_max=60.0,
                perclos_ewma_min=0.15,
                action_type=ACTION_TYPE_MOTION,
                recording="shy",
                urgency_rank=1,
            ),
            LevelRule(
                name="recovered",
                stamina_min=65.0,
                trigger="after_any_intervention",
                action_type=ACTION_TYPE_MOTION,
                recording="happy_wiggle",
                urgency_rank=-1,
            ),
        ],
    )


def _frame(stamina: float, perclos: float = 0.0, *, events: list[dict] | None = None) -> dict:
    return {
        "ts": 9999999999.0,
        "subject": {"stamina": stamina, "perclos_ewma": perclos},
        "events": [] if events is None else events,
    }


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
            dry_run=True,
            enable_memory=False,
        )


class GlobalBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_is_recorded_in_budget_history(self) -> None:
        listener = _build_listener(_profile_with_budget())
        listener._begin_session()

        await listener._handle(_frame(stamina=55.0, perclos=0.2))

        self.assertEqual(len(listener._dispatch_history), 1)
        self.assertEqual(listener._dispatch_history[0][1], "mild")

    async def test_second_non_severe_dispatch_is_dropped_when_budget_full(self) -> None:
        listener = _build_listener(_profile_with_budget())
        listener._begin_session()

        await listener._handle(_frame(stamina=55.0, perclos=0.2))
        self.assertIn("mild", listener._last_dispatch_ts)

        await listener._handle(_frame(stamina=35.0, perclos=0.3))

        self.assertNotIn("moderate", listener._last_dispatch_ts)
        self.assertEqual(listener._metrics.level_counts, {"mild": 1})

    async def test_severe_bypasses_global_budget(self) -> None:
        listener = _build_listener(_profile_with_budget())
        listener._begin_session()

        await listener._handle(_frame(stamina=55.0, perclos=0.2))
        await listener._handle(
            _frame(
                stamina=20.0,
                perclos=0.4,
                events=[{"kind": "microsleep_detected"}],
            )
        )

        self.assertIn("severe", listener._last_dispatch_ts)
        self.assertEqual(listener._metrics.level_counts, {"mild": 1, "severe": 1})


if __name__ == "__main__":
    unittest.main()
