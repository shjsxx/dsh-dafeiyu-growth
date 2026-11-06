"""Process-lifetime scheduler host is off by default and explicit."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from research_runtime import ResearchSchedulerHost

APP = QApplication.instance() or QApplication([])


class FakeQueue:
    def list(self):
        return []


class FakeService:
    def __init__(self):
        self.research = FakeQueue()
        self.calls = []

    def run_research(self, task_id):
        self.calls.append(task_id)
        return {"id": task_id, "status": "done"}


class SchedulerHostTests(unittest.TestCase):
    def test_host_defaults_off_and_can_be_stopped(self):
        service = FakeService()
        host = ResearchSchedulerHost(service)
        try:
            self.assertFalse(host.state()["enabled"])
            host.configure(True, 15)
            self.assertTrue(host.state()["enabled"])
            self.assertTrue(host.timer.isActive())
            host.configure(False, 15)
            self.assertFalse(host.state()["enabled"])
            self.assertFalse(host.timer.isActive())
            self.assertEqual(service.calls, [])
        finally:
            host.shutdown()

    def test_host_config_is_optional_and_does_not_write_without_it(self):
        service = FakeService()
        host = ResearchSchedulerHost(service)
        try:
            self.assertFalse(host.state()["persisted"])
            host.configure(True, 30)
            self.assertEqual(host.scheduler.interval, 30)
        finally:
            host.shutdown()


if __name__ == "__main__":
    unittest.main()
