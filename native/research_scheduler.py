"""Session-local research scheduler.

It is deliberately not a daemon and never approves work. A caller must enable
it explicitly; each tick only selects the oldest already-approved task, and the
caller remains responsible for running it and enforcing its stored budget.
"""
from __future__ import annotations

import time

MIN_INTERVAL = 15
MAX_INTERVAL = 3600


class ResearchScheduler:
    def __init__(self, queue, interval=60, clock=None):
        self.queue = queue
        self.clock = clock or (lambda: time.time())
        self.enabled = False
        self.next_at = 0.0
        self.interval = self._bounded(interval)

    @staticmethod
    def _bounded(value):
        try:
            return max(MIN_INTERVAL, min(MAX_INTERVAL, int(value)))
        except (TypeError, ValueError):
            return 60

    def set_interval(self, value):
        self.interval = self._bounded(value)
        if self.enabled:
            self.next_at = max(self.next_at, self.clock() + self.interval)

    def enable(self, now=None):
        self.enabled = True
        # Enabling is not a run button: wait one full interval before the first
        # automatic selection, so toggling it on cannot immediately fetch.
        self.next_at = float(self.clock() if now is None else now) + self.interval

    def disable(self):
        self.enabled = False
        self.next_at = 0.0

    def postpone(self, now=None):
        """Start the next interval after a completed run, not after a timer."""
        if self.enabled:
            self.next_at = float(self.clock() if now is None else now) + self.interval

    def due(self, now=None):
        return self.enabled and float(self.clock() if now is None else now) >= self.next_at

    def select_task(self):
        """Only an explicitly approved task is eligible; never propose/approve."""
        try:
            rows = self.queue.list()
        except Exception:
            return None
        return next((row for row in rows if row.get("status") == "approved"), None)

    def tick(self, now=None):
        """Return one approved task when due, otherwise None.

        The caller starts the run and prevents overlap; this class does not
        spawn threads or make network requests.
        """
        stamp = float(self.clock() if now is None else now)
        if not self.due(stamp):
            return None
        task = self.select_task()
        self.next_at = stamp + self.interval
        return task
