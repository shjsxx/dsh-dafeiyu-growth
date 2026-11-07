"""Integration soak test: simulated days, reloads, and a verified backup."""
import tempfile
import unittest

from soak_state import run


class PersistenceSoakTests(unittest.TestCase):
    def test_simulated_days_reload_without_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run(30, directory)
        self.assertTrue(result["ok"])
        self.assertEqual(result["cycles"], 30)
        self.assertGreaterEqual(result["reloads"], 1)
        self.assertEqual(result["growth"]["xp"], 30 * 37)
        self.assertGreater(result["memory"]["total"], 0)
        self.assertTrue(result["continuity"]["stable"])


if __name__ == "__main__":
    unittest.main()
