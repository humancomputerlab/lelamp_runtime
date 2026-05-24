"""Tests for ``lelamp.memory.summary``.

The contract we're nailing down:

* Manual playback-only sessions produce the LIFECYCLE.md §"Manual
  session summary 的合法 shape" exactly -- ``fallback_rate=null``,
  ``style_histogram={}``, ``narrative=null``, all 4 event_counts keys.
* Agent sessions aggregate from events correctly.
* Summaries are atomic (no stray .tmp) and contract-guarded on write.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lelamp.memory import ids as memids
from lelamp.memory import session as memsession
from lelamp.memory import summary as memsummary
from lelamp.memory.summary import SUMMARY_SCHEMA, compute_summary, write_summary
from lelamp.memory.writer import MemoryWriter


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "mem"))
    return MemoryWriter()


def _agent_session(writer) -> str:
    handle = memsession.start_agent_session(
        writer, now=datetime(2026, 4, 17, 23, 11, 15, tzinfo=timezone.utc)
    )
    return handle.session_id


def _manual_session(writer) -> str:
    handle = memsession.attach_or_create_session(
        writer, now=datetime(2026, 4, 17, 9, 32, 10, tzinfo=timezone.utc)
    )
    assert handle.is_manual
    return handle.session_id


class TestManualPlaybackOnly:
    def test_playback_only_shape_matches_contract(self, writer):
        sid = _manual_session(writer)
        writer.write_playback(
            session_id=sid,
            source="remote_control",
            action="play",
            initiator="remote_control",
            recording_name="curious",
            duration_ms=2034,
            ok=True,
        )

        summary = compute_summary(writer, sid)

        assert summary["schema"] == SUMMARY_SCHEMA
        assert summary["session_id"] == sid
        # All 4 keys present and playback==1 everything else zero.
        assert summary["event_counts"] == {
            "conversation": 0,
            "function_tool": 0,
            "fallback_expression": 0,
            "playback": 1,
        }
        assert summary["style_histogram"] == {}
        assert summary["fallback_rate"] is None  # not 0.0, not 0
        assert summary["top_recordings"] == ["curious"]
        assert summary["narrative"] is None

    def test_zero_event_session_still_produces_shape(self, writer):
        sid = _manual_session(writer)
        summary = compute_summary(writer, sid)
        assert summary["event_counts"] == {
            "conversation": 0,
            "function_tool": 0,
            "fallback_expression": 0,
            "playback": 0,
        }
        assert summary["fallback_rate"] is None
        assert summary["top_recordings"] == []
        assert summary["duration_s"] >= 0


class TestAgentAggregation:
    def test_style_histogram_from_conversations(self, writer):
        sid = _agent_session(writer)
        for style in ("caring", "caring", "excited", "sad"):
            writer.write_conversation(
                session_id=sid,
                source="voice_agent",
                user_text="hi",
                assistant_text="ok",
                assistant_style=style,
            )
        summary = compute_summary(writer, sid)
        assert summary["style_histogram"] == {"caring": 2, "excited": 1, "sad": 1}

    def test_fallback_rate_computed_when_conversations_nonzero(self, writer):
        sid = _agent_session(writer)
        for _ in range(6):
            writer.write_conversation(
                session_id=sid,
                source="voice_agent",
                user_text="u",
                assistant_text="a",
                assistant_style="caring",
            )
        for _ in range(2):
            writer.write_fallback_expression(
                session_id=sid,
                source="auto_expression",
                style="curious",
                trigger="voice_silence_timeout",
            )
        summary = compute_summary(writer, sid)
        assert summary["event_counts"]["conversation"] == 6
        assert summary["event_counts"]["fallback_expression"] == 2
        assert summary["fallback_rate"] == pytest.approx(2 / 6, rel=1e-3)

    def test_top_recordings_includes_play_recording_invokes(self, writer):
        sid = _agent_session(writer)
        inv1 = memids.generate_invoke_id()
        inv2 = memids.generate_invoke_id()
        inv3 = memids.generate_invoke_id()
        for name, inv in (("curious", inv1), ("happy_wiggle", inv2), ("curious", inv3)):
            writer.write_function_tool(
                session_id=sid,
                source="voice_agent",
                invoke_id=inv,
                phase="invoke",
                tool_name="play_recording",
                args={"recording_name": name},
                caller="llm",
            )
            writer.write_function_tool(
                session_id=sid,
                source="voice_agent",
                invoke_id=inv,
                phase="result",
                tool_name="play_recording",
                args={"recording_name": name},
                caller="llm",
                duration_ms=1000,
                ok=True,
            )
        summary = compute_summary(writer, sid)
        # result phase must NOT be double-counted.
        assert summary["top_recordings"][:2] == ["curious", "happy_wiggle"]

    def test_other_tool_invokes_do_not_pollute_recordings(self, writer):
        sid = _agent_session(writer)
        writer.write_function_tool(
            session_id=sid,
            source="voice_agent",
            invoke_id=memids.generate_invoke_id(),
            phase="invoke",
            tool_name="set_rgb_solid",
            args={"rgb": [255, 0, 0]},
            caller="llm",
        )
        summary = compute_summary(writer, sid)
        assert summary["top_recordings"] == []


class TestWriteSummary:
    def test_roundtrip_atomic(self, writer):
        sid = _manual_session(writer)
        writer.write_playback(
            session_id=sid,
            source="dashboard",
            action="play",
            initiator="dashboard",
            recording_name="curious",
            duration_ms=100,
            ok=True,
        )
        path = memsummary.compute_and_write_summary(writer, sid)
        assert path.exists()
        sessions_dir = writer.user_dir / "sessions"
        # No stray tmp files.
        assert list(sessions_dir.glob("*.tmp")) == []
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["session_id"] == sid
        assert loaded["fallback_rate"] is None

    def test_write_rejects_wrong_session_id(self, writer):
        sid = _agent_session(writer)
        summary = compute_summary(writer, sid)
        summary["session_id"] = "sess_manual_2026-04-17_00-00-00"
        with pytest.raises(ValueError, match="session_id mismatch"):
            write_summary(writer, sid, summary)

    def test_write_rejects_wrong_schema(self, writer):
        sid = _agent_session(writer)
        summary = compute_summary(writer, sid)
        summary["schema"] = "other.schema"
        with pytest.raises(ValueError, match="schema"):
            write_summary(writer, sid, summary)

    def test_write_rejects_null_style_histogram(self, writer):
        sid = _agent_session(writer)
        summary = compute_summary(writer, sid)
        summary["style_histogram"] = None
        with pytest.raises(ValueError, match="style_histogram"):
            write_summary(writer, sid, summary)

    def test_write_rejects_manual_with_narrative(self, writer):
        sid = _manual_session(writer)
        summary = compute_summary(writer, sid)
        summary["narrative"] = "forbidden"
        with pytest.raises(ValueError, match="narrative"):
            write_summary(writer, sid, summary)

    def test_write_rejects_zero_conv_with_fallback_rate_zero(self, writer):
        sid = _agent_session(writer)  # no events
        summary = compute_summary(writer, sid)
        summary["fallback_rate"] = 0.0
        with pytest.raises(ValueError, match="fallback_rate"):
            write_summary(writer, sid, summary)


class TestTimestamps:
    def test_start_pulled_from_meta(self, writer):
        sid = _agent_session(writer)
        meta = memsession.load_meta(writer, sid)
        writer.write_conversation(
            session_id=sid,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        summary = compute_summary(writer, sid)
        assert summary["start_ts_ms"] == meta["start_ts_ms"]

    def test_end_from_last_event_if_not_provided(self, writer):
        sid = _agent_session(writer)
        record = writer.write_conversation(
            session_id=sid,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
            ts_ms=1_900_000_000_000,
        )
        summary = compute_summary(writer, sid)
        assert summary["end_ts_ms"] == record["ts_ms"]
