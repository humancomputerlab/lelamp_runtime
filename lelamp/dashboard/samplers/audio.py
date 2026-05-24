"""Best-effort audio sampling for the dashboard."""

from __future__ import annotations

import re
import subprocess


_VOLUME_PATTERN = re.compile(r"\[(\d{1,3})%\]")


def _unknown_audio_snapshot(last_result: str) -> dict[str, object]:
    return {
        "status": "unknown",
        "output_device": None,
        "volume_percent": None,
        "last_result": last_result,
    }


def collect_audio_snapshot(settings, *, run_command=subprocess.run) -> dict[str, object]:
    try:
        result = run_command(
            ["sudo", "-u", settings.audio_user, "amixer", "sget", "Line"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return _unknown_audio_snapshot("amixer unavailable")

    if getattr(result, "returncode", 0) not in (0, None):
        return _unknown_audio_snapshot(f"amixer exited with {result.returncode}")

    match = _VOLUME_PATTERN.search(getattr(result, "stdout", "") or "")
    if match is None:
        return _unknown_audio_snapshot("volume parse failed")

    volume = int(match.group(1))
    return {
        "status": "ready" if volume > 0 else "muted",
        "output_device": "Line",
        "volume_percent": volume,
        "last_result": "sampled from amixer",
    }
