import unittest

from lelamp.service.rgb.rgb_service import RGBService


class RGBServicePayloadTests(unittest.TestCase):
    def test_device_payload_uses_rgb_order_without_header_byte(self) -> None:
        payload = RGBService._build_device_payload(
            [(1, 2, 3), (10, 20, 30)],
            led_count=2,
        )

        self.assertEqual(payload, bytes([1, 2, 3, 0, 10, 20, 30, 0]))

    def test_device_frame_keeps_full_color_values_when_brightness_is_lowered(self) -> None:
        brightness, payload = RGBService._build_device_frame(
            [(255, 128, 0)],
            led_count=1,
            led_brightness=128,
        )

        self.assertEqual(brightness, 128)
        self.assertEqual(payload, bytes([255, 128, 0, 0]))

    def test_device_payload_pads_remaining_leds_with_black(self) -> None:
        payload = RGBService._build_device_payload(
            [(7, 8, 9)],
            led_count=2,
        )

        self.assertEqual(payload, bytes([7, 8, 9, 0, 0, 0, 0, 0]))

    def test_device_frame_clamps_brightness_to_driver_range(self) -> None:
        brightness, payload = RGBService._build_device_frame(
            [(7, 8, 9)],
            led_count=1,
            led_brightness=999,
        )

        self.assertEqual(brightness, 255)
        self.assertEqual(payload, bytes([7, 8, 9, 0]))


if __name__ == "__main__":
    unittest.main()
