"""Periodic memory health is read-only and explicitly opt-in."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from evolution_service import EvolutionService
from memory_engine import MemoryEngine
from memory_health import MemoryHealthConfig, MemoryHealthHost

APP = QApplication.instance() or QApplication([])


class MemoryHealthConfigTests(unittest.TestCase):
    def test_absent_config_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            config = MemoryHealthConfig(Path(directory) / "health.json")
            self.assertFalse(config.enabled)
            self.assertEqual(config.interval, 6 * 60 * 60)

    def test_explicit_enable_persists_and_stop_disables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            config = MemoryHealthConfig(path, clock=lambda: 100)
            self.assertTrue(config.set(True, 3600, latest={"createdAt": 100, "counts": {"archive": 2}}))
            loaded = MemoryHealthConfig(path)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.interval, 3600)
            self.assertEqual(loaded.counts["archive"], 2)
            loaded.set(False, 3600)
            self.assertFalse(MemoryHealthConfig(path).enabled)

    def test_corrupt_config_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            path.write_text("{bad", encoding="utf-8")
            config = MemoryHealthConfig(path)
            self.assertFalse(config.enabled)
            config.set(True, 1)
            self.assertEqual(config.interval, 60)


class MemoryHealthHostTests(unittest.TestCase):
    def test_scan_is_read_only_and_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dsh-dafeiyu-growth"
            root.mkdir()
            memory = MemoryEngine(root / "memory.json", clock=lambda: 1_800_000_000)
            memory.remember_research("public fact", now=1_800_000_000)
            before = memory.snapshot()["total"]
            service = EvolutionService(root)
            config = MemoryHealthConfig(root / "health.json", clock=lambda: 1_800_000_000)
            host = MemoryHealthHost(service, memory, config=config)
            try:
                plan = host.scan()
                self.assertEqual(plan["records"], before)
                self.assertEqual(memory.snapshot()["total"], before)
                self.assertFalse(config.enabled)
                self.assertEqual(service.status()["auditRecent"][-1]["decision"], "scanned")
                self.assertEqual(host.state()["records"], before)
            finally:
                host.shutdown()

    def test_configure_enable_scans_without_deleting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dsh-dafeiyu-growth"
            root.mkdir()
            memory = MemoryEngine(root / "memory.json", clock=lambda: 1_800_000_000)
            memory.remember_research("keep me", now=1_800_000_000)
            service = EvolutionService(root)
            config = MemoryHealthConfig(root / "health.json")
            host = MemoryHealthHost(service, memory, config=config)
            try:
                host.configure(True, 3600)
                self.assertTrue(host.timer.isActive())
                self.assertEqual(memory.snapshot()["total"], 1)
                self.assertTrue(config.enabled)
                host.configure(False, 3600)
                self.assertFalse(host.timer.isActive())
                reloaded = MemoryHealthConfig(root / "health.json")
                self.assertFalse(reloaded.enabled)
            finally:
                host.shutdown()
