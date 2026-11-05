"""Audit all Yunlan source and interpolated WebM assets.

Read-only: no owner profile, no network. Uses ffprobe/ffmpeg available on PATH.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import subprocess
import sys
from pathlib import Path

SOURCE = Path.home() / '.dsh' / 'yunlan-dsh-pet-runtime' / 'prepared-3c99b1b' / 'assets' / 'webm'
OVERLAY = Path.home() / '.dsh' / 'yunlan-renderer' / 'interpolated'
OUT = Path(__file__).resolve().parent.parent / 'native' / 'verification' / 'asset-audit.json'


def probe(path: Path) -> dict:
    command = ['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
               '-show_entries', 'stream=codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,'
               'nb_frames,nb_read_frames,duration,bit_rate:stream_tags=ALPHA_MODE:format=format_name,duration,size',
               '-of', 'json', str(path)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        return {'error': (result.stderr or 'ffprobe failed')[-300:]}
    raw = json.loads(result.stdout)
    stream = (raw.get('streams') or [{}])[0]
    fmt = raw.get('format') or {}
    return {
        'codec': stream.get('codec_name'), 'width': stream.get('width'), 'height': stream.get('height'),
        'pix_fmt': stream.get('pix_fmt'), 'avg_fps': stream.get('avg_frame_rate'),
        'real_fps': stream.get('r_frame_rate'), 'frames': stream.get('nb_frames'),
        'read_frames': stream.get('nb_read_frames'), 'duration': stream.get('duration'),
        'bit_rate': stream.get('bit_rate'), 'alpha': (stream.get('tags') or {}).get('ALPHA_MODE'),
        'format': fmt.get('format_name'), 'format_duration': fmt.get('duration'),
        'size': fmt.get('size'),
    }


def decode(path: Path) -> str:
    result = subprocess.run(
        ['ffmpeg', '-v', 'error', '-vcodec', 'libvpx-vp9', '-i', str(path), '-f', 'null', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return '' if result.returncode == 0 else (result.stderr or 'decode failed')[-300:]


def rate(value):
    if not isinstance(value, str) or '/' not in value:
        return 0.0
    try:
        a, b = value.split('/', 1)
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def audit(name: str, path: Path, kind: str) -> dict:
    info = probe(path)
    info.update({'name': name, 'kind': kind, 'path': str(path), 'bytes': path.stat().st_size})
    if 'error' not in info:
        info['fps_float'] = round(rate(info.get('avg_fps')), 3)
        try:
            info['duration_float'] = float(info.get('format_duration') or info.get('duration') or 0)
        except (TypeError, ValueError):
            info['duration_float'] = 0.0
        info['bitrate_mbps'] = round((info.get('bit_rate') or 0) / 1_000_000, 3) if str(info.get('bit_rate', '')).isdigit() else None
        info['decode_error'] = decode(path)
    return info


def main() -> int:
    names = sorted(path.stem for path in SOURCE.glob('*.webm'))
    tasks = [(name, SOURCE / f'{name}.webm', 'source') for name in names]
    tasks += [(name, OVERLAY / f'{name}.webm', 'overlay') for name in names if (OVERLAY / f'{name}.webm').exists()]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(audit, *task) for task in tasks]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            rows.append(future.result())
            if index % 20 == 0:
                print(f'{index}/{len(tasks)}', flush=True)
    rows.sort(key=lambda row: (row['name'], row['kind']))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'assets': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    problems = [row for row in rows if row.get('error') or row.get('decode_error') or row.get('fps_float', 0) <= 0]
    print(json.dumps({'rows': len(rows), 'problems': len(problems), 'out': str(OUT)}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
