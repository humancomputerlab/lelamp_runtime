"""Dashboard motion action registration."""

from __future__ import annotations


def build_motion_actions(executor, bridge) -> dict[str, object]:
    return {
        "startup": lambda: executor.submit(
            "startup",
            bridge.startup,
            section="motion",
            success_patch={
                "status": "idle",
                "current_recording": bridge.settings.home_recording,
            },
        ),
        "play": lambda name: executor.submit(
            f"play:{name}",
            lambda: bridge.play(name),
            section="motion",
            success_patch={
                "status": "idle",
                "current_recording": bridge.settings.home_recording,
                "last_completed_recording": name,
            },
        ),
        "shutdown_pose": lambda: executor.submit(
            "shutdown_pose",
            bridge.shutdown_pose,
            section="motion",
            success_patch={"status": "idle", "current_recording": "power_off"},
        ),
        "stop": lambda: executor.submit(
            "stop",
            bridge.stop,
            section="motion",
            success_patch={
                "status": "idle",
                "current_recording": bridge.settings.home_recording,
            },
        ),
    }
