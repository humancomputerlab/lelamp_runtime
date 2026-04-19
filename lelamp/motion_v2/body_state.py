from __future__ import annotations

from collections.abc import Mapping


def build_body_state_snapshot(
    *,
    pose_norm: Mapping[str, float],
    profile: Mapping[str, object],
    recent_motion_energy: float = 0.0,
) -> dict[str, object]:
    joints = profile["joints"]
    slack = {}
    near_boundary = []

    for joint, current in pose_norm.items():
        cfg = joints[joint]
        comfort_low, comfort_high = cfg["comfort_window_norm"]
        slack[f"{joint}_down"] = round(float(current) - float(comfort_low), 3)
        slack[f"{joint}_up"] = round(float(comfort_high) - float(current), 3)
        if slack[f"{joint}_down"] < 10.0 or slack[f"{joint}_up"] < 10.0:
            near_boundary.append(joint)

    stability_mode = "guarded" if near_boundary else "normal"
    return {
        "pose_norm": {joint: float(value) for joint, value in pose_norm.items()},
        "slack": slack,
        "near_boundary": near_boundary,
        "recent_motion_energy": float(recent_motion_energy),
        "stability_mode": stability_mode,
    }
