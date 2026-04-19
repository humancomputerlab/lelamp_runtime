from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy

JOINT_ROLES = frozenset({"lead", "support", "accent"})
EASINGS = frozenset({"linear", "ease_in", "ease_out", "ease_in_out"})


def validate_program(program: Mapping[str, object]) -> None:
    if not isinstance(program, Mapping):
        raise ValueError("program must be a mapping")
    if program.get("version") != "v2":
        raise ValueError("program version must be 'v2'")

    intent = program.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("intent must be a non-empty string")

    phases = program.get("phases")
    if (
        not isinstance(phases, Sequence)
        or isinstance(phases, (str, bytes))
        or not phases
    ):
        raise ValueError("phases must be a non-empty list")

    for phase in phases:
        if not isinstance(phase, Mapping):
            raise ValueError("phase must be a mapping")

        joints = phase.get("joints", {})
        if not isinstance(joints, Mapping):
            raise ValueError("phase joints must be a mapping")

        for joint_name, joint_cfg in joints.items():
            if not isinstance(joint_cfg, Mapping):
                raise ValueError(f"joint config must be a mapping for {joint_name}")

            role = joint_cfg.get("role")
            if role not in JOINT_ROLES:
                raise ValueError(f"unknown joint role: {role!r}")

            target = joint_cfg.get("target")
            if not isinstance(target, (int, float)):
                raise ValueError(f"joint target must be numeric for {joint_name}")
            if float(target) < -1.0 or float(target) > 1.0:
                raise ValueError(
                    f"joint target must be within [-1.0, 1.0] for {joint_name}"
                )

        easing = phase.get("easing")
        if easing not in EASINGS:
            raise ValueError(f"unknown easing: {easing!r}")


def normalize_program(program: Mapping[str, object]) -> dict[str, object]:
    validate_program(program)
    normalized = deepcopy(dict(program))
    normalized["intent"] = str(normalized["intent"]).strip()
    return normalized
