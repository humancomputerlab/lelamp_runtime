import unittest
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory

from lelamp.voice_telemetry import (
    VoiceTelemetryStore,
    configure_voice_telemetry,
    get_voice_telemetry,
    read_voice_telemetry,
)


class VoiceTelemetryTests(unittest.TestCase):
    def test_read_voice_telemetry_returns_unknown_when_file_is_missing(self) -> None:
        snapshot = read_voice_telemetry(Path("/tmp/lelamp-voice-telemetry-missing.json"))

        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["local_state"], "unknown")
        self.assertEqual(snapshot["last_result"], "voice telemetry unavailable")

    def test_store_persists_voice_snapshot_to_json_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "voice-state.json"
            store = VoiceTelemetryStore(path)

            store.update(
                status="ready",
                local_state="idle",
                speech_threshold_db=-47.0,
                noise_floor_db=-55.0,
                last_result="voice telemetry sampled",
                force=True,
            )

            snapshot = read_voice_telemetry(path)

        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["local_state"], "idle")
        self.assertEqual(snapshot["speech_threshold_db"], -47.0)
        self.assertEqual(snapshot["noise_floor_db"], -55.0)
        self.assertEqual(snapshot["last_result"], "voice telemetry sampled")

    def test_configured_store_is_preserved_by_get_voice_telemetry(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "voice-state.json"

            configured = configure_voice_telemetry(path)
            current = get_voice_telemetry()

        self.assertEqual(current.path, path)
        self.assertIs(current, configured)

    def test_store_persists_voice_snapshot_with_private_file_permissions(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "voice-state.json"
            store = VoiceTelemetryStore(path)

            store.update(status="ready", force=True)

            mode = S_IMODE(path.stat().st_mode)

        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
