"""Web-learning dialog construction; no network and no real profile."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from web_learning import UserWebSources, WebArticleFetcher, WebLearningService
from web_learning_ui import WebLearningDialog

APP = QApplication.instance() or QApplication([])


def resolver(host, port, type=None):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


class WebLearningUiTests(unittest.TestCase):
    def test_dialog_lists_explicit_sources_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = UserWebSources(root / "web-sources.json", resolver=resolver)
            sources.add("https://example.com/")
            service = WebLearningService(root, sources=sources,
                                         fetcher=WebArticleFetcher(sources, resolver=resolver))
            dialog = WebLearningDialog(service=service)
            try:
                self.assertEqual(dialog.sources.count(), 1)
                self.assertIn("example.com", dialog.sources.item(0).text())
                self.assertTrue(dialog.learn_button.isEnabled())
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
