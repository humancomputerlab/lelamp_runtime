"""Motor sampling helpers for the dashboard."""

from __future__ import annotations

from pathlib import Path


def collect_motor_snapshot(settings, bridge, *, path_exists=None) -> dict[str, object]:
    if path_exists is None:
        path_exists = Path.exists

    recordings_error = False
    try:
        recordings = bridge.list_recordings()
    except Exception:
        recordings = []
        recordings_error = True

    port_path = Path(settings.port)
    port_present = path_exists(port_path)
    motors_connected: bool | str = "unknown" if port_present else False
    status = "unknown" if port_present else "error"

    if recordings_error and port_present:
        motors_connected = "unknown"
        status = "unknown"

    return {
        "status": status,
        "current_recording": None,
        "last_completed_recording": None,
        "home_recording": settings.home_recording,
        "startup_recording": settings.startup_recording,
        "last_result": None,
        "motors_connected": motors_connected,
        "calibration_state": "unknown",
        "available_recordings": recordings,
    }
