"""Realtime usage is numeric bookkeeping, never model fitting or transcripts."""
import json
from pathlib import Path
import tempfile
import unittest
from training_journal import TrainingJournal


class RealtimeJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'training.json'
        self.journal = TrainingJournal(self.path)
    def tearDown(self):
        self.directory.cleanup()
    def record(self, event, metrics=None, source='realtime_voice', status='observed'):
        return self.journal.record('voice_io', 'general', source, status, metrics or {}, {'voice_event':event})
    def test_session_and_numeric_usage_not_weight_training(self):
        self.assertTrue(self.record('rt_start'))
        self.assertTrue(self.record('rt_usage', {'audio_input_bytes':48000,'audio_output_bytes':96000,'played_ms':500,'input_tokens':10,'output_tokens':20,'input_audio_tokens':8,'output_audio_tokens':16,'cached_tokens':2,'trained':1,'weight_steps':999}))
        self.assertTrue(self.record('rt_stop', {'duration_ms':1234}))
        snapshot = self.journal.snapshot()
        totals = snapshot['totals']
        self.assertEqual(totals['realtime_sessions'], 1)
        self.assertEqual(totals['realtime_duration_ms'], 1234)
        self.assertEqual(totals['realtime_audio_input_bytes'], 48000)
        self.assertEqual(totals['realtime_audio_output_bytes'], 96000)
        self.assertEqual(totals['realtime_played_ms'], 500)
        self.assertEqual(totals['realtime_input_tokens'], 10)
        self.assertEqual(totals['realtime_output_tokens'], 20)
        self.assertEqual(totals['weight_steps'], 0)
        self.assertEqual(totals['emotion_adaptation_passes'], 0)
        self.assertEqual(totals['voice_sessions'], 0)
        self.assertEqual(totals['computer_executed'], 0)
    def test_only_realtime_source_and_usage_event_can_increment_usage(self):
        self.assertTrue(self.record('rt_usage', {'input_tokens':100}, source='local_voice'))
        self.assertTrue(self.record('rt_start', {'input_tokens':100}))
        self.assertEqual(self.journal.snapshot()['totals']['realtime_input_tokens'], 0)
    def test_failed_or_cancelled_observation_not_successful_usage(self):
        self.record('rt_usage', {'input_tokens':100}, status='cancelled')
        self.record('rt_start', status='failed')
        self.assertEqual(self.journal.snapshot()['totals']['realtime_input_tokens'], 0)
        self.assertEqual(self.journal.snapshot()['totals']['realtime_sessions'], 0)
    def test_realtime_segments_interruptions_and_failures_separate(self):
        for event in ('rt_recognized','rt_interrupt','rt_error'):
            self.assertTrue(self.record(event))
        totals = self.journal.snapshot()['totals']
        self.assertEqual(totals['realtime_segments'], 1)
        self.assertEqual(totals['realtime_interrupted'], 1)
        self.assertEqual(totals['realtime_errors'], 1)
        self.assertEqual(totals['voice_segments'], 0)
    def test_unknown_transcript_key_audio_and_provider_fields_erased(self):
        self.assertTrue(self.journal.record('voice_io','general','realtime_voice','observed', {'input_tokens':3,'text':'PRIVATE_TRANSCRIPT_0123','key':'API_SECRET_0123','pcm':'RAW_AUDIO_0123'}, {'voice_event':'rt_usage','endpoint':'PRIVATE_ENDPOINT_0123','item_id':'ITEMID_0123'}))
        document = self.path.read_text(encoding='utf-8')
        for forbidden in ('PRIVATE_TRANSCRIPT_0123','API_SECRET_0123','RAW_AUDIO_0123','PRIVATE_ENDPOINT_0123','ITEMID_0123'):
            self.assertNotIn(forbidden, document)
    def test_saved_old_schema_missing_realtime_totals_loads_zero(self):
        self.record('session_start', source='local_voice')
        document = json.loads(self.path.read_text(encoding='utf-8'))
        for key in list(document['totals']):
            if key.startswith('realtime_'):
                del document['totals'][key]
        self.path.write_text(json.dumps(document), encoding='utf-8')
        loaded = TrainingJournal(self.path).snapshot()
        self.assertNotEqual(loaded['status'], 'protected')
        self.assertEqual(loaded['totals']['realtime_sessions'], 0)
        self.assertEqual(loaded['totals']['voice_sessions'], 1)


if __name__ == '__main__':
    unittest.main()
