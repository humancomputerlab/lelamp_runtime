import os
from typing import Any, Iterable, List, Union

from ..base import ServiceBase

try:
    from rpi_ws281x import Color as WsColor
    from rpi_ws281x import PixelStrip
except ImportError:  # pragma: no cover - optional dependency on non-Pi hosts
    PixelStrip = None
    WsColor = None


ColorCode = Union[int, tuple[int, int, int], list[int]]


class RGBService(ServiceBase):
    def __init__(
        self,
        led_count: int = 64,
        led_pin: int = 12,
        led_freq_hz: int = 800000,
        led_dma: int = 10,
        led_brightness: int = 255,
        led_invert: bool = False,
        led_channel: int = 0,
    ):
        super().__init__("rgb")

        self.led_count = led_count
        self.led_brightness = max(0, min(255, led_brightness))
        self.device = "/dev/leds0"
        self.current_colors: list[tuple[int, int, int]] = [(0, 0, 0)] * led_count
        self.strip = None
        self._device_brightness: int | None = None

        if os.path.exists(self.device):
            self.backend = "device"
        else:
            self.backend = "ws281x"
            if PixelStrip is None or WsColor is None:
                raise RuntimeError(
                    "RGB backend unavailable: /dev/leds0 not found and rpi_ws281x is not installed"
                )
            self.strip = PixelStrip(
                led_count,
                led_pin,
                led_freq_hz,
                led_dma,
                led_invert,
                self.led_brightness,
                led_channel,
            )
            self.strip.begin()

    def handle_event(self, event_type: str, payload: Any):
        if event_type == "solid":
            self._handle_solid(payload)
        elif event_type == "paint":
            self._handle_paint(payload)
        else:
            self.logger.warning(f"Unknown event type: {event_type}")

    def _handle_solid(self, color_code: ColorCode):
        color = self._parse_color(color_code)
        if color is None:
            self.logger.error(f"Invalid color format: {color_code}")
            return

        self.current_colors = [color] * self.led_count
        self._write_leds()
        self.logger.debug(f"Applied solid color: {color}")

    def _handle_paint(self, colors: List[ColorCode]):
        if not isinstance(colors, list):
            self.logger.error(f"Paint payload must be a list, got: {type(colors)}")
            return

        updated_colors = [(0, 0, 0)] * self.led_count
        max_pixels = min(len(colors), self.led_count)

        for i in range(max_pixels):
            color = self._parse_color(colors[i])
            if color is None:
                self.logger.warning(f"Invalid color at index {i}: {colors[i]}")
                continue
            updated_colors[i] = color

        self.current_colors = updated_colors
        self._write_leds()
        self.logger.debug(f"Applied paint pattern with {max_pixels} colors")

    def _write_leds(self):
        if self.backend == "device":
            brightness, payload = self._build_device_frame(
                self.current_colors,
                led_count=self.led_count,
                led_brightness=self.led_brightness,
            )
            try:
                fd = os.open(self.device, os.O_WRONLY)
                try:
                    if self._device_brightness != brightness:
                        os.write(fd, bytes((brightness,)))
                        self._device_brightness = brightness
                    os.write(fd, payload)
                finally:
                    os.close(fd)
            except OSError as exc:
                self.logger.error(f"LED write error: {exc}")
            return

        assert self.strip is not None
        for i, (red, green, blue) in enumerate(self.current_colors):
            self.strip.setPixelColor(i, WsColor(red, green, blue))
        self.strip.show()

    def clear(self):
        self.current_colors = [(0, 0, 0)] * self.led_count
        self._write_leds()

    def stop(self, timeout: float = 5.0):
        self.clear()
        super().stop(timeout)

    @staticmethod
    def _parse_color(color_code: ColorCode) -> tuple[int, int, int] | None:
        if isinstance(color_code, (tuple, list)) and len(color_code) == 3:
            return tuple(max(0, min(255, int(value))) for value in color_code)
        if isinstance(color_code, int):
            return (
                (color_code >> 16) & 0xFF,
                (color_code >> 8) & 0xFF,
                color_code & 0xFF,
            )
        return None

    @staticmethod
    def _build_device_payload(
        colors: Iterable[tuple[int, int, int]],
        *,
        led_count: int,
    ) -> bytes:
        payload = bytearray()
        padded_colors = list(colors)[:led_count]

        if len(padded_colors) < led_count:
            padded_colors.extend([(0, 0, 0)] * (led_count - len(padded_colors)))

        for red, green, blue in padded_colors:
            payload.extend(
                (
                    max(0, min(255, red)),
                    max(0, min(255, green)),
                    max(0, min(255, blue)),
                    0,
                )
            )

        return bytes(payload)

    @staticmethod
    def _build_device_frame(
        colors: Iterable[tuple[int, int, int]],
        *,
        led_count: int,
        led_brightness: int = 255,
    ) -> tuple[int, bytes]:
        brightness = max(0, min(255, int(led_brightness)))
        payload = RGBService._build_device_payload(
            colors,
            led_count=led_count,
        )
        return brightness, payload
