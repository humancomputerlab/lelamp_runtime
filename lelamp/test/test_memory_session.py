"""Tests for ``lelamp.memory.session``.

These tests lock down the two-phase meta.json contract and the
attach_or_create_session scan + flock discipline.  They avoid reaching
for real live pids by stubbing ``os.kill`` via ``monkeypatch``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

import pytest

from lelamp.memory import session as memsession
from lelamp.memory.session import (
    META_SCHEMA,
    STANDALONE_SOURCE,
    SessionError,
    attach_or_create_session,
    start_agent_session,
)
from lelamp.memory.writer import MemoryWriter


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "mem"))
    return MemoryWriter()


def _fixed_ts(hour: int = 9, minute: int = 32, second: int = 10) -> datetime:
    return datetime(2026, 4, 17, hour, minute, second, tzinfo=timezone.utc)


class TestStartAgentSession:
    def test_phase1_meta_skeleton(self, writer):
        handle = start_agent_session(writer, now=_fixed_ts(), model_providers=["qwen"])
        assert handle.session_id.startswith("sess_")
        assert not handle.is_manual

        meta = json.loads(handle.meta_path.read_text(encoding="utf-8"))
        assert meta["schema"] == META_SCHEMA
        assert meta["user_id"] == "default"
        assert meta["pid"] == os.getpid()
        assert meta["model_providers"] == ["qwen"]
        assert meta["flags"]["motor_bus_enabled"] is None
        assert meta["flags"]["fluxchi_enabled"] is False
        # Agent session must NOT carry the standalone marker.
        assert "source" not in meta["flags"]

    def test_phase2_patches_motor_bus_true(self, writer):
        handle = start_agent_session(writer, now=_fixed_ts())
        handle.set_motor_bus_enabled(True)
        meta = json.loads(handle.meta_path.read_text(encoding="utf-8"))
        assert meta["flags"]["motor_bus_enabled"] is True
        # Other fields must survive the patch.
        assert meta["session_id"] == handle.session_id
        assert meta["pid"] == os.getpid()

    def test_phase2_patches_motor_bus_false(self, writer):
        handle = start_agent_session(writer, now=_fixed_ts())
        handle.set_motor_bus_enabled(False)
        meta = json.loads(handle.meta_path.read_text(encoding="utf-8"))
        assert meta["flags"]["motor_bus_enabled"] is False

    def test_phase2_can_stay_null_if_skipped(self, writer):
        # If the process is SIGKILLed between phase 1 and phase 2, the
        # flag stays null -- selfcheck must NOT rewrite it.
        handle = start_agent_session(writer, now=_fixed_ts())
        meta = json.loads(handle.meta_path.read_text(encoding="utf-8"))
        assert meta["flags"]["motor_bus_enabled"] is None

    def test_phase1_resolves_git_ref_from_repo_root_not_memory_dir(self, writer, monkeypatch):
        seen = {}

        def fake_git_ref(cwd=None):
            seen["cwd"] = cwd
            return "abc1234"

        monkeypatch.setattr(memsession, "_git_ref", fake_git_ref)

        handle = start_agent_session(writer, now=_fixed_ts())
        meta = json.loads(handle.meta_path.read_text(encoding="utf-8"))

        assert seen["cwd"] == Path(__file__).resolve().parents[2]
        assert meta["git_ref"] == "abc1234"

    def test_phase1_resolves_git_ref_before_taking_global_lock(self, writer, monkeypatch):
        events = []

        @contextmanager
        def fake_flock(_path):
            events.append("lock_enter")
            try:
                yield
            finally:
                events.append("lock_exit")

        def fake_git_ref(cwd=None):
            events.append("git_ref")
            return "abc1234"

        monkeypatch.setattr(memsession, "_flock", fake_flock)
        monkeypatch.setattr(memsession, "_git_ref", fake_git_ref)

        start_agent_session(writer, now=_fixed_ts())

        assert events[:3] == ["git_ref", "lock_enter", "lock_exit"]

    def test_collision_suffix_appended(self, writer):
        ts = _fixed_ts()
        h1 = start_agent_session(writer, now=ts)
        h2 = start_agent_session(writer, now=ts)
        assert h1.session_id != h2.session_id
        assert h2.session_id.endswith("-1")


class TestAttachOrCreateSession:
    def test_creates_manual_when_no_agent(self, writer):
        handle = attach_or_create_session(writer, now=_fixed_ts())
        assert handle.is_manual
        assert handle.session_id.startswith("sess_manual_")

        meta = json.loads(handle.meta_path.read_text(encoding="utf-8"))
        assert meta["pid"] is None
        assert meta["flags"]["source"] == STANDALONE_SOURCE
        # Manual sessions must stay null forever.
        assert meta["flags"]["motor_bus_enabled"] is None

    def test_manual_set_motor_bus_raises(self, writer):
        handle = attach_or_create_session(writer, now=_fixed_ts())
        with pytest.raises(SessionError, match="manual sessions"):
            handle.set_motor_bus_enabled(True)

    def test_attaches_to_live_agent(self, writer, monkeypatch):
        agent = start_agent_session(writer, now=_fixed_ts())
        # os.kill(pid, 0) for this pytest process always succeeds, so
        # attach_or_create should find the running agent.
        handle = attach_or_create_session(writer, now=_fixed_ts(second=20))
        assert not handle.is_manual
        assert handle.session_id == agent.session_id
        # No extra meta file should be created.
        sessions_dir = writer.user_dir / "sessions"
        metas = list(sessions_dir.glob("*.meta.json"))
        assert len(metas) == 1

    def test_creates_manual_when_agent_pid_is_dead(self, writer, monkeypatch):
        # Forge an agent meta with a pid known to be dead (pid=1 in a
        # container is likely alive; we pick a deliberately bogus high
        # pid and rewrite the meta).
        agent = start_agent_session(writer, now=_fixed_ts())
        meta_path = agent.meta_path
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # Pick a pid that cannot plausibly exist; 2^31-1 beats the
        # default pid_max on both Linux and macOS.
        meta["pid"] = 2**31 - 1
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        handle = attach_or_create_session(writer, now=_fixed_ts(second=20))
        assert handle.is_manual
        assert handle.session_id.startswith("sess_manual_")

    def test_skips_manual_when_scanning(self, writer):
        # First standalone create -> manual session.
        first = attach_or_create_session(writer, now=_fixed_ts())
        assert first.is_manual
        # Second call must NOT attach to the previous manual
        # (LIFECYCLE scenario C: no manual-to-manual attach).
        second = attach_or_create_session(writer, now=_fixed_ts(second=11))
        assert second.is_manual
        assert second.session_id != first.session_id


class TestPidAlive:
    def test_current_process_alive(self):
        assert memsession._pid_alive(os.getpid()) is True

    def test_bogus_pid_dead(self):
        assert memsession._pid_alive(2**31 - 1) is False

    def test_non_positive_pid_dead(self):
        assert memsession._pid_alive(0) is False
        assert memsession._pid_alive(-5) is False


class TestAtomicWrite:
    def test_leaves_no_tmp_on_success(self, writer):
        handle = start_agent_session(writer, now=_fixed_ts())
        # The *.tmp sibling must not be left behind.
        sessions_dir = writer.user_dir / "sessions"
        tmps = list(sessions_dir.glob("*.tmp"))
        assert tmps == []
        assert handle.meta_path.exists()


class TestLoadMeta:
    def test_roundtrip(self, writer):
        handle = start_agent_session(writer, now=_fixed_ts(), model_providers=["glm"])
        handle.set_motor_bus_enabled(False)
        loaded = memsession.load_meta(writer, handle.session_id)
        assert loaded["flags"]["motor_bus_enabled"] is False
        assert loaded["model_providers"] == ["glm"]
