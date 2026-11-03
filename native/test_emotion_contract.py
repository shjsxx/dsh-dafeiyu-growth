"""Emotion label coverage, abstention and explicit-group leakage regressions."""
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from emotion_eval import EMOTIONS, grouped_split, metrics, prepare
from emotion_dataset_update import MAX_BYTES, load_local, main, parse_csv
from emotion_model import EmotionModel


def csv_body(records, fields=('text', 'label', 'source_id', 'group_id')):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode('utf-8')


class EmotionContractTests(unittest.TestCase):
    def test_parser_abstentions_are_distinct_from_unmapped_and_calm(self):
        body = ('text,label\n今天心情很平淡,平淡\n今天没有特别情绪,neutral\n'
                '现在心情很平静,平静\n今天实在很担心,anxious\n'
                '今天实在很疲惫,tired\n今天什么样心情,未知\n').encode('utf-8')
        rows, stats = parse_csv(body)
        self.assertEqual([row[1] for row in rows], ['calm', 'anxious', 'tired'])
        self.assertEqual(stats['abstentions'], 2)
        self.assertEqual(stats['unmapped'], 1)
        self.assertNotIn('neutral', stats['label_counts'])

    def test_prepare_neutral_never_becomes_positive_training_label(self):
        rows, quality = prepare([('今天没有情绪', 'neutral'), ('今天未知情绪', 'unknown'),
                                 ('今天心情平静', 'calm')])
        self.assertEqual(rows, [('今天心情平静', 'calm')])
        self.assertEqual((quality['abstentions'], quality['unmapped']), (1, 1))
        self.assertEqual(quality['supported_labels'], ['calm'])

    def test_four_class_metrics_name_both_means_and_missing_classes(self):
        truth = ['joy', 'sad', 'anger', 'calm']
        result = metrics(truth, truth)
        self.assertEqual(result['macro_f1_supported'], 1)
        self.assertEqual(result['macro_f1_full_taxonomy'], .6667)
        self.assertEqual(result['macro_f1'], result['macro_f1_full_taxonomy'])
        self.assertEqual(result['missing_labels'], ['anxious', 'tired'])
        self.assertEqual(result['class_coverage'], .6667)
        self.assertEqual(result['labels'], list(EMOTIONS))

    def test_neutral_prediction_is_abstention_even_without_explicit_taxonomy(self):
        result = metrics(['joy', 'joy'], ['joy', 'neutral'])
        self.assertEqual(result['abstentions'], 1)
        self.assertEqual(result['accuracy'], .5)
        self.assertNotIn('neutral', result['labels'])
        self.assertEqual(result['per_class'][0]['recall'], .5)

    def test_empty_metrics_have_complete_coverage_contract(self):
        result = metrics([], [])
        self.assertEqual(result['support'], 0)
        self.assertEqual(result['missing_labels'], list(EMOTIONS))
        self.assertEqual(result['macro_f1_supported'], 0)

    def test_groups_remain_disjoint_and_split_is_order_independent(self):
        rows = [{'text': f'第{group}组第{turn}句我很开心', 'label': 'joy',
                 'source_id': 'source', 'group_id': str(group)}
                for group in range(12) for turn in range(3)]
        train, valid = grouped_split(rows)
        group_by_text = {row['text']: row['group_id'] for row in rows}
        self.assertTrue(valid)
        self.assertFalse({group_by_text[text] for text, _ in train}
                         & {group_by_text[text] for text, _ in valid})
        self.assertEqual((train, valid), grouped_split(list(reversed(rows))))
        self.assertEqual(len(train) + len(valid), len(rows))

    def test_duplicate_text_joins_dialogues_transitively(self):
        rows = [
            {'text': '重复文本我很开心', 'label': 'joy', 'group_id': 'a'},
            {'text': '重复文本我很开心', 'label': 'joy', 'group_id': 'b'},
            {'text': '另一重复我很开心', 'label': 'joy', 'group_id': 'b'},
            {'text': '另一重复我很开心', 'label': 'joy', 'group_id': 'c'},
            {'text': '第一组的独有文本', 'label': 'joy', 'group_id': 'a'},
            {'text': '第三组的独有文本', 'label': 'joy', 'group_id': 'c'},
        ]
        rows += [{'text': f'其他组文本我很开心{i}', 'label': 'joy', 'group_id': f'x{i}'}
                 for i in range(10)]
        train, valid = grouped_split(rows)
        linked = {'重复文本我很开心', '另一重复我很开心', '第一组的独有文本', '第三组的独有文本'}
        self.assertTrue(linked <= {text for text, _ in train} or linked <= {text for text, _ in valid})
        self.assertFalse({text for text, _ in train} & {text for text, _ in valid})
        self.assertEqual((train, valid), grouped_split(list(reversed(rows))))

    def test_one_dialogue_cannot_fall_back_to_leaking_row_split(self):
        rows = [{'text': f'第{i}句今天我很开心', 'label': 'joy', 'dialogue_id': 'one'} for i in range(60)]
        train, valid = grouped_split(rows)
        self.assertEqual(len(train), 60)
        self.assertEqual(valid, [])
        with tempfile.TemporaryDirectory() as directory:
            model = EmotionModel(Path(directory) / 'emotion.json')
            with mock.patch.object(model, '_learn') as learn:
                audit = model.learn_dataset(rows)
            learn.assert_not_called()
            self.assertEqual(audit['reason'], 'too_few_rows')
            self.assertEqual(audit['split_strategy'], 'explicit_group')

    def test_model_grouped_training_never_sees_validation_dialogues(self):
        rows = [{'text': f'第{group}组第{turn}句我很开心', 'label': 'joy',
                 'source_id': 'private-source', 'group_id': f'private-group-{group}'}
                for group in range(20) for turn in range(4)]
        group_by_text = {row['text']: row['group_id'] for row in rows}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.json'
            model = EmotionModel(path)
            with mock.patch.object(model, '_learn', wraps=model._learn) as learn, \
                    mock.patch('emotion_model.eval_rows', wraps=__import__('emotion_eval').evaluate) as evaluate, \
                    mock.patch('emotion_model.eval_acceptable', return_value=False):
                audit = model.learn_dataset(rows)
            train_texts = {call.args[0] for call in learn.call_args_list}
            validation_texts = {text for text, _ in evaluate.call_args_list[0].args[1]}
            self.assertFalse({group_by_text[text] for text in train_texts}
                             & {group_by_text[text] for text in validation_texts})
            self.assertEqual(audit['split_strategy'], 'explicit_group')
            self.assertNotIn('private-group', path.read_text(encoding='utf-8'))
            self.assertNotIn('private-source', path.read_text(encoding='utf-8'))

    def test_legacy_evaluation_is_stale_instead_of_claiming_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.json'
            model = EmotionModel(path)
            model.last_eval = {'revision': 0, 'eval_version': 2, 'macro_f1': .9}
            model.save()
            snapshot = EmotionModel(path).snapshot()
            self.assertEqual(snapshot['evaluation'], {})
            self.assertTrue(snapshot['evaluation_stale'])
            self.assertFalse(snapshot['label_contract']['neutral_is_calm'])
            self.assertEqual(snapshot['name'], '未判定')

    def test_source_namespaces_separate_identical_group_ids(self):
        rows = [{'text': f'来源{source}第{turn}句我很开心', 'label': 'joy',
                 'source_id': source, 'group_id': 'same'}
                for source in ('甲', '乙') for turn in range(3)]
        train, valid = grouped_split(rows)
        self.assertEqual((len(train), len(valid)), (3, 3))
        self.assertTrue(all('甲' in text for text, _ in train) or all('乙' in text for text, _ in train))

    def test_csv_preserves_source_and_dialogue_ids_without_persisting_them(self):
        rows, stats = parse_csv(csv_body([{'text': '今天我真的很开心', 'label': 'joy',
                                          'source_id': 'private-source', 'group_id': 'private-group'}]))
        self.assertEqual(rows[0]['source_id'], 'private-source')
        self.assertEqual(rows[0]['group_id'], 'private-group')
        self.assertEqual(stats['group_tagged'], 1)
        self.assertNotIn('private', json.dumps(stats))
        clean, quality = prepare(rows, preserve_groups=True)
        self.assertNotIn('private', json.dumps(quality))
        self.assertNotIn('private', clean[0][2])

    def test_nonadjacent_negations_and_contrasts_abstain_without_learning(self):
        phrases = ('我并不是很开心', '我不难过但很焦虑', '我不是难过，是很开心',
                   '我没有那么开心', '我并不觉得开心', '我不难过，但是我很开心',
                   '我没有感到特别的开心', '我并非很开心')
        with tempfile.TemporaryDirectory() as directory:
            model = EmotionModel(Path(directory) / 'emotion.json')
            weights = copy.deepcopy(model.weights)
            for phrase in phrases:
                with self.subTest(phrase=phrase):
                    self.assertIsNone(model.detect_self_label(phrase))
                    self.assertEqual(model.classify(phrase)['label'], 'neutral')
                    result = model.observe(phrase)
                    self.assertFalse(result['learned'])
                    self.assertEqual(result['audit']['changed_weights'], 0)
                    self.assertEqual(result['audit']['adaptation_passes'], 0)
                    self.assertEqual(result['mood'], 'neutral')
            self.assertEqual(model.weights, weights)
            self.assertEqual(model.fit_samples, 0)

    def test_adjacent_negative_inference_is_not_positive_pseudo_label(self):
        with tempfile.TemporaryDirectory() as directory:
            model = EmotionModel(Path(directory) / 'emotion.json')
            # Compatibility: legacy heuristic can read 不累 as calm, but a
            # denial of tiredness is insufficient evidence to FIT calm.
            self.assertEqual(model.classify('我不累')['label'], 'calm')
            self.assertIsNone(model.detect_self_label('我不累'))
            self.assertFalse(model.observe('我不累')['learned'])
            self.assertEqual(model.detect_self_label('我不开心')['label'], 'sad')

    def test_multiple_positive_emotions_are_not_pseudo_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            model = EmotionModel(Path(directory) / 'emotion.json')
            self.assertIsNone(model.detect_self_label('我很开心但我也很难过'))
            self.assertFalse(model.observe('我很开心但我也很难过')['learned'])

    def test_local_byte_and_row_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rows.csv'
            path.write_bytes(b'x' * (MAX_BYTES + 1))
            with self.assertRaisesRegex(RuntimeError, 'dataset_too_large'):
                load_local(path)
        with self.assertRaisesRegex(RuntimeError, 'dataset_too_many_rows'):
            parse_csv(('text,label\n' + '今天很开心,joy\n' * 20001).encode('utf-8'))

    def test_local_cli_never_downloads_or_uses_default_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rows.csv'
            path.write_bytes(csv_body([{'text': '今天我真的很开心', 'label': 'joy',
                                       'source_id': 'private-source', 'group_id': 'private-group'}]))
            profile = Path(directory) / 'model.json'
            with mock.patch('sys.argv', ['tool', '--labeled-file', str(path), '--model-file', str(profile)]), \
                    mock.patch('emotion_dataset_update.download') as download, \
                    mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                code = main()
            download.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual(code, 2)  # Too few rows; no model training performed.
            self.assertEqual(result['audit']['reason'], 'too_few_rows')
            self.assertEqual(result['parser']['group_tagged'], 1)
            self.assertFalse(profile.exists())
            self.assertNotIn('private-source', output.getvalue())
            self.assertNotIn('private-group', output.getvalue())

    def test_local_cli_requires_explicit_destination_or_staging(self):
        with mock.patch('sys.argv', ['tool', '--labeled-file', 'unused.csv']), \
                mock.patch('sys.stderr', new_callable=io.StringIO), \
                mock.patch('emotion_dataset_update.load_local') as load:
            with self.assertRaises(SystemExit):
                main()
            load.assert_not_called()

    def test_four_class_evaluation_roundtrip_reports_exact_coverage(self):
        rows = [(f'第{i}次今天我心情特别好一直笑', 'joy') for i in range(20)]
        rows += [(f'第{i}次今天我真的很难过想哭', 'sad') for i in range(20)]
        rows += [(f'第{i}次今天我真的非常生气', 'anger') for i in range(20)]
        rows += [(f'第{i}次今天我真的非常平静', 'calm') for i in range(20)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'emotion.json'
            model = EmotionModel(path)
            with mock.patch('emotion_model.eval_acceptable', return_value=False):
                audit = model.learn_dataset(rows)
            self.assertEqual(audit['missing_labels'], ['anxious', 'tired'])
            self.assertEqual(audit['validation_missing_labels'], ['anxious', 'tired'])
            evaluation = EmotionModel(path).snapshot()['evaluation']
            self.assertEqual(evaluation['dataset_missing_labels'], ['anxious', 'tired'])
            self.assertEqual(evaluation['train_missing_labels'], ['anxious', 'tired'])
            self.assertEqual(evaluation['missing_labels'], ['anxious', 'tired'])
            self.assertEqual(evaluation['macro_f1'], evaluation['macro_f1_full_taxonomy'])
            self.assertEqual(evaluation['split_strategy'], 'row_stratified')


if __name__ == '__main__':
    unittest.main()
