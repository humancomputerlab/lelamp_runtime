from __future__ import annotations

from typing import Any


def execute_compiled_program(
    *,
    compiled: dict[str, Any],
    animation_service: Any = None,
    rgb_service: Any = None,
) -> dict[str, Any]:
    frames = [_normalize_frame(frame) for frame in compiled.get("frames", [])]
    lighting = compiled.get("lighting")

    if animation_service is not None and frames:
        animation_service.dispatch("frames", frames)

    light_event_count = 0
    if rgb_service is not None and isinstance(lighting, dict):
        mode = lighting.get("mode")
        if mode == "solid" and isinstance(lighting.get("rgb"), (list, tuple)):
            rgb_service.dispatch("solid", tuple(lighting["rgb"]))
            light_event_count = 1
        elif mode == "gradient" and isinstance(lighting.get("palette"), list):
            rgb_service.dispatch(
                "paint",
                [tuple(color) for color in lighting["palette"]],
            )
            light_event_count = 1

    return {
        "decision": compiled.get("decision", "exact"),
        "motion_frame_count": len(frames),
        "light_event_count": light_event_count,
    }


def _normalize_frame(frame: Any) -> dict[str, float]:
    if not isinstance(frame, dict):
        return {}
    normalized: dict[str, float] = {}
    for joint_name, value in frame.items():
        key = str(joint_name)
        if not key.endswith(".pos"):
            key = f"{key}.pos"
        normalized[key] = float(value)
    return normalized
