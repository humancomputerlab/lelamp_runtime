import unittest

from lelamp.local_voice import (
    AmbientNoiseCalibrator,
    LocalTurnDetector,
    OutputSuppressionGate,
    choose_preferred_device,
    choose_first_available_device,
    install_console_audio_patch,
    is_resolved_device_usable,
    resolve_console_devices,
    should_suppress_console_input,
)


class LocalTurnDetectorTests(unittest.TestCase):
    def test_default_detector_handles_moderate_voice_levels(self) -> None:
        detector = LocalTurnDetector()

        self.assertIsNone(detector.update(-50.0, 0.0))
        self.assertIsNone(detector.update(-48.0, 0.2))
        self.assertIsNone(detector.update(-51.0, 0.35))
        self.assertIsNone(detector.update(-72.0, 0.6))
        self.assertEqual(detector.update(-75.0, 0.8), "commit")

    def test_detector_commits_after_sustained_speech_and_silence(self) -> None:
        detector = LocalTurnDetector(
            speech_threshold_db=-42.0,
            silence_duration_s=0.6,
            min_speech_duration_s=0.3,
            commit_cooldown_s=0.5,
        )

        self.assertIsNone(detector.update(-60.0, 0.0))
        self.assertIsNone(detector.update(-30.0, 0.1))
        self.assertIsNone(detector.update(-28.0, 0.3))
        self.assertIsNone(detector.update(-29.0, 0.45))
        self.assertIsNone(detector.update(-50.0, 0.8))
        self.assertEqual(detector.update(-55.0, 1.1), "commit")

    def test_detector_clears_short_noise_without_commit(self) -> None:
        detector = LocalTurnDetector(
            speech_threshold_db=-42.0,
            silence_duration_s=0.5,
            min_speech_duration_s=0.3,
            commit_cooldown_s=0.5,
            speech_start_duration_s=0.1,
        )

        self.assertIsNone(detector.update(-35.0, 0.0))
        self.assertIsNone(detector.update(-35.0, 0.12))
        self.assertIsNone(detector.update(-55.0, 0.25))
        self.assertEqual(detector.update(-60.0, 0.8), "clear")

    def test_detector_respects_cooldown_before_rearming(self) -> None:
        detector = LocalTurnDetector(
            speech_threshold_db=-42.0,
            silence_duration_s=0.4,
            min_speech_duration_s=0.2,
            commit_cooldown_s=0.5,
            speech_start_duration_s=0.1,
        )

        detector.update(-30.0, 0.0)
        detector.update(-30.0, 0.15)
        detector.update(-30.0, 0.35)
        detector.update(-60.0, 0.6)
        self.assertEqual(detector.update(-60.0, 0.8), "commit")

        self.assertIsNone(detector.update(-30.0, 0.9))
        self.assertIsNone(detector.update(-30.0, 1.1))
        self.assertIsNone(detector.update(-55.0, 1.3))
        self.assertIsNone(detector.update(-30.0, 1.5))

    def test_detector_ignores_single_frame_noise_spike_before_start_window(self) -> None:
        detector = LocalTurnDetector(
            speech_threshold_db=-42.0,
            silence_duration_s=0.4,
            min_speech_duration_s=0.2,
            commit_cooldown_s=0.5,
            speech_start_duration_s=0.12,
        )

        self.assertIsNone(detector.update(-34.0, 0.0))
        self.assertIsNone(detector.update(-60.0, 0.05))
        self.assertIsNone(detector.update(-62.0, 0.7))


class AmbientNoiseCalibratorTests(unittest.TestCase):
    def test_calibrator_raises_threshold_from_noise_floor_and_marks_ready(self) -> None:
        calibrator = AmbientNoiseCalibrator(
            baseline_threshold_db=-48.0,
            calibration_duration_s=1.0,
            calibration_margin_db=8.0,
            max_threshold_db=-42.0,
        )

        self.assertIsNone(calibrator.observe(-56.0, 0.0))
        self.assertIsNone(calibrator.observe(-55.0, 0.25))
        self.assertIsNone(calibrator.observe(-54.0, 0.5))
        self.assertIsNone(calibrator.observe(-53.0, 0.75))
        result = calibrator.observe(-52.0, 1.0)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["noise_floor_db"], -54.0)
        self.assertAlmostEqual(result["speech_threshold_db"], -46.0)

    def test_calibrator_can_lower_threshold_in_quiet_room(self) -> None:
        calibrator = AmbientNoiseCalibrator(
            baseline_threshold_db=-52.0,
            calibration_duration_s=1.0,
            calibration_margin_db=8.0,
        )

        self.assertIsNone(calibrator.observe(-78.0, 0.0))
        self.assertIsNone(calibrator.observe(-77.0, 0.25))
        self.assertIsNone(calibrator.observe(-76.0, 0.5))
        self.assertIsNone(calibrator.observe(-75.0, 0.75))
        result = calibrator.observe(-74.0, 1.0)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["noise_floor_db"], -76.0)
        self.assertAlmostEqual(result["speech_threshold_db"], -66.0)


