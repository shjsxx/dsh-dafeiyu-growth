"""Ensure the four controlled-evolution entries live in the growth card."""
import unittest

from PySide6.QtWidgets import QApplication, QWidget

from growth_native import GrowthDialog

APP = QApplication.instance() or QApplication([])


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.status_opened = 0

    def _show_evolution_status(self):
        self.status_opened += 1


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

    def test_status_button_prefers_host_and_renders_inline(self):
        host = _Host()
        dialog = GrowthDialog(host)
        try:
            dialog.evolution_status_button.click()
            self.assertEqual(host.status_opened, 1)
            dialog.show_evolution_status("受控进化 · 只读状态\n审计链：正常")
            self.assertIn("审计链", dialog.evolution_feedback.text())
            self.assertIs(dialog.tabs.currentWidget(), dialog.page_scrolls[6])
            self.assertEqual(dialog.tabs.count(), 7)
        finally:
            dialog.close()
            host.close()


if __name__ == "__main__":
    unittest.main()
