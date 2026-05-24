"""Tests for ``lelamp.memory.root``.

The STORAGE.md contract is tiny but load-bearing -- a regression here
silently moves every downstream artefact, so we lock it down here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lelamp.memory import root as memroot


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LELAMP_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


def test_memory_root_defaults_under_home():
    expected = Path(os.environ["HOME"]) / ".lelamp" / "memory"
    assert memroot.memory_root() == expected


def test_memory_root_respects_env_override(monkeypatch, tmp_path):
    override = tmp_path / "alt_memory"
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(override))
    assert memroot.memory_root() == override


def test_resolve_user_id_is_pinned_to_default():
    # v0 contract: any incoming user_id collapses to DEFAULT_USER_ID so
    # premature plumbing of per-user ids never silently fragments the
    # single-user store.
    for candidate in (None, "", "alice", "bob", "default"):
        assert memroot.resolve_user_id(candidate) == memroot.DEFAULT_USER_ID


def test_user_memory_root_composes_without_touching_fs(tmp_path):
    path = memroot.user_memory_root("ignored")
    assert path == memroot.memory_root() / memroot.DEFAULT_USER_ID
    assert not path.exists()


def test_ensure_user_memory_root_creates_tree_mode_0700(tmp_path, monkeypatch):
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "mem"))

    user_dir = memroot.ensure_user_memory_root()

    assert user_dir.is_dir()
    assert (user_dir / "sessions").is_dir()
    assert (user_dir / "archive").is_dir()

    # 0o700 applies to all three directories; mask off the type bits.
    for node in (user_dir, user_dir / "sessions", user_dir / "archive"):
        mode = node.stat().st_mode & 0o777
        assert mode == 0o700, f"{node} has mode {oct(mode)}"


def test_ensure_user_memory_root_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("LELAMP_MEMORY_ROOT", str(tmp_path / "mem"))

    first = memroot.ensure_user_memory_root()
    (first / "sessions" / "marker.txt").write_text("keep me")
    second = memroot.ensure_user_memory_root()

    assert first == second
    assert (second / "sessions" / "marker.txt").read_text() == "keep me"
