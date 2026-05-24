import inspect
import unittest

import main
import smooth_animation


class ToolSignatureTests(unittest.TestCase):
    def test_main_get_available_recordings_has_no_placeholder_args(self) -> None:
        params = inspect.signature(main.LeLamp.get_available_recordings).parameters
        self.assertEqual(list(params), ["self"])

    def test_smooth_animation_get_available_recordings_has_no_placeholder_args(self) -> None:
        params = inspect.signature(smooth_animation.LeLamp.get_available_recordings).parameters
        self.assertEqual(list(params), ["self"])

    def test_motion_tool_docstring_pushes_direct_execution_without_narration(self) -> None:
        doc = inspect.getdoc(main.LeLamp.play_recording)
        assert doc is not None
        self.assertIn("Do not ask for confirmation first", doc)
        self.assertIn("Do not narrate the tool call", doc)

    def test_light_tool_docstring_pushes_direct_execution_without_narration(self) -> None:
        doc = inspect.getdoc(main.LeLamp.set_rgb_solid)
        assert doc is not None
        self.assertIn("Do not ask for confirmation first", doc)
        self.assertIn("Do not narrate the tool call", doc)

    def test_expression_tool_has_small_high_level_schema(self) -> None:
        params = inspect.signature(main.LeLamp.express).parameters
        self.assertEqual(list(params), ["self", "style"])

        doc = inspect.getdoc(main.LeLamp.express)
        assert doc is not None
        self.assertIn("Prefer this over low-level motion/light tools", doc)
        self.assertIn("Do not narrate the tool call", doc)


if __name__ == "__main__":
    unittest.main()
