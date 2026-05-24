import tempfile
import unittest
from pathlib import Path

from PIL import Image

from lelamp.glyph_player import load_builtin_animation, load_image_animation, mask_to_pixels


class GlyphPlayerTests(unittest.TestCase):
    def test_mask_to_pixels_uses_serpentine_layout(self) -> None:
        pixels = mask_to_pixels(
            [
                "10000000",
                "00000001",
                "00000000",
                "00000000",
                "00000000",
                "00000000",
                "00000000",
                "00000000",
            ],
            on_color=(9, 8, 7),
        )

        self.assertEqual(len(pixels), 64)
        self.assertEqual(pixels[0], (9, 8, 7))
        self.assertEqual(pixels[8], (9, 8, 7))

    def test_builtin_animation_returns_full_frames(self) -> None:
        frames = load_builtin_animation("nothing_orbit", on_color=(1, 2, 3))

        self.assertGreaterEqual(len(frames), 6)
        self.assertTrue(all(len(frame.pixels) == 64 for frame in frames))
        self.assertTrue(all(frame.duration > 0 for frame in frames))
        self.assertTrue(any(pixel == (1, 2, 3) for pixel in frames[0].pixels))

    def test_load_image_animation_reads_gif_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir, "wink.gif")

            first = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            first.putpixel((0, 0), (255, 255, 255, 255))

            second = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            second.putpixel((7, 7), (255, 255, 255, 255))

            first.save(path, save_all=True, append_images=[second], duration=[40, 70], loop=0)

            frames = load_image_animation(path, on_color=(4, 5, 6))

        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].pixels[0], (4, 5, 6))
        self.assertEqual(frames[1].pixels[56], (4, 5, 6))
        self.assertEqual(frames[0].duration, 0.04)
        self.assertEqual(frames[1].duration, 0.07)


if __name__ == "__main__":
    unittest.main()
