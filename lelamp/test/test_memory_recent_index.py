"""Tests for ``lelamp.memory.recent_index``.

The two invariants we care about most:

1.  Manual sessions never leak into either ``sessions`` or
    ``event_tail_refs`` -- the prompt path depends on this.
2.  ``sessions`` caps at 3 newest-first, ``event_tail_refs`` caps at
    200 and preserves chronological append order.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lelamp.memory import recent_index as memidx
from lelamp.memory import session as memsession
from lelamp.memory import summary as memsummary
from lelamp.memory.recent_index import (
    RECENT_EVENT_TAIL_LIMIT,
    RECENT_INDEX_SCHEMA,
    RECENT_SESSION_LIMIT,
    build_recent_index,
    rebuild_recent_index,
)
from lelamp.memory.writer import MemoryWriter


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "mem"))
    return MemoryWriter()


def _agent_session(writer, *, hour: int):
    handle = memsession.start_agent_session(
        writer, now=datetime(2026, 4, 17, hour, 0, 0, tzinfo=timezone.utc)
    )
    return handle


def _manual_session(writer, *, minute: int = 0):
    # Ensure attach_or_create_session cannot latch on to any existing
    # agent session by mutating their persisted pid to a bogus value.
    sessions_dir = writer.user_dir / "sessions"
    for meta_path in sessions_dir.glob("*.meta.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("pid") is not None:
            meta["pid"] = 2**31 - 1
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
    handle = memsession.attach_or_create_session(
        writer, now=datetime(2026, 4, 17, 0, minute, 0, tzinfo=timezone.utc)
    )
    assert handle.is_manual
    return handle


class TestSessionsFilter:
    def test_manual_summaries_excluded(self, writer):
        manual = _manual_session(writer)
        writer.write_playback(
            session_id=manual.session_id,
            source="dashboard",
            action="play",
            initiator="dashboard",
            recording_name="curious",
            duration_ms=100,
            ok=True,
        )
        memsummary.compute_and_write_summary(writer, manual.session_id)

        index = build_recent_index(writer)
        assert index["schema"] == RECENT_INDEX_SCHEMA
        assert index["sessions"] == []

    def test_caps_at_three_newest_first(self, writer):
        sessions = []
        for hour in range(1, 6):  # 5 agent sessions
            h = _agent_session(writer, hour=hour)
            writer.write_conversation(
                session_id=h.session_id,
                source="voice_agent",
                user_text="u",
                assistant_text="a",
                assistant_style="caring",
            )
            memsummary.compute_and_write_summary(writer, h.session_id)
            sessions.append(h.session_id)

        index = build_recent_index(writer)
        assert len(index["sessions"]) == RECENT_SESSION_LIMIT
        # Newest three in descending order = hours 5, 4, 3.
        ordered_ids = [s["session_id"] for s in index["sessions"]]
        assert ordered_ids == list(reversed(sessions[-RECENT_SESSION_LIMIT:]))

    def test_summary_ref_points_under_sessions_dir(self, writer):
        h = _agent_session(writer, hour=1)
        writer.write_conversation(
            session_id=h.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        memsummary.compute_and_write_summary(writer, h.session_id)

        index = build_recent_index(writer)
        assert index["sessions"][0]["summary_ref"] == f"sessions/{h.session_id}.summary.json"


class TestEventTailRefs:
    def test_excludes_manual_events(self, writer):
        agent = _agent_session(writer, hour=1)
        manual = _manual_session(writer, minute=5)
        writer.write_conversation(
            session_id=agent.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        writer.write_playback(
            session_id=manual.session_id,
            source="dashboard",
            action="play",
            initiator="dashboard",
            recording_name="curious",
            duration_ms=100,
            ok=True,
        )

        refs = build_recent_index(writer)["event_tail_refs"]
        assert len(refs) == 1
        assert refs[0]["kind"] == "conversation"

    def test_event_tail_only_keeps_events_from_last_three_agent_sessions(self, writer):
        written_ids = []
        for hour in range(1, 5):
            handle = _agent_session(writer, hour=hour)
            event = writer.write_conversation(
                session_id=handle.session_id,
                source="voice_agent",
                user_text=f"u{hour}",
                assistant_text=f"a{hour}",
                assistant_style="caring",
                ts_ms=1_000_000 + hour,
            )
            memsummary.compute_and_write_summary(writer, handle.session_id)
            written_ids.append(event["event_id"])

        refs = build_recent_index(writer)["event_tail_refs"]
        ref_ids = [ref["event_id"] for ref in refs]

        assert written_ids[0] not in ref_ids
        assert ref_ids == written_ids[1:]

    def test_caps_at_200_most_recent(self, writer):
        h = _agent_session(writer, hour=1)
        for i in range(RECENT_EVENT_TAIL_LIMIT + 50):
            writer.write_conversation(
                session_id=h.session_id,
                source="voice_agent",
                user_text=f"u{i}",
                assistant_text=f"a{i}",
                assistant_style="caring",
                ts_ms=1_000_000 + i,
            )

        refs = build_recent_index(writer)["event_tail_refs"]
        assert len(refs) == RECENT_EVENT_TAIL_LIMIT
        # First ref should be the 51st event (oldest kept), last is newest.
        assert refs[0]["ts_ms"] == 1_000_000 + 50
        assert refs[-1]["ts_ms"] == 1_000_000 + RECENT_EVENT_TAIL_LIMIT + 49

    def test_projection_includes_expected_fields(self, writer):
        h = _agent_session(writer, hour=1)
        writer.write_conversation(
            session_id=h.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        ref = build_recent_index(writer)["event_tail_refs"][0]
        assert set(ref.keys()) == {"event_id", "kind", "ts_ms"}


class TestRebuildAtomic:
    def test_rebuild_writes_atomic(self, writer):
        h = _agent_session(writer, hour=1)
        writer.write_conversation(
            session_id=h.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        memsummary.compute_and_write_summary(writer, h.session_id)

        path = rebuild_recent_index(writer)
        assert path.exists()
        # No stray tmp.
        assert list(writer.user_dir.glob("*.tmp")) == []
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["schema"] == RECENT_INDEX_SCHEMA
        assert loaded["sessions"][0]["session_id"] == h.session_id

    def test_rebuild_is_idempotent(self, writer):
        h = _agent_session(writer, hour=1)
        writer.write_conversation(
            session_id=h.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        memsummary.compute_and_write_summary(writer, h.session_id)

        first = json.loads(rebuild_recent_index(writer).read_text(encoding="utf-8"))
        second = json.loads(rebuild_recent_index(writer).read_text(encoding="utf-8"))
        # built_at_ms may tick forward; compare structural fields only.
        assert first["sessions"] == second["sessions"]
        assert first["event_tail_refs"] == second["event_tail_refs"]


class TestEmptyStore:
    def test_empty_memory_produces_valid_index(self, writer):
        path = rebuild_recent_index(writer)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["sessions"] == []
        assert loaded["event_tail_refs"] == []
        assert loaded["schema"] == RECENT_INDEX_SCHEMA
