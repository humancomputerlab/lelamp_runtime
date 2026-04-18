from __future__ import annotations

import pytest

from lelamp.action_dsl.schema import validate_scene


def test_validate_scene_accepts_known_primitives():
    scene = {
        "body": [{"type": "gesture", "name": "nod", "intensity": 0.4, "repeats": 1}],
        "light": [
            {
                "type": "pulse",
                "palette": [[255, 180, 90], [255, 120, 40]],
                "bpm": 92,
                "cycles": 2,
            }
        ],
    }

    validate_scene(scene)


def test_validate_scene_rejects_unknown_primitive():
    scene = {"body": [{"type": "servo_frame", "angles": [1, 2, 3]}], "light": []}

    with pytest.raises(ValueError, match="unsupported primitive"):
        validate_scene(scene)


def test_validate_scene_rejects_invalid_palette_rgb_range():
    scene = {
        "body": [],
        "light": [{"type": "gradient", "palette": [[999, 0, 0], [0, 0, 0]], "duration_ms": 800}],
    }

    with pytest.raises(ValueError, match="RGB"):
        validate_scene(scene)
