"""Execute compiled DSL events through existing services."""

from __future__ import annotations

from typing import Any

from .compiler import compile_scene


def execute_compiled_scene(
    compiled: dict[str, list[tuple[str, object]]],
    *,
    animation_service: Any = None,
    rgb_service: Any = None,
) -> dict[str, list[tuple[str, object]]]:
    if animation_service is not None:
        for event_type, payload in compiled.get("motion", []):
            animation_service.dispatch(event_type, payload)
    if rgb_service is not None:
        for event_type, payload in compiled.get("light", []):
            rgb_service.dispatch(event_type, payload)
    return compiled


def execute_scene(
    scene: dict[str, object],
    *,
    animation_service: Any = None,
    rgb_service: Any = None,
) -> dict[str, list[tuple[str, object]]]:
    compiled = compile_scene(scene)
    return execute_compiled_scene(
        compiled,
        animation_service=animation_service,
        rgb_service=rgb_service,
    )
