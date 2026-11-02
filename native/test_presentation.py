"""Ambient presentation values: bounded, deterministic, reduced-motion safe."""
import unittest

from presentation import ambient_pose, mood_glow


class PresentationTests(unittest.TestCase):
    def test_pose_is_bounded_and_deterministic(self):
        first = ambient_pose(123456, "joy", .8, True, 90)
        second = ambient_pose(123456, "joy", .8, True, 90)
        self.assertEqual(first, second)
        self.assertTrue(.97 <= first["breath"] <= 1.03)
        self.assertTrue(-3 <= first["sway"] <= 3)
        self.assertEqual(first["glow"], mood_glow("joy"))

    def test_reduced_motion_removes_movement_and_glow(self):
        pose = ambient_pose(123456, "sad", .2, True, 90, reduced=True)
        self.assertEqual(pose["breath"], 1.0)
        self.assertEqual(pose["sway"], 0.0)
        self.assertEqual(pose["glow_alpha"], 0)

    def test_pointer_lean_is_bounded_and_glow_known(self):
        self.assertLessEqual(abs(ambient_pose(0, "neutral", 1, True, 9999)["sway"]), 3)
        self.assertEqual(mood_glow("unknown"), mood_glow("neutral"))


if __name__ == '__main__':
    unittest.main()
