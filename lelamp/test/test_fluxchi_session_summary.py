"""Tests for the listener's R4 relationship-memory hook.

These exercise ``SessionMetrics`` and the write-on-disconnect flush
path in :mod:`lelamp.integrations.fluxchi_listener` without spinning
up a real websocket, dashboard, or motor_bus sentinel. The goal is
to lock the contract that one harness session produces (at most)
one ``session_summary`` event with structurally sound counts — the
voice agent's next prompt relies on it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from lelamp.integrations.fluxchi_listener import (
    FluxChiStateListener,
    ScenarioProfile,
    SessionMetrics,
    VoiceGate,
)
from lelamp.memory.writer import MemoryWriter


class SessionMetricsTests(unittest.TestCase):
    def test_is_interesting_only_true_with_real_activity(self) -> None:
        empty = SessionMetrics()
        self.assertFalse(empty.is_interesting())

        with_level = SessionMetrics(level_counts={"mild": 1})
        self.assertTrue(with_level.is_interesting())

        with_breath = SessionMetrics(breath_completed=1)
        self.assertTrue(with_breath.is_interesting())

        with_interrupt = SessionMetrics(breath_interrupts=2)
        self.assertTrue(with_interrupt.is_interesting())

    def test_note_subject_tracks_trailing_scalars(self) -> None:
        m = SessionMetrics()
        m.note_subject({"stamina": 72.4, "perclos_ewma": 0.18})
        self.assertAlmostEqual(m.final_stamina, 72.4)
        self.assertAlmostEqual(m.final_perclos, 0.18)
        # Non-numeric updates should not corrupt the scalars.
        m.note_subject({"stamina": "bogus", "perclos_ewma": None})
        self.assertAlmostEqual(m.final_stamina, 72.4)
        self.assertAlmostEqual(m.final_perclos, 0.18)

    def test_note_breath_exit_splits_completed_vs_interrupt(self) -> None:
        m = SessionMetrics()
        m.note_breath_exit(completed=True)
        m.note_breath_exit(completed=False)
        m.note_breath_exit(completed=False)
        self.assertEqual(m.breath_completed, 1)
        self.assertEqual(m.breath_interrupts, 2)


class _FakeDashboard:
    """Minimal DashboardClient surface the listener touches in tests."""

    async def aclose(self):  # pragma: no cover - not exercised in this suite
        pass


def _bare_profile() -> ScenarioProfile:
    # Profile with no levels — decide() always returns None so tests
    # never try to reach the dashboard. Metrics side still exercises.
    return ScenarioProfile(name="test_profile", levels=[])


def _build_listener(*, writer: MemoryWriter) -> FluxChiStateListener:
    profile = _bare_profile()
    # Skip the websockets runtime guard — we never call run() in these tests.
    with mock.patch("lelamp.integrations.fluxchi_listener.websockets", new=object()):
        return FluxChiStateListener(
            ws_url="ws://ignored",
            profile=profile,
            dashboard=_FakeDashboard(),
            voice_gate=VoiceGate(enabled=False),
            memory_writer=writer,
            enable_memory=True,
        )


class _FinishedBreath:
    def __init__(self, *, completed: bool = True, interrupt_reason=None):
        self.running = False
        self.stats = SimpleNamespace(
            completed=completed,
            interrupt_reason=interrupt_reason,
        )


class FlushSessionSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="fluxchi-memtest-")
        self.addCleanup(self._cleanup)
        # Pin the memory root to the tmp dir so we don't pollute
        # ~/.lelamp when the test suite runs on a dev box.
        os.environ["LELAMP_MEMORY_ROOT"] = self._tmp
        self.writer = MemoryWriter()

    def _cleanup(self) -> None:
        os.environ.pop("LELAMP_MEMORY_ROOT", None)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _read_events(self) -> list[dict]:
        import json
        path = self.writer.events_path
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_flush_writes_one_event_with_expected_counts(self) -> None:
        listener = _build_listener(writer=self.writer)
        listener._begin_session()
        # Simulate two mild dispatches + one natural breath + one interrupt.
        listener._metrics.note_dispatch("mild")
        listener._metrics.note_dispatch("mild")
        listener._metrics.note_breath_exit(completed=True)
        listener._metrics.note_breath_exit(completed=False)
        listener._metrics.note_subject({"stamina": 58.0, "perclos_ewma": 0.14})

        listener._flush_session_summary("ws_disconnect")

        events = self._read_events()
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual(evt["kind"], "session_summary")
        self.assertEqual(evt["source"], "fluxchi_listener")
        self.assertEqual(evt["payload"]["reason"], "ws_disconnect")
        self.assertEqual(evt["payload"]["level_counts"]["mild"], 2)
        self.assertEqual(evt["payload"]["breath_completed"], 1)
        self.assertEqual(evt["payload"]["breath_interrupts"], 1)
        self.assertAlmostEqual(evt["payload"]["final_stamina"], 58.0)
        self.assertEqual(evt["payload"]["profile_name"], "test_profile")

    def test_flush_skipped_when_session_had_no_activity(self) -> None:
        listener = _build_listener(writer=self.writer)
        listener._begin_session()
        # No dispatches, no breath — boring session. Writer should
        # not have anything to persist because dead-air summaries
        # would pollute the R4 narrative with noise.
        listener._flush_session_summary("ws_disconnect")
        self.assertEqual(self._read_events(), [])

    def test_flush_is_idempotent(self) -> None:
        listener = _build_listener(writer=self.writer)
        listener._begin_session()
        listener._metrics.note_dispatch("moderate")
        listener._flush_session_summary("shutdown")
        # Second call should be a no-op because metrics is None now.
        listener._flush_session_summary("shutdown")
        self.assertEqual(len(self._read_events()), 1)

    def test_ws_reconnect_flushes_and_starts_fresh_session(self) -> None:
        listener = _build_listener(writer=self.writer)
        listener._begin_session()
        first_sid = listener._session_id
        listener._metrics.note_dispatch("mild")
        listener._flush_session_summary("ws_disconnect")

        listener._begin_session()
        second_sid = listener._session_id
        listener._metrics.note_dispatch("severe")
        listener._flush_session_summary("ws_disconnect")

        events = self._read_events()
        self.assertEqual(len(events), 2)
        sids = {evt["session_id"] for evt in events}
        self.assertEqual(sids, {first_sid, second_sid})
        self.assertNotEqual(first_sid, second_sid)
        # Level_counts must NOT bleed across sessions.
        level_counts_by_sid = {evt["session_id"]: evt["payload"]["level_counts"] for evt in events}
        self.assertEqual(level_counts_by_sid[first_sid], {"mild": 1})
        self.assertEqual(level_counts_by_sid[second_sid], {"severe": 1})

    def test_enable_memory_false_skips_write(self) -> None:
        listener = _build_listener(writer=self.writer)
        listener._enable_memory = False
        listener._begin_session()
        listener._metrics.note_dispatch("mild")
        listener._flush_session_summary("ws_disconnect")
        self.assertEqual(self._read_events(), [])

    def test_flush_harvests_naturally_completed_breath_before_write(self) -> None:
        listener = _build_listener(writer=self.writer)
        listener._begin_session()
        listener._metrics.note_dispatch("moderate")
        listener._current_breath = _FinishedBreath(completed=True)

        listener._flush_session_summary("ws_disconnect")

        events = self._read_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["breath_completed"], 1)
        self.assertEqual(events[0]["payload"]["breath_interrupts"], 0)

    def test_handle_harvests_completed_breath_before_processing_next_frame(self) -> None:
        listener = _build_listener(writer=self.writer)
        listener._begin_session()
        listener._current_breath = _FinishedBreath(completed=True)

        import asyncio

        asyncio.run(listener._handle({"ts": 9999999999.0, "subject": {}, "events": []}))

        self.assertEqual(listener._metrics.breath_completed, 1)
        self.assertIsNone(listener._current_breath)


if __name__ == "__main__":
    unittest.main()
