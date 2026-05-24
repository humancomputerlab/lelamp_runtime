"""Voice telemetry sampling for the dashboard."""

from __future__ import annotations

from lelamp.voice_telemetry import read_voice_telemetry


def collect_voice_snapshot(settings) -> dict[str, object]:
    snapshot = read_voice_telemetry(settings.voice_state_path)
    return {
        "status": snapshot.get("status", "unknown"),
        "local_state": snapshot.get("local_state", "unknown"),
        "speech_threshold_db": snapshot.get("speech_threshold_db"),
        "noise_floor_db": snapshot.get("noise_floor_db"),
        "last_level_db": snapshot.get("last_level_db"),
        "calibration_enabled": snapshot.get("calibration_enabled", False),
        "calibration_progress": snapshot.get("calibration_progress", 0.0),
        "last_asr_status": snapshot.get("last_asr_status", "unknown"),
        "last_asr_error_code": snapshot.get("last_asr_error_code"),
        "last_asr_text": snapshot.get("last_asr_text"),
        "last_reply_text": snapshot.get("last_reply_text"),
        "last_result": snapshot.get("last_result", "voice telemetry unavailable"),
    }
