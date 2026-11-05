"""Tests for the explicit v0.34b research queue; no real network."""
import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from evolution_kernel import EvolutionKernel, EvolutionLedger, PolicyGate
from research_queue import ResearchError, ResearchQueue
from web_scout import WebScout


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
        self.calls.append(url)
        if not self.responses:
            raise AssertionError("unexpected network call")
        return self.responses.pop(0)


class ResearchQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.kernel = EvolutionKernel(
            EvolutionLedger(self.root / "evolution.json", clock=lambda: 1_800_000_000),
            PolicyGate({"example.com", "allowed.test"}))
        self.transport = FakeTransport([])
        self.scout = WebScout(self.kernel, self.root / "cache", transport=self.transport,
                              clock=lambda: 1_800_000_000)
        self.queue = ResearchQueue(self.root / "research.json", self.scout, clock=lambda: 1_800_000_000)

    def tearDown(self):
        self.directory.cleanup()

    def test_proposal_needs_approval_and_memory_needs_second_confirmation(self):
        self.transport.responses.append(FakeResponse(
            200, {"Content-Type": "text/html"}, b"<title>Source</title><p>Useful fact.</p>",
            "https://example.com/page"))
        task = self.queue.propose("learn a fact", ["https://example.com/page"], max_requests=2)
        self.assertEqual(task["status"], "proposed")
        self.assertEqual(self.transport.calls, [])
        self.assertIsNone(self.queue.run_next())
        self.queue.approve(task["id"])
        done = self.queue.run_next()
        self.assertEqual(done["status"], "done")
        self.assertIn("Useful fact", done["summary"])
        self.assertEqual(len(done["citations"]), 1)
        with self.assertRaises(ResearchError):
            self.queue.export_for_memory(task["id"], approved=False)
        exported = self.queue.export_for_memory(task["id"], approved=True)
        self.assertEqual(exported["trust"], "untrusted")
        self.queue.mark_committed(task["id"])
        with self.assertRaises(ResearchError):
            self.queue.export_for_memory(task["id"], approved=True)

    def test_pause_resume_and_cancel(self):
        task = self.queue.propose("learn", ["https://example.com/a"])
        self.queue.pause(task["id"])
        self.assertEqual(self.queue.list()[0]["status"], "paused")
        self.queue.resume(task["id"])
        self.assertEqual(self.queue.list()[0]["status"], "approved")
        self.queue.cancel(task["id"])
        self.assertEqual(self.queue.list()[0]["status"], "cancelled")

    def test_request_budget_stops_at_limit(self):
        self.transport.responses.append(FakeResponse(
            200, {"Content-Type": "text/plain"}, b"one", "https://example.com/one"))
        task = self.queue.propose("budget", ["https://example.com/one", "https://example.com/two"],
                                  max_requests=1)
        self.queue.approve(task["id"])
        done = self.queue.run_next()
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["used"], 1)
        self.assertEqual(len(self.transport.calls), 1)

    def test_failed_fetch_marks_task_failed(self):
        self.transport.responses.append(FakeResponse(500, {"Content-Type": "text/plain"}, b"error",
                                                     "https://example.com/fail"))
        task = self.queue.propose("fail", ["https://example.com/fail"])
        self.queue.approve(task["id"])
        failed = self.queue.run_next()
        self.assertEqual(failed["status"], "failed")
        self.assertIn("HTTP 500", failed["results"][0]["error"])

    def test_queue_persists_and_recovers_running_task(self):
        task = self.queue.propose("persist", ["https://example.com/a"])
        self.queue.approve(task["id"])
        self.queue._find(task["id"])["status"] = "running"
        self.queue._save()
        reloaded = ResearchQueue(self.root / "research.json", self.scout, clock=lambda: 1_800_000_000)
        self.assertEqual(reloaded.list()[0]["status"], "paused")
        self.assertIn("interrupted", reloaded.list()[0]["error"])

    def test_rejects_non_allowlisted_urls_before_any_fetch(self):
        with self.assertRaises(ResearchError):
            self.queue.propose("bad", ["https://evil.example/page"])
        self.assertEqual(self.queue.list(), [])
        self.assertEqual(self.transport.calls, [])

    def test_corrupt_queue_is_protected(self):
        self.queue.path.write_text("{bad", encoding="utf-8")
        reloaded = ResearchQueue(self.queue.path, self.scout, clock=lambda: 1_800_000_000)
        self.assertEqual(reloaded.status, "protected")
        with self.assertRaises(ResearchError):
            reloaded.propose("x", ["https://example.com/a"])

    def test_clear_finished(self):
        task = self.queue.propose("clear", ["https://example.com/a"])
        self.queue.cancel(task["id"])
        self.assertEqual(self.queue.clear_finished(), 1)
        self.assertEqual(self.queue.list(), [])


if __name__ == "__main__":
    unittest.main()
