"""Dashboard light action registration."""

from __future__ import annotations


def build_light_actions(executor, bridge) -> dict[str, object]:
    return {
        "solid": lambda red, green, blue: executor.submit(
            "light:solid",
            lambda: bridge.set_light_solid((red, green, blue)),
            section="light",
            success_patch={
                "status": "solid",
                "color": {"red": red, "green": green, "blue": blue},
            },
        ),
        "clear": lambda: executor.submit(
            "light:clear",
            bridge.clear_light,
            section="light",
            success_patch={"status": "off", "color": None},
        ),
    }
