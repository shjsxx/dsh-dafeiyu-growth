"""Tests for the v1.0 integration facade; fake transport only."""
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from evolution_kernel import EvolutionKernel, EvolutionLedger, INVARIANTS, PolicyGate
from evolution_service import EvolutionService
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
        return self.responses.pop(0)


class EvolutionServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.transport = FakeTransport([])
        self.kernel = EvolutionKernel(
            EvolutionLedger(self.root / "evolution.json", clock=lambda: 1_800_000_000),
            PolicyGate({"example.com"}))
        self.service = EvolutionService(
            self.root, {"example.com"},
            scout=WebScout(self.kernel, self.root / "cache", transport=self.transport,
                           clock=lambda: 1_800_000_000),
            clock=lambda: 1_800_000_000)

    def tearDown(self):
        self.directory.cleanup()

    def test_status_is_local_and_bounded(self):
        status = self.service.status()
        self.assertTrue(status["localOnly"])
        self.assertEqual(status["network"]["mode"], "allowlist")
        self.assertEqual(status["memory"]["mode"], "proposal-only")
        self.assertEqual(status["persona"]["mode"], "bounded-reflection")
        self.assertTrue(status["audit"]["ok"])
        self.assertEqual(status["research"]["total"], 0)

    def test_research_requires_approval_and_runs_explicitly(self):
        self.transport.responses.append(FakeResponse(
            200, {"Content-Type": "text/plain"}, b"fact", "https://example.com/page"))
        task = self.service.propose_research("learn", ["https://example.com/page"])
        self.assertEqual(task["status"], "proposed")
        self.assertEqual(self.transport.calls, [])
        self.service.approve_research(task["id"])
        done = self.service.run_research(task["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.service.status()["research"]["statuses"]["done"], 1)
        recent = self.service.status()["auditRecent"]
        decisions = [row["decision"] for row in recent]
        self.assertIn("proposed", decisions)
        self.assertIn("approved", decisions)
        self.assertIn("started", decisions)
        self.assertIn("done", decisions)
        research_decisions = [row["decision"] for row in recent if row["kind"] == "research"]
        self.assertEqual(research_decisions, ["proposed", "approved", "started", "done"])

    def test_record_audit_is_bounded_and_failure_does_not_escape(self):
        self.assertTrue(self.service.record_audit("memory", "restored", "explicit", "1 record",
                                                  {"count": 1}))
        events = self.service.status()["auditRecent"]
        self.assertEqual(events[-1]["kind"], "memory")
        self.assertEqual(events[-1]["decision"], "restored")
        self.assertEqual(len(events), 1)

    def test_metrics_are_numeric_local_operational_values(self):
        metrics = self.service.metrics()
        self.assertEqual(metrics["research"]["total"], 0)
        self.assertIsNone(metrics["research"]["successRate"])
        self.assertIsInstance(metrics["memory"]["total"], int)
        self.assertIsInstance(metrics["persona"]["historyEntries"], int)
        self.assertEqual(set(metrics["automation"]),
                         {"researchScheduler", "memoryHealth", "personaHealth"})
        self.assertIn("byKind", metrics["audit"])

    def test_memory_and_persona_are_read_only_helpers(self):
        plan = self.service.plan_memory([
            {"id": "a", "kind": "preference", "slot": "name", "text": "称呼：小云",
             "importance": 80, "hits": 2, "lastSeen": 1_800_000_000},
        ])
        self.assertTrue(plan["requiresApproval"])
        previous = {"revision": 1, "traits": {key: 50 for key in
                    ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")}, **INVARIANTS}
        current = {"revision": 2, "traits": {**previous["traits"], "warmth": 50.5}, **INVARIANTS}
        reflected = self.service.reflect_persona(previous, current, [])
        self.assertIn(reflected.status, ("ok", "needs_review"))
        self.assertEqual(self.service.status()["memory"]["autoDelete"], False)

    def test_run_research_rejects_unapproved_task(self):
        task = self.service.propose_research("learn", ["https://example.com/page"])
        with self.assertRaises(Exception):
            self.service.run_research(task["id"])


if __name__ == "__main__":
    unittest.main()
