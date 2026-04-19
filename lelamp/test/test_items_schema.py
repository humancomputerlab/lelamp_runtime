from __future__ import annotations

import pytest

from lelamp.items.projections import (
    project_action_compile_result,
    project_action_program,
    project_body_state_snapshot,
)
from lelamp.items.schema import ITEM_KINDS, ITEM_SCHEMA, build_item, validate_item


def test_build_item_emits_envelope():
    item = build_item(
        kind="conversation.reply",
        producer="speaker_realtime",
        session_id="sess_2026-04-19_20-00-00",
        payload={"text": "灯灯在。"},
        ts_ms=1776500000000,
        item_id="itm_1",
    )

    assert item["schema"] == ITEM_SCHEMA
    assert item["kind"] == "conversation.reply"
    assert item["payload"]["text"] == "灯灯在。"
    validate_item(item)


def test_validate_item_rejects_unknown_kind():
    item = {
        "schema": ITEM_SCHEMA,
        "item_id": "itm_1",
        "ts_ms": 1776500000000,
        "session_id": "sess_2026-04-19_20-00-00",
        "kind": "unknown.kind",
        "producer": "speaker_realtime",
        "payload": {},
    }

    with pytest.raises(ValueError, match="unknown item kind"):
        validate_item(item)


def test_item_schema_includes_motion_v2_kinds() -> None:
    assert "body.state_snapshot" in ITEM_KINDS
    assert "action.program" in ITEM_KINDS
    assert "action.critique" in ITEM_KINDS
    assert "action.compile_result" in ITEM_KINDS


def test_project_action_program_builds_valid_item() -> None:
    item = project_action_program(
        session_id="sess_2026-04-19_20-00-00",
        summary="Lamp performs a proud upward look.",
        program={
            "version": "v2",
            "intent": "proud_look_up",
            "phases": [],
            "style": {
                "exaggeration": 0.5,
                "smoothness": 0.5,
                "tension": 0.5,
                "tempo": 1.0,
                "symmetry_break": 0.0,
            },
            "expression": {
                "attention": "up",
                "attitude": "confident",
                "emotion": "warm",
                "novelty": 0.4,
            },
            "settle_policy": {
                "return_to_home_bias": 0.5,
                "preserve_attention_heading": True,
            },
            "lighting": {
                "mode": "gradient",
                "palette": [[120, 180, 255], [255, 255, 255]],
            },
        },
        source_item_id="itm_user_1",
        fingerprint="fp_motion_1",
        ts_ms=1776500000000,
    )

    validate_item(item)
    assert item["kind"] == "action.program"
    assert item["payload"]["fingerprint"] == "fp_motion_1"


def test_project_body_state_snapshot_builds_valid_item() -> None:
    item = project_body_state_snapshot(
        session_id="sess_2026-04-19_20-00-00",
        snapshot={
            "pose_norm": {
                "base_yaw": 0.0,
                "base_pitch": 1.0,
                "elbow_pitch": 2.0,
                "wrist_roll": 3.0,
                "wrist_pitch": 4.0,
            },
            "slack": {"base_pitch_up": 20.0},
            "near_boundary": [],
            "recent_motion_energy": 0.1,
            "stability_mode": "normal",
        },
        ts_ms=1776500000000,
    )

    validate_item(item)
    assert item["kind"] == "body.state_snapshot"
    assert item["payload"]["stability_mode"] == "normal"


def test_project_action_compile_result_builds_valid_item() -> None:
    item = project_action_compile_result(
        session_id="sess_2026-04-19_20-00-00",
        action_item_id="itm_action_1",
        result={
            "decision": "exact",
            "generated_frames": 4,
            "adjustments": [],
            "preserved_intent": {
                "lead_joint": "base_pitch",
                "intent_retention": 1.0,
            },
            "keyframes": [],
        },
        ts_ms=1776500000100,
    )

    validate_item(item)
    assert item["kind"] == "action.compile_result"
    assert item["payload"]["action_item_id"] == "itm_action_1"
