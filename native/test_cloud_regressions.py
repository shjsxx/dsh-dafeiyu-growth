"""Private config/transport regression cases, only mocks and temporary files."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cloud_chat as cloud


class CloudRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'cloud.json'

    def config(self, endpoint, **extra):
        self.path.write_text(json.dumps({'endpoint': endpoint, 'model':'m', **extra}), encoding='utf-8')

    def test_bad_ports_ipv6_and_controls_are_config_errors(self):
        for endpoint in ('https://example.invalid:99999/v1', 'https://example.invalid:wrong/v1',
                         'https://[bad/v1', 'https://exam\nple.invalid/v1', 'https://exam ple.invalid/v1'):
            with self.subTest(endpoint=repr(endpoint)), self.assertRaises(cloud.CloudConfigError):
                cloud.normalize_endpoint(endpoint)

    def test_load_malformed_endpoint_does_not_raise(self):
        self.config('https://[bad/v1')
        self.assertEqual(cloud.load_config(self.path, env={})['endpoint'], '')

    def test_env_key_requires_explicit_binding_for_local_server(self):
        self.config('http://localhost:8888/v1')
        env = {'DSH_DAFEIYU_CLOUD_KEY':'sensitive-provider-key'}
        self.assertEqual(cloud.load_config(self.path, env=env)['key'], '')
        env['DSH_DAFEIYU_CLOUD_KEY_ENDPOINT'] = 'http://localhost:8888/v1'
        self.assertEqual(cloud.load_config(self.path, env=env)['key'], env['DSH_DAFEIYU_CLOUD_KEY'])

    def test_explicit_env_binding_takes_precedence_over_implicit_default_host(self):
        self.config('https://api.deepseek.com/v1')
        env = {'DSH_DAFEIYU_CLOUD_KEY':'secret',
               'DSH_DAFEIYU_CLOUD_KEY_ENDPOINT':'https://other.invalid/v1'}
        self.assertEqual(cloud.load_config(self.path, env=env)['key'], '')

    def test_nonstandard_provider_port_cannot_inherit_key(self):
        self.config('https://api.deepseek.com:9443/v1')
        self.assertEqual(cloud.load_config(self.path, env={'DSH_DAFEIYU_CLOUD_KEY':'secret'})['key'], '')

    def test_normalized_key_binding_retains_key_for_same_api_path(self):
        self.config('https://api.deepseek.com/v1', key_protected='encrypted',
                    key_endpoint='https://api.deepseek.com/v1')
        with patch.object(cloud, 'unprotect_secret', return_value='secret'):
            self.assertEqual(cloud.load_config(self.path, env={})['key'], 'secret')
        cloud.save_config('https://api.deepseek.com/v1/chat/completions', 'm', 64, path=self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['key_protected'], 'encrypted')

    def test_rebound_saved_key_cannot_be_laundered_by_save(self):
        self.config('https://other.invalid/v1', key_protected='encrypted',
                    key_endpoint='https://original.invalid/v1')
        before = self.path.read_bytes()
        with self.assertRaises(cloud.CloudConfigError):
            cloud.save_config('https://other.invalid/v1', 'm', 64, path=self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_response_types_return_error_not_exception(self):
        config = {'endpoint':'https://example.invalid/v1', 'model':'m', 'max_tokens':64}
        for data in ({'choices':[None]}, {'choices':[1]}, {'choices':{}}, [],
                     {'choices':[{'message':None}]}, {'choices':[{'message':7}]}):
            with self.subTest(data=data):
                with patch.object(cloud.urllib.request, 'build_opener') as opener:
                    opener.return_value.open.return_value = io.BytesIO(json.dumps(data).encode())
                    result = cloud.ask_cloud(config, 'test')
                self.assertFalse(result['ok'])
                self.assertIsInstance(result['error'], str)


if __name__ == '__main__': unittest.main()
