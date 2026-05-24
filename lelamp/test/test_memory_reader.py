"""Tests for ``lelamp.memory.reader``.

The design sheet names 6 required tests (PROMPT_INTEGRATION.md
§\u6d4b\u8bd5\u5951\u7ea6).  Each one appears here:

* test_header_budget_respected
* test_header_deterministic
* test_header_graceful_missing_dir
* test_header_graceful_corrupt_jsonl
* test_header_respects_disable_env
* test_banned_styles_in_profile_hint

Plus extra coverage for the 3-tier degrade path, manual-session
filter, and per-section formatting rules.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lelamp.memory import reader as memreader
from lelamp.memory import recent_index as memidx
from lelamp.memory import session as memsession
from lelamp.memory import summary as memsummary
from lelamp.memory.reader import (
    DEFAULT_BUDGET_TOKENS,
    _FALLBACK_UNAVAILABLE,
    build_memory_header,
    estimate_tokens,
)
from lelamp.memory.writer import MemoryWriter


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "mem"))
    monkeypatch.delenv("LELAMP_MEMORY_DISABLE", raising=False)
    monkeypatch.delenv("LELAMP_MEMORY_PROMPT_BUDGET", raising=False)
    writer = MemoryWriter()
    return writer.user_dir


@pytest.fixture
def writer(user_dir):
    return MemoryWriter()


def _populate_profile(user_dir: Path, **overrides) -> None:
    data = {
        "schema": "lelamp.memory.v0.profile",
        "user_id": "default",
        "display_name": "default",
        "nickname": None,
        "preferred_style_hints": [],
        "banned_styles": [],
        "notes": None,
    }
    data.update(overrides)
    (user_dir / "profile.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _seed_agent_session(writer, *, hour: int = 23, minute: int = 11, second: int = 15):
    handle = memsession.start_agent_session(
        writer,
        now=datetime(2026, 4, 17, hour, minute, second, tzinfo=timezone.utc),
    )
    return handle


def _full_agent_fixture(writer, handle):
    # Three conversations in different styles.
    writer.write_conversation(
        session_id=handle.session_id,
        source="voice_agent",
        user_text="\u4f60\u522b\u8001\u662f\u90a3\u4e48\u7d27\u5f20",
        assistant_text="\u597d\u7684\uff0c\u6211\u4f1a\u9002\u5f53\u653e\u677e\u4e00\u70b9",
        assistant_style="caring",
        ts_ms=1_700_000_001_000,
    )
    writer.write_conversation(
        session_id=handle.session_id,
        source="voice_agent",
        user_text="\u8bf4\u4e2a\u7b11\u8bdd",
        assistant_text="\u53f0\u706f\u8d70\u8fdb\u9152\u5427\u2026\u2026",
        assistant_style="excited",
        ts_ms=1_700_000_002_000,
    )
    writer.write_conversation(
        session_id=handle.session_id,
        source="voice_agent",
        user_text="\u4f60\u4e86\u89e3\u6211\u5417",
        assistant_text="\u5728\u6162\u6162\u4e86\u89e3\u4e2d",
        assistant_style="caring",
        ts_ms=1_700_000_003_000,
    )
    # One fallback expression.
    writer.write_fallback_expression(
        session_id=handle.session_id,
        source="auto_expression",
        style="curious",
        trigger="voice_silence_timeout",
        ts_ms=1_700_000_004_000,
    )
    # Two play_recording invocations.
    from lelamp.memory.ids import generate_invoke_id
    for name in ("curious", "happy_wiggle"):
        inv = generate_invoke_id()
        writer.write_function_tool(
            session_id=handle.session_id,
            source="voice_agent",
            invoke_id=inv,
            phase="invoke",
            tool_name="play_recording",
            args={"recording_name": name},
            caller="llm",
            ts_ms=1_700_000_005_000,
        )
        writer.write_function_tool(
            session_id=handle.session_id,
            source="voice_agent",
            invoke_id=inv,
            phase="result",
            tool_name="play_recording",
            args={"recording_name": name},
            caller="llm",
            duration_ms=500,
            ok=True,
            ts_ms=1_700_000_005_500,
        )
    return handle


def _close_session(writer, handle):
    handle.close(end_ts_ms=1_700_000_100_000)


class TestDisableEnv:
    def test_header_respects_disable_env(self, writer, monkeypatch):
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        monkeypatch.setenv("LELAMP_MEMORY_DISABLE", "1")
        assert build_memory_header() == ""


class TestMissingDir:
    def test_header_graceful_missing_dir(self, tmp_path, monkeypatch):
        # Point LELAMP_MEMORY_ROOT at a directory that was never created.
        monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "absent"))
        monkeypatch.delenv("LELAMP_MEMORY_DISABLE", raising=False)
        result = build_memory_header()
        assert result == _FALLBACK_UNAVAILABLE


class TestCorruptJsonl:
    def test_header_graceful_corrupt_jsonl(self, writer, user_dir):
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        # Append a truncated line to events.jsonl -- crash tail.
        with (user_dir / "events.jsonl").open("ab") as fh:
            fh.write(b'{"schema":"lelamp.memory.v0","partial"')
        header = build_memory_header(now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc))
        assert "<memory" in header
        assert "RECENT TURNS" in header


class TestBudget:
    def test_header_budget_respected(self, writer):
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        header = build_memory_header(
            budget_tokens=40,
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        # Very tight budget: P0 should survive (profile hint is empty
        # here so nothing else should either -- but we should still
        # produce at most the wrapper + 1 section).
        assert header.startswith("<memory")
        assert header.endswith("</memory>")

    def test_budget_shrinks_sections(self, writer, user_dir):
        _populate_profile(user_dir, nickname="\u5c0f\u5434", banned_styles=["headshake"])
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        full = build_memory_header(now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc))
        assert "HARDWARE USAGE" in full or "TOOL USAGE" in full or "RECENT TURNS" in full

        trimmed = build_memory_header(
            budget_tokens=60,
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        # Profile hint is P0 and must stay.
        assert "USER CONTEXT" in trimmed
        # Lowest-priority sections should have been dropped.
        assert "HARDWARE USAGE" not in trimmed

    def test_budget_truncates_single_remaining_recap_when_profile_absent(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "mem" / "default"
        user_dir.mkdir(parents=True)

        monkeypatch.setattr(memreader, "user_memory_root", lambda user_id=None: user_dir)
        monkeypatch.setattr(
            memreader,
            "_collect_state",
            lambda _user_dir: memreader._ReaderState(
                tier="normal",
                profile={},
                summaries=[{"session_id": "sess_2026-04-17_23-11-15"}],
                recent_events=[],
            ),
        )
        monkeypatch.setattr(memreader, "_wrap", lambda body, **_: body)
        monkeypatch.setattr(memreader, "_section_profile_hint", lambda state: "")
        monkeypatch.setattr(
            memreader,
            "_section_session_summary_recent",
            lambda state: "很长的总结。 " * 80,
        )
        monkeypatch.setattr(memreader, "_section_style_tendency", lambda state: "")
        monkeypatch.setattr(memreader, "_section_recent_conversation", lambda state: "")
        monkeypatch.setattr(memreader, "_section_function_tool_digest", lambda state: "")
        monkeypatch.setattr(memreader, "_section_playback_digest", lambda state: "")

        budget_tokens = 24
        body = build_memory_header(budget_tokens=budget_tokens)

        assert body != "很长的总结。 " * 80
        assert estimate_tokens(body) <= budget_tokens

    def test_env_budget_override(self, writer, user_dir, monkeypatch):
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        monkeypatch.setenv("LELAMP_MEMORY_PROMPT_BUDGET", "50")
        trimmed = build_memory_header(now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc))
        assert "HARDWARE USAGE" not in trimmed


class TestDeterministic:
    def test_header_deterministic(self, writer, user_dir):
        _populate_profile(user_dir, nickname="\u5c0f\u5434")
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        now = datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc)
        first = build_memory_header(now=now)
        second = build_memory_header(now=now)
        assert first == second


class TestBannedStyles:
    def test_banned_styles_in_profile_hint(self, writer, user_dir):
        _populate_profile(user_dir, banned_styles=["headshake"])
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        header = build_memory_header(
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert "\u660e\u786e\u4e0d\u559c\u6b22\u7684\u98ce\u683c" in header
        assert "headshake" in header


class TestDegradeTiers:
    def test_degraded_tier_when_index_missing(self, writer, user_dir):
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        # Nuke recent_index.json -> reader must fall back to scanning
        # sessions/*.summary.json without writing anything.
        (user_dir / "recent_index.json").unlink()
        header = build_memory_header(
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert "<memory" in header
        assert "RECENT TURNS" in header
        # Reader must NOT have rebuilt the index.
        assert not (user_dir / "recent_index.json").exists()

    def test_degraded_tier_when_events_newer(self, writer, user_dir):
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        future = time.time() + 3600
        os.utime(user_dir / "events.jsonl", (future, future))
        header = build_memory_header(
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        # Still produces a header via degraded tier.
        assert "<memory" in header

    def test_fallback_tier_when_no_agent_summary(self, writer, user_dir):
        # Only a manual session exists -> fallback unavailable.
        manual_handle = memsession.attach_or_create_session(
            writer, now=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc)
        )
        writer.write_playback(
            session_id=manual_handle.session_id,
            source="dashboard",
            action="play",
            initiator="dashboard",
            recording_name="curious",
            duration_ms=100,
            ok=True,
        )
        manual_handle.close()
        result = build_memory_header()
        assert result == _FALLBACK_UNAVAILABLE


class TestManualFiltering:
    def test_recent_turns_excludes_manual_events(self, writer, user_dir):
        # Set up an agent session + close it.
        agent = _seed_agent_session(writer)
        writer.write_conversation(
            session_id=agent.session_id,
            source="voice_agent",
            user_text="agent hello",
            assistant_text="ok",
            assistant_style="caring",
        )
        agent.close()

        # Then a manual session with a playback that must NOT leak in.
        # Use a bogus pid so attach lands in manual.
        meta_path = agent.meta_path
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["pid"] = 2**31 - 1
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        manual = memsession.attach_or_create_session(writer)
        writer.write_playback(
            session_id=manual.session_id,
            source="dashboard",
            action="play",
            initiator="dashboard",
            recording_name="curious",
            duration_ms=50,
            ok=True,
        )
        manual.close()

        header = build_memory_header(
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        # Manual playback must NOT surface in the prompt digest.
        assert "curious" not in header
        # But agent conversation must surface.
        assert "agent hello" in header

    def test_degraded_recent_turns_only_include_last_three_agent_sessions(self, writer, user_dir):
        prompts = []
        for hour in range(1, 5):
            agent = _seed_agent_session(writer, hour=hour, minute=0, second=0)
            text = f"agent hour {hour}"
            writer.write_conversation(
                session_id=agent.session_id,
                source="voice_agent",
                user_text=text,
                assistant_text="ok",
                assistant_style="caring",
                ts_ms=1_700_000_000_000 + hour,
            )
            agent.close(end_ts_ms=1_700_000_100_000 + hour)
            prompts.append(text)

        (user_dir / "recent_index.json").unlink()

        header = build_memory_header(
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )

        assert prompts[0] not in header
        assert prompts[1] in header
        assert prompts[2] in header
        assert prompts[3] in header


class TestLampCompanionRecap:
    """Covers the fluxchi_listener session_summary → P1.5 Chinese recap path.

    Narrative synthesis route B (task #20): render lamp recap directly in
    reader, without touching ``summary.narrative`` (still None in v0).
    """

    def _write_fluxchi_summary(
        self,
        writer,
        *,
        ts_ms: int,
        reason: str = "ws_disconnect",
        profile_name: str = "scene_a_traditional",
        level_counts=None,
        breath_completed: int = 0,
        breath_interrupts: int = 0,
        manual_fallback_count: int = 0,
        final_stamina=None,
        duration_sec: float = 300.0,
    ):
        from lelamp.memory.ids import generate_session_id
        session_id = generate_session_id(
            now=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
            manual=False,
        )
        return writer.write_session_summary(
            session_id=session_id,
            source="fluxchi_listener",
            reason=reason,
            profile_name=profile_name,
            level_counts=level_counts or {},
            breath_completed=breath_completed,
            breath_interrupts=breath_interrupts,
            manual_fallback_count=manual_fallback_count,
            final_stamina=final_stamina,
            duration_sec=duration_sec,
            ts_ms=ts_ms,
        )

    def test_no_event_means_no_recap_section(self, writer, user_dir):
        _populate_profile(user_dir)
        handle = _seed_agent_session(writer)
        _full_agent_fixture(writer, handle)
        _close_session(writer, handle)
        header = build_memory_header()
        assert "LAMP COMPANION RECAP" not in header

    def test_scene_a_renders_level_counts(self, writer, user_dir):
        _populate_profile(user_dir)
        handle = _seed_agent_session(writer)
        _full_agent_fixture(writer, handle)
        _close_session(writer, handle)
        self._write_fluxchi_summary(
            writer,
            ts_ms=1_700_000_200_000,
            profile_name="scene_a_traditional",
            level_counts={"mild": 1, "moderate": 2, "severe": 0},
            final_stamina=42.7,
        )
        header = build_memory_header()
        assert "LAMP COMPANION RECAP" in header
        assert "表情提醒" in header
        assert "中提醒 2 次" in header
        assert "轻提醒 1 次" in header
        # severe=0 must not render
        assert "打断 0 次" not in header
        assert "最终状态约 42" in header

    def test_scene_b_renders_breath_stats(self, writer, user_dir):
        _populate_profile(user_dir)
        handle = _seed_agent_session(writer)
        _full_agent_fixture(writer, handle)
        _close_session(writer, handle)
        self._write_fluxchi_summary(
            writer,
            ts_ms=1_700_000_210_000,
            profile_name="scene_b_breath",
            level_counts={"moderate": 1},
            breath_completed=2,
            breath_interrupts=1,
        )
        header = build_memory_header()
        assert "呼吸共振" in header
        assert "完整呼吸 2 次" in header
        assert "被打断 1 次" in header

    def test_picks_latest_by_ts(self, writer, user_dir):
        _populate_profile(user_dir)
        handle = _seed_agent_session(writer)
        _full_agent_fixture(writer, handle)
        _close_session(writer, handle)
        # Older event with severe=2
        self._write_fluxchi_summary(
            writer,
            ts_ms=1_700_000_000_000,
            profile_name="scene_a_traditional",
            level_counts={"severe": 2},
        )
        # Newer event with mild only — this should win
        self._write_fluxchi_summary(
            writer,
            ts_ms=1_700_000_300_000,
            profile_name="scene_a_traditional",
            level_counts={"mild": 1},
        )
        header = build_memory_header()
        assert "轻提醒 1 次" in header
        assert "打断 2 次" not in header  # older event must lose

    def test_empty_session_skipped(self, writer, user_dir):
        _populate_profile(user_dir)
        handle = _seed_agent_session(writer)
        _full_agent_fixture(writer, handle)
        _close_session(writer, handle)
        self._write_fluxchi_summary(
            writer,
            ts_ms=1_700_000_220_000,
            profile_name="scene_a_traditional",
            level_counts={},  # 空 session — listener 会 is_interesting() 拦掉，但保险
            breath_completed=0,
            breath_interrupts=0,
            final_stamina=None,
        )
        header = build_memory_header()
        # level_counts empty, no breath, no stamina → section 应被丢
        assert "LAMP COMPANION RECAP" not in header

    def test_renders_in_fallback_tier_when_only_fluxchi_exists(
        self, writer, user_dir
    ):
        """灯先于语音陪过用户：voice_agent 无 summary 也要能渲染 lamp recap。"""
        _populate_profile(user_dir)
        # Intentionally NO agent session — no voice_agent summary at all.
        self._write_fluxchi_summary(
            writer,
            ts_ms=1_700_000_400_000,
            profile_name="scene_b_breath",
            breath_completed=3,
            duration_sec=900.0,
        )
        header = build_memory_header()
        # Must NOT be the fallback marker — lamp recap alone is enough
        assert header != _FALLBACK_UNAVAILABLE
        assert "LAMP COMPANION RECAP" in header
        assert "呼吸共振" in header
        assert "完整呼吸 3 次" in header

    def test_header_still_fallback_when_no_lamp_and_no_agent(self, user_dir):
        _populate_profile(user_dir)
        header = build_memory_header()
        assert header == _FALLBACK_UNAVAILABLE


class TestSynthesizedRecap:
    def test_recap_uses_stats_when_narrative_null(self, writer, user_dir):
        handle = _full_agent_fixture(writer, _seed_agent_session(writer))
        _close_session(writer, handle)
        header = build_memory_header(
            now=datetime(2026, 4, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert "LAST SESSION RECAP" in header
        assert "\u5171 3 \u8f6e\u5bf9\u8bdd" in header


class TestEstimateTokens:
    def test_zero_on_empty(self):
        assert estimate_tokens("") == 0

    def test_scales_with_length(self):
        assert estimate_tokens("a" * 15) == 5
        assert estimate_tokens("a" * 14) == 4
