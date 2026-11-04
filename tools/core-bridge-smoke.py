"""Isolated end-to-end probe: real Python core + real Electron renderer.

Uses temporary stores and the restricted named-pipe transport. It never reads the
owner's DSH profile, keys, memory, or the installed pet.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'native'
PYTHON = sys.executable
RUN = ROOT / 'native' / 'verification' / (sys.argv[1] if len(sys.argv) > 1 else 'core-smoke')
if RUN.exists():
    raise SystemExit('Refusing to overwrite an existing smoke directory: ' + str(RUN))
RUN.mkdir(parents=True)

sys.path.insert(0, str(NATIVE))
from smoke_environment import build_environment  # noqa: E402

runtime_root = Path.home() / '.dsh' / 'yunlan-dsh-pet-runtime'
renderer_entry = RUN / 'entry.cjs'
renderer_entry.write_text(
    'process.argv.push("--smoke-out",' + json.dumps(str(RUN)) + ',"--smoke-interact");'
    'require(' + json.dumps(str(ROOT / 'desktop' / 'main.cjs')) + ');',
    encoding='utf-8')
config = {
    'enabled': True,
    'electronPath': str(runtime_root / 'electron' / 'DeepSeek Harness.exe'),
    'scriptPath': str(renderer_entry),
    'runtimeRoot': str(runtime_root / 'prepared-3c99b1b'),
    'userDataDir': str(RUN / 'chromium'),
}
config_path = RUN / 'renderer.json'
config_path.write_text(json.dumps(config), encoding='utf-8')

asset_root = runtime_root / 'prepared-3c99b1b' / 'assets'
env = build_environment(os.environ, RUN, NATIVE, asset_root)
env['DSH_HOME'] = str(RUN / 'home')
env.pop('QT_QPA_PLATFORM', None)  # keep offscreen hidden host for this probe
env['QT_QPA_PLATFORM'] = 'offscreen'
proc = subprocess.Popen(
    ([sys.argv[2]] if len(sys.argv) > 2 else [PYTHON, str(NATIVE / 'runtime' / 'helper.py')]) +
    ['--external-renderer', '--renderer-config', str(config_path)],
    cwd=str(NATIVE / 'runtime'), env=env, stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
    errors='replace', bufsize=1)
assert proc.stdin and proc.stdout
lines = []


def read_until(kind, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            return False
        lines.append(line.rstrip('\n'))
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get('kind') == kind:
            return True
    return False


try:
    if not read_until('ready'):
        raise RuntimeError('core did not report ready')
    time.sleep(4)
    proc.stdin.write(json.dumps({'protocolVersion': 1, 'kind': 'state', 'state': 'WORKING'}) + '\n')
    proc.stdin.flush()
    time.sleep(4)
    proc.stdin.write(json.dumps({'protocolVersion': 1, 'kind': 'state', 'state': 'SUCCESS'}) + '\n')
    proc.stdin.flush()
    # Let Electron capture its own frame at 12s and exit; then stop the core.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (RUN / 'renderer.json').exists() and (RUN / 'renderer.png').exists():
            break
        time.sleep(0.5)
    proc.stdin.write(json.dumps({'protocolVersion': 1, 'kind': 'shutdown'}) + '\n')
    proc.stdin.flush()
    proc.stdin.close()
    code = proc.wait(timeout=20)
finally:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ''
    (RUN / 'core-output.json').write_text(json.dumps({
        'code': proc.returncode, 'lines': lines,
        'stderr': stderr[-4000:],
        'rendererDebug': json.loads((RUN / 'renderer.json').read_text(encoding='utf-8'))
        if (RUN / 'renderer.json').exists() else None,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'code': proc.returncode, 'output': str(RUN)}))
