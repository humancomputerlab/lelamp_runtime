from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping


POSE_FIELDS = (
    "base_yaw",
    "base_pitch",
    "elbow_pitch",
    "wrist_roll",
    "wrist_pitch",
)


def build_static_recording_rows(
    pose: Mapping[str, float],
    *,
    fps: int,
    frame_count: int,
) -> list[dict[str, str]]:
    if fps <= 0:
        raise ValueError("fps must be positive")
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")

    missing = [field for field in POSE_FIELDS if field not in pose]
    if missing:
        raise ValueError(f"pose is missing fields: {', '.join(missing)}")

    rows: list[dict[str, str]] = []
    for index in range(frame_count):
        row = {"timestamp": f"{index / fps:.6f}"}
        for field in POSE_FIELDS:
            row[f"{field}.pos"] = f"{float(pose[field]):.6f}"
        rows.append(row)
    return rows


def write_static_recording(
    path: Path,
    pose: Mapping[str, float],
    *,
    fps: int,
    frame_count: int,
) -> Path:
    rows = build_static_recording_rows(pose, fps=fps, frame_count=frame_count)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["timestamp", *[f"{field}.pos" for field in POSE_FIELDS]]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated = False
    output_lines: list[str] = []
    prefix = f"{key}="

    for line in lines:
        if line.startswith(prefix):
            output_lines.append(f"{key}={value}")
            updated = True
        else:
            output_lines.append(line)

    if not updated:
        output_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
