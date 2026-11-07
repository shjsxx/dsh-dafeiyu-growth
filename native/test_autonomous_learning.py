"""Autonomous catalog learning: feed parsing, category selection, config gates."""
import tempfile
import unittest
from pathlib import Path

from autonomous_learning import (
    AutonomousLearner, AutonomousLearningConfig, CATALOG, parse_feed,
)

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>First article</title><link>https://example.com/one</link><description>One</description></item>
<item><title>Second article</title><link>https://example.com/two</link><description>Two</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom article</title><link rel="alternate" href="https://example.com/atom"/><summary>Text</summary></entry>
</feed>"""


class FakeSources:
    def allows(self, url):
        return True


class FakeFetcher:
    def fetch(self, url, allow_xml=False):
        self.last = (url, allow_xml)
        return {"text": RSS.decode("utf-8")}


class FakeService:
    def __init__(self):
        self.sources = FakeSources()
        self.fetcher = FakeFetcher()
        self.added = []
        self.learned = []

    def add_source(self, url):
        self.added.append(url)
        return {"ok": True}

    def learn(self, url):
        self.learned.append(url)
        return {"ok": True, "title": "First article", "chars": 10, "added": 1}


class AutonomousLearningTests(unittest.TestCase):
    def test_parse_rss_and_atom_are_bounded(self):
        rss = parse_feed(RSS)
        self.assertEqual(rss[0]["title"], "First article")
        self.assertEqual(rss[0]["link"], "https://example.com/one")
        atom = parse_feed(ATOM)
        self.assertEqual(atom[0]["title"], "Atom article")
        self.assertEqual(atom[0]["link"], "https://example.com/atom")

    def test_catalog_covers_all_requested_categories(self):
        categories = {row["category"] for row in CATALOG}
        self.assertTrue({"news", "video", "emotion", "office"}.issubset(categories))

    def test_learner_selects_feed_item_without_user_url(self):
        service = FakeService()
        learner = AutonomousLearner(service)
        result = learner.next()
        self.assertTrue(result["ok"])
        self.assertEqual(service.learned, ["https://example.com/one"])
        self.assertTrue(service.fetcher.last[1])  # feed XML explicitly allowed

    def test_config_defaults_off_and_persists_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "auto-learning.json"
            config = AutonomousLearningConfig(path)
            self.assertFalse(config.enabled)
            self.assertTrue(config.set(True, 120))
            loaded = AutonomousLearningConfig(path)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.interval, 120)


if __name__ == "__main__":
    unittest.main()
