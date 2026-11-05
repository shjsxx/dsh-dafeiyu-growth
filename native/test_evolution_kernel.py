"""Tests for the v0.33 controlled-evolution policy and ledger."""
import json
import tempfile
import unittest
from pathlib import Path

from evolution_kernel import (
    EvolutionError,
    EvolutionKernel,
    EvolutionLedger,
    INVARIANTS,
    PolicyGate,
    TRAIT_DIMENSIONS,
)


class EvolutionKernelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.ledger_path = self.root / "evolution.json"
        self.now = 1_800_000_000

    def tearDown(self):
        self.directory.cleanup()

    def ledger(self):
        return EvolutionLedger(self.ledger_path, clock=lambda: self.now)

    @staticmethod
    def persona(**changes):
        traits = {key: 50.0 for key in TRAIT_DIMENSIONS}
        traits.update(changes)
        return {**traits, **INVARIANTS}

    def test_ledger_append_verify_and_reload(self):
        ledger = self.ledger()
        event = ledger.append("network", "allowed", "public allowlist source", meta={"host": "github.com"})
        self.assertEqual(event["seq"], 1)
        self.assertTrue(ledger.verify()["ok"])
        reloaded = self.ledger()
        self.assertTrue(reloaded.verify()["ok"])
        self.assertEqual(reloaded.recent(1)[0]["meta"]["host"], "github.com")

    def test_ledger_detects_tampering(self):
        ledger = self.ledger()
        ledger.append("network", "allowed", "ok")
        document = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        document["events"][0]["reason"] = "tampered"
        self.ledger_path.write_text(json.dumps(document), encoding="utf-8")
        protected = self.ledger()
        self.assertEqual(protected.status, "protected")
        self.assertFalse(protected.verify()["ok"])
        with self.assertRaises(EvolutionError):
            protected.append("network", "allowed", "ok")

    def test_ledger_does_not_store_full_url(self):
        ledger = self.ledger()
        ledger.append("network", "allowed", "ok", summary="https://github.com/very/private/query?token=secret",
                      meta={"host": "github.com"})
        body = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn("private/query", body)
        self.assertNotIn("secret", body)

    def test_network_allowlist_and_budget(self):
        gate = PolicyGate({"github.com"})
        self.assertTrue(gate.network("https://github.com/public").allowed)
        self.assertFalse(gate.network("http://github.com/public").allowed)
        self.assertFalse(gate.network("https://evil.example/public").allowed)
        self.assertFalse(gate.network("https://github.com/public", method="POST").allowed)
        self.assertFalse(gate.network("https://github.com/public", used=20, limit=20).allowed)
        self.assertFalse(gate.network("https://user:pass@github.com/public").allowed)
        self.assertFalse(gate.network("https://127.0.0.1/public").allowed)

    def test_deletion_requires_approval_and_backup(self):
        gate = PolicyGate({"github.com"})
        self.assertFalse(gate.deletion("memory", 3, False, "snap").allowed)
        self.assertFalse(gate.deletion("memory", 3, True, "").allowed)
        self.assertTrue(gate.deletion("memory", 3, True, "snap-1").allowed)

    def test_persona_core_is_immutable_and_drift_is_bounded(self):
        gate = PolicyGate({"github.com"})
        current = self.persona()
        self.assertTrue(gate.persona(current, self.persona(warmth=50.5)).allowed)
        self.assertFalse(gate.persona(current, self.persona(warmth=55)).allowed)
        bad = self.persona(warmth=50)
        bad["no_silent_delete"] = False
        self.assertFalse(gate.persona(current, bad).allowed)
        out_of_range = self.persona(warmth=101)
        self.assertFalse(gate.persona(current, out_of_range).allowed)

    def test_self_modification_needs_approval_and_rollback_point(self):
        gate = PolicyGate({"github.com"})
        self.assertFalse(gate.self_modify(False, "snap").allowed)
        self.assertFalse(gate.self_modify(True, "").allowed)
        self.assertTrue(gate.self_modify(True, "snap").allowed)

    def test_kernel_records_both_allowed_and_denied_decisions(self):
        kernel = EvolutionKernel(self.ledger(), PolicyGate({"github.com"}))
        self.assertTrue(kernel.review_network("https://github.com/public").allowed)
        self.assertFalse(kernel.review_network("https://evil.example/private").allowed)
        self.assertFalse(kernel.review_deletion("memory", 1, False, "").allowed)
        recent = kernel.ledger.recent(10)
        self.assertEqual([row["decision"] for row in recent], ["allowed", "denied", "denied"])
        self.assertTrue(kernel.ledger.verify()["ok"])


if __name__ == "__main__":
    unittest.main()
