"""Dataset learning is bounded, transactional and never stores source text."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from emotion_model import EmotionModel
from emotion_dataset_update import parse_csv
from training_journal import TrainingJournal


def rows(count=60):
    result = []
    for index in range(count):
        result += [(f'第{index}次今天心情特别好，忍不住一直笑', 'joy'),
                   (f'第{index}件事让我很难过，想一个人待着', 'sad'),
                   (f'第{index}次我真的生气了，别再这样', 'anger'),
                   (f'第{index}天现在很平静，慢慢呼吸', 'calm'),
                   (f'第{index}天没什么特别的，就普通的一天', 'neutral')]
    return result


class DatasetLearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'emotion.json'

    def tearDown(self):
        self.directory.cleanup()

    def test_bounded_batch_learns_evaluates_and_never_stores_text(self):
        model = EmotionModel(self.path)
        audit = model.learn_dataset(rows(), holdout=.25, source='test/dataset')
        self.assertTrue(audit['persisted'])
        self.assertGreater(audit['accuracy_after'], audit['accuracy_before'])
        self.assertGreater(audit['changed_weights'], 0)
        self.assertEqual(model.revision, 1)
        body = self.path.read_text(encoding='utf-8')
        self.assertNotIn('心情特别好', body)
        self.assertNotIn('普通的一天', body)
        self.assertIn('test/dataset', model.snapshot()['source'])

    def test_failed_commit_rolls_back_weights_and_reports_not_persisted(self):
        model = EmotionModel(self.path)
        before = model._feature_count()
        with mock.patch.object(model, 'save', return_value=False):
            audit = model.learn_dataset(rows(), holdout=.25, source='test/dataset')
        self.assertFalse(audit['persisted'])
        self.assertEqual(audit['reason'], 'save_failed')
        self.assertEqual(model._feature_count(), before)
        self.assertFalse(self.path.exists())

    def test_small_or_unmapped_input_is_skipped(self):
        model = EmotionModel(self.path)
        audit = model.learn_dataset([('开心', 'joy')] * 10, holdout=.2)
        self.assertFalse(audit['persisted'])
        self.assertIn(audit['reason'], {'too_few_rows'})
        self.assertFalse(self.path.exists())

    def test_parser_accepts_label_column_and_filters_unsafe_or_unmapped(self):
        body = ('text,label\r\n'
                '今天很开心,开心\r\n'
                '这件事让我很生气,生气\r\n'
                '约炮加微信,开心\r\n'
                '访问 https://example.com,平静\r\n'
                '这是什么,关心\r\n').encode('utf-8')
        parsed, stats = parse_csv(body)
        self.assertEqual([label for _, label in parsed], ['joy', 'anger'])
        self.assertGreaterEqual(stats['filtered'], 2)
        self.assertEqual(stats['unmapped'], 1)

    def test_journal_accepts_dataset_metrics_without_text(self):
        journal = TrainingJournal(Path(self.directory.name) / 'training.json')
        event = journal.record('emotion_fit', 'emotion', 'public_dataset', 'success', {
            'dataset_rows': 2305, 'dataset_skipped': 0, 'dataset_validation': 461,
            'dataset_accuracy_before': .2104, 'dataset_accuracy_after': .7093,
            'changed_weights': 2505})
        self.assertIsNotNone(event)
        document = (Path(self.directory.name) / 'training.json').read_text(encoding='utf-8')
        self.assertIn('dataset', document)
        self.assertNotIn('心情特别好', document)


if __name__ == '__main__':
    unittest.main()
