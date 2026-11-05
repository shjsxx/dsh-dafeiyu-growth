"""Count consecutive duplicate frames in all source/overlay WebM clips."""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
from pathlib import Path

SOURCE = Path.home() / '.dsh' / 'yunlan-dsh-pet-runtime' / 'prepared-3c99b1b' / 'assets' / 'webm'
OVERLAY = Path.home() / '.dsh' / 'yunlan-renderer' / 'interpolated'
OUT = Path(__file__).resolve().parent.parent / 'native' / 'verification' / 'frame-duplicates.json'


def hashes(path: Path):
    result = subprocess.run(
        ['ffmpeg', '-v', 'error', '-vcodec', 'libvpx-vp9', '-i', str(path),
         '-vf', 'format=rgba', '-f', 'framemd5', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        return [], (result.stderr or 'decode failed')[-200:]
    values = []
    for line in result.stdout.splitlines():
        if not line or line.startswith('#'):
            continue
        parts = [part.strip() for part in line.split(',')]
        if len(parts) >= 6:
            values.append(parts[-1])
    return values, ''


def audit(name, path, kind):
    values, error = hashes(path)
    duplicates = sum(1 for i in range(1, len(values)) if values[i] == values[i - 1])
    longest = 0
    run = 1
    for i in range(1, len(values)):
        if values[i] == values[i - 1]:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    unique = len(set(values))
    return {'name': name, 'kind': kind, 'frames': len(values), 'unique': unique,
            'duplicate_pairs': duplicates, 'longest_run': longest,
            'unique_ratio': round(unique / max(1, len(values)), 4), 'error': error}


def main():
    names = sorted(path.stem for path in SOURCE.glob('*.webm'))
    tasks = [(name, SOURCE / f'{name}.webm', 'source') for name in names]
    tasks += [(name, OVERLAY / f'{name}.webm', 'overlay') for name in names if (OVERLAY / f'{name}.webm').exists()]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(audit, *task) for task in tasks]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.append(future.result())
            if index % 40 == 0:
                print(f'{index}/{len(tasks)}', flush=True)
    rows.sort(key=lambda row: (row['name'], row['kind']))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'assets': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    overlay = [row for row in rows if row['kind'] == 'overlay']
    source = [row for row in rows if row['kind'] == 'source']
    print(json.dumps({
        'rows': len(rows), 'overlay_duplicate_pairs': sum(r['duplicate_pairs'] for r in overlay),
        'overlay_longest_run': max((r['longest_run'] for r in overlay), default=0),
        'source_duplicate_pairs': sum(r['duplicate_pairs'] for r in source),
        'source_longest_run': max((r['longest_run'] for r in source), default=0),
        'errors': sum(bool(r['error']) for r in rows), 'out': str(OUT),
    }, ensure_ascii=False))
    return 1 if any(row['error'] for row in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
