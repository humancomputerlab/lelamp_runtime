"""Tests for ``lelamp.memory.selfcheck`` + SessionHandle.close().

Covers the LIFECYCLE crash-recovery contract end-to-end:

* Dead-agent / manual orphans get their summaries backfilled.
* Live agent sessions are preserved (not backfilled mid-flight).
* recent_index staleness detection + rebuild.
* SessionHandle.close() writes summary + recent_index for owners and
  is a no-op for attached handles.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest

from lelamp.memory import recent_index as memidx
from lelamp.memory import selfcheck as memselfcheck
from lelamp.memory import session as memsession
from lelamp.memory import summary as memsummary
from lelamp.memory.writer import MemoryWriter


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "mem"))
    return MemoryWriter()


def _kill_pid_in_meta(meta_path):
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["pid"] = 2**31 - 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def _touch_newer(path):
    # Bump mtime by 2s to force events_mtime_ms > built_at_ms.
    future = time.time() + 2.0
    os.utime(path, (future, future))


class TestBackfill:
    def test_backfills_dead_agent_orphan(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        _kill_pid_in_meta(handle.meta_path)

        report = memselfcheck.run_selfcheck(writer)
        assert handle.session_id in report.summaries_backfilled
        assert memsummary.summary_path(writer, handle.session_id).exists()

    def test_skips_live_agent_session(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )

        report = memselfcheck.run_selfcheck(writer)
        assert handle.session_id not in report.summaries_backfilled
        assert not memsummary.summary_path(writer, handle.session_id).exists()

    def test_backfills_manual_orphan(self, writer):
        # Attach returns a manual handle when no live agent exists.
        handle = memsession.attach_or_create_session(
            writer, now=datetime(2026, 4, 17, 9, 32, 10, tzinfo=timezone.utc)
        )
        assert handle.is_manual
        writer.write_playback(
            session_id=handle.session_id,
            source="dashboard",
            action="play",
            initiator="dashboard",
            recording_name="curious",
            duration_ms=100,
            ok=True,
        )
        # Manual sessions have pid=null already, so no mutation needed.

        report = memselfcheck.run_selfcheck(writer)
        assert handle.session_id in report.summaries_backfilled
        summary = memsummary.load_summary(writer, handle.session_id)
        # Manual summary must match the contract shape.
        assert summary["fallback_rate"] is None
        assert summary["style_histogram"] == {}
        assert summary["top_recordings"] == ["curious"]
        assert summary["narrative"] is None

    def test_skips_when_summary_already_exists(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        memsummary.compute_and_write_summary(writer, handle.session_id)
        _kill_pid_in_meta(handle.meta_path)

        report = memselfcheck.run_selfcheck(writer)
        assert handle.session_id not in report.summaries_backfilled


class TestRecentIndex:
    def test_rebuilds_when_missing(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        _kill_pid_in_meta(handle.meta_path)

        report = memselfcheck.run_selfcheck(writer)
        assert report.recent_index_rebuilt is True
        assert memidx.recent_index_path(writer).exists()

    def test_rebuilds_when_events_newer(self, writer):
        # Seed: one finalized agent session.
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        _kill_pid_in_meta(handle.meta_path)
        memselfcheck.run_selfcheck(writer)  # builds index once

        # Now touch events.jsonl so its mtime jumps past built_at_ms.
        _touch_newer(writer.events_path)

        report = memselfcheck.run_selfcheck(writer)
        assert report.recent_index_rebuilt is True
        assert report.stale_reason is not None
        assert report.stale_reason.startswith("events_mtime_ms=")

    def test_noop_when_index_fresh(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        _kill_pid_in_meta(handle.meta_path)
        memselfcheck.run_selfcheck(writer)

        report = memselfcheck.run_selfcheck(writer)
        assert report.summaries_backfilled == []
        assert report.recent_index_rebuilt is False

    def test_corrupt_events_do_not_abort_selfcheck(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u2",
            assistant_text="a2",
            assistant_style="caring",
        )
        _kill_pid_in_meta(handle.meta_path)

        path = writer.events_path
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        lines[0] = "not valid json\n"
        path.write_text("".join(lines), encoding="utf-8")

        report = memselfcheck.run_selfcheck(writer)
        assert report.summaries_backfilled == []
        assert report.recent_index_rebuilt is False
        assert not memidx.recent_index_path(writer).exists()


class TestSessionHandleClose:
    def test_owner_close_writes_summary_and_index(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_conversation(
            session_id=handle.session_id,
            source="voice_agent",
            user_text="u",
            assistant_text="a",
            assistant_style="caring",
        )
        handle.close()

        assert handle.closed is True
        assert memsummary.summary_path(writer, handle.session_id).exists()
        assert memidx.recent_index_path(writer).exists()
        idx = json.loads(memidx.recent_index_path(writer).read_text(encoding="utf-8"))
        assert idx["sessions"][0]["session_id"] == handle.session_id

    def test_close_is_idempotent(self, writer):
        handle = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        handle.close()
        summary_path = memsummary.summary_path(writer, handle.session_id)
        first_mtime = summary_path.stat().st_mtime
        handle.close()  # no-op
        assert summary_path.stat().st_mtime == first_mtime

    def test_attached_handle_does_not_write_summary(self, writer):
        agent = memsession.start_agent_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        # Attaching dashboard gets the agent's session_id with
        # is_owner=False.
        attached = memsession.attach_or_create_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 5, tzinfo=timezone.utc)
        )
        assert attached.session_id == agent.session_id
        assert attached.is_owner is False

        attached.close()  # must NOT write summary: agent is still live.
        assert not memsummary.summary_path(writer, agent.session_id).exists()

    def test_manual_owner_close_writes_summary(self, writer):
        handle = memsession.attach_or_create_session(
            writer, now=datetime(2026, 4, 17, 9, 32, 10, tzinfo=timezone.utc)
        )
        assert handle.is_manual
        assert handle.is_owner
        writer.write_playback(
            session_id=handle.session_id,
            source="dashboard",
            action="play",
            initiator="dashboard",
            recording_name="curious",
            duration_ms=100,
            ok=True,
        )
        handle.close()

        summary = memsummary.load_summary(writer, handle.session_id)
        assert summary["narrative"] is None
        assert summary["fallback_rate"] is None
        # Manual sessions never land in recent_index.
        idx = json.loads(memidx.recent_index_path(writer).read_text(encoding="utf-8"))
        assert idx["sessions"] == []
