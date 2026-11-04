"""Restricted local Electron presentation transport. No Qt, HTTP, profile or secret IO."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import threading
import secrets
import sys
import time

COMMANDS = frozenset(('open-conversation', 'open-growth', 'feedback-positive',
                      'feedback-negative', 'emergency-stop', 'show', 'hide', 'open-menu',
                      'interact-head', 'interact-poke', 'toggle-roam', 'toggle-reduced'))
STATES = frozenset(('IDLE', 'THINKING', 'WORKING', 'WAITING', 'SUCCESS', 'ERROR', 'DISCONNECTED'))
INTENTS = frozenset(('run', 'hop', 'spin', 'greet', 'celebrate', 'head_pat', 'poke', 'tail',
                     'sad', 'sleep', 'think', 'idle'))
FIELDS = frozenset(('enabled', 'electronPath', 'scriptPath', 'runtimeRoot', 'userDataDir'))


def load_renderer_config(path=None):
    """An explicit local activation marker, never automatic enablement by dependency presence."""
    target = Path(path or os.environ.get('DSH_YUNLAN_RENDERER_CONFIG') or
                  Path.home() / '.dsh' / 'yunlan-renderer' / 'active.json')
    if not target.is_absolute():
        raise ValueError('renderer config must be absolute')
    if not target.exists():
        if path or os.environ.get('DSH_YUNLAN_RENDERER_CONFIG'):
            raise ValueError('renderer config missing')
        return None
    if target.stat().st_size > 16384:
        raise ValueError('renderer config too large')
    config = json.loads(target.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or set(config) - FIELDS:
        raise ValueError('invalid renderer config')
    if config.get('enabled') is not True:
        return None
    for key in FIELDS - {'enabled'}:
        value = config.get(key)
        if not isinstance(value, str) or not value or '\x00' in value or not Path(value).is_absolute():
            raise ValueError('invalid renderer path')
    if not Path(config['electronPath']).is_file() or not Path(config['scriptPath']).is_file():
        raise ValueError('renderer executable or script missing')
    if not Path(config['runtimeRoot']).is_dir():
        raise ValueError('renderer runtime missing')
    return dict(config)


def sanitize_event(value):
    if not isinstance(value, dict):
        return None
    kind = value.get('kind')
    if kind == 'ready':
        return {'kind': 'ready'}
    if kind == 'command' and isinstance(value.get('command'), str) and value.get('command') in COMMANDS:
        return {'kind': 'command', 'command': value['command']}
    if kind == 'action':
        name = value.get('name')
        action_kind = value.get('actionKind')
        distance = value.get('distance', 0)
        if (isinstance(name, str) and 0 < len(name) <= 80 and
                action_kind in ('move', 'jump', 'hop', 'action', 'click') and
                type(distance) in (int, float) and 0 <= distance <= 10000):
            return {'kind': 'action', 'name': name, 'actionKind': action_kind, 'distance': distance}
        return None
    if kind == 'status' and type(value.get('visible')) is bool and type(value.get('connected')) is bool:
        return {'kind': 'status', 'visible': value['visible'], 'connected': value['connected']}
    if kind == 'anchor':
        limits = {'x': (-100000, 100000), 'y': (-100000, 100000),
                  'width': (1, 8192), 'height': (1, 8192)}
        if all(type(value.get(k)) is int and lo <= value[k] <= hi for k, (lo, hi) in limits.items()):
            return {'kind': 'anchor', **{k: value[k] for k in limits}}
    return None


class ExternalRenderer:
    def __init__(self, config, on_event, *, popen=subprocess.Popen,
                 pipe_opener=open, sleep=time.sleep, platform=None):
        self.config = config
        self.on_event = on_event
        self._popen = popen
        self.process = None
        self._lock = threading.Lock()
        self._closed = False
        self._seq = 0
        self._pipe_opener = pipe_opener
        self._sleep = sleep
        self._windows = (sys.platform if platform is None else platform) == 'win32'
        self._control = None

    def start(self):
        allowed_env = {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'APPDATA',
                       'LOCALAPPDATA', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'COMSPEC', 'PATHEXT'}
        # Renderer inherits OS prerequisites only, never provider keys/configuration.
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed_env}
        pipe_path = None
        control_key = None
        if self._windows:
            pipe_path = '\\\\.\\pipe\\yunlan-renderer-' + secrets.token_hex(16)
            control_key = secrets.token_hex(32)
            env['DSH_YUNLAN_CONTROL_PIPE'] = pipe_path
            env['DSH_YUNLAN_CONTROL_KEY'] = control_key
        self.process = self._popen(
            [self.config['electronPath'], self.config['scriptPath'],
             '--runtime-root', self.config['runtimeRoot'], '--user-data-dir', self.config['userDataDir']],
            cwd=str(Path(self.config['scriptPath']).parent), env=env, shell=False,
            stdin=subprocess.DEVNULL if self._windows else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='strict', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if self._windows:
            try:
                # Node creates the randomly named local pipe; no socket or HTTP listener.
                for attempt in range(100):
                    if self.process.poll() is not None:
                        raise OSError('renderer exited before control connection')
                    try:
                        self._control = self._pipe_opener(pipe_path, 'r+b', buffering=0)
                        break
                    except OSError:
                        if attempt == 99:
                            raise OSError('renderer control connection unavailable') from None
                        self._sleep(0.1)
                if not self.send({'kind': 'handshake', 'key': control_key}):
                    raise OSError('renderer control handshake failed')
            except OSError:
                self.close()
                raise OSError('renderer control unavailable') from None
        threading.Thread(target=self._read, name='yunlan-renderer-events', daemon=True).start()

    def _notify(self, event):
        try:
            self.on_event(event)
        except RuntimeError:
            pass  # Qt owner already destroyed during process shutdown.

    def _read(self):
        try:
            while not self._closed:
                line = self.process.stdout.readline(16385)
                if not line:
                    break
                if len(line) > 16384 or not line.endswith('\n'):
                    break
                try:
                    event = sanitize_event(json.loads(line))
                except (ValueError, TypeError):
                    continue
                if event is not None:
                    self._notify(event)
        except (OSError, UnicodeError, ValueError):
            pass
        finally:
            if not self._closed:
                self._notify({'kind': 'status', 'visible': False, 'connected': False})

    def send(self, message):
        with self._lock:
            if self._closed or self.process is None or self.process.poll() is not None:
                return False
            try:
                line = json.dumps(message, ensure_ascii=False) + '\n'
                if self._windows:
                    if self._control is None:
                        return False
                    remaining = memoryview(line.encode('utf-8'))
                    while remaining:
                        written = self._control.write(remaining)
                        if not written:
                            return False
                        remaining = remaining[written:]
                else:
                    self.process.stdin.write(line)
                    self.process.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def present(self, text='', state='IDLE', ttl_ms=4200, intent=None):
        if not isinstance(text, str) or state not in STATES or type(ttl_ms) is not int:
            return False
        if intent is not None:
            if (not isinstance(intent, str) or not 0 < len(intent) <= 80 or
                    any(ord(char) < 32 or ord(char) == 127 for char in intent)):
                return False
        self._seq += 1
        return self.send({'kind': 'presentation', 'seq': self._seq, 'text': text[:2000],
                          'state': state, 'ttlMs': max(250, min(60000, ttl_ms)),
                          **({'intent': intent} if intent else {})})

    def close(self):
        if self._closed:
            return
        self.send({'kind': 'shutdown'})
        self._closed = True
        if self._control is not None:
            try:
                self._control.close()
            except OSError:
                pass
            self._control = None
        if self.process is not None:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()
                self.process.wait(timeout=2)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
