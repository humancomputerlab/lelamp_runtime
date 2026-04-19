from __future__ import annotations

from copy import deepcopy

DEFAULT_ROBOT_PROFILE = {
    "joint_order": [
        "base_yaw",
        "base_pitch",
        "elbow_pitch",
        "wrist_roll",
        "wrist_pitch",
    ],
    "joints": {
        "base_yaw": {
            "servo_id": 1,
            "physical_hard_limit_rad": [-5.02103, 1.26215],
            "physical_hard_limit_deg": [-287.68, 72.32],
            "comfort_window_norm": [-25.0, 25.0],
            "protected_window_norm": [-35.0, 30.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 6.0,
            "max_speed_norm_s": 45.0,
            "max_accel_norm_s2": 120.0,
            "semantic_role": "attention_heading",
            "risk_tags": ["yaw_axis", "guard_band_required"],
        },
        "base_pitch": {
            "servo_id": 2,
            "physical_hard_limit_rad": [-1.08426, 2.05734],
            "physical_hard_limit_deg": [-62.12, 117.88],
            "comfort_window_norm": [-20.0, 90.0],
            "protected_window_norm": [-30.0, 98.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 7.0,
            "max_speed_norm_s": 60.0,
            "max_accel_norm_s2": 150.0,
            "semantic_role": "attention_pitch",
            "risk_tags": ["lead_axis"],
        },
        "elbow_pitch": {
            "servo_id": 3,
            "physical_hard_limit_rad": [-2.82002, 0.32157],
            "physical_hard_limit_deg": [-161.58, 18.42],
            "comfort_window_norm": [-10.0, 70.0],
            "protected_window_norm": [-20.0, 80.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 8.0,
            "max_speed_norm_s": 70.0,
            "max_accel_norm_s2": 160.0,
            "semantic_role": "reach_axis",
            "risk_tags": ["support_axis"],
        },
        "wrist_roll": {
            "servo_id": 4,
            "physical_hard_limit_rad": [-3.78654, 2.49665],
            "physical_hard_limit_deg": [-216.95, 143.05],
            "comfort_window_norm": [40.0, 100.0],
            "protected_window_norm": [20.0, 110.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 10.0,
            "max_speed_norm_s": 90.0,
            "max_accel_norm_s2": 220.0,
            "semantic_role": "accent_roll",
            "risk_tags": ["accent_axis"],
        },
        "wrist_pitch": {
            "servo_id": 5,
            "physical_hard_limit_rad": [-0.85334, 2.28826],
            "physical_hard_limit_deg": [-48.89, 131.11],
            "comfort_window_norm": [45.0, 90.0],
            "protected_window_norm": [35.0, 100.0],
            "hard_window_norm": [-100.0, 100.0],
            "max_step_norm": 8.0,
            "max_speed_norm_s": 70.0,
            "max_accel_norm_s2": 170.0,
            "semantic_role": "attention_fine_pitch",
            "risk_tags": ["head_axis"],
        },
    },
}


def load_default_robot_profile() -> dict[str, object]:
    return deepcopy(DEFAULT_ROBOT_PROFILE)
