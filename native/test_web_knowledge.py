"""Independent web knowledge: URL/content dedupe, bounded eviction, retrieval."""
import tempfile
import unittest
from pathlib import Path

from web_knowledge import WebKnowledgeStore, canonical_url, content_hash


class WebKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "web-knowledge.json"

    def store(self, **kwargs):
        return WebKnowledgeStore(self.path, **kwargs)

    def test_canonical_url_removes_fragment_and_tracking(self):
        one = canonical_url("https://Example.com/a/?utm_source=x&b=2")
        two = canonical_url("https://example.com/a?b=2#section")
        self.assertEqual(one, two)

    def test_url_and_content_duplicates_are_rejected(self):
        store = self.store()
        first = store.add("https://example.com/a", title="A", text="A useful public article about memory.")
        self.assertTrue(first["ok"])
        self.assertEqual(store.add("https://example.com/a?utm_source=x", title="A2",
                                   text="Different text long enough for the store.")["reason"],
                         "duplicate_url")
        self.assertEqual(store.add("https://example.com/b", title="B",
                                   text="A useful public article about memory.")["reason"],
                         "duplicate_content")

    def test_new_page_is_kept_while_low_value_old_page_is_evicted(self):
        store = self.store(max_records=50)
        for index in range(60):
            result = store.add(f"https://example.com/{index}", title=f"T{index}",
                               text=f"public article number {index} " + "x" * 30,
                               quality=index + 1)
            self.assertTrue(result["ok"])
        stats = store.stats()
        self.assertEqual(stats["total"], 50)
        titles = [row["title"] for row in store.data["records"]]
        self.assertIn("T59", titles)
        self.assertNotIn("T0", titles)

    def test_search_is_local_and_bounded(self):
        store = self.store()
        store.add("https://example.com/python", title="Python memory",
                  text="Python uses references and garbage collection.")
        hits = store.search("Python memory", 3)
        self.assertTrue(hits)
        self.assertIn("Python", hits[0]["title"])
        self.assertLessEqual(len(hits), 3)

    def test_restart_preserves_records(self):
        store = self.store()
        store.add("https://example.com/a", title="A", text="A useful public article about memory.")
        loaded = self.store()
        self.assertEqual(loaded.stats()["total"], 1)
        self.assertTrue(loaded.has_url("https://example.com/a#x"))


if __name__ == "__main__":
    unittest.main()
