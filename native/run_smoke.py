"""Run native context-menu integration test with isolated data/layout."""
import json
import os
from pathlib import Path
import subprocess
import sys
from smoke_environment import build_environment, FILES

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'verification' / ('packaged' if len(sys.argv) > 1 else 'source')
OUTPUT.mkdir(parents=True, exist_ok=True)
fixture = OUTPUT / 'profile.json'
# Synthetic fixture only: the smoke test must never copy the owner's real
# profile (it contains session ids, tool usage and diary text) into the repo.
fixture.write_bytes((ROOT / 'fixtures' / 'smoke-profile.json').read_bytes())
for name in sorted(set(FILES.values()) - {'profile.json'} | {'training.json.bak', 'result.json'}):
    target = (OUTPUT / name).resolve()
    assert target.parent == OUTPUT.resolve(), 'cleanup must stay inside isolated verification output'
    if target.exists():
        target.unlink()
env = build_environment(os.environ, OUTPUT, ROOT,
                        Path.home() / '.dsh' / 'profiles' / 'desktop' / 'node_modules' / 'dsh-dafeiyu')
command = [sys.argv[1]] if len(sys.argv) > 1 else [sys.executable, str(ROOT / 'runtime' / 'helper.py')]
command = command + ['--smoke-probe']
with (OUTPUT / 'process.log').open('w', encoding='utf-8') as stream:
    child = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=stream, stderr=stream)
    try:
        code = child.wait(timeout=120)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        raise
    finally:
        child.stdin.close()
result_path = OUTPUT / 'result.json'
if not result_path.exists():
    print((OUTPUT / 'process.log').read_text(encoding='utf-8'))
    sys.exit(code or 1)
result = json.loads(result_path.read_text(encoding='utf-8'))
print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(0 if code == 0 and result.get('ok') else 1)
