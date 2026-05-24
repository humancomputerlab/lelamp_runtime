import tempfile
import unittest
from pathlib import Path


class PosePresetTests(unittest.TestCase):
    def test_build_pose_presets_derives_sleep_and_power_off_from_home_safe(self) -> None:
        from lelamp.pose_presets import DEFAULT_HOME_SAFE_POSE, build_pose_presets

        presets = build_pose_presets()

        self.assertEqual(set(presets), {"home_safe", "sleep_pose", "power_off"})
        self.assertEqual(presets["power_off"], presets["sleep_pose"])
        self.assertEqual(presets["home_safe"], DEFAULT_HOME_SAFE_POSE)
        self.assertLess(presets["sleep_pose"]["base_pitch"], presets["home_safe"]["base_pitch"])
        self.assertGreater(presets["sleep_pose"]["elbow_pitch"], presets["home_safe"]["elbow_pitch"])
        self.assertGreater(presets["sleep_pose"]["wrist_roll"], presets["home_safe"]["wrist_roll"])
        self.assertLess(presets["sleep_pose"]["wrist_pitch"], presets["home_safe"]["wrist_pitch"])

    def test_write_pose_recordings_materializes_static_recording_files(self) -> None:
        from lelamp.pose_presets import build_pose_presets, write_pose_recordings

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_paths = write_pose_recordings(
                Path(tmp_dir),
                fps=20,
                frame_count=4,
            )

            self.assertEqual(
                {path.name for path in output_paths},
                {"home_safe.csv", "sleep_pose.csv", "power_off.csv", "idle.csv"},
            )

            expected = build_pose_presets()
            for path in output_paths:
                content = path.read_text(encoding="utf-8")
                self.assertIn("timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos", content)
                self.assertEqual(len(content.strip().splitlines()), 5)
                stem = "home_safe" if path.stem == "idle" else path.stem
                first_frame = content.strip().splitlines()[1].split(",")
                self.assertAlmostEqual(float(first_frame[1]), expected[stem]["base_yaw"], places=6)


if __name__ == "__main__":
    unittest.main()
