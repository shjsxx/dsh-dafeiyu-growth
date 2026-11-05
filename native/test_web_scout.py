"""Tests for the controlled WebScout; all transports are fake, no network."""
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from evolution_kernel import EvolutionKernel, EvolutionLedger, PolicyGate
from web_scout import ResearchTask, WebScout


@dataclass
class FakeResponse:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    final_url: str = ""


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, timeout, max_bytes, headers):
        self.calls.append({"url": url, "headers": dict(headers), "maxBytes": max_bytes, "timeout": timeout})
        if not self.responses:
            raise AssertionError("unexpected network call")
        return self.responses.pop(0)


class WebScoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.ledger = EvolutionLedger(self.root / "evolution.json", clock=lambda: 1_800_000_000)
        self.kernel = EvolutionKernel(self.ledger, PolicyGate({"example.com", "allowed.test"}))
        self.cache = self.root / "cache"
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def scout(self, responses):
        return WebScout(self.kernel, self.cache, transport=FakeTransport(responses), clock=lambda: self.now)

    def test_fetch_extracts_text_citation_and_cache(self):
        transport = FakeTransport([FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"},
                                                b"<html><head><title>Example</title><script>bad()</script></head>"
                                                b"<body><h1>Hello</h1><p>Public text</p></body></html>",
                                                "https://example.com/page")])
        scout = WebScout(self.kernel, self.cache, transport=transport, clock=lambda: self.now)
        task = ResearchTask("learn", max_requests=2)
        first = scout.fetch("https://example.com/page", task)
        self.assertTrue(first["ok"])
        self.assertEqual(first["title"], "Example")
        self.assertIn("Public text", first["text"])
        self.assertNotIn("bad()", first["text"])
        self.assertEqual(first["citation"]["trust"], "untrusted")
        self.assertNotIn("Cookie", " ".join(transport.calls[0]["headers"]))
        self.assertEqual(task.used, 1)

        second = scout.fetch("https://example.com/page", task)
        self.assertTrue(second["ok"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(transport.calls), 1)

    def test_request_budget_stops_before_transport(self):
        transport = FakeTransport([FakeResponse(200, {"Content-Type": "text/plain"}, b"ok", "https://example.com/a")])
        scout = WebScout(self.kernel, self.cache, transport=transport, clock=lambda: self.now)
        task = ResearchTask("learn", max_requests=1)
        self.assertTrue(scout.fetch("https://example.com/a", task)["ok"])
        result = scout.fetch("https://example.com/b", task)
        self.assertFalse(result["ok"])
        self.assertIn("budget", result["error"])
        self.assertEqual(len(transport.calls), 1)

    def test_allowed_redirect_is_checked_and_followed(self):
        transport = FakeTransport([
            FakeResponse(302, {"Location": "https://allowed.test/final"}, b"", "https://example.com/start"),
            FakeResponse(200, {"Content-Type": "text/plain"}, b"final text", "https://allowed.test/final"),
        ])
        scout = WebScout(self.kernel, self.cache, transport=transport, clock=lambda: self.now)
        task = ResearchTask("learn", max_requests=3)
        result = scout.fetch("https://example.com/start", task)
        self.assertTrue(result["ok"])
        self.assertIn("final", result["citation"]["url"])
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(task.used, 2)

    def test_redirect_to_unlisted_host_is_denied(self):
        transport = FakeTransport([FakeResponse(302, {"Location": "https://evil.example/private"}, b"", "https://example.com/start")])
        scout = WebScout(self.kernel, self.cache, transport=transport, clock=lambda: self.now)
        task = ResearchTask("learn", max_requests=3)
        result = scout.fetch("https://example.com/start", task)
        self.assertFalse(result["ok"])
        self.assertIn("redirect denied", result["error"])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.ledger.recent(1)[0]["decision"], "denied")

    def test_byte_budget_and_content_type_are_enforced(self):
        transport = FakeTransport([
            FakeResponse(200, {"Content-Type": "text/plain"}, b"x" * 20, "https://example.com/big"),
            FakeResponse(200, {"Content-Type": "image/png"}, b"png", "https://example.com/image"),
        ])
        scout = WebScout(self.kernel, self.cache, transport=transport, clock=lambda: self.now)
        too_small = scout.fetch("https://example.com/big", ResearchTask("learn", max_bytes=10))
        self.assertFalse(too_small["ok"])
        self.assertIn("byte budget", too_small["error"])
        wrong_type = scout.fetch("https://example.com/image", ResearchTask("learn"))
        self.assertFalse(wrong_type["ok"])
        self.assertIn("content type", wrong_type["error"])

    def test_research_returns_citations_and_clear_cache(self):
        transport = FakeTransport([FakeResponse(200, {"Content-Type": "text/plain"}, b"one", "https://example.com/one")])
        scout = WebScout(self.kernel, self.cache, transport=transport, clock=lambda: self.now)
        result = scout.research(["https://example.com/one", "https://example.com/two"],
                                ResearchTask("learn", max_requests=1))
        self.assertEqual(len(result["citations"]), 1)
        self.assertEqual(result["citations"][0]["host"], "example.com")
        self.assertGreaterEqual(scout.clear_cache(), 1)

    def test_ledger_hash_chain_survives_web_decisions(self):
        transport = FakeTransport([FakeResponse(200, {"Content-Type": "text/plain"}, b"ok", "https://example.com/a")])
        scout = WebScout(self.kernel, self.cache, transport=transport, clock=lambda: self.now)
        scout.fetch("https://example.com/a", ResearchTask("learn"))
        scout.fetch("https://evil.example/b", ResearchTask("learn"))
        self.assertTrue(self.ledger.verify()["ok"])
        self.assertEqual([row["decision"] for row in self.ledger.recent(2)], ["allowed", "denied"])


if __name__ == "__main__":
    unittest.main()
