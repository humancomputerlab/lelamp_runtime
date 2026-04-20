import unittest
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


class BootServiceTests(unittest.TestCase):
    def test_service_example_uses_local_console_mode(self) -> None:
        unit = (ROOT / "scripts" / "lelamp.service.example").read_text(encoding="utf-8")

        self.assertIn("smooth_animation.py console", unit)
        self.assertNotIn("smooth_animation.py start", unit)
        self.assertIn("User=pi", unit)
        self.assertNotIn("User=root", unit)

    def test_pi_setup_max_installs_console_mode_service(self) -> None:
        script = (ROOT / "scripts" / "pi_setup_max.sh").read_text(encoding="utf-8")

        self.assertIn("${UV_BIN} run ${MODE_SCRIPT} console", script)
        self.assertNotIn("${UV_BIN} run ${MODE_SCRIPT} start", script)
        self.assertIn("User=${SERVICE_USER}", script)
        self.assertIn("KERNEL==\"leds0\", MODE=\"0660\", GROUP=\"gpio\"", script)

    def test_all_in_one_console_boot_gate_does_not_require_livekit_credentials(self) -> None:
        script = (ROOT / "scripts" / "pi5_all_in_one.sh").read_text(encoding="utf-8")
        match = re.search(
            r"has_voice_runtime_config\(\) \{\n(?P<body>.*?)\n\}",
            script,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "has_voice_runtime_config() should exist")
        body = match.group("body")

        self.assertNotIn("LIVEKIT_URL", body)
        self.assertNotIn("LIVEKIT_API_KEY", body)
        self.assertNotIn("LIVEKIT_API_SECRET", body)
        self.assertIn('if [[ -n "$MODEL_API_KEY" ]]; then', body)

    def test_post_boot_service_persists_uv_bin_for_root_finalize_step(self) -> None:
        all_in_one = (ROOT / "scripts" / "pi5_all_in_one.sh").read_text(encoding="utf-8")
        finalize = (ROOT / "scripts" / "pi5_post_reboot_finalize.sh").read_text(encoding="utf-8")

        self.assertIn("UV_BIN=", all_in_one)
        self.assertIn('UV_BIN="${UV_BIN:-$(command -v uv || true)}"', finalize)


if __name__ == "__main__":
    unittest.main()
