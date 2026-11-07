"""Startup imports that have previously broken visual mode must stay importable."""
import unittest


class StartupImportTests(unittest.TestCase):
    def test_legacy_public_learning_api_is_available(self):
        import web_learning
        self.assertTrue(callable(web_learning.fetch_builtin_lexicon))
        self.assertIsInstance(web_learning.PUBLIC_SOURCE_NAME, str)

    def test_visual_runtime_imports_without_creating_a_window(self):
        import runtime.helper  # noqa: F401
        import runtime.public_learning_runtime  # noqa: F401


if __name__ == "__main__":
    unittest.main()
