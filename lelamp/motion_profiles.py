from __future__ import annotations

from typing import Iterable


STARTUP_JOINT_ORDER = (
    "wrist_pitch.pos",
    "wrist_roll.pos",
    "elbow_pitch.pos",
    "base_pitch.pos",
    "base_yaw.pos",
)
STARTUP_ACTIVE_JOINTS = (
    "base_pitch.pos",
    "elbow_pitch.pos",
    "wrist_roll.pos",
    "wrist_pitch.pos",
)
SHUTDOWN_JOINT_ORDER = (
    "base_pitch.pos",
    "elbow_pitch.pos",
    "wrist_roll.pos",
    "wrist_pitch.pos",
)
SHUTDOWN_RELEASE_ORDER = (
    "base_pitch",
    "elbow_pitch",
    "wrist_roll",
    "wrist_pitch",
    "base_yaw",
)


def first_pose(actions: Iterable[dict[str, float]]) -> dict[str, float] | None:
    for action in actions:
        return action.copy()
    return None


def transform_actions_relative_to_pose(
    actions: list[dict[str, float]],
    home_pose: dict[str, float] | None,
) -> list[dict[str, float]]:
    if not actions or home_pose is None:
        return [action.copy() for action in actions]

    anchor = actions[0]
    transformed: list[dict[str, float]] = []
    for action in actions:
        frame: dict[str, float] = {}
        for joint, value in action.items():
            base = home_pose.get(joint, anchor[joint])
            frame[joint] = base + (value - anchor[joint])
        transformed.append(frame)
    return transformed


def _smoothstep(progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return progress * progress * (3.0 - 2.0 * progress)


def build_sequential_transition(
    start_pose: dict[str, float],
    target_pose: dict[str, float],
    *,
    joint_order: tuple[str, ...] = STARTUP_JOINT_ORDER,
    frames_per_joint: int = 12,
) -> list[dict[str, float]]:
    if frames_per_joint <= 0:
        raise ValueError("frames_per_joint must be positive")

    state = target_pose.copy()
    state.update(start_pose)
    frames: list[dict[str, float]] = []

    for joint in joint_order:
        if joint not in target_pose:
            continue

        joint_start = state.get(joint, target_pose[joint])
        joint_end = target_pose[joint]
        if joint_start == joint_end:
            continue

        for index in range(1, frames_per_joint + 1):
            progress = _smoothstep(index / frames_per_joint)
            frame = state.copy()
            frame[joint] = joint_start + (joint_end - joint_start) * progress
            frames.append(frame)

        state[joint] = joint_end

    return frames


def build_parallel_transition(
    start_pose: dict[str, float],
    target_pose: dict[str, float],
    *,
    joints: tuple[str, ...] = STARTUP_ACTIVE_JOINTS,
    frame_count: int = 12,
) -> list[dict[str, float]]:
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")

    state = target_pose.copy()
    state.update(start_pose)
    frames: list[dict[str, float]] = []

    for index in range(1, frame_count + 1):
        progress = _smoothstep(index / frame_count)
        frame = state.copy()
        for joint in joints:
            if joint not in target_pose:
                continue
            start = start_pose.get(joint, target_pose[joint])
            end = target_pose[joint]
            frame[joint] = start + (end - start) * progress
        frames.append(frame)

    return frames


def build_dynamic_startup_actions(
    current_pose: dict[str, float],
    home_pose: dict[str, float],
    wake_up_actions: list[dict[str, float]],
    *,
    settle_joints: tuple[str, ...] = STARTUP_ACTIVE_JOINTS,
    settle_frame_count: int = 12,
    settle_hold_frames: int = 6,
    return_home_frame_count: int = 12,
) -> list[dict[str, float]]:
    startup_anchor = home_pose.copy()
    startup_anchor["base_yaw.pos"] = current_pose.get(
        "base_yaw.pos",
        startup_anchor.get("base_yaw.pos", 0.0),
    )

    settle_frames = build_parallel_transition(
        current_pose,
        startup_anchor,
        joints=settle_joints,
        frame_count=settle_frame_count,
    )
    wake_up_frames = transform_actions_relative_to_pose(wake_up_actions, startup_anchor)
    for frame in wake_up_frames:
        frame["base_yaw.pos"] = startup_anchor["base_yaw.pos"]

    if settle_hold_frames < 0:
        raise ValueError("settle_hold_frames must be non-negative")
    if return_home_frame_count < 0:
        raise ValueError("return_home_frame_count must be non-negative")

    hold_frames = [startup_anchor.copy() for _ in range(settle_hold_frames)]
    return_home_frames: list[dict[str, float]] = []
    if wake_up_frames and return_home_frame_count > 0:
        return_home_frames = build_parallel_transition(
            wake_up_frames[-1],
            startup_anchor,
            joints=settle_joints,
            frame_count=return_home_frame_count,
        )

    return settle_frames + hold_frames + wake_up_frames + return_home_frames


def build_staged_shutdown_actions(
    current_pose: dict[str, float],
    power_off_pose: dict[str, float],
    *,
    joint_order: tuple[str, ...] = SHUTDOWN_JOINT_ORDER,
    prepare_fraction: float = 0.28,
    prepare_frames_per_joint: int = 10,
    settle_frames_per_joint: int = 18,
    hold_frames: int = 6,
) -> list[dict[str, float]]:
    if not 0.0 <= prepare_fraction <= 1.0:
        raise ValueError("prepare_fraction must be between 0 and 1")
    if hold_frames < 0:
        raise ValueError("hold_frames must be non-negative")

    final_pose = power_off_pose.copy()
    final_pose["base_yaw.pos"] = current_pose.get("base_yaw.pos", final_pose.get("base_yaw.pos", 0.0))

    prepare_pose = final_pose.copy()
    for joint in joint_order:
        if joint not in final_pose:
            continue
        start = current_pose.get(joint, final_pose[joint])
        end = final_pose[joint]
        prepare_pose[joint] = start + (end - start) * prepare_fraction

    prepare_frames = build_sequential_transition(
        current_pose,
        prepare_pose,
        joint_order=joint_order,
        frames_per_joint=prepare_frames_per_joint,
    )
    settle_frames = build_sequential_transition(
        prepare_pose,
        final_pose,
        joint_order=joint_order,
        frames_per_joint=settle_frames_per_joint,
    )
    hold = [prepare_pose.copy() for _ in range(hold_frames)]
    return prepare_frames + hold + settle_frames
