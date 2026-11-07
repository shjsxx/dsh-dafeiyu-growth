"""Repeatable synthetic soak check for bounded native state.

Runs growth, memory, personality and continuity through simulated days, reloads
them repeatedly, and optionally creates/restores an explicit backup. It uses a
temporary directory and synthetic data only; it never touches the real profile.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from continuity import ContinuityStore
from growth_engine import GrowthEngine
from memory_engine import MemoryEngine
from personality_engine import PersonalityEngine
from state_backup import StateBackup


def run(cycles=90, root=None):
    cycles = max(1, min(3650, int(cycles)))
    directory = Path(root) if root else Path(tempfile.mkdtemp(prefix="yunlan-soak-"))
    directory.mkdir(parents=True, exist_ok=True)
    clock = {"now": 1_800_000_000}
    paths = {
        "growth": directory / "growth.json",
        "memory": directory / "memory.json",
        "personality": directory / "personality.json",
        "continuity": directory / "continuity.json",
    }
    growth = GrowthEngine(paths["growth"], clock=lambda: clock["now"])
    memory = MemoryEngine(paths["memory"], clock=lambda: clock["now"])
    personality = PersonalityEngine(paths["personality"], clock=lambda: clock["now"])
    continuity = ContinuityStore(paths["continuity"], clock=lambda: clock["now"])
    backup = StateBackup(directory, directory / "backups", max_snapshots=6, clock=lambda: clock["now"])
    continuity.record_startup()
    first_snapshot = None
    reloads = 0
    for day in range(cycles):
        clock["now"] += 86400
        growth.sync({
            "xp": (day + 1) * 37,
            "days": {f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}": (day % 7) + 1},
            "counters": {"turns": day + 1},
        })
        memory.ingest_behavior({"userName": "小云", "likes": [f"合成喜好{day % 5}"]}, now=clock["now"])
        memory.ingest_state({"learned": [{"text": f"合成知识{day % 11}"}],
                             "diary": [{"text": f"合成日记{day % 13}"}]}, now=clock["now"])
        memory.remember_feedback(f"合成动作{day % 5}", day % 2 == 0, now=clock["now"])
        personality.feedback(day % 2 == 0, now=clock["now"])
        personality.update(
            companion={"trust": .35 + min(.5, day / 500), "curiosity": .45 + min(.4, day / 800),
                       "energy": .7, "valence": .2 + min(.5, day / 1000)},
            state={"counters": {"turns": day + 1}, "streak": min(day, 20)},
            now=clock["now"], interaction=True,
        )
        continuity.checkpoint(personality.snapshot(), memory.snapshot(), now=clock["now"])
        if day % 30 == 0:
            first_snapshot = first_snapshot or backup.create("soak")
        if day % 17 == 0:
            # Reload every engine, as a restart would.
            growth = GrowthEngine(paths["growth"], clock=lambda: clock["now"])
            memory = MemoryEngine(paths["memory"], clock=lambda: clock["now"])
            personality = PersonalityEngine(paths["personality"], clock=lambda: clock["now"])
            continuity = ContinuityStore(paths["continuity"], clock=lambda: clock["now"])
            reloads += 1
            assert growth.status == "ready", growth.error
            assert memory.status == "ready", memory.error
            assert personality.status == "ready", personality.error
            assert continuity.status == "ready", continuity.error
    verify = continuity.verify(personality.snapshot(), memory.snapshot())
    assert verify["stable"] is True, verify
    assert growth.snapshot()["xp"] == cycles * 37
    assert memory.snapshot()["total"] > 0
    if first_snapshot:
        backup.verify(first_snapshot)
    return {
        "ok": True, "cycles": cycles, "reloads": reloads,
        "growth": growth.snapshot(), "memory": memory.snapshot(),
        "personality": personality.snapshot(), "continuity": verify,
        "backup": str(first_snapshot) if first_snapshot else None,
        "directory": str(directory),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=90)
    parser.add_argument("--directory", default=None)
    args = parser.parse_args(argv)
    result = run(args.cycles, args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
