from __future__ import annotations

import pytest

from lelamp.motion_v2.program_schema import normalize_program, validate_program


def test_validate_program_accepts_minimal_v2_program() -> None:
    program = {
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
                "joints": {"base_pitch": {"target": 0.7, "role": "lead"}},
            }
        ],
        "lighting": {
            "mode": "gradient",
            "palette": [[120, 180, 255], [255, 255, 255]],
        },
    }

    validate_program(program)
    normalized = normalize_program(program)
    assert normalized["phases"][0]["joints"]["base_pitch"]["role"] == "lead"


def test_validate_program_rejects_unknown_joint_role() -> None:
    with pytest.raises(ValueError, match="unknown joint role"):
        validate_program(
            {
                "version": "v2",
                "intent": "bad",
                "why": "bad",
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
                        "joints": {"base_pitch": {"target": 0.7, "role": "main"}},
                    }
                ],
                "lighting": {
                    "mode": "gradient",
                    "palette": [[120, 180, 255], [255, 255, 255]],
                },
            }
        )
