"""Deterministic tests for the run/tumble planner (no GUI)."""
import unittest

from motion_model import plan_run, run_pose


class FakeRandom:
    def __init__(self, roll=0.5, fall_at=0.7, hop=10.0):
        self.roll, self.fall_at, self.hop = roll, fall_at, hop

    def random(self):
        return self.roll

    def uniform(self, low, high):
        return {'fall_at': self.fall_at, 'hop_height': self.hop}.get(
            'fall_at' if low == .55 else 'hop_height', low)

    def randint(self, low, high):
        return low


class PlannerTests(unittest.TestCase):
    def test_probability_is_sampled_once_and_never_trusted_as_flag(self):
        low = plan_run(100, 500, 0, 1000, 300, 1, 300, 0.18, None, FakeRandom(roll=0.99))
        high = plan_run(100, 500, 0, 1000, 300, 1, 300, 0.18, None, FakeRandom(roll=0.0))
        self.assertFalse(low['fall'])
        self.assertTrue(high['fall'])

    def test_forced_outcomes_win(self):
        forced = plan_run(0, 0, 0, 1000, 300, 1, 300, 0.0, True, FakeRandom())
        suppressed = plan_run(0, 0, 0, 1000, 300, 1, 300, 1.0, False, FakeRandom())
        self.assertTrue(forced['fall'])
        self.assertFalse(suppressed['fall'])

    def test_short_runs_never_fall(self):
        plan = plan_run(0, 0, 0, 1000, 150, 1, 300, 1.0, None, FakeRandom())
        self.assertFalse(plan['fall'])

    def test_target_clamped_to_screen_and_minimum_distance(self):
        plan = plan_run(990, 0, 0, 1000, 400, 1, 300, 0.0, None, FakeRandom())
        self.assertIsNotNone(plan)
        self.assertLessEqual(plan['to'], 1000)
        self.assertGreaterEqual(plan['from'], 0)
        self.assertGreaterEqual(abs(plan['to'] - plan['from']), 60)
        self.assertEqual(plan['direction'], -1)
        self.assertIsNone(plan_run(0, 0, 0, 50, 100, 1, 300, 0.0, None, FakeRandom()))

    def test_run_pose_bounces_and_returns_to_ground(self):
        plan = plan_run(0, 500, 0, 1000, 300, 1, 300, 0.0, None, FakeRandom())
        start = run_pose(plan, 0)
        mid = run_pose(plan, 0.5)
        end = run_pose(plan, 1)
        self.assertEqual(start['y'], 500)
        self.assertEqual(end['y'], 500)
        self.assertLessEqual(mid['y'], 500)
        self.assertGreaterEqual(start['y'] - mid['y'], 0)
        self.assertEqual(start['facing'], 1)
        self.assertLessEqual(start['scale_y'], 1.0)
        self.assertLessEqual(abs(mid['tilt']), 2.0)

    def test_pose_never_leaves_horizontal_bounds(self):
        plan = plan_run(0, 500, 0, 1000, 900, 1, 300, 0.0, None, FakeRandom())
        for step in range(21):
            pose = run_pose(plan, step / 20)
            self.assertGreaterEqual(pose['x'], 0)
            self.assertLessEqual(pose['x'], 1000)


if __name__ == '__main__':
    unittest.main()
