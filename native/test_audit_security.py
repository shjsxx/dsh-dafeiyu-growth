"""Regression tests for audit fixes: cloud key binding and schema fail-closed."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cloud_chat import CloudConfigError, load_config, save_config
from embedding_benchmark import Reranker
from continuity import ContinuityStore


class CloudKeyBindingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'cloud.json'

    def tearDown(self):
        self.directory.cleanup()

    def test_endpoint_change_requires_a_new_key(self):
        save_config('https://api.deepseek.com/v1', 'm', 64, 'sk-old', path=self.path)
        with self.assertRaises(CloudConfigError):
            save_config('https://api.example.com/v1', 'm', 64, '', path=self.path)
        save_config('https://api.example.com/v1', 'm', 64, 'sk-new', path=self.path)
        config = load_config(self.path, env={})
        self.assertEqual(config['key'], 'sk-new')

    def test_legacy_key_is_not_attached_after_endpoint_file_edit(self):
        save_config('https://api.deepseek.com/v1', 'm', 64, 'sk-old', path=self.path)
        import json
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        raw['endpoint'] = 'https://api.example.com/v1/chat/completions'
        self.path.write_text(json.dumps(raw), encoding='utf-8')
        config = load_config(self.path, env={})
        self.assertEqual(config['key'], '')
        self.assertIn('different endpoint', config['error'])


class SchemaFailClosedTests(unittest.TestCase):
    def test_malformed_reranker_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rerank.json'
            path.write_text('{"version":1,"features":["cosine","hash","concept","overlap","kind_prior","importance","length"],"weights":[1],"bias":0,"mean":[0],"std":[1],"metrics":{"improved":true},"enabled":true}', encoding='utf-8')
            self.assertIsNone(Reranker.load(path))

    def test_continuity_traits_list_does_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'continuity.json'
            path.write_text('{"version":1,"revision":1,"startups":1,"anchors":{"2026-11-02":{"personality":1,"memory":1,"total":1,"traits":[1],"counts":[]}}}', encoding='utf-8')
            store = ContinuityStore(path)
            self.assertEqual(store.status, 'ready')
            self.assertEqual(store.verify()['days'], 1)


if __name__ == '__main__':
    unittest.main()
