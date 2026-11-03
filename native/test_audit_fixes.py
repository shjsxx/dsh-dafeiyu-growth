"""Regression tests for the audit fixes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from continuity import ContinuityStore
from emotion_eval import metrics
from emotion_model import EmotionModel
from memory_engine import MemoryEngine, MAX_BYTES
from personality_engine import PersonalityEngine


class AuditFixTests(unittest.TestCase):
    def test_abstentions_are_counted_in_recall_and_support(self):
        result = metrics(['joy', 'joy', 'joy', 'sad'], ['joy', 'neutral', 'neutral', 'sad'],
                         ['joy', 'sad'])
        self.assertEqual(result['accuracy'], .5)
        joy = next(row for row in result['per_class'] if row['label'] == 'joy')
        self.assertEqual(joy['support'], 3)
        self.assertAlmostEqual(joy['recall'], 1 / 3, places=3)
        self.assertEqual(result['abstentions'], 2)
        self.assertAlmostEqual(result['balanced_accuracy'], (1 / 3 + 1) / 2, places=3)

    def test_high_logits_keep_the_true_order(self):
        model = EmotionModel.__new__(EmotionModel)
        model.weights = {emotion: {} for emotion in ('joy', 'sad', 'anger', 'anxious', 'tired', 'calm')}
        model.extra_cues = {emotion: set() for emotion in model.weights}
        model.lexicon_count = 0
        model.mood = 'neutral'
        model.mood_confidence = 0
        model.mood_ms = 0
        model.revision = 1
        model.fit_samples = 0
        model.adaptation_passes = 0
        model.learned_count = 0
        model.events = 0
        model.status = 'ready'
        model.error = ''
        model.source_host = ''
        model.last_import = {}
        model.last_eval = {}
        model._features = lambda text: {'x': 1.0}
        model.weights['joy'] = {'x': 12.0}
        model.weights['sad'] = {'x': 18.0}
        result = model.classify('测试')
        self.assertEqual(result['label'], 'sad')

    def test_continuity_detects_content_change_with_same_revisions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContinuityStore(Path(directory) / 'continuity.json', clock=lambda: 1_800_000_000)
            trait = {'revision': 2, 'traits': {'warmth': 60}}
            memory = {'revision': 3, 'total': 4, 'counts': {'habit': 4}}
            store.checkpoint(trait, memory)
            self.assertTrue(store.verify(trait, memory)['stable'])
            self.assertFalse(store.verify({'revision': 2, 'traits': {'warmth': 99}},
                                          {'revision': 3, 'total': 4, 'counts': {'public': 4}})['stable'])

    def test_personality_save_failure_rolls_back_traits(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PersonalityEngine(Path(directory) / 'personality.json', clock=lambda: 1_800_000_000)
            before = dict(engine.data['traits'])
            with mock.patch('personality_engine.os.replace', side_effect=OSError('disk')):
                engine.update({'trust': .9, 'curiosity': .9, 'energy': .9, 'valence': .8})
            self.assertEqual(engine.data['traits'], before)
            self.assertEqual(engine.data['revision'], 0)

    def test_memory_serialized_document_stays_under_byte_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = MemoryEngine(Path(directory) / 'memory.json', clock=lambda: 1_800_000_000)
            text = '很' * 240
            engine.data = {'version': 1, 'revision': 0, 'publicRevision': 0, 'records': [
                {'id': str(index), 'kind': 'diary', 'text': text, 'source': 's' * 60,
                 'importance': index % 100, 'createdAt': index, 'lastSeen': index, 'hits': 1}
                for index in range(600)]}
            self.assertTrue(engine._save(engine.data))
            body = (Path(directory) / 'memory.json').read_bytes()
            self.assertLessEqual(len(body), MAX_BYTES)
            reloaded = MemoryEngine(Path(directory) / 'memory.json')
            self.assertEqual(reloaded.status, 'ready')


if __name__ == '__main__':
    unittest.main()
