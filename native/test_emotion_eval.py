"""Pure evaluation: data quality, stratified holdout and metrics."""
import unittest

from emotion_eval import acceptable, metrics, prepare, stratified_split


class EmotionEvalTests(unittest.TestCase):
    def test_conflicts_and_duplicates_are_removed(self):
        rows = [('今天很开心', 'joy'), ('今天很开心', 'joy'),
                ('今天很难过', 'sad'), ('今天很难过', 'joy'),
                ('普通的一天', 'neutral')]
        clean, audit = prepare(rows)
        self.assertEqual([label for _, label in clean], ['joy'])
        self.assertEqual(audit['duplicates'], 1)
        self.assertEqual(audit['conflicts'], 1)
        self.assertEqual(audit['skipped'], 3)

    def test_stratified_split_covers_each_class_and_is_deterministic(self):
        rows = [(f'开心{index}', 'joy') for index in range(30)]
        rows += [(f'难过{index}', 'sad') for index in range(10)]
        train, valid = stratified_split(rows, .25)
        self.assertEqual(len(train) + len(valid), len(rows))
        self.assertEqual({label for _, label in valid}, {'joy', 'sad'})
        self.assertEqual((train, valid), stratified_split(rows, .25))

    def test_metrics_report_per_class_and_confusion(self):
        truth = ['joy', 'joy', 'sad', 'sad']
        predicted = ['joy', 'sad', 'sad', 'sad']
        result = metrics(truth, predicted, ['joy', 'sad'])
        self.assertEqual(result['accuracy'], .75)
        self.assertEqual(result['balanced_accuracy'], .75)
        self.assertEqual(result['confusion'], [[1, 1], [0, 2]])
        joy = next(row for row in result['per_class'] if row['label'] == 'joy')
        self.assertEqual((joy['precision'], joy['recall']), (1.0, .5))

    def test_acceptance_rejects_accuracy_collapse(self):
        before = {'accuracy': .8, 'macro_f1': .7, 'balanced_accuracy': .7}
        after = {'accuracy': .7, 'macro_f1': .8, 'balanced_accuracy': .8}
        self.assertFalse(acceptable(before, after))
        self.assertTrue(acceptable(before, {'accuracy': .81, 'macro_f1': .7, 'balanced_accuracy': .7}))


if __name__ == '__main__':
    unittest.main()
