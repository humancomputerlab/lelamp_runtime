import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lelamp.dashboard.samplers import (
    DashboardSamplerLoop,
    build_reachable_urls,
    collect_audio_snapshot,
    collect_motor_snapshot,
    collect_runtime_snapshot,
    collect_voice_snapshot,
)
from lelamp.dashboard.state_store import DashboardStateStore


def _make_settings(**overrides):
    values = {
        "dashboard_host": "0.0.0.0",
        "dashboard_port": 8765,
        "dashboard_poll_ms": 400,
        "audio_user": "pi",
        "voice_state_path": "/tmp/lelamp-voice-state.json",
        "port": "/dev/ttyACM0",
        "home_recording": "home_safe",
        "startup_recording": "wake_up",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DashboardSamplerTests(unittest.TestCase):
    def test_build_reachable_urls_includes_loopback_and_provided_ipv4_addresses(self) -> None:
        urls = build_reachable_urls(
            "0.0.0.0",
            8765,
            ip_list=["192.168.0.15", "172.20.10.3"],
        )

        self.assertEqual(
            urls,
            [
                "http://127.0.0.1:8765",
                "http://192.168.0.15:8765",
                "http://172.20.10.3:8765",
            ],
        )

    def test_build_reachable_urls_prepends_explicit_host_and_dedupes(self) -> None:
        urls = build_reachable_urls(
            "192.168.0.15",
            8765,
            ip_list=["192.168.0.15", "172.20.10.3"],
        )

        self.assertEqual(urls, ["http://192.168.0.15:8765"])

    def test_build_reachable_urls_wraps_explicit_ipv6_host(self) -> None:
        urls = build_reachable_urls("::1", 8765, ip_list=[])

        self.assertEqual(urls, ["http://[::1]:8765"])

    def test_build_reachable_urls_uses_discovery_only_when_ip_list_is_none(self) -> None:
        with patch(
            "lelamp.dashboard.samplers.network._local_ipv4_addresses",
            return_value=["10.0.0.2"],
        ):
            discovered = build_reachable_urls("0.0.0.0", 8765)
            explicit_empty = build_reachable_urls("0.0.0.0", 8765, ip_list=[])

        self.assertEqual(discovered, ["http://127.0.0.1:8765", "http://10.0.0.2:8765"])
        self.assertEqual(explicit_empty, ["http://127.0.0.1:8765"])

    def test_build_reachable_urls_discovers_local_ips_for_ipv6_wildcard(self) -> None:
        with patch(
            "lelamp.dashboard.samplers.network._local_ipv4_addresses",
            return_value=["10.0.0.2"],
        ):
            discovered = build_reachable_urls("::", 8765)
            explicit_empty = build_reachable_urls("::", 8765, ip_list=[])

        self.assertEqual(discovered, ["http://[::1]:8765", "http://10.0.0.2:8765"])
        self.assertEqual(explicit_empty, ["http://[::1]:8765"])

    def test_collect_audio_snapshot_returns_unknown_when_probe_fails(self) -> None:
        settings = _make_settings()

        def raising_run_command(*args, **kwargs):
            raise RuntimeError("amixer missing")

        snapshot = collect_audio_snapshot(settings, run_command=raising_run_command)

        self.assertEqual(snapshot["status"], "unknown")
        self.assertIsNone(snapshot["output_device"])
        self.assertIsNone(snapshot["volume_percent"])
        self.assertEqual(snapshot["last_result"], "amixer unavailable")

    def test_collect_audio_snapshot_returns_unknown_when_command_exits_non_zero(self) -> None:
        settings = _make_settings()
        result = SimpleNamespace(
            returncode=1,
            stdout="Mono: Playback 64 [64%] [-18.00dB] [on]",
        )

        snapshot = collect_audio_snapshot(
            settings,
            run_command=lambda *args, **kwargs: result,
        )

        self.assertEqual(snapshot["status"], "unknown")
        self.assertIsNone(snapshot["output_device"])
        self.assertIsNone(snapshot["volume_percent"])
        self.assertEqual(snapshot["last_result"], "amixer exited with 1")

    def test_collect_audio_snapshot_returns_unknown_when_volume_parse_fails(self) -> None:
        settings = _make_settings()
        result = SimpleNamespace(returncode=0, stdout="Mono: Playback [on]")

        snapshot = collect_audio_snapshot(
            settings,
            run_command=lambda *args, **kwargs: result,
        )

        self.assertEqual(snapshot["status"], "unknown")
        self.assertIsNone(snapshot["output_device"])
        self.assertIsNone(snapshot["volume_percent"])
        self.assertEqual(snapshot["last_result"], "volume parse failed")

    def test_collect_audio_snapshot_parses_ready_volume(self) -> None:
        settings = _make_settings()
        result = SimpleNamespace(stdout="Mono: Playback 64 [64%] [-18.00dB] [on]")

        snapshot = collect_audio_snapshot(
            settings,
            run_command=lambda *args, **kwargs: result,
        )

        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["output_device"], "Line")
        self.assertEqual(snapshot["volume_percent"], 64)
        self.assertEqual(snapshot["last_result"], "sampled from amixer")

    def test_collect_voice_snapshot_returns_unknown_when_telemetry_is_missing(self) -> None:
        settings = _make_settings(voice_state_path="/tmp/does-not-exist.json")

        snapshot = collect_voice_snapshot(settings)

        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["local_state"], "unknown")
        self.assertEqual(snapshot["last_result"], "voice telemetry unavailable")

    def test_collect_voice_snapshot_reads_voice_telemetry_file(self) -> None:
        settings = _make_settings(voice_state_path="/tmp/test-voice-state.json")

        with patch(
            "lelamp.dashboard.samplers.voice.read_voice_telemetry",
            return_value={
                "status": "ready",
                "local_state": "idle",
                "speech_threshold_db": -47.0,
                "noise_floor_db": -55.0,
                "last_level_db": -58.0,
                "last_asr_status": "ok",
                "last_asr_error_code": None,
                "last_asr_text": "你好",
                "last_reply_text": "我在",
                "last_result": "识别成功",
            },
        ):
            snapshot = collect_voice_snapshot(settings)

        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["local_state"], "idle")
        self.assertEqual(snapshot["speech_threshold_db"], -47.0)
        self.assertEqual(snapshot["noise_floor_db"], -55.0)
        self.assertEqual(snapshot["last_asr_text"], "你好")
        self.assertEqual(snapshot["last_reply_text"], "我在")

    def test_collect_motor_snapshot_marks_missing_port_and_keeps_recording_metadata(self) -> None:
        settings = _make_settings(port="/dev/missing")
        bridge = SimpleNamespace(list_recordings=lambda: ["home_safe", "wave"])

        snapshot = collect_motor_snapshot(
            settings,
            bridge,
            path_exists=lambda path: False,
        )

        self.assertEqual(snapshot["status"], "error")
        self.assertFalse(snapshot["motors_connected"])
        self.assertEqual(snapshot["home_recording"], "home_safe")
        self.assertEqual(snapshot["startup_recording"], "wake_up")
        self.assertEqual(snapshot["available_recordings"], ["home_safe", "wave"])
        self.assertIsNone(snapshot["current_recording"])
        self.assertIsNone(snapshot["last_completed_recording"])
        self.assertIsNone(snapshot["last_result"])

    def test_collect_motor_snapshot_reports_unknown_connectivity_without_hardware_probe(self) -> None:
        settings = _make_settings()
        bridge = SimpleNamespace(list_recordings=lambda: ["home_safe", "wave"])

        snapshot = collect_motor_snapshot(
            settings,
            bridge,
            path_exists=lambda path: True,
        )

        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["motors_connected"], "unknown")
        self.assertEqual(snapshot["available_recordings"], ["home_safe", "wave"])

    def test_collect_motor_snapshot_falls_back_to_empty_recordings_on_probe_error(self) -> None:
        settings = _make_settings()

        snapshot = collect_motor_snapshot(
            settings,
            SimpleNamespace(
                list_recordings=lambda: (_ for _ in ()).throw(RuntimeError("probe failed"))
            ),
            path_exists=lambda path: True,
        )

        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["motors_connected"], "unknown")
        self.assertEqual(snapshot["available_recordings"], [])

    def test_collect_runtime_snapshot_reports_busy_executor_and_reachable_urls(self) -> None:
        settings = _make_settings()
        executor = SimpleNamespace(
            is_busy=lambda: True,
            current_action=lambda: "startup",
        )

        with patch(
            "lelamp.dashboard.samplers.runtime.build_reachable_urls",
            return_value=["http://127.0.0.1:8765", "http://192.168.0.15:8765"],
        ), patch("lelamp.dashboard.samplers.runtime.time", return_value=115.8):
            snapshot = collect_runtime_snapshot(settings, executor, started_at=100.0)

        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["active_action"], "startup")
        self.assertEqual(snapshot["uptime_s"], 15)
        self.assertEqual(snapshot["server_started_at"], 100000)
        self.assertEqual(
            snapshot["reachable_urls"],
            ["http://127.0.0.1:8765", "http://192.168.0.15:8765"],
        )

    def test_collect_runtime_snapshot_uses_active_action_as_single_source_of_truth(self) -> None:
        settings = _make_settings()
        executor = SimpleNamespace(
            is_busy=lambda: False,
            current_action=lambda: "startup",
        )

        with patch(
            "lelamp.dashboard.samplers.runtime.build_reachable_urls",
            return_value=["http://127.0.0.1:8765"],
        ), patch("lelamp.dashboard.samplers.runtime.time", return_value=115.8):
            snapshot = collect_runtime_snapshot(settings, executor, started_at=100.0)

        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["active_action"], "startup")

    def test_dashboard_sampler_loop_patches_store_sections(self) -> None:
        settings = _make_settings(dashboard_poll_ms=50)
        store = DashboardStateStore()
        bridge = SimpleNamespace()
        executor = SimpleNamespace()

        with patch(
            "lelamp.dashboard.samplers.runtime.collect_runtime_snapshot",
            side_effect=[
                {
                    "status": "ready",
                    "active_action": None,
                    "uptime_s": 3,
                    "server_started_at": 1000,
                    "reachable_urls": ["http://127.0.0.1:8765"],
                }
            ],
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_motor_snapshot",
            side_effect=[
                {
                    "status": "error",
                    "current_recording": None,
                    "last_completed_recording": None,
                    "home_recording": "home_safe",
                    "startup_recording": "wake_up",
                    "last_result": None,
                    "motors_connected": False,
                    "calibration_state": "unknown",
                    "available_recordings": [],
                }
            ],
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_audio_snapshot",
            side_effect=[
                {
                    "status": "unknown",
                    "output_device": None,
                    "volume_percent": None,
                    "last_result": "amixer unavailable",
                }
            ],
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_voice_snapshot",
            side_effect=[
                {
                    "status": "ready",
                    "local_state": "idle",
                    "speech_threshold_db": -47.0,
                    "noise_floor_db": -55.0,
                    "last_level_db": -60.0,
                    "last_asr_status": "ok",
                    "last_asr_error_code": None,
                    "last_asr_text": "你好",
                    "last_reply_text": "我在",
                    "last_result": "voice telemetry sampled",
                }
            ],
        ):
            loop = DashboardSamplerLoop(
                store,
                settings,
                bridge,
                executor,
                started_at=1.0,
            )

            loop.start()
            try:
                for _ in range(50):
                    snapshot = store.snapshot()
                    if snapshot["system"]["reachable_urls"]:
                        break
                    loop._thread.join(timeout=0.01)
                else:
                    self.fail("sampler loop did not patch the store")
            finally:
                loop.stop()

        self.assertEqual(loop.interval_s, 0.2)
        self.assertEqual(snapshot["system"]["status"], "ready")
        self.assertEqual(snapshot["motion"]["status"], "error")
        self.assertEqual(snapshot["audio"]["status"], "unknown")
        self.assertEqual(snapshot["voice"]["status"], "ready")

    def test_dashboard_sampler_loop_preserves_error_system_status_when_active_errors_exist(self) -> None:
        settings = _make_settings(dashboard_poll_ms=50)
        store = DashboardStateStore()
        store.record_error("action.shutdown_pose", "motor unavailable", "motion", "error")

        with patch(
            "lelamp.dashboard.samplers.runtime.collect_runtime_snapshot",
            return_value={
                "status": "ready",
                "active_action": None,
                "uptime_s": 3,
                "server_started_at": 1000,
                "reachable_urls": ["http://127.0.0.1:8765"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_motor_snapshot",
            return_value={
                "status": "idle",
                "current_recording": None,
                "last_completed_recording": None,
                "home_recording": "home_safe",
                "startup_recording": "wake_up",
                "last_result": None,
                "motors_connected": "unknown",
                "calibration_state": "unknown",
                "available_recordings": ["home_safe"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_audio_snapshot",
            return_value={
                "status": "ready",
                "output_device": "Line",
                "volume_percent": 64,
                "last_result": "sampled from amixer",
            },
        ):
            loop = DashboardSamplerLoop(
                store,
                settings,
                SimpleNamespace(),
                SimpleNamespace(),
                started_at=1.0,
            )

            loop.start()
            try:
                for _ in range(100):
                    snapshot = store.snapshot()
                    if snapshot["system"]["reachable_urls"]:
                        break
                    loop._thread.join(timeout=0.01)
                else:
                    self.fail("sampler loop did not patch system fields")
            finally:
                loop.stop()

        self.assertEqual(snapshot["system"]["status"], "error")
        self.assertIsNone(snapshot["system"]["active_action"])

    def test_dashboard_sampler_loop_recovers_after_sampler_exception(self) -> None:
        settings = _make_settings(dashboard_poll_ms=50)
        store = DashboardStateStore()

        with patch(
            "lelamp.dashboard.samplers.runtime.collect_runtime_snapshot",
            return_value={
                "status": "ready",
                "active_action": None,
                "uptime_s": 4,
                "server_started_at": 1000,
                "reachable_urls": ["http://127.0.0.1:8765"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_motor_snapshot",
            return_value={
                "status": "idle",
                "current_recording": None,
                "last_completed_recording": None,
                "home_recording": "home_safe",
                "startup_recording": "wake_up",
                "last_result": None,
                "motors_connected": True,
                "calibration_state": "unknown",
                "available_recordings": ["home_safe"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_audio_snapshot",
            side_effect=[
                RuntimeError("probe failed"),
                {
                    "status": "ready",
                    "output_device": "Line",
                    "volume_percent": 64,
                    "last_result": "sampled from amixer",
                },
            ],
        ):
            loop = DashboardSamplerLoop(
                store,
                settings,
                SimpleNamespace(),
                SimpleNamespace(),
                started_at=1.0,
            )

            loop.start()
            try:
                for _ in range(100):
                    snapshot = store.snapshot()
                    if snapshot["audio"]["status"] == "ready":
                        break
                    loop._thread.join(timeout=0.01)
                else:
                    self.fail("sampler loop did not recover after sampler exception")
            finally:
                loop.stop()

        self.assertEqual(snapshot["system"]["status"], "ready")
        self.assertEqual(snapshot["motion"]["status"], "idle")
        self.assertEqual(snapshot["audio"]["status"], "ready")

    def test_dashboard_sampler_loop_resets_system_fields_after_runtime_failure(self) -> None:
        settings = _make_settings(dashboard_poll_ms=50)
        store = DashboardStateStore()

        with patch(
            "lelamp.dashboard.samplers.runtime.collect_runtime_snapshot",
            side_effect=[
                {
                    "status": "ready",
                    "active_action": "startup",
                    "uptime_s": 9,
                    "server_started_at": 999,
                    "reachable_urls": ["http://127.0.0.1:8765"],
                },
                RuntimeError("runtime probe failed"),
            ],
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_motor_snapshot",
            return_value={
                "status": "idle",
                "current_recording": None,
                "last_completed_recording": None,
                "home_recording": "home_safe",
                "startup_recording": "wake_up",
                "last_result": None,
                "motors_connected": True,
                "calibration_state": "unknown",
                "available_recordings": ["home_safe"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_audio_snapshot",
            return_value={
                "status": "ready",
                "output_device": "Line",
                "volume_percent": 64,
                "last_result": "sampled from amixer",
            },
        ), patch("lelamp.dashboard.samplers.runtime.time", return_value=42.0):
            loop = DashboardSamplerLoop(
                store,
                settings,
                SimpleNamespace(),
                SimpleNamespace(),
                started_at=1.0,
            )

            loop.start()
            try:
                saw_running = False
                for _ in range(100):
                    snapshot = store.snapshot()
                    if not saw_running and snapshot["system"]["status"] == "running":
                        saw_running = True
                    if saw_running and snapshot["system"]["status"] == "unknown":
                        break
                    loop._thread.join(timeout=0.01)
                else:
                    self.fail("sampler loop did not apply runtime fallback")
            finally:
                loop.stop()

        self.assertEqual(snapshot["system"]["status"], "unknown")
        self.assertIsNone(snapshot["system"]["active_action"])
        self.assertEqual(snapshot["system"]["uptime_s"], 41)
        self.assertEqual(snapshot["system"]["server_started_at"], 1000)
        self.assertEqual(snapshot["system"]["reachable_urls"], [])

    def test_dashboard_sampler_loop_does_not_erase_action_owned_motion_fields(self) -> None:
        settings = _make_settings(dashboard_poll_ms=50)
        store = DashboardStateStore()
        store.patch(
            "motion",
            {
                "status": "running",
                "current_recording": "startup",
                "last_completed_recording": "curious",
                "last_result": "in progress",
            },
        )

        with patch(
            "lelamp.dashboard.samplers.runtime.collect_runtime_snapshot",
            return_value={
                "status": "ready",
                "active_action": None,
                "uptime_s": 4,
                "server_started_at": 1000,
                "reachable_urls": ["http://127.0.0.1:8765"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_motor_snapshot",
            return_value={
                "status": "idle",
                "current_recording": None,
                "last_completed_recording": None,
                "last_result": None,
                "home_recording": "home_safe",
                "startup_recording": "wake_up",
                "motors_connected": True,
                "calibration_state": "unknown",
                "available_recordings": ["home_safe"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_audio_snapshot",
            return_value={
                "status": "ready",
                "output_device": "Line",
                "volume_percent": 64,
                "last_result": "sampled from amixer",
            },
        ):
            loop = DashboardSamplerLoop(
                store,
                settings,
                SimpleNamespace(),
                SimpleNamespace(),
                started_at=1.0,
            )

            loop.start()
            try:
                for _ in range(100):
                    snapshot = store.snapshot()
                    if snapshot["motion"]["motors_connected"] is True:
                        break
                    loop._thread.join(timeout=0.01)
                else:
                    self.fail("sampler loop did not patch motion hardware fields")
            finally:
                loop.stop()

        self.assertEqual(snapshot["motion"]["status"], "running")
        self.assertEqual(snapshot["motion"]["current_recording"], "startup")
        self.assertEqual(snapshot["motion"]["last_completed_recording"], "curious")
        self.assertEqual(snapshot["motion"]["last_result"], "in progress")
        self.assertEqual(snapshot["motion"]["motors_connected"], True)
        self.assertEqual(snapshot["motion"]["available_recordings"], ["home_safe"])

    def test_dashboard_sampler_loop_does_not_advertise_loopback_for_explicit_host_bind(self) -> None:
        settings = _make_settings(dashboard_host="192.168.0.15", dashboard_port=8765)
        snapshot = collect_runtime_snapshot(
            settings,
            SimpleNamespace(is_busy=lambda: False, current_action=lambda: None),
            started_at=1.0,
        )

        self.assertEqual(snapshot["reachable_urls"], ["http://192.168.0.15:8765"])

    def test_dashboard_sampler_loop_recovers_after_store_patch_exception(self) -> None:
        settings = _make_settings(dashboard_poll_ms=50)
        flaky_store = _FlakyPatchStore()

        with patch(
            "lelamp.dashboard.samplers.runtime.collect_runtime_snapshot",
            return_value={
                "status": "ready",
                "active_action": None,
                "uptime_s": 4,
                "server_started_at": 1000,
                "reachable_urls": ["http://127.0.0.1:8765"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_motor_snapshot",
            return_value={
                "status": "idle",
                "current_recording": None,
                "last_completed_recording": None,
                "home_recording": "home_safe",
                "startup_recording": "wake_up",
                "last_result": None,
                "motors_connected": True,
                "calibration_state": "unknown",
                "available_recordings": ["home_safe"],
            },
        ), patch(
            "lelamp.dashboard.samplers.runtime.collect_audio_snapshot",
            return_value={
                "status": "ready",
                "output_device": "Line",
                "volume_percent": 64,
                "last_result": "sampled from amixer",
            },
        ):
            loop = DashboardSamplerLoop(
                flaky_store,
                settings,
                SimpleNamespace(),
                SimpleNamespace(),
                started_at=1.0,
            )

            loop.start()
            try:
                for _ in range(100):
                    snapshot = flaky_store.snapshot()
                    if snapshot["audio"]["status"] == "ready":
                        break
                    loop._thread.join(timeout=0.01)
                else:
                    self.fail("sampler loop did not recover after store patch exception")
            finally:
                loop.stop()

        self.assertEqual(snapshot["system"]["status"], "ready")
        self.assertEqual(snapshot["audio"]["status"], "ready")
        self.assertEqual(snapshot["motion"]["motors_connected"], True)

class _FlakyPatchStore:
    def __init__(self) -> None:
        self._store = DashboardStateStore()
        self._failed_audio_patch = False

    def patch(self, section, values):
        if section == "audio" and not self._failed_audio_patch:
            self._failed_audio_patch = True
            raise RuntimeError("patch failed")
        return self._store.patch(section, values)

    def patch_with(self, section, updater):
        return self._store.patch_with(section, updater)

    def reconcile_system(self, values):
        return self._store.reconcile_system(values)

    def snapshot(self):
        return self._store.snapshot()


if __name__ == "__main__":
    unittest.main()
