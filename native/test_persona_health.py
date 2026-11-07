"""Periodic persona drift checks are read-only and explicitly opt-in."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from evolution_service import EvolutionService
from persona_health import PersonaHealthConfig, PersonaHealthHost
from persona_history import PersonaHistory
from personality_engine import PersonalityEngine

APP = QApplication.instance() or QApplication([])
TRAITS = ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")


class PersonaHealthConfigTests(unittest.TestCase):
    def test_absent_config_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            config = PersonaHealthConfig(Path(directory) / "persona-health.json")
            self.assertFalse(config.enabled)
            self.assertEqual(config.interval, 6 * 60 * 60)

    def test_explicit_enable_persists_and_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "persona-health.json"
            config = PersonaHealthConfig(path, clock=lambda: 100)
            self.assertTrue(config.set(True, 120, latest={"status": "needs_review",
                                                           "fingerprint": "abc", "changes": 1}))
            loaded = PersonaHealthConfig(path)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.interval, 120)
            self.assertEqual(loaded.last_status, "needs_review")
            loaded.set(False, 120)
            self.assertFalse(PersonaHealthConfig(path).enabled)


class PersonaHealthHostTests(unittest.TestCase):
    def test_review_is_emitted_without_mutating_personality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dsh-dafeiyu-growth"
            root.mkdir()
            traits = {key: 50.0 for key in TRAITS}
            changed = {**traits, "warmth": 55.0}
            personality_path = root / "personality.json"
            personality_path.write_text(json.dumps({
                "version": 2, "revision": 1, "traits": changed,
                "interactions": 1, "updatedAt": 1_800_000_002, "observedTurns": 1,
            }), encoding="utf-8")
            before = personality_path.read_bytes()
            history = PersonaHistory(root / "persona-history.json", clock=lambda: 1_800_000_001)
            history.record({"revision": 0, "traits": traits}, now=1_800_000_001)
            service = EvolutionService(root)
            config = PersonaHealthConfig(root / "persona-health.json", clock=lambda: 1_800_000_002)
            host = PersonaHealthHost(service, PersonalityEngine(personality_path), history, config=config)
            reviews = []
            host.review_needed.connect(lambda row: reviews.append(row))
            try:
                result = host.scan()
                self.assertEqual(result.status, "needs_review")
                self.assertEqual(len(reviews), 1)
                self.assertEqual(personality_path.read_bytes(), before)
                self.assertEqual(host.state()["changes"], 1)
                self.assertEqual(service.status()["auditRecent"][-1]["decision"], "review")
            finally:
                host.shutdown()

    def test_configure_enable_is_explicit_and_does_not_change_personality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dsh-dafeiyu-growth"
            root.mkdir()
            traits = {key: 50.0 for key in TRAITS}
            personality_path = root / "personality.json"
            personality_path.write_text(json.dumps({
                "version": 2, "revision": 0, "traits": traits,
                "interactions": 0, "updatedAt": 1_800_000_000, "observedTurns": 0,
            }), encoding="utf-8")
            before = personality_path.read_bytes()
            history = PersonaHistory(root / "persona-history.json")
            history.record({"revision": 0, "traits": traits})
            service = EvolutionService(root)
            config = PersonaHealthConfig(root / "persona-health.json")
            host = PersonaHealthHost(service, PersonalityEngine(personality_path), history, config=config)
            try:
                host.configure(True, 3600)
                self.assertTrue(host.timer.isActive())
                self.assertTrue(config.enabled)
                self.assertEqual(personality_path.read_bytes(), before)
                host.configure(False, 3600)
                self.assertFalse(host.timer.isActive())
                self.assertFalse(PersonaHealthConfig(root / "persona-health.json").enabled)
            finally:
                host.shutdown()
