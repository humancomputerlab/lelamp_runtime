import unittest

from lelamp.reply_sanitizer import sanitize_spoken_reply


class ReplySanitizerTests(unittest.TestCase):
    def test_sanitizes_light_narration_into_clean_dialogue(self) -> None:
        self.assertEqual(
            sanitize_spoken_reply("那我给你亮个节奏灯，你弹你的，我跟着晃。不过别太吵。"),
            "你弹你的，我陪着你。不过别太吵。",
        )

    def test_removes_bracketed_stage_directions(self) -> None:
        self.assertEqual(
            sanitize_spoken_reply("哼。(shock + 白光)你又来了？"),
            "哼。你又来了？",
        )


if __name__ == "__main__":
    unittest.main()
