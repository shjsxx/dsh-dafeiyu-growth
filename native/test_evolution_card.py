"""Ensure the four controlled-evolution entries live in the growth card."""
import unittest

from PySide6.QtWidgets import QApplication

from growth_native import GrowthDialog

APP = QApplication.instance() or QApplication([])


class EvolutionCardTests(unittest.TestCase):
    def test_growth_data_page_has_four_evolution_buttons(self):
        dialog = GrowthDialog()
        try:
            received = []
            dialog.evolution_status_requested.connect(lambda: received.append("status"))
            dialog.research_requested.connect(lambda: received.append("research"))
            dialog.memory_curation_requested.connect(lambda: received.append("memory"))
            dialog.persona_requested.connect(lambda: received.append("persona"))
            dialog.evolution_status_button.click()
            dialog.research_button.click()
            dialog.memory_curation_button.click()
            dialog.persona_button.click()
            self.assertEqual(received, ["status", "research", "memory", "persona"])
            self.assertEqual(dialog.tabs.count(), 7)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
