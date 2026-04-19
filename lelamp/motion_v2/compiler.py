from __future__ import annotations

from copy import deepcopy
from typing import Any

from .program_schema import normalize_program


def compile_action_program(
    *,
    program: dict[str, Any],
    critique: dict[str, Any],
    snapshot: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_program(program)
    patched = _apply_critique_patch(
        normalized,
        critique or {"patch": {"phase_adjustments": []}},
    )
    pose_norm = snapshot["pose_norm"]
    joints_cfg = profile["joints"]
    keyframes = []
    adjustments: list[str] = []
    lead_joint = None

    for phase in patched["phases"]:
        frame = dict(pose_norm)
        for joint_name, joint_cfg in phase["joints"].items():
            if joint_cfg["role"] == "lead" and lead_joint is None:
                lead_joint = joint_name

            resolved = _resolve_joint_target(
                current=float(frame[joint_name]),
                target=float(joint_cfg["target"]),
                joint_cfg=joints_cfg[joint_name],
                exaggeration=float(patched["style"]["exaggeration"]),
            )
            frame[joint_name] = resolved["value"]
            adjustments.extend(resolved["adjustments"])
            if resolved["decision"] == "rejected":
                return {
                    "decision": "rejected",
                    "reason": f"{joint_name} exceeded protected hard window",
                    "generated_frames": 0,
                    "adjustments": adjustments,
                    "preserved_intent": {
                        "lead_joint": lead_joint,
                        "intent_retention": 0.0,
                    },
                    "keyframes": [],
                    "frames": [],
                    "lighting": patched.get("lighting"),
                }
        keyframes.append({"t_ms": phase["duration_ms"], "pose_norm": frame})

    frames = _expand_keyframes(keyframes)
    decision = "clipped" if adjustments else "exact"
    return {
        "decision": decision,
        "generated_frames": len(frames),
        "adjustments": adjustments,
        "preserved_intent": {
            "lead_joint": lead_joint,
            "intent_retention": 1.0 if decision == "exact" else 0.85,
        },
        "keyframes": keyframes,
        "frames": frames,
        "lighting": patched.get("lighting"),
    }


def _apply_critique_patch(
    program: dict[str, object], critique: dict[str, object]
) -> dict[str, object]:
    patched = deepcopy(program)
    for adjustment in critique.get("patch", {}).get("phase_adjustments", []):
        for phase in patched["phases"]:
            if phase["name"] != adjustment["name"]:
                continue
            for joint_name, target in adjustment.get("joint_overrides", {}).items():
                if joint_name in phase["joints"]:
                    phase["joints"][joint_name]["target"] = target
    return patched


def _resolve_joint_target(
    *,
    current: float,
    target: float,
    joint_cfg: dict[str, object],
    exaggeration: float,
) -> dict[str, object]:
    comfort_low, comfort_high = joint_cfg["comfort_window_norm"]
    protected_low, protected_high = joint_cfg["protected_window_norm"]
    hard_low, hard_high = joint_cfg["hard_window_norm"]
    desired = current + (target * exaggeration * 20.0)
    adjustments: list[str] = []

    if desired < hard_low or desired > hard_high:
        return {"decision": "rejected", "value": current, "adjustments": adjustments}

    if desired < protected_low or desired > protected_high:
        desired = max(protected_low, min(protected_high, desired))
        adjustments.append("applied_protected_window_clip")

    if desired < comfort_low or desired > comfort_high:
        adjustments.append("left_comfort_window")

    return {
        "decision": "accepted",
        "value": round(desired, 3),
        "adjustments": adjustments,
    }


def _expand_keyframes(keyframes: list[dict[str, object]]) -> list[dict[str, float]]:
    return [frame["pose_norm"] for frame in keyframes]
