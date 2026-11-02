"""Single source of truth for the installed upstream dsh-dafeiyu contract.

No repair or upgrade is attempted here: a mismatch must stop install rather
than patch an unknown upstream layout with guessed assumptions.
"""
from __future__ import annotations
import json
from pathlib import Path


def contract_path():
    return Path(__file__).with_name('upstream_compat.json')


def load_contract(path=None):
    raw = json.loads(Path(path or contract_path()).read_text(encoding='utf-8'))
    version = raw.get('testedVersion')
    directory = raw.get('helperDirectory')
    if (not isinstance(version, str) or not version or len(version) > 40
            or not isinstance(directory, str) or not directory):
        raise ValueError('invalid upstream contract')
    return {'testedVersion': version, 'helperDirectory': directory,
            'note': str(raw.get('note', ''))[:200]}


def check(upstream_root=None):
    root = Path(upstream_root) if upstream_root else (
        Path.home() / '.dsh' / 'profiles' / 'desktop' / 'node_modules' / 'dsh-dafeiyu')
    contract = load_contract()
    package_path = root / 'package.json'
    try:
        package = json.loads(package_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {'ok': False, 'reason': 'upstream package is missing or unreadable',
                'testedVersion': contract['testedVersion']}
    version = package.get('version') if isinstance(package, dict) else None
    if version != contract['testedVersion']:
        return {'ok': False, 'reason': 'upstream version changed; rebase before installing',
                'expected': contract['testedVersion'], 'actual': str(version)[:40]}
    helper = root / contract['helperDirectory'] / 'dsh-dafeiyu-helper.exe'
    return {'ok': helper.is_file(), 'reason': '' if helper.is_file() else 'upstream helper missing',
            'testedVersion': contract['testedVersion'], 'helper': str(helper)}
