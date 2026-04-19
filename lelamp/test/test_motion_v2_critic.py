from __future__ import annotations

from lelamp.motion_v2.critic import critique_program


def test_critic_trims_small_accent_when_lead_axis_is_clear() -> None:
    critique = critique_program(
        program={
            "intent": "proud_look_up",
            "phases": [
                {
                    "name": "accent",
                    "joints": {
                        "base_pitch": {"target": 0.72, "role": "lead"},
                        "wrist_roll": {"target": 0.02, "role": "accent"},
                    },
                }
            ],
        },
        recent_fingerprints={"old_motion"},
    )

    assert critique["decision"] == "revise"
    assert (
        critique["patch"]["phase_adjustments"][0]["joint_overrides"]["wrist_roll"]
        == 0.0
    )
