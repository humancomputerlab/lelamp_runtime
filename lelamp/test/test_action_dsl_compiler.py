from __future__ import annotations

from lelamp.action_dsl.compiler import compile_scene
from lelamp.action_dsl.executor import execute_scene


def test_compile_scene_lowers_gesture_and_light_nodes():
    scene = {
        "body": [
            {"type": "gesture", "name": "nod", "intensity": 0.4, "repeats": 1},
            {"type": "gesture", "name": "greeting", "intensity": 0.7, "repeats": 1},
        ],
        "light": [
            {"type": "solid", "rgb": [255, 170, 70], "fade_ms": 300},
            {"type": "pulse", "palette": [[255, 180, 90], [255, 120, 40]], "bpm": 92, "cycles": 2},
        ],
    }

    compiled = compile_scene(scene)

    assert compiled == {
        "motion": [("play", "nod"), ("play", "wake_up")],
        "light": [
            ("solid", (255, 170, 70)),
            ("paint", [(255, 180, 90), (255, 120, 40)]),
        ],
    }


def test_execute_scene_dispatches_compiled_events():
    class _Service:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def dispatch(self, event_type: str, payload: object) -> None:
            self.calls.append((event_type, payload))

    animation = _Service()
    rgb = _Service()

    execute_scene(
        {
            "body": [{"type": "gesture", "name": "nod", "intensity": 0.4, "repeats": 1}],
            "light": [{"type": "solid", "rgb": [255, 170, 70], "fade_ms": 300}],
        },
        animation_service=animation,
        rgb_service=rgb,
    )

    assert animation.calls == [("play", "nod")]
    assert rgb.calls == [("solid", (255, 170, 70))]


def test_compile_scene_drops_trailing_settle_after_visible_motion():
    compiled = compile_scene(
        {
            "body": [
                {"type": "look", "direction": "up"},
                {"type": "settle", "style": "soft"},
            ],
            "light": [],
        }
    )

    assert compiled == {
        "motion": [("play", "wake_up")],
        "light": [],
    }


def test_compile_scene_keeps_standalone_settle():
    compiled = compile_scene(
        {
            "body": [{"type": "settle", "style": "soft"}],
            "light": [],
        }
    )

    assert compiled == {
        "motion": [("play", "home_safe")],
        "light": [],
    }
