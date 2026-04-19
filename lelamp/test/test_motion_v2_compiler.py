from __future__ import annotations

from lelamp.motion_v2.compiler import compile_action_program
from lelamp.motion_v2.robot_profile import load_default_robot_profile


def test_compile_action_program_preserves_lead_joint_and_trims_accent() -> None:
    profile = load_default_robot_profile()
    compiled = compile_action_program(
        program={
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
                "exaggeration": 0.8,
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
                    "duration_ms": 200,
                    "easing": "ease_in_out",
                    "joints": {
                        "base_pitch": {"target": 0.9, "role": "lead"},
                        "wrist_roll": {"target": 0.03, "role": "accent"},
                    },
                }
            ],
            "lighting": {
                "mode": "gradient",
                "palette": [[120, 180, 255], [255, 255, 255]],
            },
        },
        critique={
            "decision": "revise",
            "patch": {
                "phase_adjustments": [
                    {
                        "name": "accent",
                        "joint_overrides": {"wrist_roll": 0.0},
                    }
                ]
            },
        },
        snapshot={
            "pose_norm": {
                "base_yaw": 0.0,
                "base_pitch": 35.0,
                "elbow_pitch": 33.0,
                "wrist_roll": 90.0,
                "wrist_pitch": 70.0,
            },
            "slack": {"base_pitch_up": 30.0, "base_pitch_down": 55.0},
            "near_boundary": [],
            "recent_motion_energy": 0.1,
            "stability_mode": "normal",
        },
        profile=profile,
    )

    assert compiled["decision"] in {"exact", "clipped"}
    assert compiled["preserved_intent"]["lead_joint"] == "base_pitch"
    assert compiled["generated_frames"] > 0


def test_compile_action_program_rejects_motion_that_exceeds_hard_window() -> None:
    profile = load_default_robot_profile()
    compiled = compile_action_program(
        program={
            "version": "v2",
            "intent": "unsafe_pitch",
            "why": "unsafe",
            "expression": {
                "attention": "up",
                "attitude": "confident",
                "emotion": "warm",
                "novelty": 0.6,
            },
            "style": {
                "exaggeration": 1.0,
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
                    "duration_ms": 200,
                    "easing": "ease_in_out",
                    "joints": {"base_pitch": {"target": 1.0, "role": "lead"}},
                }
            ],
            "lighting": {
                "mode": "gradient",
                "palette": [[120, 180, 255], [255, 255, 255]],
            },
        },
        critique={"decision": "accept", "patch": {"phase_adjustments": []}},
        snapshot={
            "pose_norm": {
                "base_yaw": 0.0,
                "base_pitch": 99.0,
                "elbow_pitch": 33.0,
                "wrist_roll": 90.0,
                "wrist_pitch": 70.0,
            },
            "slack": {"base_pitch_up": 1.0, "base_pitch_down": 119.0},
            "near_boundary": ["base_pitch"],
            "recent_motion_energy": 0.1,
            "stability_mode": "guarded",
        },
        profile=profile,
    )

    assert compiled["decision"] == "rejected"
    assert "base_pitch" in compiled["reason"]
