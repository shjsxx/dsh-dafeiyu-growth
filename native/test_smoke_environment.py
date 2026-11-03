import json
import unittest
from pathlib import Path
from smoke_environment import build_environment, FILES


class SmokeEnvironmentTests(unittest.TestCase):
    def test_owner_paths_and_keys_cannot_escape_isolated_smoke(self):
        root = Path(__file__).parent
        out = root / 'verification' / 'test-placeholder'
        owner = {f'DSH_DAFEIYU_{name}_FILE':'OWNER_PRIVATE_PATH' for name in FILES}
        owner.update({'DSH_DAFEIYU_CLOUD_KEY':'SECRET', 'DSH_DAFEIYU_REALTIME_KEY':'SECRET',
                      'dsh_dafeiyu_review_file':'OWNER_PRIVATE_PATH',
                      'DSH_DAFEIYU_EMBEDDING_DIR':'OWNER_MODEL', 'PATH':'unchanged'})
        env = build_environment(owner, out, root, root / 'fake-assets')
        self.assertNotIn('SECRET', str(env))
        self.assertNotIn('OWNER_PRIVATE_PATH', str(env))
        self.assertNotIn('OWNER_MODEL', str(env))
        self.assertEqual(env['PATH'], 'unchanged')
        for name, filename in FILES.items():
            self.assertEqual(Path(env[f'DSH_DAFEIYU_{name}_FILE']), out.resolve() / filename)

    def test_distribution_contains_installer_contract(self):
        package = json.loads((Path(__file__).parent.parent / 'package.json').read_text(encoding='utf-8-sig'))
        self.assertIn('native/upstream_compat.json', package['files'])


if __name__ == '__main__': unittest.main()
