from __future__ import annotations

from types import SimpleNamespace

from lelamp.manager.glm_manager import GLMManager
from lelamp.manager.runtime import ManagerRuntime
from lelamp.items.projections import project_conversation_user_turn


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


def test_glm_manager_builds_scene_proposal_for_dance_request():
    manager = GLMManager(settings=SimpleNamespace())

    snapshot = manager.process(
        items=[
            {
                "kind": "conversation.user_turn",
                "item_id": "itm_user_1",
                "payload": {"text": "来跳个舞给我看一下"},
            }
        ],
        previous_snapshot=None,
    )

    assert snapshot["_scene_proposal"]["summary"] == "User requested a playful dance response."
    assert snapshot["_scene_proposal"]["scene"]["body"][0] == {
        "type": "gesture",
        "name": "happy",
        "intensity": 0.8,
        "repeats": 2,
    }
    assert snapshot["scene_priors"]["playful"] == ["sparkle palette", "happy wiggle"]


def test_glm_manager_builds_action_program_for_upward_look_request():
    manager = GLMManager(settings=SimpleNamespace())

    snapshot = manager.process(
        items=[
            {
                "kind": "conversation.user_turn",
                "item_id": "itm_user_1",
                "payload": {"text": "抬头看看我"},
            }
        ],
        previous_snapshot=None,
    )

    assert (
        snapshot["_action_program"]["summary"]
        == "User requested a confident upward attention shift."
    )
    assert snapshot["_action_program"]["program"]["intent"] == "proud_look_up"
    assert (
        snapshot["_action_program"]["program"]["phases"][0]["joints"]["base_pitch"][
            "role"
        ]
        == "lead"
    )


def test_glm_manager_recognizes_colloquial_upward_look_request():
    manager = GLMManager(settings=SimpleNamespace())

    snapshot = manager.process(
        items=[
            {
                "kind": "conversation.user_turn",
                "item_id": "itm_user_1",
                "payload": {"text": "抬个头，不要保持两秒钟可以吗？"},
            }
        ],
        previous_snapshot=None,
    )

    assert snapshot["_action_program"]["program"]["intent"] == "proud_look_up"


def test_glm_manager_builds_scene_proposal_for_generic_motion_demo_request():
    manager = GLMManager(settings=SimpleNamespace())

    snapshot = manager.process(
        items=[
            {
                "kind": "conversation.user_turn",
                "item_id": "itm_user_1",
                "payload": {"text": "随便来一个，很狂野的动作。"},
            }
        ],
        previous_snapshot=None,
    )

    assert snapshot["_scene_proposal"]["summary"] == "User requested a playful dance response."
    assert snapshot["_scene_proposal"]["scene"]["body"][0]["type"] == "gesture"


def test_manager_runtime_emits_scene_and_action_items_for_manager_proposal(tmp_path):
    session_id = "sess_2026-04-19_20-00-00"
    user_turn = project_conversation_user_turn(
        session_id=session_id,
        text="来跳个舞给我看一下",
        ts_ms=1776500000000,
    )

    class FakeManager:
        def process(self, *, items, previous_snapshot):
            assert previous_snapshot is None
            assert items == [user_turn]
            return {
                "profile_summary": "Recent user theme: 来跳个舞给我看一下",
                "preference_hints": [],
                "scene_priors": {"playful": ["sparkle palette", "happy wiggle"]},
                "banned_patterns": ["repeat same scene twice in a row"],
                "updated_at_ms": 1776500000100,
                "_scene_proposal": {
                    "summary": "User requested a playful dance response.",
                    "scene": {
                        "body": [{"type": "gesture", "name": "happy", "intensity": 0.8, "repeats": 2}],
                        "light": [{"type": "sparkle", "palette": [[255, 120, 40], [255, 220, 90], [70, 255, 120]]}],
                    },
                },
            }

    runtime = ManagerRuntime(
        manager=FakeManager(),
        item_store_path=tmp_path / "items.jsonl",
        derived_root=tmp_path / "memory",
    )

    runtime.process_once(session_id=session_id, items=[user_turn])

    emitted = list(runtime._item_store.iter_session_items(session_id))
    assert [item["kind"] for item in emitted] == ["scene.proposal", "action.plan"]
    assert emitted[0]["payload"]["source_item_id"] == user_turn["item_id"]
    assert emitted[1]["payload"]["scene_item_id"] == emitted[0]["item_id"]


