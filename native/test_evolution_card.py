"""Ensure the four controlled-evolution entries live in the growth card."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from growth_native import GrowthDialog
from runtime.companion_runtime import CompanionRuntime

APP = QApplication.instance() or QApplication([])


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.status_opened = 0

    def _show_evolution_status(self):
        self.status_opened += 1


class _RealHost(QWidget, CompanionRuntime):
    pass


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
            dialog.show_evolution_status(
                "受控进化 · 只读状态\n审计链：正常\n研究任务：2 个",
                {"audit": {"ok": True, "events": 3}, "hosts": 4,
                 "research": {"total": 2, "statuses": {"done": 2}}},
            )
            self.assertIn("审计链", dialog.evolution_feedback.text())
            self.assertEqual(dialog.evolution_audit_value.text(), "正常")
            self.assertEqual(dialog.evolution_hosts_value.text(), "4")
            self.assertEqual(dialog.evolution_research_value.text(), "2")
            self.assertIs(dialog.tabs.currentWidget(), dialog.page_scrolls[6])
            self.assertEqual(dialog.tabs.count(), 7)
        finally:
            dialog.close()
            host.close()

    def test_real_host_renders_status_inside_card(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"DSH_HOME": directory}):
            host = _RealHost()
            dialog = GrowthDialog(host)
            host.growth_dialog = dialog
            try:
                dialog.evolution_status_button.click()
                text = dialog.evolution_feedback.text()
                self.assertIn("只读检查", text)
                self.assertIn("本地运行指标", text)
                self.assertIs(dialog.tabs.currentWidget(), dialog.page_scrolls[6])
            finally:
                dialog.close()
                host.close()

    def test_other_three_buttons_call_their_host_methods(self):
        host = _RealHost()
        dialog = GrowthDialog(host)
        host.growth_dialog = dialog
        try:
            with mock.patch.object(host, "_open_research_dialog") as research, \
                    mock.patch.object(host, "_open_memory_curation_dialog") as memory, \
                    mock.patch.object(host, "_open_persona_dialog") as persona:
                dialog.research_button.click()
                dialog.memory_curation_button.click()
                dialog.persona_button.click()
                research.assert_called_once_with()
                memory.assert_called_once_with()
                persona.assert_called_once_with()
        finally:
            dialog.close()
            host.close()


if __name__ == "__main__":
    unittest.main()
