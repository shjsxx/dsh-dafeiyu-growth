"""Scheduler tests: approved-only, interval-gated, never a daemon."""
import unittest

from research_scheduler import ResearchScheduler


class FakeQueue:
    def __init__(self, rows):
        self.rows = rows

    def list(self):
        return list(self.rows)


class ResearchSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"id": "a", "status": "proposed"},
            {"id": "b", "status": "approved"},
            {"id": "c", "status": "approved"},
        ]
        self.scheduler = ResearchScheduler(FakeQueue(self.rows), interval=60,
                                           clock=lambda: 0)

    def test_disabled_scheduler_never_selects(self):
        self.assertEqual(self.scheduler.tick(now=100), None)

    def test_enabled_scheduler_only_selects_approved(self):
        self.scheduler.enable(now=100)
        self.assertIsNone(self.scheduler.tick(now=159))
        task = self.scheduler.tick(now=160)
        self.assertEqual(task["id"], "b")
        self.assertNotEqual(task["status"], "proposed")

    def test_interval_gate_and_postpone(self):
        self.scheduler.enable(now=100)
        self.assertIsNone(self.scheduler.tick(now=159))
        self.assertEqual(self.scheduler.tick(now=160)["id"], "b")
        self.assertIsNone(self.scheduler.tick(now=219))
        self.scheduler.postpone(now=220)
        self.assertIsNone(self.scheduler.tick(now=279))
        self.assertEqual(self.scheduler.tick(now=280)["id"], "b")

    def test_no_approved_task_is_idle(self):
        queue = FakeQueue([{"id": "a", "status": "proposed"}])
        scheduler = ResearchScheduler(queue, interval=60, clock=lambda: 0)
        scheduler.enable(now=100)
        self.assertIsNone(scheduler.tick(now=100))
        self.assertEqual(scheduler.next_at, 160)
