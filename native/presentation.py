"""Deterministic ambient presentation: breathing, sway and mood glow.

No assets and no Qt: returns numeric pose values the native renderer applies.
"""
from __future__ import annotations

import math

GLOW = {
    "joy": (224, 176, 104), "sad": (88, 120, 176), "anger": (188, 100, 104),
    "anxious": (146, 124, 178), "tired": (126, 142, 158), "calm": (98, 158, 148),
    "neutral": (92, 158, 150),
}


def ambient_pose(now_ms, mood="neutral", energy=.7, pointer_near=False,
                 pointer_dx=0.0, reduced=False):
    """Bounded breathing/sway/lean plus a soft mood color."""
    energy = max(0.0, min(1.0, float(energy or 0.0)))
    mood = mood if mood in GLOW else "neutral"
    phase = max(0, int(now_ms)) / 1000.0
    if reduced:
        breath, sway, lean = 1.0, 0.0, 0.0
    else:
        breath = 1.0 + (0.006 + 0.012 * energy) * math.sin(phase * 1.35)
        sway = (0.45 + 0.65 * energy) * math.sin(phase * 0.62)
        lean = max(-2.2, min(2.2, float(pointer_dx or 0.0) / 180.0)) if pointer_near else 0.0
    color = GLOW[mood]
    alpha = 0 if reduced else int(18 + 24 * energy)
    return {"breath": round(breath, 4), "sway": round(sway + lean, 3),
            "glow": color, "glow_alpha": alpha,
            "glow_radius": round(.72 + .14 * energy, 3)}


def mood_glow(mood):
    return GLOW.get(mood, GLOW["neutral"])
