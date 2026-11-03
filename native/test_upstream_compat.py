import json
import tempfile
import unittest
from pathlib import Path
from upstream_compat import check, load_contract


class UpstreamCompatTests(unittest.TestCase):
    def test_contract_is_safe_and_single_source(self):
        contract = load_contract()
        self.assertEqual(contract['testedVersion'], '0.1.23')
        self.assertTrue(contract['helperDirectory'])

    def test_mismatch_or_missing_helper_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text(json.dumps({'version': '9.9.9'}), encoding='utf-8')
            result = check(root)
            self.assertFalse(result['ok'])
            self.assertIn('rebase', result['reason'])

    def test_expected_version_without_helper_does_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text(json.dumps({'version': '0.1.23'}), encoding='utf-8')
            self.assertFalse(check(root)['ok'])


if __name__ == '__main__':
    unittest.main()
