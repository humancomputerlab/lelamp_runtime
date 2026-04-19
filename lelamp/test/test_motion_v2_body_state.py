from __future__ import annotations

from lelamp.motion_v2.body_state import build_body_state_snapshot
from lelamp.motion_v2.robot_profile import load_default_robot_profile


def test_build_body_state_snapshot_marks_near_boundary_and_slack() -> None:
    profile = load_default_robot_profile()
    snapshot = build_body_state_snapshot(
        pose_norm={
            "base_yaw": 0.0,
            "base_pitch": 88.0,
            "elbow_pitch": 33.0,
            "wrist_roll": 99.0,
            "wrist_pitch": 71.0,
        },
        profile=profile,
        recent_motion_energy=0.42,
    )

    assert snapshot["stability_mode"] == "guarded"
    assert "base_pitch" in snapshot["near_boundary"]
    assert snapshot["slack"]["base_pitch_up"] < snapshot["slack"]["base_pitch_down"]
