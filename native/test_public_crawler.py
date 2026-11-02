"""Crawler and memory-reply contracts; all HTTP is faked."""
import base64
import json
import unittest
from pathlib import Path
import tempfile

from public_crawler import CrawlError, PoetryCrawler
from memory_engine import MemoryEngine
from memory_reply import compose_memory_answer, compose_public_answer


def encoded_poems():
    poems = [{"title": "关雎", "content": ["关关雎鸠，在河之洲。", "窈窕淑女，君子好逑。"]},
             {"title": "蒹葭", "content": ["蒹葭苍苍，白露为霜。", "所谓伊人，在水一方。"]}]
    payload = {"content": base64.b64encode(json.dumps(poems, ensure_ascii=False).encode()).decode()}
    return json.dumps(payload).encode()


class CrawlerTests(unittest.TestCase):
    def test_fixed_poetry_source_and_bounded_record(self):
        calls = []
        def fake(url, limit):
            calls.append(url)
            return encoded_poems()
        record = PoetryCrawler(fetcher=fake, clock=lambda: 0).fetch_poem()
        self.assertEqual(record["kind"], "culture")
        self.assertEqual(record["topic"], "关雎")
        self.assertIn("关关雎鸠", record["text"])
        self.assertEqual(record["license"], "MIT repository / public-domain text")
        self.assertEqual(len(calls), 1)
        self.assertIn("chinese-poetry", calls[0])

    def test_non_allowlisted_urls_are_rejected_before_network(self):
        for url in ("http://api.github.com/x", "https://evil.example/x",
                    "https://api.github.com/repos/other/repo"):
            with self.assertRaises(CrawlError):
                PoetryCrawler._default_fetch(url, 1024)

    def test_malformed_payloads_fail_closed(self):
        for payload in (b"not json", b'{"content":"!!!"}', b'{"content":"e30="}'):
            with self.assertRaises(CrawlError):
                PoetryCrawler(fetcher=lambda url, limit, p=payload: p).fetch_poem()


class MemoryReplyTests(unittest.TestCase):
    def test_memory_answer_uses_only_existing_facts(self):
        answer = compose_memory_answer("你还记得我喜欢什么吗", "小林", ["夜里散步"],
                                       [{"kindLabel": "习惯", "text": "喜欢在夜里散步"}])
        self.assertIn("小林", answer)
        self.assertIn("夜里散步", answer)
        self.assertNotIn("生日", answer)

    def test_memory_answer_admits_empty_memory(self):
        answer = compose_memory_answer("你记得我吗", "", [], [])
        self.assertIn("还没有记住", answer)

    def test_public_answer_requires_question_and_relevant_public_hit(self):
        hits = [{"kind": "public", "text": "法国：首都是巴黎", "source": "countries", "score": 30}]
        text, meta = compose_public_answer("法国的首都是什么", hits)
        self.assertIn("巴黎", text)
        self.assertIn("公开知识", meta)
        self.assertEqual(compose_public_answer("我今天很难过", hits), (None, ""))

    def test_external_memory_ingest_and_recall(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = MemoryEngine(Path(directory) / "memory.json", clock=lambda: 1_800_000_000)
            record = {"kind": "culture", "topic": "关雎", "text": "关关雎鸠，在河之洲。",
                      "sourceLabel": "诗经 · chinese-poetry (MIT)", "license": "MIT"}
            self.assertEqual(engine.ingest_external(record), 1)
            self.assertEqual(engine.snapshot()["counts"]["culture"], 1)
            self.assertIn("关雎", engine.recall("关雎")[0]["text"])


if __name__ == '__main__':
    unittest.main()
