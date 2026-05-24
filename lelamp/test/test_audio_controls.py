import unittest
from unittest.mock import patch

from lelamp.audio_controls import build_amixer_volume_commands


class AudioControlsTests(unittest.TestCase):
    @patch("lelamp.audio_controls.os.geteuid", return_value=0)
    @patch("lelamp.audio_controls.getpass.getuser", return_value="root")
    def test_build_amixer_volume_commands_uses_sudo_when_switching_users(self, *_mocks) -> None:
        commands = build_amixer_volume_commands(
            audio_user="wujiajun",
            card_index=2,
            volume_percent=100,
        )

        self.assertEqual(
            commands,
            [
                ["sudo", "-u", "wujiajun", "amixer", "-c", "2", "sset", "PCM", "100%"],
                ["sudo", "-u", "wujiajun", "amixer", "-c", "2", "sset", "Line", "100%"],
                ["sudo", "-u", "wujiajun", "amixer", "-c", "2", "sset", "Line", "unmute"],
                ["sudo", "-u", "wujiajun", "amixer", "-c", "2", "sset", "Line DAC", "100%"],
                ["sudo", "-u", "wujiajun", "amixer", "-c", "2", "sset", "HP", "100%"],
                ["sudo", "-u", "wujiajun", "amixer", "-c", "2", "sset", "HP", "unmute"],
                ["sudo", "-u", "wujiajun", "amixer", "-c", "2", "sset", "HP DAC", "100%"],
            ],
        )

    @patch("lelamp.audio_controls.os.geteuid", return_value=1000)
    @patch("lelamp.audio_controls.getpass.getuser", return_value="wujiajun")
    def test_build_amixer_volume_commands_skips_sudo_for_same_user_service(self, *_mocks) -> None:
        commands = build_amixer_volume_commands(
            audio_user="wujiajun",
            card_index=2,
            volume_percent=100,
        )

        self.assertEqual(
            commands,
            [
                ["amixer", "-c", "2", "sset", "PCM", "100%"],
                ["amixer", "-c", "2", "sset", "Line", "100%"],
                ["amixer", "-c", "2", "sset", "Line", "unmute"],
                ["amixer", "-c", "2", "sset", "Line DAC", "100%"],
                ["amixer", "-c", "2", "sset", "HP", "100%"],
                ["amixer", "-c", "2", "sset", "HP", "unmute"],
                ["amixer", "-c", "2", "sset", "HP DAC", "100%"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