class PreferredDeviceTests(unittest.TestCase):
    def test_choose_preferred_device_picks_seeed_output(self) -> None:
        devices = [
            {"name": "Built-in Audio", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "seeed-2mic-voicecard", "max_output_channels": 2, "max_input_channels": 2},
        ]

        index = choose_preferred_device(
            devices,
            output=True,
            preferred_keywords=("seeed", "respeaker"),
        )

        self.assertEqual(index, 1)

    def test_choose_preferred_device_returns_none_when_capability_missing(self) -> None:
        devices = [
            {"name": "seeed-2mic-voicecard", "max_output_channels": 0, "max_input_channels": 2},
        ]

        index = choose_preferred_device(
            devices,
            output=True,
            preferred_keywords=("seeed",),
        )

        self.assertIsNone(index)

    def test_resolve_console_devices_prefers_specific_hardware_over_generic_default(self) -> None:
        devices = [
            {"name": "Built-in Audio", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "seeed-2mic-voicecard", "max_output_channels": 2, "max_input_channels": 2},
            {"name": "default", "max_output_channels": 32, "max_input_channels": 32},
        ]

        resolved = resolve_console_devices(
            current_devices=(2, 2),
            devices=devices,
        )

        self.assertEqual(resolved, (1, 1))

    def test_resolve_console_devices_prefers_explicit_alsa_plugins_over_generic_default(self) -> None:
        devices = [
            {"name": "dmixer", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "dsnoop0", "max_output_channels": 0, "max_input_channels": 2},
            {"name": "default", "max_output_channels": 32, "max_input_channels": 32},
        ]

        resolved = resolve_console_devices(
            current_devices=(2, 2),
            devices=devices,
        )

        self.assertEqual(resolved, (1, 0))

    def test_choose_first_available_device_skips_unstable_raw_nodes(self) -> None:
        devices = [
            {"name": "dsnoop0_raw", "max_output_channels": 0, "max_input_channels": 2},
            {"name": "default", "max_output_channels": 32, "max_input_channels": 32},
        ]

        index = choose_first_available_device(
            devices,
            output=False,
            avoid_keywords=("dsnoop", "raw"),
        )

        self.assertEqual(index, 1)

    def test_is_resolved_device_usable_rejects_unstable_raw_defaults(self) -> None:
        devices = [
            {"name": "dsnoop0_raw", "max_output_channels": 0, "max_input_channels": 2},
            {"name": "default", "max_output_channels": 32, "max_input_channels": 32},
        ]

        self.assertFalse(
            is_resolved_device_usable(
                devices,
                index=0,
                output=False,
                avoid_keywords=("dsnoop", "raw"),
            )
        )
        self.assertTrue(
            is_resolved_device_usable(
                devices,
                index=1,
                output=False,
                avoid_keywords=("dsnoop", "raw"),
            )
        )

    def test_resolve_console_devices_replaces_unstable_input_default(self) -> None:
        devices = [
            {"name": "dsnoop0_raw", "max_output_channels": 0, "max_input_channels": 2},
            {"name": "default", "max_output_channels": 32, "max_input_channels": 32},
        ]

        resolved = resolve_console_devices(
            current_devices=(0, 1),
            devices=devices,
        )

        self.assertEqual(resolved, (1, 1))

    def test_resolve_console_devices_falls_back_to_preferred_when_missing(self) -> None:
        devices = [
            {"name": "Built-in Audio", "max_output_channels": 2, "max_input_channels": 0},
            {"name": "seeed-2mic-voicecard", "max_output_channels": 2, "max_input_channels": 2},
        ]

        resolved = resolve_console_devices(
            current_devices=(-1, -1),
            devices=devices,
        )

        self.assertEqual(resolved, (1, 1))

    def test_install_console_audio_patch_can_be_applied_multiple_times(self) -> None:
        install_console_audio_patch(enable_apm=False)
        install_console_audio_patch(enable_apm=True)


class OutputSuppressionGateTests(unittest.TestCase):
    def test_gate_suppresses_input_for_configured_window(self) -> None:
        gate = OutputSuppressionGate(suppression_seconds=0.35)

        gate.mark_output(1.0)

        self.assertTrue(gate.should_suppress(1.1))
        self.assertFalse(gate.should_suppress(1.36))

    def test_gate_extends_window_when_output_continues(self) -> None:
        gate = OutputSuppressionGate(suppression_seconds=0.35)

        gate.mark_output(1.0)
        gate.mark_output(1.2)

        self.assertTrue(gate.should_suppress(1.45))
        self.assertFalse(gate.should_suppress(1.56))

    def test_playback_in_progress_always_suppresses_console_input(self) -> None:
        gate = OutputSuppressionGate(suppression_seconds=0.35)

        self.assertTrue(
            should_suppress_console_input(
                playback_in_progress=True,
                output_gate=gate,
                now=10.0,
            )
        )

    def test_recent_output_tail_suppresses_console_input(self) -> None:
        gate = OutputSuppressionGate(suppression_seconds=0.35)
        gate.mark_output(1.0)

        self.assertTrue(
            should_suppress_console_input(
                playback_in_progress=False,
                output_gate=gate,
                now=1.2,
            )
        )
        self.assertFalse(
            should_suppress_console_input(
                playback_in_progress=False,
                output_gate=gate,
                now=1.5,
            )
        )


if __name__ == "__main__":
    unittest.main()
