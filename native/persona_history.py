"""Bounded, structured history for automatic persona reflection.

This store records only numeric trait snapshots and already-structured evidence
(kind/trait/action/liked/time). It never stores chat text, user names, URLs or
memory content. A history record is a review aid, not a personality mutation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
from pathlib import Path

VERSION = 1
MAX_ENTRIES = 180
MAX_BYTES = 256 * 1024
TRAITS = ("warmth", "curiosity", "playfulness", "steadiness", "expressiveness")
EVENT_KINDS = ("feedback", "interaction", "practice", "reflection")


def default_path():
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "persona-history.json"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _traits(snapshot):
    traits = snapshot.get("traits") if isinstance(snapshot, dict) else None
    if not isinstance(traits, dict):
        raise ValueError("snapshot has no traits")
    result = {}
    for key in TRAITS:
        value = _number(traits.get(key))
        if value is None:
            raise ValueError(f"snapshot trait is missing: {key}")
        result[key] = round(max(0.0, min(100.0, float(value))), 2)
    return result


def _digest(revision, traits):
    body = json.dumps({"revision": revision, "traits": traits}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _evidence(events):
    clean = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")[:24]
        if kind not in EVENT_KINDS:
            continue
        row = {"kind": kind, "at": int(_number(event.get("at")) or 0)}
        if isinstance(event.get("trait"), str) and event["trait"] in TRAITS:
            row["trait"] = event["trait"]
        if isinstance(event.get("action"), str):
            row["action"] = " ".join(event["action"].split())[:80]
        if isinstance(event.get("liked"), bool):
            row["liked"] = event["liked"]
        clean.append(row)
    return clean[:20]


class PersonaHistory:
    def __init__(self, path=None, clock=None):
        self.path = Path(path).expanduser() if path is not None else default_path()
        self._clock = clock or (lambda: time.time())
        self.status = "new"
        self.error = ""
        self.data = None
        self._load()

    def _load(self):
        try:
            with self.path.open("rb") as source:
                body = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            self.data = self._empty()
            return
        except OSError:
            self.status, self.error = "protected", "persona history is unreadable"
            return
        if len(body) > MAX_BYTES:
            self.status, self.error = "protected", "persona history is too large"
            return
        try:
            raw = json.loads(body.decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != VERSION:
                raise ValueError
            entries = raw.get("entries")
            if not isinstance(entries, list):
                raise ValueError
            clean = []
            for row in entries[-MAX_ENTRIES:]:
                if not isinstance(row, dict):
                    continue
                try:
                    traits = _traits(row)
                except ValueError:
                    continue
                revision = int(_number(row.get("revision")) or 0)
                clean.append({
                    "at": int(_number(row.get("at")) or 0),
                    "revision": max(0, revision),
                    "traits": traits,
                    "evidence": _evidence(row.get("evidence")),
                    "reason": str(row.get("reason") or "state")[:40],
                    "digest": str(row.get("digest") or _digest(revision, traits))[:16],
                })
            self.data = {"version": VERSION, "entries": clean}
            self.status = "ready"
        except (ValueError, TypeError, UnicodeDecodeError):
            self.data = None
            self.status, self.error = "protected", "persona history is corrupt or unsupported"

    def _empty(self):
        return {"version": VERSION, "entries": []}

    def _save(self, before):
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(text.encode("utf-8")) > MAX_BYTES:
                raise ValueError("persona history is too large")
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self.status, self.error = "ready", ""
            return True
        except (OSError, ValueError, TypeError):
            self.data = before
            self.status, self.error = "error", "persona history could not be written"
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def record(self, snapshot, evidence=(), reason="state", now=None):
        """Append a changed snapshot; unchanged snapshots are a no-op."""
        if self.status == "protected" or not isinstance(snapshot, dict):
            return False
        try:
            traits = _traits(snapshot)
        except ValueError:
            return False
        revision = int(snapshot.get("revision") or 0)
        digest = _digest(revision, traits)
        if self.data["entries"]:
            last = self.data["entries"][-1]
            if last.get("digest") == digest and last.get("revision") == revision:
                return False
        entry = {
            "at": int(self._clock() if now is None else now),
            "revision": max(0, revision),
            "traits": traits,
            "evidence": _evidence(evidence),
            "reason": str(reason or "state")[:40],
            "digest": digest,
        }
        before = copy.deepcopy(self.data)
        self.data["entries"].append(entry)
        self.data["entries"] = self.data["entries"][-MAX_ENTRIES:]
        if not self._save(before):
            return False
        return True

    def entries(self, limit=None):
        rows = self.data["entries"] if isinstance(self.data, dict) else []
        if limit is None:
            return copy.deepcopy(rows)
        return copy.deepcopy(rows[-max(1, min(MAX_ENTRIES, int(limit))):])

    def latest(self):
        rows = self.entries(1)
        return rows[0] if rows else None
