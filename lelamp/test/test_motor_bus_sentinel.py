import json
import os
import unittest
from pathlib import Path
from unittest import mock

from lelamp.motor_bus import sentinel as sentinel_mod


class SentinelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_path = Path(
            os.environ.get("TMPDIR", "/tmp")
        ) / f"lelamp-motor-bus-test-{os.getpid()}.json"
        self._env_patcher = mock.patch.dict(
            os.environ, {"LELAMP_MOTOR_BUS_SENTINEL": str(self.tmp_path)}
        )
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        self.addCleanup(self._cleanup_file)

    def _cleanup_file(self) -> None:
        try:
            self.tmp_path.unlink()
        except FileNotFoundError:
            pass

    def test_roundtrip(self) -> None:
        info = sentinel_mod.SentinelInfo(
            pid=os.getpid(),
            port=8770,
            base_url="http://127.0.0.1:8770",
            started_at_ms=123,
        )
        sentinel_mod.write_sentinel(info)
        loaded = sentinel_mod.read_sentinel()
        self.assertEqual(loaded, info)

    def test_read_missing_returns_none(self) -> None:
        self.assertIsNone(sentinel_mod.read_sentinel())

    def test_read_corrupt_returns_none(self) -> None:
        self.tmp_path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_path.write_text("not json")
        self.assertIsNone(sentinel_mod.read_sentinel())

    def test_read_missing_keys_returns_none(self) -> None:
        self.tmp_path.write_text(json.dumps({"pid": 1}))
        self.assertIsNone(sentinel_mod.read_sentinel())

    def test_read_live_rejects_dead_pid(self) -> None:
        info = sentinel_mod.SentinelInfo(
            pid=99999999,  # almost certainly dead
            port=8770,
            base_url="http://127.0.0.1:8770",
            started_at_ms=1,
        )
        sentinel_mod.write_sentinel(info)
        self.assertIsNone(sentinel_mod.read_live_sentinel())

    def test_read_live_accepts_current_pid(self) -> None:
        info = sentinel_mod.SentinelInfo(
            pid=os.getpid(),
            port=8770,
            base_url="http://127.0.0.1:8770",
            started_at_ms=1,
        )
        sentinel_mod.write_sentinel(info)
        loaded = sentinel_mod.read_live_sentinel()
        self.assertEqual(loaded, info)

    def test_read_live_rejects_wrong_version(self) -> None:
        payload = {
            "pid": os.getpid(),
            "port": 8770,
            "base_url": "http://127.0.0.1:8770",
            "started_at_ms": 1,
            "version": 999,
        }
        self.tmp_path.write_text(json.dumps(payload))
        self.assertIsNone(sentinel_mod.read_live_sentinel())

    def test_remove_is_idempotent(self) -> None:
        sentinel_mod.remove_sentinel()
        sentinel_mod.write_sentinel(
            sentinel_mod.SentinelInfo(
                pid=os.getpid(),
                port=8770,
                base_url="http://127.0.0.1:8770",
                started_at_ms=1,
            )
        )
        sentinel_mod.remove_sentinel()
        sentinel_mod.remove_sentinel()
        self.assertFalse(self.tmp_path.exists())

    def test_is_process_alive_negative_pid(self) -> None:
        self.assertFalse(sentinel_mod.is_process_alive(-1))
        self.assertFalse(sentinel_mod.is_process_alive(0))


if __name__ == "__main__":
    unittest.main()
