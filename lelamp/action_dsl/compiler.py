"""Lower constrained scenes into current motion/RGB dispatch events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schema import validate_scene

_GESTURE_RECORDINGS = {
    "nod": "nod",
    "greeting": "wake_up",
    "happy": "happy_wiggle",
    "happy_wiggle": "happy_wiggle",
    "curious": "curious",
    "sad": "sad",
    "shock": "shock",
    "shy": "shy",
    "worried": "headshake",
    "headshake": "headshake",
    "calm": "idle",
}
_LOOK_RECORDINGS = {
    "left": "curious",
    "right": "curious",
    "up": "wake_up",
    "down": "shy",
    "center": "idle",
}
_SETTLE_RECORDINGS = {
    "soft": "home_safe",
    "home": "home_safe",
    "idle": "idle",
    "off": "power_off",
    "power_off": "power_off",
    "wake": "wake_up",
    "wake_up": "wake_up",
}


def compile_scene(scene: Mapping[str, Any]) -> dict[str, list[tuple[str, object]]]:
    validate_scene(scene)
    motion_events: list[tuple[str, object]] = []
    light_events: list[tuple[str, object]] = []

    for node in scene.get("body", []):
        primitive = node["type"]
        if primitive == "pose":
            motion_events.append(("play", str(node["name"])))
            continue
        if primitive == "gesture":
            recording = _GESTURE_RECORDINGS.get(str(node["name"]))
            if recording is None:
                raise ValueError(f"unsupported gesture: {node['name']!r}")
            repeats = int(node.get("repeats", 1))
            motion_events.extend([("play", recording)] * repeats)
            continue
        if primitive == "look":
            recording = _LOOK_RECORDINGS.get(str(node["direction"]).lower())
            if recording is None:
                raise ValueError(f"unsupported look direction: {node['direction']!r}")
            motion_events.append(("play", recording))
            continue
        if primitive == "sweep":
            motion_events.append(("play", "wake_up"))
            continue
        if primitive == "settle":
            recording = _SETTLE_RECORDINGS.get(str(node["style"]).lower())
            if recording is None:
                raise ValueError(f"unsupported settle style: {node['style']!r}")
            motion_events.append(("play", recording))
            continue
        raise ValueError(f"unsupported primitive: {primitive!r}")

    for node in scene.get("light", []):
        primitive = node["type"]
        if primitive == "solid":
            light_events.append(("solid", tuple(node["rgb"])))
            continue
        palette = [tuple(color) for color in node["palette"]]
        if primitive in {"gradient", "pulse", "sparkle"}:
            light_events.append(("paint", palette))
            continue
        raise ValueError(f"unsupported primitive: {primitive!r}")

    return {"motion": motion_events, "light": light_events}
