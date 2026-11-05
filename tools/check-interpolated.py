"""Self-check locally interpolated action clips against pinned sources.

Reads only the local runtime/overlay directories; writes temporary PNGs to a
temporary directory and removes them. No network, no user profile.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path.home() / '.dsh' / 'yunlan-dsh-pet-runtime' / 'prepared-3c99b1b' / 'assets' / 'webm'
OVERLAY = Path.home() / '.dsh' / 'yunlan-renderer' / 'interpolated'
TIMES = (0.5, 4.0, 8.0)


def probe(path: Path) -> dict:
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries',
         'stream=codec_name,pix_fmt,width,height,avg_frame_rate:stream_tags=ALPHA_MODE:format=duration',
         '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def decode_ok(path: Path) -> bool:
    result = subprocess.run(
        ['ffmpeg', '-v', 'error', '-vcodec', 'libvpx-vp9', '-i', str(path), '-f', 'null', '-'],
        capture_output=True, text=True)
    return result.returncode == 0


def frame(path: Path, seconds: float, target: Path) -> None:
    subprocess.run(
        ['ffmpeg', '-v', 'error', '-vcodec', 'libvpx-vp9', '-ss', str(seconds),
         '-i', str(path), '-frames:v', '1', '-y', str(target)], check=True)


def metrics(source: Path, target: Path, directory: Path, index: int) -> dict:
    a = directory / f'a{index}.png'
    b = directory / f'b{index}.png'
    frame(source, TIMES[index], a)
    frame(target, TIMES[index], b)
    src = np.asarray(Image.open(a).convert('RGBA')).astype(np.float32)
    dst = np.asarray(Image.open(b).convert('RGBA')).astype(np.float32)
    sa, da = src[..., 3], dst[..., 3]
    coverage = float((da > 8).mean() - (sa > 8).mean())
    fringe_src = float(((sa > 0) & (sa < 9)).mean())
    fringe_dst = float(((da > 0) & (da < 9)).mean())
    mask = (sa > 200) & (da > 200)
    rgb_diff = float(np.abs(src[..., :3] - dst[..., :3])[mask].mean()) if mask.any() else 0.0
    corner_src = float(max(sa[0, 0], sa[0, -1], sa[-1, 0], sa[-1, -1]))
    corner_dst = float(max(da[0, 0], da[0, -1], da[-1, 0], da[-1, -1]))
    return {'alphaCoverageDelta': round(coverage, 5),
            'fringeDelta': round(fringe_dst - fringe_src, 5),
            'rgbMeanAbsDiff': round(rgb_diff, 2),
            'cornerAlphaDelta': round(abs(corner_dst - corner_src) / 255.0, 5),
            'cornerAlpha': round(corner_dst / 255.0, 5)}


def main() -> int:
    names = sys.argv[1:] or sorted(path.stem for path in OVERLAY.glob('*.webm'))
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name in names:
            src = ROOT / f'{name}.webm'
            dst = OVERLAY / f'{name}.webm'
            if not src.exists() or not dst.exists():
                failures.append(f'{name}: missing source or overlay')
                continue
            p_src, p_dst = probe(src), probe(dst)
            s_stream = p_src['streams'][0]
            d_stream = p_dst['streams'][0]
            checks = {
                'fps60': d_stream.get('avg_frame_rate') == '60/1',
                'alphaTag': d_stream.get('tags', {}).get('ALPHA_MODE') == '1',
                'size': (d_stream.get('width'), d_stream.get('height')) == (s_stream.get('width'), s_stream.get('height')),
                'decodes': decode_ok(dst),
            }
            rows = [metrics(src, dst, directory, i) for i in range(len(TIMES))]
            worst = {
                'coverageDelta': max(abs(row['alphaCoverageDelta']) for row in rows),
                'fringeDelta': max(abs(row['fringeDelta']) for row in rows),
                'rgbDiff': max(row['rgbMeanAbsDiff'] for row in rows),
                'cornerAlphaDelta': max(row['cornerAlphaDelta'] for row in rows),
                'cornerAlpha': max(row['cornerAlpha'] for row in rows),
            }
            sd = float(p_src.get('format', {}).get('duration', 0))
            dd = float(p_dst.get('format', {}).get('duration', 0))
            ok = (all(checks.values()) and worst['coverageDelta'] < .01
                  and worst['cornerAlphaDelta'] == 0 and worst['rgbDiff'] < 15
                  and abs(sd - dd) < .2)
            print(json.dumps({'name': name, 'ok': ok, 'checks': checks, 'duration': [round(sd, 3), round(dd, 3)],
                              'worst': worst, 'bytes': dst.stat().st_size}, ensure_ascii=False))
            if not ok:
                failures.append(name)
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print(f'OK: {len(names)} clips')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
