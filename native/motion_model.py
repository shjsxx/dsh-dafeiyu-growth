"""Qt-free movement decisions, bounded to one screen; injected RNG for tests."""
import math


def plan_run(x, y, left, right, distance, direction, speed, fall_chance, force_fall=None, rng=None):
    if rng is None:
        import random
        rng = random
    if right - left < 120:
        return None
    x = min(right, max(left, x))
    target = min(right, max(left, x + direction * distance))
    if abs(target - x) < 100:
        target = min(right, max(left, x - direction * 220))
    travel = abs(target - x)
    if travel < 60:
        return None
    probability = min(1.0, max(0.0, float(fall_chance)))
    # A probability is NOT a flag. Sample once for this run, never per frame.
    fall = bool(force_fall) if force_fall is not None else rng.random() < probability
    if travel < 200:
        fall = False
    return {'kind': 'run', 'from': x, 'to': target, 'y': y,
            'dur': int(max(420, min(3200, travel / max(120.0, speed) * 1000))),
            'direction': 1 if target >= x else -1, 'fall': fall,
            'fall_at': rng.uniform(.55, .85), 'steps': max(2, min(12, round(travel / 88))),
            'hop_height': rng.uniform(8.0, 12.0), 'last_dust': 0}


def run_pose(motion, progress):
    p = min(1.0, max(0.0, progress))
    hop = abs(math.sin(p * motion['steps'] * math.pi)) * motion['hop_height'] if p < 1 else 0
    return {'x': round(motion['from'] + (motion['to'] - motion['from']) * p),
            'y': round(motion['y'] - hop), 'hop': hop,
            'facing': motion['direction'], 'tilt': motion['direction'] * 1.5 if p < 1 else 0,
            'scale_y': 1.0 - .035 * (1.0 - hop / max(1.0, motion['hop_height'])) if p < 1 else 1.0}
