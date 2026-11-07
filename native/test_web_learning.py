"""Owner-directed web learning: explicit sites, public-address checks, records."""
import tempfile
import unittest
from pathlib import Path

from emotion_model import EmotionModel
from owner_web_learning import (
    FetchResult, UserWebSources, WebArticleFetcher, WebLearningError, WebLearningService,
    normalize_url,
)


PUBLIC_IP = "93.184.216.34"


def public_resolver(host, port, type=None):
    return [(2, 1, 6, "", (PUBLIC_IP, port))]


def private_resolver(host, port, type=None):
    return [(2, 1, 6, "", ("127.0.0.1", port))]


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.page = b"<html><title>Example title</title><body>Useful public article text for learning.</body></html>"
        self.robots = b"User-agent: *\nAllow: /\n"

    def __call__(self, url, timeout, headers):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FetchResult(200, {"Content-Type": "text/plain"}, self.robots, url)
        return FetchResult(200, {"Content-Type": "text/html"}, self.page, url)


class FakeJournal:
    def __init__(self):
        self.finished = []

    def begin(self, kind, domain, source):
        self.kind = (kind, domain, source)
        return 7

    def finish(self, event_id, status="success", metrics=None, error=""):
        self.finished.append((event_id, status, metrics, error))
        return True


class FakeMemory:
    def __init__(self):
        self.records = []

    def ingest_external(self, record, now=None):
        self.records.append(dict(record))
        return 1


class FakeEmotion:
    def __init__(self, ok=True):
        self.ok = ok
        self.records = []

    def note_web_learning(self, host, title="", chars=0, status="success"):
        self.records.append((host, title, chars, status))
        return self.ok


class WebLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = UserWebSources(self.root / "web-sources.json", resolver=public_resolver)

    def test_source_is_explicit_persistent_and_host_only(self):
        self.assertTrue(self.sources.add("https://example.com/some/page?q=1"))
        loaded = UserWebSources(self.root / "web-sources.json", resolver=public_resolver)
        self.assertTrue(loaded.allows("https://example.com/other"))
        self.assertFalse(loaded.allows("https://other.example.net/"))
        self.assertFalse(loaded.allows("http://127.0.0.1/"))
        self.assertEqual(loaded.snapshot()["sites"][0]["host"], "example.com")

    def test_private_and_credentials_are_rejected(self):
        with self.assertRaises(WebLearningError):
            UserWebSources(self.root / "x.json", resolver=private_resolver).add("https://example.com/")
        with self.assertRaises(WebLearningError):
            normalize_url("https://user:pass@example.com/")
        with self.assertRaises(WebLearningError):
            normalize_url("file:///etc/passwd")

    def test_fetcher_extracts_public_page_and_respects_robots(self):
        self.sources.add("https://example.com/")
        transport = FakeTransport()
        fetcher = WebArticleFetcher(self.sources, resolver=public_resolver, transport=transport)
        result = fetcher.fetch("https://example.com/article")
        self.assertEqual(result["title"], "Example title")
        self.assertIn("Useful public article", result["text"])
        self.assertGreaterEqual(len(transport.calls), 2)  # robots + page

    def test_fetcher_refuses_unadded_redirect(self):
        self.sources.add("https://example.com/")

        def redirect(url, timeout, headers):
            if url.endswith("/robots.txt"):
                return FetchResult(200, {"Content-Type": "text/plain"},
                                   b"User-agent: *\nAllow: /\n", url)
            return FetchResult(302, {"Location": "https://evil.example.net/"}, b"", url)

        fetcher = WebArticleFetcher(self.sources, resolver=public_resolver, transport=redirect)
        with self.assertRaises(WebLearningError) as caught:
            fetcher.fetch("https://example.com/")
        self.assertEqual(caught.exception.code, "redirect_not_added")

    def test_learning_writes_training_and_emotion_before_memory(self):
        self.sources.add("https://example.com/")
        fetcher = WebArticleFetcher(self.sources, resolver=public_resolver, transport=FakeTransport())
        journal, memory, emotion = FakeJournal(), FakeMemory(), FakeEmotion()
        service = WebLearningService(self.root, sources=self.sources, fetcher=fetcher,
                                     memory=memory, emotion=emotion, journal=journal)
        result = service.learn("https://example.com/article")
        self.assertTrue(result["ok"])
        self.assertEqual(journal.kind, ("knowledge_index", "general", "user_web"))
        self.assertEqual(journal.finished[0][1], "success")
        self.assertEqual(emotion.records[0][0], "example.com")
        self.assertEqual(len(memory.records), 1)
        self.assertNotIn("Useful public article", str(journal.finished))

    def test_emotion_record_failure_blocks_memory_and_marks_training_failed(self):
        self.sources.add("https://example.com/")
        fetcher = WebArticleFetcher(self.sources, resolver=public_resolver, transport=FakeTransport())
        journal, memory, emotion = FakeJournal(), FakeMemory(), FakeEmotion(ok=False)
        service = WebLearningService(self.root, sources=self.sources, fetcher=fetcher,
                                     memory=memory, emotion=emotion, journal=journal)
        result = service.learn("https://example.com/article")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "emotion_record_failed")
        self.assertEqual(journal.finished[0][1], "failed")
        self.assertEqual(memory.records, [])

    def test_real_emotion_store_records_web_event_without_user_emotion(self):
        path = self.root / "emotion.json"
        model = EmotionModel(path)
        self.assertTrue(model.note_web_learning("example.com", "Example title", 42, "success"))
        loaded = EmotionModel(path)
        self.assertEqual(loaded.snapshot()["webLearning"], 1)
        self.assertEqual(loaded.snapshot()["mood"], "neutral")
        self.assertEqual(loaded.web_learning[0]["host"], "example.com")
        self.assertNotIn("Useful public article", path.read_text(encoding="utf-8"))

    def test_service_writes_real_training_emotion_and_memory(self):
        from memory_engine import MemoryEngine
        from training_journal import TrainingJournal

        self.sources.add("https://example.com/")
        fetcher = WebArticleFetcher(self.sources, resolver=public_resolver, transport=FakeTransport())
        service = WebLearningService(self.root, sources=self.sources, fetcher=fetcher)
        result = service.learn("https://example.com/article")
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 1)
        journal = TrainingJournal(self.root / "training.json", read_only=True)
        events = [row for row in journal.data["events"] if row["source"] == "user_web"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "knowledge_index")
        self.assertEqual(events[0]["status"], "success")
        self.assertGreater(events[0]["metrics"]["characters"], 0)
        emotion = EmotionModel(self.root / "emotion.json")
        self.assertEqual(emotion.snapshot()["webLearning"], 1)
        memory = MemoryEngine(self.root / "memory.json")
        self.assertGreaterEqual(memory.stats()["total"], 1)


if __name__ == "__main__":
    unittest.main()
