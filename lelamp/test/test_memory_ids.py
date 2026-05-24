"""Tests for ``lelamp.memory.ids``."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from lelamp.memory import ids as memids


def test_generate_event_id_is_32_hex():
    value = memids.generate_event_id()
    assert re.fullmatch(r"[0-9a-f]{32}", value)
    assert memids.validate_event_id(value)


def test_generate_invoke_id_has_inv_prefix():
    value = memids.generate_invoke_id()
    assert value.startswith("inv_")
    assert memids.validate_invoke_id(value)


def test_event_ids_are_unique_over_a_sample():
    sample = {memids.generate_event_id() for _ in range(1024)}
    assert len(sample) == 1024


def test_generate_session_id_agent_format():
    ts = datetime(2026, 4, 17, 9, 8, 7, tzinfo=timezone.utc)
    sid = memids.generate_session_id(manual=False, now=ts)
    assert sid.startswith("sess_")
    assert not sid.startswith("sess_manual_")
    assert memids.validate_session_id(sid)


def test_generate_session_id_manual_prefix():
    ts = datetime(2026, 4, 17, 9, 8, 7, tzinfo=timezone.utc)
    sid = memids.generate_session_id(manual=True, now=ts)
    assert sid.startswith("sess_manual_")
    assert memids.validate_session_id(sid)
    assert memids.is_manual_session(sid)


def test_is_manual_session_contract():
    assert memids.is_manual_session("sess_manual_2026-04-17_09-08-07")
    assert not memids.is_manual_session("sess_2026-04-17_09-08-07")
    assert not memids.is_manual_session("sess_2026-04-17_09-08-07-1")


def test_session_id_regex_accepts_collision_suffix():
    assert memids.validate_session_id("sess_2026-04-17_09-08-07")
    assert memids.validate_session_id("sess_2026-04-17_09-08-07-1")
    assert memids.validate_session_id("sess_manual_2026-04-17_09-08-07-42")


def test_session_id_regex_rejects_malformed():
    for bad in (
        "session_2026-04-17_09-08-07",  # wrong prefix
        "sess_20260417_090807",         # missing dashes
        "sess_2026-4-17_9-8-7",         # unpadded components
        "sess_manual",                  # no timestamp
        "",
    ):
        assert not memids.validate_session_id(bad), bad


def test_current_timestamp_ms_is_positive_utc():
    before = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    value = memids.current_timestamp_ms()
    after = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    assert before <= value <= after + 5
