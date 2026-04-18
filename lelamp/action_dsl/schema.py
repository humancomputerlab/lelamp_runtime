"""Validation helpers for constrained action scenes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

BODY_PRIMITIVES = frozenset({"pose", "gesture", "look", "sweep", "settle"})
LIGHT_PRIMITIVES = frozenset({"solid", "gradient", "pulse", "sparkle"})


def validate_scene(scene: Mapping[str, Any]) -> None:
    if not isinstance(scene, Mapping):
        raise ValueError("scene must be a mapping")

    body = scene.get("body", [])
    light = scene.get("light", [])
    if not isinstance(body, Sequence) or isinstance(body, (str, bytes)):
        raise ValueError("scene body must be a list")
    if not isinstance(light, Sequence) or isinstance(light, (str, bytes)):
        raise ValueError("scene light must be a list")

    for node in body:
        _validate_body_node(node)
    for node in light:
        _validate_light_node(node)


def _validate_body_node(node: Mapping[str, Any]) -> None:
    if not isinstance(node, Mapping):
        raise ValueError("body node must be a mapping")
    primitive = node.get("type")
    if primitive not in BODY_PRIMITIVES:
        raise ValueError(f"unsupported primitive: {primitive!r}")

    if primitive in {"pose", "gesture"}:
        name = node.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("body node name must be a non-empty string")
    if primitive == "gesture":
        repeats = node.get("repeats", 1)
        if not isinstance(repeats, int) or repeats <= 0:
            raise ValueError("gesture repeats must be a positive integer")
        intensity = node.get("intensity")
        if intensity is not None and not isinstance(intensity, (int, float)):
            raise ValueError("gesture intensity must be numeric")
    if primitive == "look":
        direction = node.get("direction")
        if not isinstance(direction, str) or not direction.strip():
            raise ValueError("look direction must be a non-empty string")
    if primitive == "settle":
        style = node.get("style")
        if not isinstance(style, str) or not style.strip():
            raise ValueError("settle style must be a non-empty string")


def _validate_light_node(node: Mapping[str, Any]) -> None:
    if not isinstance(node, Mapping):
        raise ValueError("light node must be a mapping")
    primitive = node.get("type")
    if primitive not in LIGHT_PRIMITIVES:
        raise ValueError(f"unsupported primitive: {primitive!r}")

    if primitive == "solid":
        _validate_rgb(node.get("rgb"))
        return

    palette = node.get("palette")
    if not isinstance(palette, Sequence) or isinstance(palette, (str, bytes)) or not palette:
        raise ValueError("palette must be a non-empty list of RGB colors")
    for color in palette:
        _validate_rgb(color)


def _validate_rgb(rgb: Any) -> None:
    if not isinstance(rgb, Sequence) or isinstance(rgb, (str, bytes)) or len(rgb) != 3:
        raise ValueError("RGB value must have exactly 3 channels")
    for channel in rgb:
        if not isinstance(channel, int) or isinstance(channel, bool):
            raise ValueError("RGB channel must be an integer")
        if channel < 0 or channel > 255:
            raise ValueError("RGB channel must be within 0..255")
