from typing import Any, List, Union
from rpi_ws281x import PixelStrip, Color
from ..base import ServiceBase


class RGBService(ServiceBase):
    def __init__(self, 
                 led_count: int = 64,
                 led_pin: int = 12,
                 led_freq_hz: int = 800000,
                 led_dma: int = 10,
                 led_brightness: int = 255,
                 led_invert: bool = False,
                 led_channel: int = 0):
        super().__init__("rgb")
        
        self.led_count = led_count
        self.strip = PixelStrip(
            led_count, led_pin, led_freq_hz, led_dma, 
            led_invert, led_brightness, led_channel
        )
        self.strip.begin()
        
    def handle_event(self, event_type: str, payload: Any):
        if event_type == "solid":
            self._handle_solid(payload)
        elif event_type == "paint":
            self._handle_paint(payload)
        else:
            self.logger.warning(f"Unknown event type: {event_type}")
    
    def _handle_solid(self, color_code: Union[int, tuple]):
        """Fill entire strip with single color"""
        if isinstance(color_code, tuple) and len(color_code) == 3:
            color = Color(color_code[0], color_code[1], color_code[2])
        elif isinstance(color_code, int):
            color = color_code
        else:
            self.logger.error(f"Invalid color format: {color_code}")
            return
            
        for i in range(self.led_count):
            self.strip.setPixelColor(i, color)
        self.strip.show()
        self.logger.debug(f"Applied solid color: {color_code}")
    
    def _handle_paint(self, colors: List[Union[int, tuple]]):
        """Set individual pixel colors from array"""
        if not isinstance(colors, list):
            self.logger.error(f"Paint payload must be a list, got: {type(colors)}")
            return
            
        max_pixels = min(len(colors), self.led_count)
        
        for i in range(max_pixels):
            color_code = colors[i]
            if isinstance(color_code, tuple) and len(color_code) == 3:
                color = Color(color_code[0], color_code[1], color_code[2])
            elif isinstance(color_code, int):
                color = color_code
            else:
                self.logger.warning(f"Invalid color at index {i}: {color_code}")
                continue
                
            self.strip.setPixelColor(i, color)
        
        self.strip.show()
        self.logger.debug(f"Applied paint pattern with {max_pixels} colors")
    
    def clear(self):
        """Turn off all LEDs"""
        for i in range(self.led_count):
            self.strip.setPixelColor(i, Color(0, 0, 0))
        self.strip.show()
    
    def stop(self, timeout: float = 5.0):
        """Override stop to clear LEDs before stopping"""
        self.clear()
        super().stop(timeout)