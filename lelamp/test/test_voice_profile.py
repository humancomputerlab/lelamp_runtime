import os
import unittest
from unittest.mock import patch

from lelamp.runtime_config import load_runtime_settings
from lelamp.voice_profile import (
    build_agent_instructions,
    build_startup_reply_instructions,
)


class VoiceProfileTests(unittest.TestCase):
    def test_defaults_to_chinese_voice_profile(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)
        startup = build_startup_reply_instructions(settings)

        self.assertEqual(settings.agent_language, "zh-CN")
        self.assertIn("刚搬来的室友", instructions)
        self.assertIn("不是助手，不是工具", instructions)
        self.assertIn("灯灯醒了", startup)

    def test_english_profile_can_be_enabled_by_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LELAMP_AGENT_LANGUAGE": "en",
                "LELAMP_AGENT_OPENING_LINE": "Tadaaaa, I'm awake.",
            },
            clear=True,
        ):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)
        startup = build_startup_reply_instructions(settings)

        self.assertIn("desk lamp with attitude", instructions)
        self.assertIn("Not an assistant, not a tool", instructions)
        self.assertIn("Tadaaaa, I'm awake.", startup)

    def test_chinese_profile_guides_multi_action_demo_requests(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)

        self.assertIn("演示", instructions)
        self.assertIn("继续", instructions)
        self.assertIn("连续", instructions)

    def test_chinese_profile_defaults_to_probabilistic_expression_engine(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)

        self.assertIn("每次回复前", instructions)
        self.assertIn("15% 只说话", instructions)
        self.assertIn("35% 说话 + 灯光", instructions)
        self.assertIn("情绪越明显", instructions)

    def test_chinese_profile_executes_safe_expression_without_confirmation(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)

        self.assertIn("直接执行", instructions)
        self.assertIn("不要先问用户要不要", instructions)

    def test_chinese_profile_keeps_actions_and_lights_off_mic(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)

        self.assertIn("不要口头播报", instructions)
        self.assertIn("不要复述自己刚刚执行了哪个动作", instructions)
        self.assertIn("不要说“我给你亮个节奏灯”", instructions)
        self.assertIn("默认把外显留给系统", instructions)

    def test_chinese_profile_has_dedicated_tool_policy_section(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)

        self.assertIn("## 工具决策策略", instructions)
        self.assertIn("优先直接调用工具", instructions)

    def test_chinese_profile_bans_spoken_stage_directions(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        instructions = build_agent_instructions(settings)

        self.assertIn("不要输出像“(shock + 白光)”这样的舞台提示", instructions)
        self.assertNotIn("关心某人 → shy + 暖黄光", instructions)

    def test_memory_header_is_prepended_before_voice_profile(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        with patch(
            "lelamp.voice_profile.build_memory_header",
            create=True,
            return_value='<memory user_id="default">remember this</memory>',
        ):
            instructions = build_agent_instructions(settings)

        self.assertTrue(
            instructions.startswith('<memory user_id="default">remember this</memory>\n\n')
        )
        self.assertIn("刚搬来的室友", instructions)


if __name__ == "__main__":
    unittest.main()
