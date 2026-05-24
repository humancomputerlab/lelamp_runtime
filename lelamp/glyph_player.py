from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageSequence

from .service.rgb.rgb_service import RGBService


Color = tuple[int, int, int]

MATRIX_SIZE = 8
OFF: Color = (0, 0, 0)
DEFAULT_GLYPH: Color = (245, 245, 245)


@dataclass(frozen=True)
class AnimationFrame:
    pixels: list[Color]
    duration: float


def serpentine_index(x: int, y: int, *, size: int = MATRIX_SIZE) -> int:
    return y * size + x if y % 2 == 0 else y * size + (size - 1 - x)


def mask_to_pixels(
    rows: Iterable[str],
    *,
    on_color: Color = DEFAULT_GLYPH,
    off_color: Color = OFF,
    size: int = MATRIX_SIZE,
) -> list[Color]:
    normalized_rows = [row[:size].ljust(size, "0") for row in list(rows)[:size]]
    if len(normalized_rows) < size:
        normalized_rows.extend(["0" * size] * (size - len(normalized_rows)))

    pixels = [off_color] * (size * size)
    for y, row in enumerate(normalized_rows):
        for x, cell in enumerate(row):
            if cell == "1":
                pixels[serpentine_index(x, y, size=size)] = on_color

    return pixels


def load_builtin_animation(name: str, *, on_color: Color = DEFAULT_GLYPH) -> list[AnimationFrame]:
    if name != "nothing_orbit":
        raise ValueError(f"Unknown builtin animation: {name}")

    frame_rows = [
        [
            "00111100",
            "01000000",
            "10000000",
            "10011000",
            "10011000",
            "10000000",
            "01000000",
            "00111100",
        ],
        [
            "00011110",
            "00000010",
            "00000010",
            "00011010",
            "00011010",
            "00000010",
            "00000010",
            "00011110",
        ],
        [
            "00111100",
            "01000010",
            "00000001",
            "00011001",
            "00011001",
            "00000001",
            "01000010",
            "00111100",
        ],
        [
            "00111100",
            "00000010",
            "00000001",
            "00011001",
            "00011001",
            "00000001",
            "00000010",
            "00111100",
        ],
        [
            "00111100",
            "00000010",
            "00000010",
            "00011010",
            "00011010",
            "00000010",
            "00000010",
            "00011110",
        ],
        [
            "00111100",
            "01000000",
            "10000000",
            "10011000",
            "10011000",
            "10000000",
            "01000000",
            "00111100",
        ],
        [
            "00111100",
            "01000010",
            "10000000",
            "10011000",
            "10011000",
            "10000000",
            "01000010",
            "00111100",
        ],
        [
            "00111100",
            "01111110",
            "11000011",
            "10011001",
            "10011001",
            "11000011",
            "01111110",
            "00111100",
        ],
    ]

    durations = [0.08, 0.08, 0.08, 0.08, 0.08, 0.08, 0.12, 0.2]
    return [
        AnimationFrame(mask_to_pixels(rows, on_color=on_color), duration)
        for rows, duration in zip(frame_rows, durations, strict=True)
    ]


def _fit_to_matrix(image: Image.Image, *, size: int) -> Image.Image:
    if image.size != (size, size):
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def _image_to_pixels(
    image: Image.Image,
    *,
    size: int = MATRIX_SIZE,
    on_color: Color = DEFAULT_GLYPH,
    off_color: Color = OFF,
    threshold: int = 96,
) -> list[Color]:
    image = _fit_to_matrix(image.convert("RGBA"), size=size)
    pixels = [off_color] * (size * size)

    for y in range(size):
        for x in range(size):
            red, green, blue, alpha = image.getpixel((x, y))
            intensity = (red + green + blue) // 3
            if alpha >= 32 and intensity >= threshold:
                pixels[serpentine_index(x, y, size=size)] = on_color

    return pixels


def load_image_animation(
    path: str | Path,
    *,
    size: int = MATRIX_SIZE,
    on_color: Color = DEFAULT_GLYPH,
    threshold: int = 96,
    default_duration: float = 0.12,
) -> list[AnimationFrame]:
    source = Path(path)
    with Image.open(source) as image:
        if getattr(image, "is_animated", False):
            frames = []
            for frame in ImageSequence.Iterator(image):
                duration_ms = frame.info.get("duration", 0)
                duration = duration_ms / 1000 if duration_ms else default_duration
                frames.append(
                    AnimationFrame(
                        _image_to_pixels(frame, size=size, on_color=on_color, threshold=threshold),
                        duration,
                    )
                )
            return frames

        return [
            AnimationFrame(
                _image_to_pixels(image, size=size, on_color=on_color, threshold=threshold),
                default_duration,
            )
        ]


def play_animation(
    frames: list[AnimationFrame],
    *,
    led_count: int = MATRIX_SIZE * MATRIX_SIZE,
    brightness: int = 255,
    loops: int = 1,
    clear_at_end: bool = False,
) -> None:
    service = RGBService(led_count=led_count, led_brightness=brightness)

    loop_total = max(1, loops)
    for _ in range(loop_total):
        for frame in frames:
            service.handle_event("paint", frame.pixels)
            time.sleep(frame.duration)

    if clear_at_end:
        service.clear()


def _parse_color(value: str) -> Color:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("Color must be formatted as R,G,B")
    try:
        red, green, blue = (max(0, min(255, int(part))) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Color must contain integers") from exc
    return red, green, blue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play 8x8 RGB glyph animations on LeLamp")
    parser.add_argument("--brightness", type=int, default=220)
    parser.add_argument("--loops", type=int, default=6)
    parser.add_argument("--color", type=_parse_color, default=DEFAULT_GLYPH)
    parser.add_argument("--clear-at-end", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)

    preset = subparsers.add_parser("preset", help="Play a builtin glyph animation")
    preset.add_argument("name", choices=["nothing_orbit"])

    image = subparsers.add_parser("image", help="Render an image or GIF onto the 8x8 matrix")
    image.add_argument("path")
    image.add_argument("--threshold", type=int, default=96)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "preset":
        frames = load_builtin_animation(args.name, on_color=args.color)
    else:
        frames = load_image_animation(args.path, on_color=args.color, threshold=args.threshold)

    play_animation(
        frames,
        brightness=args.brightness,
        loops=args.loops,
        clear_at_end=args.clear_at_end,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
