from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from threading import Lock
from time import time
from typing import Any


DEFAULT_VOICE_TELEMETRY = {
    "status": "unknown",
    "local_state": "unknown",
    "speech_threshold_db": None,
    "noise_floor_db": None,
    "last_level_db": None,
    "calibration_enabled": False,
    "calibration_progress": 0.0,
    "last_speech_started_at_ms": None,
    "last_speech_finished_at_ms": None,
    "last_commit_at_ms": None,
    "last_clear_at_ms": None,
    "last_asr_status": "unknown",
    "last_asr_error_code": None,
    "last_asr_text": None,
    "last_reply_text": None,
    "last_response_id": None,
    "last_result": "voice telemetry unavailable",
    "updated_at_ms": 0,
}

_DEFAULT_PATH = Path("/tmp/lelamp-voice-state.json")
_DEFAULT_FLUSH_INTERVAL_MS = 200
_GLOBAL_STORE: "VoiceTelemetryStore | None" = None
_GLOBAL_LOCK = Lock()


def _now_ms() -> int:
    return int(time() * 1000)


def default_voice_telemetry() -> dict[str, Any]:
    return deepcopy(DEFAULT_VOICE_TELEMETRY)


class VoiceTelemetryStore:
    def __init__(
        self,
        path: str | Path,
        *,
        flush_interval_ms: int = _DEFAULT_FLUSH_INTERVAL_MS,
    ) -> None:
        self._path = Path(path)
        self._flush_interval_ms = max(int(flush_interval_ms), 0)
        self._lock = Lock()
        self._state = default_voice_telemetry()
        self._last_flush_ms = 0

    @property
    def path(self) -> Path:
        return self._path

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def update(self, *, force: bool = False, **values: Any) -> dict[str, Any]:
        with self._lock:
            changed = False
            for key, value in values.items():
                if self._state.get(key) != value:
                    self._state[key] = deepcopy(value)
                    changed = True

            now_ms = _now_ms()
            if changed or force:
                self._state["updated_at_ms"] = now_ms

            should_flush = force or (
                changed and now_ms - self._last_flush_ms >= self._flush_interval_ms
            )
            if should_flush:
                self._flush_locked(now_ms)

            return deepcopy(self._state)

    def _flush_locked(self, now_ms: int) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(self._state, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temp_path.chmod(0o600)
            temp_path.replace(self._path)
            self._path.chmod(0o600)
        except OSError:
            return

        self._last_flush_ms = now_ms


def configure_voice_telemetry(path: str | Path | None) -> VoiceTelemetryStore:
    global _GLOBAL_STORE

    resolved_path = Path(path) if path else _DEFAULT_PATH
    with _GLOBAL_LOCK:
        if _GLOBAL_STORE is None or _GLOBAL_STORE.path != resolved_path:
            _GLOBAL_STORE = VoiceTelemetryStore(resolved_path)
        return _GLOBAL_STORE


def get_voice_telemetry() -> VoiceTelemetryStore:
    global _GLOBAL_STORE

    with _GLOBAL_LOCK:
        if _GLOBAL_STORE is None:
            _GLOBAL_STORE = VoiceTelemetryStore(_DEFAULT_PATH)
        return _GLOBAL_STORE


def read_voice_telemetry(path: str | Path | None) -> dict[str, Any]:
    resolved_path = Path(path) if path else _DEFAULT_PATH
    if not resolved_path.is_file():
        return default_voice_telemetry()

    try:
        loaded = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snapshot = default_voice_telemetry()
        snapshot["last_result"] = "voice telemetry invalid"
        return snapshot

    snapshot = default_voice_telemetry()
    if isinstance(loaded, dict):
        snapshot.update(loaded)
    if snapshot.get("last_result") in (None, ""):
        snapshot["last_result"] = "voice telemetry sampled"
    return snapshot
