from __future__ import annotations

from lelamp.motion_v2.robot_profile import load_default_robot_profile


def test_default_robot_profile_contains_expected_joint_map() -> None:
    profile = load_default_robot_profile()

    assert profile["joint_order"] == [
        "base_yaw",
        "base_pitch",
        "elbow_pitch",
        "wrist_roll",
        "wrist_pitch",
    ]
    assert profile["joints"]["base_yaw"]["servo_id"] == 1
    assert profile["joints"]["base_pitch"]["semantic_role"] == "attention_pitch"
    assert profile["joints"]["base_yaw"]["risk_tags"] == [
        "yaw_axis",
        "guard_band_required",
    ]
