"""Movement-model tests: motor ability and preference are separate layers."""
import unittest

from movement_model import derive_movement


class MovementModelTests(unittest.TestCase):
    def test_speed_and_fall_follow_agility_and_balance(self):
        result = derive_movement({"agility": 50, "balance": 40, "runs": 10, "jumps": 2,
                                  "falls": 4, "recoveries": 3})
        self.assertEqual(result["speed"], 330)
        self.assertAlmostEqual(result["fall_chance"], 9.5, places=1)
        self.assertAlmostEqual(result["stability"], 90.5, places=1)
        self.assertAlmostEqual(result["recovery"], 75.0, places=1)

    def test_caps_are_bounded_and_explained(self):
        result = derive_movement({"agility": 100, "balance": 100, "falls": 10, "recoveries": 10})
        self.assertEqual(result["speed"], 420)
        self.assertEqual(result["fall_chance"], 5.0)
        self.assertTrue(result["at_speed_cap"])
        self.assertTrue(result["at_stability_floor"])

    def test_no_falls_is_not_reported_as_zero_recovery(self):
        result = derive_movement({"agility": 10, "balance": 10})
        self.assertEqual(result["falls"], 0)
        self.assertEqual(result["recovery"], 100.0)

    def test_preferences_change_selection_not_motor_speed(self):
        base = derive_movement({"agility": 60, "balance": 50})
        enriched = derive_movement({"agility": 60, "balance": 50},
                                   {"actions": 88, "observations": 3000, "liked": 1, "disliked": 1})
        self.assertEqual(base["speed"], enriched["speed"])
        self.assertEqual(base["fall_chance"], enriched["fall_chance"])
        self.assertEqual(enriched["preference_tracked"], 88)
        self.assertEqual(derive_movement({"agility": 60}, {"tracked": 88})["preference_tracked"], 88)
        self.assertIn("动作选择", " ".join(row["label"] for row in enriched["layers"]))
        self.assertTrue(any("偏好观察" in note for note in enriched["notes"]))


if __name__ == "__main__":
    unittest.main()
