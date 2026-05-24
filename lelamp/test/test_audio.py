import os
import unittest

try:
    import numpy as np
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional hardware dependency
    np = None
    sd = None


def get_seeed_device(output: bool = True) -> int | None:
    """Return the first Seeed/ReSpeaker device index for output or input."""
    if sd is None:
        return None

    capability_key = "max_output_channels" if output else "max_input_channels"
    for index, device in enumerate(sd.query_devices()):
        name = str(device.get("name", "")).lower()
        if "seeed" not in name and "respeaker" not in name:
            continue
        if int(device.get(capability_key, 0) or 0) > 0:
            return index
    return None


@unittest.skipUnless(
    os.getenv("LELAMP_RUN_HARDWARE_AUDIO_TEST") == "1",
    "hardware audio smoke test disabled",
)
class AudioHardwareSmokeTests(unittest.TestCase):
    def test_seeed_input_output_roundtrip(self) -> None:
        if sd is None or np is None:
            self.skipTest("sounddevice/numpy unavailable")

        seeed_output = get_seeed_device(output=True)
        seeed_input = get_seeed_device(output=False)
        if seeed_output is None or seeed_input is None:
            self.skipTest("Seeed/ReSpeaker device not found")

        duration = 1.0
        sample_rate = 44100

        frequency = 440
        timeline = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        tone = 0.5 * np.sin(2 * np.pi * frequency * timeline)
        sd.play(tone, samplerate=sample_rate, device=seeed_output)
        sd.wait()

        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            device=seeed_input,
        )
        sd.wait()

        sd.play(recording, samplerate=sample_rate, device=seeed_output)
        sd.wait()


if __name__ == "__main__":
    unittest.main()
