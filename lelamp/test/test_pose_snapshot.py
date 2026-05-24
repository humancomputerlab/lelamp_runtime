import tempfile
import unittest
from pathlib import Path


class PoseSnapshotTests(unittest.TestCase):
    def test_build_static_recording_rows_repeats_pose_with_timestamps(self) -> None:
        from lelamp.pose_snapshot import build_static_recording_rows

        pose = {
            "base_yaw": 12.5,
            "base_pitch": -30.0,
            "elbow_pitch": 44.0,
            "wrist_roll": 7.0,
            "wrist_pitch": 18.5,
        }

        rows = build_static_recording_rows(pose, fps=20, frame_count=3)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["timestamp"], "0.000000")
        self.assertEqual(rows[1]["timestamp"], "0.050000")
        self.assertEqual(rows[2]["timestamp"], "0.100000")
        self.assertEqual(rows[0]["base_yaw.pos"], "12.500000")
        self.assertEqual(rows[1]["wrist_pitch.pos"], "18.500000")

    def test_upsert_env_value_replaces_and_appends(self) -> None:
        from lelamp.pose_snapshot import upsert_env_value

        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text("FOO=1\nLELAMP_IDLE_RECORDING=old_idle\n", encoding="utf-8")

            upsert_env_value(env_path, "LELAMP_IDLE_RECORDING", "home_safe")
            upsert_env_value(env_path, "LELAMP_STARTUP_RECORDING", "home_safe")

            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "FOO=1\nLELAMP_IDLE_RECORDING=home_safe\nLELAMP_STARTUP_RECORDING=home_safe\n",
            )


if __name__ == "__main__":
    unittest.main()
