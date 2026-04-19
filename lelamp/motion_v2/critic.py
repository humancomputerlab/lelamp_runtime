from __future__ import annotations

from collections.abc import Mapping, Set
from copy import deepcopy


def critique_program(
    *, program: Mapping[str, object], recent_fingerprints: Set[str]
) -> dict[str, object]:
    patched = deepcopy(dict(program))
    phase_adjustments = []
    issues = []

    for phase in patched.get("phases", []):
        joint_overrides = {}
        joints = phase.get("joints", {})
        lead_present = any(cfg.get("role") == "lead" for cfg in joints.values())

        for joint_name, cfg in joints.items():
            if (
                lead_present
                and cfg.get("role") == "accent"
                and abs(float(cfg.get("target", 0.0))) < 0.05
            ):
                joint_overrides[joint_name] = 0.0
                issues.append(f"weak accent removed: {joint_name}")

        if joint_overrides:
            phase_adjustments.append(
                {"name": phase.get("name"), "joint_overrides": joint_overrides}
            )

    if recent_fingerprints:
        issues.append(
            "novelty pressure increased because recent motion history is non-empty"
        )

    if phase_adjustments:
        return {
            "decision": "revise",
            "summary": "Trim weak accents and reserve motion budget for the lead axis.",
            "scores": {
                "expressivity": 0.7,
                "novelty": 0.5,
                "clarity": 0.8,
                "safety_prior": 0.8,
            },
            "issues": issues,
            "patch": {"phase_adjustments": phase_adjustments},
        }

    return {
        "decision": "accept",
        "summary": "Program is clear enough for compilation.",
        "scores": {
            "expressivity": 0.7,
            "novelty": 0.5,
            "clarity": 0.8,
            "safety_prior": 0.8,
        },
        "issues": issues,
        "patch": {"phase_adjustments": []},
    }
