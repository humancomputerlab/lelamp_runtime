import unittest
from unittest.mock import patch


class AutoExpressionTests(unittest.TestCase):
    def test_infer_expression_style_prefers_worried_for_complaint_transcript(self) -> None:
        from lelamp.auto_expression import infer_expression_style

        style = infer_expression_style("抢不过啊，太烦了，他一直黏着我。")

        self.assertEqual(style, "worried")

    def test_pick_fallback_expression_waits_then_dispatches_or_skips(self) -> None:
        from lelamp.auto_expression import pick_fallback_expression

        snapshot = {
            "last_asr_status": "ok",
            "last_asr_text": "我回来了，快看我这波。",
            "last_commit_at_ms": 1000,
        }

        self.assertIsNone(
            pick_fallback_expression(
                snapshot=snapshot,
                now_ms=1200,
                handled_commit_at_ms=0,
                last_tool_dispatch_at_ms=0,
                delay_ms=350,
            )
        )
        self.assertEqual(
            pick_fallback_expression(
                snapshot=snapshot,
                now_ms=1400,
                handled_commit_at_ms=0,
                last_tool_dispatch_at_ms=0,
                delay_ms=350,
            ),
            (1000, "greeting"),
        )
        self.assertEqual(
            pick_fallback_expression(
                snapshot=snapshot,
                now_ms=1400,
                handled_commit_at_ms=0,
                last_tool_dispatch_at_ms=1200,
                delay_ms=350,
            ),
            (1000, None),
        )

    def test_controller_dispatch_fallback_reports_memory_callback(self) -> None:
        from lelamp.auto_expression import AutoExpressionController

        recorded = []
        controller = AutoExpressionController(
            animation_service="anim",
            get_animation_service_error=lambda: None,
            rgb_service="rgb",
            led_count=8,
            on_fallback_expression=lambda **kwargs: recorded.append(kwargs),
        )

        with patch(
            "lelamp.auto_expression.dispatch_expression",
            return_value="expression_ok",
        ), patch(
            "lelamp.auto_expression._now_ms",
            side_effect=[1500, 1700],
        ), patch.object(
            controller,
            "note_tool_dispatch",
        ):
            controller._dispatch_fallback(
                style="greeting",
                trigger="voice_silence_timeout",
            )

        self.assertEqual(
            recorded,
            [
                {
                    "style": "greeting",
                    "trigger": "voice_silence_timeout",
                    "started_ts_ms": 1500,
                    "ended_ts_ms": 1700,
                    "ok": True,
                    "error": None,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
