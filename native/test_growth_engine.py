"""Growth engine contract: versioned, bounded, idempotent and non-inventing."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from growth_engine import GrowthEngine, VERSION, level_from_xp


class GrowthEngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'growth.json'
        self.now = datetime(2026, 11, 2, 12, 0, 0).timestamp()

    def tearDown(self):
        self.directory.cleanup()

    def engine(self):
        return GrowthEngine(self.path, clock=lambda: self.now)

    @staticmethod
    def state(xp=0, days=None):
        return {'xp': xp, 'days': days or {}, 'counters': {}}

    def test_first_sync_creates_versioned_file_with_start_milestone(self):
        engine = self.engine()
        snapshot = engine.sync(self.state(20))
        self.assertTrue(self.path.exists())
        self.assertEqual(snapshot['status'], 'ready')
        self.assertEqual(snapshot['revision'], 1)
        self.assertEqual(snapshot['milestones'], 1)
        document = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(document['version'], VERSION)
        self.assertEqual(document['xp'], 20)
        self.assertEqual(document['milestones'][0]['kind'], 'start')

    def test_idempotent_sync_does_not_rewrite_or_bump_revision(self):
        engine = self.engine()
        first = engine.sync(self.state(120))
        stamp = self.path.stat().st_mtime_ns
        second = engine.sync(self.state(120))
        self.assertEqual(second['revision'], first['revision'])
        self.assertEqual(second['milestones'], first['milestones'])
        self.assertEqual(self.path.stat().st_mtime_ns, stamp)

    def test_level_and_stage_milestones_and_reset(self):
        engine = self.engine()
        engine.sync(self.state(100))
        level_up = engine.sync(self.state(9000))
        self.assertEqual(level_up['level'], level_from_xp(9000))
        self.assertEqual(level_up['stage']['key'], 'fish')
        self.assertGreaterEqual(level_up['milestones'], 3)
        reset = engine.sync(self.state(0))
        self.assertEqual((reset['xp'], reset['level']), (0, 1))
        self.assertEqual(reset['days'], {})
        self.assertGreaterEqual(reset['milestones'], level_up['milestones'] + 1)

    def test_streak_is_derived_from_days_not_trusted_input(self):
        today = datetime(2026, 11, 2).date()
        days = {today.isoformat(): 3, '2026-11-01': 2, '2026-10-31': 1}
        snapshot = self.engine().sync({'xp': 80, 'days': days})
        self.assertEqual(snapshot['streak'], 3)
        self.assertEqual(snapshot['days'][today.isoformat()], 3)

    def test_corrupt_and_unknown_files_are_preserved_and_protected(self):
        self.path.write_text('{not json', encoding='utf-8')
        before = self.path.read_bytes()
        engine = self.engine()
        self.assertEqual(engine.status, 'protected')
        self.assertIsNone(engine.sync(self.state(100)))
        self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text(json.dumps({'version': 99}), encoding='utf-8')
        self.assertEqual(self.engine().status, 'protected')

    def test_write_failure_preserves_previous_snapshot_on_disk_and_memory(self):
        engine = self.engine()
        engine.sync(self.state(120))
        before = self.path.read_bytes()
        with mock.patch('growth_engine.os.replace', side_effect=OSError('disk')):
            snapshot = engine.sync(self.state(500))
        self.assertEqual(snapshot['status'], 'error')
        self.assertEqual(snapshot['xp'], 120)
        self.assertEqual(self.path.read_bytes(), before)

    def test_days_are_bounded_deduplicated_and_recent(self):
        days = {f'2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}': i for i in range(300)}
        engine = self.engine()
        snapshot = engine.sync({'xp': 10, 'days': days})
        self.assertLessEqual(len(snapshot['days']), 400)
        self.assertTrue(all(key.count('-') == 2 for key in snapshot['days']))

    def test_malformed_state_is_ignored(self):
        engine = self.engine()
        for bad in (None, {}, {'xp': '100'}, {'xp': float('nan')}, {'xp': -5}):
            self.assertIsNone(engine.sync(bad))
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