def test_manager_runtime_skips_duplicate_action_plan_for_same_user_turn(tmp_path):
    session_id = "sess_2026-04-19_20-00-00"
    user_turn = project_conversation_user_turn(
        session_id=session_id,
        text="抬头看一下",
        ts_ms=1776500000000,
    )

    class FakeManager:
        def process(self, *, items, previous_snapshot):
            return {
                "profile_summary": "Recent user theme: 抬头看一下",
                "preference_hints": [],
                "scene_priors": {"look_up": ["cool gradient", "upward sweep"]},
                "banned_patterns": ["repeat same scene twice in a row"],
                "updated_at_ms": 1776500000100,
                "_scene_proposal": {
                    "summary": "User requested an upward look response.",
                    "scene": {
                        "body": [{"type": "look", "direction": "up"}],
                        "light": [{"type": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]}],
                    },
                },
            }

    runtime = ManagerRuntime(
        manager=FakeManager(),
        item_store_path=tmp_path / "items.jsonl",
        derived_root=tmp_path / "memory",
    )

    runtime.process_once(session_id=session_id, items=[user_turn])
    runtime.process_once(session_id=session_id, items=[user_turn])

    emitted = list(runtime._item_store.iter_session_items(session_id))
    assert [item["kind"] for item in emitted] == ["scene.proposal", "action.plan"]


def test_manager_runtime_emits_action_program_item_for_manager_output(tmp_path):
    session_id = "sess_2026-04-19_20-00-00"
    user_turn = project_conversation_user_turn(
        session_id=session_id,
        text="抬头看看我",
        ts_ms=1776500000000,
    )

    class FakeManager:
        def process(self, *, items, previous_snapshot):
            return {
                "profile_summary": "Recent user theme: 抬头看看我",
                "preference_hints": [],
                "scene_priors": {"look_up": ["cool gradient"]},
                "banned_patterns": [],
                "updated_at_ms": 1776500000100,
                "_action_program": {
                    "summary": "User requested a confident upward attention shift.",
                    "program": {
                        "version": "v2",
                        "intent": "proud_look_up",
                        "why": "User asked lamp to look up.",
                        "expression": {
                            "attention": "up",
                            "attitude": "confident",
                            "emotion": "warm",
                            "novelty": 0.6,
                        },
                        "style": {
                            "exaggeration": 0.7,
                            "smoothness": 0.4,
                            "tension": 0.5,
                            "tempo": 1.0,
                            "symmetry_break": 0.2,
                        },
                        "settle_policy": {
                            "return_to_home_bias": 0.5,
                            "preserve_attention_heading": True,
                        },
                        "phases": [
                            {
                                "name": "accent",
                                "duration_ms": 220,
                                "easing": "ease_in_out",
                                "joints": {
                                    "base_pitch": {"target": 0.7, "role": "lead"}
                                },
                            }
                        ],
                        "lighting": {
                            "mode": "gradient",
                            "palette": [[120, 180, 255], [255, 255, 255]],
                        },
                    },
                    "source_item_id": user_turn["item_id"],
                },
            }

    runtime = ManagerRuntime(
        manager=FakeManager(),
        item_store_path=tmp_path / "items.jsonl",
        derived_root=tmp_path / "memory",
    )
    runtime.process_once(session_id=session_id, items=[user_turn])

    emitted = list(runtime._item_store.iter_session_items(session_id))
    assert [item["kind"] for item in emitted] == ["action.program"]
    assert emitted[0]["payload"]["program"]["intent"] == "proud_look_up"
