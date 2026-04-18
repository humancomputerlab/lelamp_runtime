from __future__ import annotations

from types import SimpleNamespace

from lelamp.manager.glm_manager import GLMManager
from lelamp.manager.runtime import ManagerRuntime


def test_manager_runtime_writes_snapshot_from_items(tmp_path):
    class FakeManager:
        def process(self, *, items, previous_snapshot):
            assert previous_snapshot is None
            assert items == [{"kind": "conversation.user_turn", "payload": {"text": "你回来啦"}}]
            return {
                "profile_summary": "User keeps saying hi after silence.",
                "preference_hints": ["use greeting scenes"],
                "scene_priors": {"greeting": ["warm_gradient"]},
                "banned_patterns": [],
                "updated_at_ms": 1776500000000,
            }

    runtime = ManagerRuntime(
        manager=FakeManager(),
        item_store_path=tmp_path / "items.jsonl",
        derived_root=tmp_path / "memory",
    )

    runtime.process_once(
        session_id="sess_2026-04-19_20-00-00",
        items=[{"kind": "conversation.user_turn", "payload": {"text": "你回来啦"}}],
    )

    snapshot = runtime.load_snapshot()
    assert snapshot["scene_priors"]["greeting"] == ["warm_gradient"]


def test_glm_manager_uses_latest_user_turn_text():
    manager = GLMManager(settings=SimpleNamespace())

    snapshot = manager.process(
        items=[
            {"kind": "conversation.user_turn", "payload": {"text": "第一句"}},
            {"kind": "conversation.reply", "payload": {"text": "收到"}},
            {"kind": "conversation.user_turn", "payload": {"text": "你回来啦"}},
        ],
        previous_snapshot={"profile_summary": "old"},
    )

    assert snapshot["profile_summary"] == "Recent user theme: 你回来啦"
    assert snapshot["scene_priors"] == {}
