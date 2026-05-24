"""Runtime sampler loop for the dashboard."""

from __future__ import annotations

from threading import Event, Thread
from time import time

from .audio import collect_audio_snapshot
from .motors import collect_motor_snapshot
from .network import build_reachable_urls
from .voice import collect_voice_snapshot


def collect_runtime_snapshot(settings, executor, started_at: float) -> dict[str, object]:
    active_action = executor.current_action()
    return {
        "status": "running" if active_action is not None else "ready",
        "active_action": active_action,
        "uptime_s": int(time() - started_at),
        "server_started_at": int(started_at * 1000),
        "reachable_urls": build_reachable_urls(
            settings.dashboard_host,
            settings.dashboard_port,
        ),
    }


class DashboardSamplerLoop:
    def __init__(
        self,
        store,
        settings,
        bridge,
        executor,
        *,
        started_at: float | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._bridge = bridge
        self._executor = executor
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._started_at = time() if started_at is None else started_at
        self.interval_s = max(self._settings.dashboard_poll_ms / 1000.0, 0.2)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._patch_section(
                "system",
                lambda: collect_runtime_snapshot(
                    self._settings,
                    self._executor,
                    self._started_at,
                ),
                fallback=self._system_fallback(),
            )
            self._patch_section(
                "motion",
                lambda: collect_motor_snapshot(self._settings, self._bridge),
                fallback={
                    "status": "unknown",
                    "current_recording": None,
                    "last_completed_recording": None,
                    "home_recording": self._settings.home_recording,
                    "startup_recording": self._settings.startup_recording,
                    "last_result": None,
                    "motors_connected": "unknown",
                    "calibration_state": "unknown",
                    "available_recordings": [],
                },
            )
            self._patch_section(
                "audio",
                lambda: collect_audio_snapshot(self._settings),
                fallback={
                    "status": "unknown",
                    "output_device": None,
                    "volume_percent": None,
                    "last_result": "audio sampler failed",
                },
            )
            self._patch_section(
                "voice",
                lambda: collect_voice_snapshot(self._settings),
                fallback={
                    "status": "unknown",
                    "local_state": "unknown",
                    "speech_threshold_db": None,
                    "noise_floor_db": None,
                    "last_level_db": None,
                    "calibration_enabled": False,
                    "calibration_progress": 0.0,
                    "last_asr_status": "unknown",
                    "last_asr_error_code": None,
                    "last_asr_text": None,
                    "last_reply_text": None,
                    "last_result": "voice sampler failed",
                },
            )
            self._stop_event.wait(self.interval_s)

    def _patch_section(self, section: str, collect_values, *, fallback: dict[str, object]) -> None:
        try:
            values = collect_values()
        except Exception:
            values = fallback

        try:
            if section == "system":
                self._store.reconcile_system(values)
                return
            if section == "motion":
                self._store.patch_with(
                    section,
                    lambda current: self._merge_motion_snapshot(current, values),
                )
                return

            self._store.patch(section, values)
        except Exception:
            return

    def _system_fallback(self) -> dict[str, object]:
        return {
            "status": "unknown",
            "active_action": None,
            "uptime_s": int(time() - self._started_at),
            "server_started_at": int(self._started_at * 1000),
            "reachable_urls": [],
        }

    @staticmethod
    def _merge_motion_snapshot(
        current: dict[str, object],
        observed: dict[str, object],
    ) -> dict[str, object]:
        merged = dict(current)
        merged.update(observed)

        if current.get("status") in {"running", "error"}:
            merged["status"] = current["status"]

        for key in ("current_recording", "last_completed_recording", "last_result"):
            if observed.get(key) is None and key in current:
                merged[key] = current[key]

        return merged
