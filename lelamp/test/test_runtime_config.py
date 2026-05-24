import os
import sys
import types
import unittest
from unittest.mock import patch

try:
    from livekit import plugins as _livekit_plugins  # noqa: F401
except Exception:
    fake_livekit = types.ModuleType("livekit")
    fake_plugins = types.ModuleType("livekit.plugins")
    fake_plugins.openai = object()
    fake_livekit.plugins = fake_plugins
    sys.modules["livekit"] = fake_livekit
    sys.modules["livekit.plugins"] = fake_plugins

from lelamp.runtime_config import build_realtime_model_config, load_runtime_settings


class RuntimeConfigTests(unittest.TestCase):
    def test_motion_defaults_point_to_home_safe_relative_flow(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        self.assertEqual(settings.idle_recording, "home_safe")
        self.assertEqual(settings.home_recording, "home_safe")
        self.assertTrue(settings.use_home_pose_relative)

    def test_rgb_enabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        self.assertTrue(settings.enable_rgb)

    def test_rgb_can_be_disabled_by_env(self) -> None:
        with patch.dict(os.environ, {"LELAMP_ENABLE_RGB": "false"}, clear=True):
            settings = load_runtime_settings()

        self.assertFalse(settings.enable_rgb)

    def test_home_pose_relative_flags_can_be_loaded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LELAMP_IDLE_RECORDING": "home_safe",
                "LELAMP_HOME_RECORDING": "home_safe",
                "LELAMP_USE_HOME_POSE_RELATIVE": "true",
            },
            clear=True,
        ):
            settings = load_runtime_settings()

        self.assertEqual(settings.home_recording, "home_safe")
        self.assertTrue(settings.use_home_pose_relative)

    def test_glm_defaults_to_realtime_endpoint(self) -> None:
        with patch.dict(os.environ, {"MODEL_PROVIDER": "glm"}, clear=True):
            settings = load_runtime_settings()

        self.assertEqual(
            settings.model_base_url,
            "https://open.bigmodel.cn/api/paas/v4/realtime",
        )
        self.assertFalse(settings.glm_use_server_vad)
        self.assertEqual(settings.audio_card_index, 2)
        self.assertFalse(settings.console_enable_apm)
        self.assertEqual(settings.console_speech_threshold_db, -54.0)
        self.assertEqual(settings.console_silence_duration_s, 0.4)
        self.assertEqual(settings.console_min_speech_duration_s, 0.2)
        self.assertEqual(settings.console_commit_cooldown_s, 0.75)
        self.assertEqual(settings.console_output_suppression_s, 0.35)
        self.assertTrue(settings.console_auto_calibrate)
        self.assertEqual(settings.console_calibration_duration_s, 1.6)
        self.assertEqual(settings.console_calibration_margin_db, 8.0)
        self.assertEqual(settings.console_start_trigger_s, 0.08)
        self.assertEqual(settings.voice_state_path, "/tmp/lelamp-voice-state.json")

    def test_qwen_defaults_to_dashscope_realtime_endpoint(self) -> None:
        with patch.dict(os.environ, {"MODEL_PROVIDER": "qwen"}, clear=True):
            settings = load_runtime_settings()

        self.assertEqual(
            settings.model_base_url,
            "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
        )
        self.assertEqual(settings.model_name, "qwen3.5-omni-flash-realtime")
        self.assertEqual(settings.model_voice, "Tina")

    def test_qwen_is_selected_when_dashscope_key_is_present(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "dashscope-test-key"}, clear=True):
            settings = load_runtime_settings()

        self.assertEqual(settings.model_provider, "qwen")
        self.assertEqual(settings.model_api_key, "dashscope-test-key")

    def test_qwen_prefers_dashscope_key_over_other_provider_keys(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "qwen",
                "DASHSCOPE_API_KEY": "dashscope-test-key",
                "OPENAI_API_KEY": "openai-test-key",
                "ZAI_API_KEY": "glm-test-key",
            },
            clear=True,
        ):
            settings = load_runtime_settings()

        self.assertEqual(settings.model_api_key, "dashscope-test-key")

    def test_console_vad_thresholds_can_be_loaded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LELAMP_CONSOLE_SPEECH_THRESHOLD_DB": "-52.5",
                "LELAMP_CONSOLE_SILENCE_DURATION_S": "0.35",
                "LELAMP_CONSOLE_MIN_SPEECH_DURATION_S": "0.15",
                "LELAMP_CONSOLE_COMMIT_COOLDOWN_S": "1.1",
                "LELAMP_CONSOLE_OUTPUT_SUPPRESSION_S": "0.5",
                "LELAMP_CONSOLE_AUTO_CALIBRATE": "false",
                "LELAMP_CONSOLE_CALIBRATION_DURATION_S": "2.4",
                "LELAMP_CONSOLE_CALIBRATION_MARGIN_DB": "6.5",
                "LELAMP_CONSOLE_START_TRIGGER_S": "0.12",
                "LELAMP_VOICE_STATE_PATH": "/tmp/custom-voice-state.json",
            },
            clear=True,
        ):
            settings = load_runtime_settings()

        self.assertEqual(settings.console_speech_threshold_db, -52.5)
        self.assertEqual(settings.console_silence_duration_s, 0.35)
        self.assertEqual(settings.console_min_speech_duration_s, 0.15)
        self.assertEqual(settings.console_commit_cooldown_s, 1.1)
        self.assertEqual(settings.console_output_suppression_s, 0.5)
        self.assertFalse(settings.console_auto_calibrate)
        self.assertEqual(settings.console_calibration_duration_s, 2.4)
        self.assertEqual(settings.console_calibration_margin_db, 6.5)
        self.assertEqual(settings.console_start_trigger_s, 0.12)
        self.assertEqual(settings.voice_state_path, "/tmp/custom-voice-state.json")

    def test_explicit_custom_model_base_url_is_preserved(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "custom",
                "MODEL_BASE_URL": "http://127.0.0.1:8000/v1/realtime",
                "MODEL_NAME": "local-realtime",
            },
            clear=True,
        ):
            settings = load_runtime_settings()
            config = build_realtime_model_config(settings)

        self.assertEqual(settings.model_base_url, "http://127.0.0.1:8000/v1/realtime")
        self.assertEqual(config["base_url"], "http://127.0.0.1:8000/v1/realtime")
        self.assertEqual(config["model"], "local-realtime")


if __name__ == "__main__":
    unittest.main()
