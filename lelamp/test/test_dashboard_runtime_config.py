import os
import unittest
from unittest.mock import patch

from lelamp.runtime_config import load_runtime_settings


class DashboardRuntimeConfigTests(unittest.TestCase):
    def test_load_runtime_settings_includes_dashboard_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        self.assertEqual(settings.dashboard_host, "127.0.0.1")
        self.assertEqual(settings.dashboard_port, 8765)
        self.assertEqual(settings.dashboard_poll_ms, 400)
        self.assertFalse(settings.dashboard_expose_transcripts)

    def test_load_runtime_settings_supports_dashboard_env_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LELAMP_DASHBOARD_HOST": "127.0.0.1",
                "LELAMP_DASHBOARD_PORT": "9876",
                "LELAMP_DASHBOARD_POLL_MS": "250",
                "LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS": "true",
            },
            clear=True,
        ):
            settings = load_runtime_settings()

        self.assertEqual(settings.dashboard_host, "127.0.0.1")
        self.assertEqual(settings.dashboard_port, 9876)
        self.assertEqual(settings.dashboard_poll_ms, 250)
        self.assertTrue(settings.dashboard_expose_transcripts)

    def test_load_runtime_settings_rejects_non_integer_dashboard_port(self) -> None:
        with patch.dict(os.environ, {"LELAMP_DASHBOARD_PORT": "invalid"}, clear=True):
            with self.assertRaisesRegex(
                ValueError,
                "LELAMP_DASHBOARD_PORT must be an integer",
            ):
                load_runtime_settings()

    def test_load_runtime_settings_rejects_non_integer_dashboard_poll_ms(self) -> None:
        with patch.dict(os.environ, {"LELAMP_DASHBOARD_POLL_MS": "invalid"}, clear=True):
            with self.assertRaisesRegex(
                ValueError,
                "LELAMP_DASHBOARD_POLL_MS must be an integer",
            ):
                load_runtime_settings()

    def test_load_runtime_settings_rejects_non_positive_dashboard_port(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"LELAMP_DASHBOARD_PORT": value}, clear=True):
                    with self.assertRaisesRegex(
                        ValueError,
                        "LELAMP_DASHBOARD_PORT must be greater than 0",
                    ):
                        load_runtime_settings()

    def test_load_runtime_settings_rejects_non_positive_dashboard_poll_ms(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"LELAMP_DASHBOARD_POLL_MS": value}, clear=True):
                    with self.assertRaisesRegex(
                        ValueError,
                        "LELAMP_DASHBOARD_POLL_MS must be greater than 0",
                    ):
                        load_runtime_settings()

    def test_load_runtime_settings_rejects_invalid_dashboard_transcript_flag(self) -> None:
        with patch.dict(
            os.environ,
            {"LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS": "sometimes"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS must be a boolean",
            ):
                load_runtime_settings()


if __name__ == "__main__":
    unittest.main()
