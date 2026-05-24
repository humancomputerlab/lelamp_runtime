from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .pose_snapshot import write_static_recording


DEFAULT_HOME_SAFE_POSE = {
    "base_yaw": -2.783109404990398,
    "base_pitch": 34.978354978354986,
    "elbow_pitch": 33.588761174968084,
    "wrist_roll": 99.95115995115995,
    "wrist_pitch": 70.82469954413594,
}

SLEEP_POSE_DELTAS = {
    "base_pitch": -7.5,
    "elbow_pitch": 11.0,
    "wrist_roll": 2.5,
    "wrist_pitch": -50.0,
}


def _normalize_pose(pose: Mapping[str, float]) -> dict[str, float]:
    return {joint: float(value) for joint, value in pose.items()}


def build_sleep_pose(home_pose: Mapping[str, float] | None = None) -> dict[str, float]:
    home = _normalize_pose(home_pose or DEFAULT_HOME_SAFE_POSE)
    sleep_pose = home.copy()

    # Preserve the designed shutdown shape as offsets from the current HomeSafe.
    for joint, delta in SLEEP_POSE_DELTAS.items():
        sleep_pose[joint] = home[joint] + delta
    return sleep_pose


def build_pose_presets(home_pose: Mapping[str, float] | None = None) -> dict[str, dict[str, float]]:
    home_safe = _normalize_pose(home_pose or DEFAULT_HOME_SAFE_POSE)
    sleep_pose = build_sleep_pose(home_safe)
    return {
        "home_safe": home_safe,
        "sleep_pose": sleep_pose,
        "power_off": sleep_pose.copy(),
    }


def write_pose_recordings(
    recordings_dir: Path,
    *,
    home_pose: Mapping[str, float] | None = None,
    fps: int = 30,
    frame_count: int = 30,
) -> list[Path]:
    presets = build_pose_presets(home_pose)
    output_paths: list[Path] = []
    for name, pose in presets.items():
        output_paths.append(
            write_static_recording(
                recordings_dir / f"{name}.csv",
                pose,
                fps=fps,
                frame_count=frame_count,
            )
        )
    output_paths.append(
        write_static_recording(
            recordings_dir / "idle.csv",
            presets["home_safe"],
            fps=fps,
            frame_count=frame_count,
        )
    )
    return output_paths
