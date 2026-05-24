import unittest

from lelamp.motion_profiles import (
    SHUTDOWN_RELEASE_ORDER,
    build_dynamic_startup_actions,
    build_parallel_transition,
    build_sequential_transition,
    build_staged_shutdown_actions,
    first_pose,
    transform_actions_relative_to_pose,
)


class MotionProfilesTests(unittest.TestCase):
    def test_first_pose_returns_copy_of_first_frame(self) -> None:
        pose = first_pose([{"base_yaw.pos": 1.0}, {"base_yaw.pos": 2.0}])

        self.assertEqual(pose, {"base_yaw.pos": 1.0})
        self.assertIsNot(pose, None)

    def test_transform_actions_relative_to_pose_reanchors_sequence(self) -> None:
        actions = [
            {"base_yaw.pos": 10.0, "wrist_pitch.pos": 20.0},
            {"base_yaw.pos": 15.0, "wrist_pitch.pos": 17.0},
        ]
        home_pose = {"base_yaw.pos": 100.0, "wrist_pitch.pos": 200.0}

        transformed = transform_actions_relative_to_pose(actions, home_pose)

        self.assertEqual(
            transformed,
            [
                {"base_yaw.pos": 100.0, "wrist_pitch.pos": 200.0},
                {"base_yaw.pos": 105.0, "wrist_pitch.pos": 197.0},
            ],
        )

    def test_transform_actions_relative_to_pose_passes_through_without_home_pose(self) -> None:
        actions = [{"base_yaw.pos": 10.0}]

        transformed = transform_actions_relative_to_pose(actions, None)

        self.assertEqual(transformed, actions)
        self.assertIsNot(transformed[0], actions[0])

    def test_build_sequential_transition_moves_joints_from_5_to_1(self) -> None:
        start_pose = {
            "base_yaw.pos": 1.0,
            "base_pitch.pos": 2.0,
            "elbow_pitch.pos": 3.0,
            "wrist_roll.pos": 4.0,
            "wrist_pitch.pos": 5.0,
        }
        home_pose = {
            "base_yaw.pos": 10.0,
            "base_pitch.pos": 20.0,
            "elbow_pitch.pos": 30.0,
            "wrist_roll.pos": 40.0,
            "wrist_pitch.pos": 50.0,
        }

        frames = build_sequential_transition(start_pose, home_pose, frames_per_joint=2)

        self.assertEqual(len(frames), 10)
        self.assertEqual(
            frames[0],
            {
                "base_yaw.pos": 1.0,
                "base_pitch.pos": 2.0,
                "elbow_pitch.pos": 3.0,
                "wrist_roll.pos": 4.0,
                "wrist_pitch.pos": 27.5,
            },
        )
        self.assertEqual(
            frames[1],
            {
                "base_yaw.pos": 1.0,
                "base_pitch.pos": 2.0,
                "elbow_pitch.pos": 3.0,
                "wrist_roll.pos": 4.0,
                "wrist_pitch.pos": 50.0,
            },
        )
        self.assertEqual(
            frames[2],
            {
                "base_yaw.pos": 1.0,
                "base_pitch.pos": 2.0,
                "elbow_pitch.pos": 3.0,
                "wrist_roll.pos": 22.0,
                "wrist_pitch.pos": 50.0,
            },
        )
        self.assertEqual(frames[-1], home_pose)

    def test_build_dynamic_startup_actions_settles_then_reanchors_wake_up(self) -> None:
        current_pose = {
            "base_yaw.pos": -1.0,
            "base_pitch.pos": -2.0,
            "elbow_pitch.pos": -3.0,
            "wrist_roll.pos": -4.0,
            "wrist_pitch.pos": -5.0,
        }
        home_pose = {
            "base_yaw.pos": 10.0,
            "base_pitch.pos": 20.0,
            "elbow_pitch.pos": 30.0,
            "wrist_roll.pos": 40.0,
            "wrist_pitch.pos": 50.0,
        }
        wake_up_actions = [
            {
                "base_yaw.pos": 1.0,
                "base_pitch.pos": 2.0,
                "elbow_pitch.pos": 3.0,
                "wrist_roll.pos": 4.0,
                "wrist_pitch.pos": 5.0,
            },
            {
                "base_yaw.pos": 6.0,
                "base_pitch.pos": 7.0,
                "elbow_pitch.pos": 8.0,
                "wrist_roll.pos": 9.0,
                "wrist_pitch.pos": 10.0,
            },
        ]

        frames = build_dynamic_startup_actions(
            current_pose,
            home_pose,
            wake_up_actions,
            settle_frame_count=1,
            settle_hold_frames=1,
        )

        self.assertEqual(
            frames[:2],
            [
                {
                    "base_yaw.pos": -1.0,
                    "base_pitch.pos": 20.0,
                    "elbow_pitch.pos": 30.0,
                    "wrist_roll.pos": 40.0,
                    "wrist_pitch.pos": 50.0,
                },
                {
                    "base_yaw.pos": -1.0,
                    "base_pitch.pos": 20.0,
                    "elbow_pitch.pos": 30.0,
                    "wrist_roll.pos": 40.0,
                    "wrist_pitch.pos": 50.0,
                },
            ],
        )
        self.assertEqual(
            frames[2],
            {
                "base_yaw.pos": -1.0,
                "base_pitch.pos": 20.0,
                "elbow_pitch.pos": 30.0,
                "wrist_roll.pos": 40.0,
                "wrist_pitch.pos": 50.0,
            },
        )
        self.assertEqual(
            frames[3],
            {
                "base_yaw.pos": -1.0,
                "base_pitch.pos": 25.0,
                "elbow_pitch.pos": 35.0,
                "wrist_roll.pos": 45.0,
                "wrist_pitch.pos": 55.0,
            },
        )
        self.assertEqual(
            frames[-1],
            {
                "base_yaw.pos": -1.0,
                "base_pitch.pos": 20.0,
                "elbow_pitch.pos": 30.0,
                "wrist_roll.pos": 40.0,
                "wrist_pitch.pos": 50.0,
            },
        )

    def test_build_parallel_transition_moves_all_active_joints_together(self) -> None:
        start_pose = {
            "base_yaw.pos": 1.0,
            "base_pitch.pos": 2.0,
            "elbow_pitch.pos": 3.0,
            "wrist_roll.pos": 4.0,
            "wrist_pitch.pos": 5.0,
        }
        target_pose = {
            "base_yaw.pos": 10.0,
            "base_pitch.pos": 20.0,
            "elbow_pitch.pos": 30.0,
            "wrist_roll.pos": 40.0,
            "wrist_pitch.pos": 50.0,
        }

        frames = build_parallel_transition(start_pose, target_pose, frame_count=2)

        self.assertEqual(
            frames,
            [
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 11.0,
                    "elbow_pitch.pos": 16.5,
                    "wrist_roll.pos": 22.0,
                    "wrist_pitch.pos": 27.5,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 20.0,
                    "elbow_pitch.pos": 30.0,
                    "wrist_roll.pos": 40.0,
                    "wrist_pitch.pos": 50.0,
                },
            ],
        )

    def test_build_staged_shutdown_actions_uses_intermediate_pose_and_keeps_base_yaw(self) -> None:
        current_pose = {
            "base_yaw.pos": 1.0,
            "base_pitch.pos": 10.0,
            "elbow_pitch.pos": 20.0,
            "wrist_roll.pos": 30.0,
            "wrist_pitch.pos": 40.0,
        }
        power_off_pose = {
            "base_yaw.pos": 999.0,
            "base_pitch.pos": 110.0,
            "elbow_pitch.pos": 120.0,
            "wrist_roll.pos": 130.0,
            "wrist_pitch.pos": 140.0,
        }

        frames = build_staged_shutdown_actions(
            current_pose,
            power_off_pose,
            prepare_fraction=0.5,
            prepare_frames_per_joint=1,
            settle_frames_per_joint=1,
            hold_frames=1,
        )

        self.assertEqual(
            frames,
            [
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 60.0,
                    "elbow_pitch.pos": 20.0,
                    "wrist_roll.pos": 30.0,
                    "wrist_pitch.pos": 40.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 60.0,
                    "elbow_pitch.pos": 70.0,
                    "wrist_roll.pos": 30.0,
                    "wrist_pitch.pos": 40.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 60.0,
                    "elbow_pitch.pos": 70.0,
                    "wrist_roll.pos": 80.0,
                    "wrist_pitch.pos": 40.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 60.0,
                    "elbow_pitch.pos": 70.0,
                    "wrist_roll.pos": 80.0,
                    "wrist_pitch.pos": 90.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 60.0,
                    "elbow_pitch.pos": 70.0,
                    "wrist_roll.pos": 80.0,
                    "wrist_pitch.pos": 90.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 110.0,
                    "elbow_pitch.pos": 70.0,
                    "wrist_roll.pos": 80.0,
                    "wrist_pitch.pos": 90.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 110.0,
                    "elbow_pitch.pos": 120.0,
                    "wrist_roll.pos": 80.0,
                    "wrist_pitch.pos": 90.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 110.0,
                    "elbow_pitch.pos": 120.0,
                    "wrist_roll.pos": 130.0,
                    "wrist_pitch.pos": 90.0,
                },
                {
                    "base_yaw.pos": 1.0,
                    "base_pitch.pos": 110.0,
                    "elbow_pitch.pos": 120.0,
                    "wrist_roll.pos": 130.0,
                    "wrist_pitch.pos": 140.0,
                },
            ],
        )

    def test_shutdown_release_order_leaves_base_yaw_for_last(self) -> None:
        self.assertEqual(
            SHUTDOWN_RELEASE_ORDER,
            ("base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch", "base_yaw"),
        )


if __name__ == "__main__":
    unittest.main()
