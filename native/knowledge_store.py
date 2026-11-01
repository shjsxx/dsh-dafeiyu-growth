"""Atomic bounded public knowledge index with deterministic, cited retrieval.

Indexing is not neural weight training. Queries are transient: no query text,
input hash, private sentence or retrieval history is written to disk.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path

from core_curriculum import REFERENCES
from knowledge_sources import CATALOG

DOMAINS = {"geography", "astronomy", "science", "computing", "general"}
INTENTS = {"capital", "unit_name", "mime", "concept"}
LIMIT = 1500
MAX_BYTES = 2 * 1024 * 1024


def default_path():
    explicit = os.environ.get("DSH_DAFEIYU_KNOWLEDGE_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    growth = os.environ.get("DSH_DAFEIYU_GROWTH_FILE", "").strip()
    if growth:
        return Path(growth).expanduser().parent / "knowledge.json"
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "dsh-dafeiyu-growth" / "knowledge.json"


def _plain(value, limit):
    if not isinstance(value, str) or not 0 < len(value) <= limit or any(char in value for char in "\x00<>\r\n"):
        raise ValueError("invalid plain field")
    return value


def _record(raw):
    if not isinstance(raw, dict) or raw.get("domain") not in DOMAINS or raw.get("intent") not in INTENTS:
        raise ValueError("record schema")
    key = _plain(raw.get("key"), 100)
    if not re.fullmatch(r"[a-z0-9.+_-]{1,100}", key):
        raise ValueError("record id")
    aliases = raw.get("aliases")
    if not isinstance(aliases, list) or not 1 <= len(aliases) <= 20:
        raise ValueError("aliases")
    aliases = list(dict.fromkeys(_plain(alias, 100) for alias in aliases))
    reference = raw.get("reference", "")
    if reference not in REFERENCES:
        reference = ""
    return {"key": key, "domain": raw["domain"], "intent": raw["intent"], "topic": _plain(raw.get("topic"), 100), "aliases": aliases, "answer": _plain(raw.get("answer"), 600), "reference": reference}


class KnowledgeStore:
    def __init__(self, path=None, read_only=False):
        self.path = Path(path) if path is not None else default_path()
        self.read_only = read_only
        self.status = "new"
        self.error = ""
        self.data = {"version": 1, "revision": 0, "sources": {}, "records": {}}
        if self.path.exists():
            try:
                if self.path.stat().st_size > MAX_BYTES:
                    raise ValueError("oversized")
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("records"), dict) or len(raw["records"]) > LIMIT:
                    raise ValueError("index schema")
                records = {}
                for identity, row in raw["records"].items():
                    if not isinstance(identity, str) or ":" not in identity:
                        raise ValueError("record identity")
                    source, key = identity.split(":", 1)
                    if source not in CATALOG:
                        raise ValueError("source")
                    clean = _record(row)
                    if clean["key"] != key:
                        raise ValueError("id mismatch")
                    records[identity] = clean
                revision = raw.get("revision", 0)
                if type(revision) is not int or not 0 <= revision <= 1_000_000_000:
                    raise ValueError("revision")
                sources = raw.get("sources", {})
                if not isinstance(sources, dict):
                    raise ValueError("sources")
                clean_sources = {}
                for source, info in sources.items():
                    if source not in CATALOG or not isinstance(info, dict):
                        raise ValueError("source metadata")
                    fingerprint = info.get("hash", "")
                    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                        raise ValueError("source digest")
                    clean_sources[source] = {"hash": fingerprint, "version": info.get("version") if info.get("version") == CATALOG[source]["version"] else "", "updated": info.get("updated") if isinstance(info.get("updated"), (int, float)) and not isinstance(info.get("updated"), bool) and 0 <= info["updated"] <= 32_503_680_000 else 0}
                self.data = {"version": 1, "revision": revision, "sources": clean_sources, "records": records}
                self.status = "ready"
            except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                self.status = "protected"
                self.error = "知识索引不可读；保留原文件，不覆盖。"

    def has_source(self, source, version=None):
        info = self.data["sources"].get(source)
        return bool(info and (version is None or info.get("version") == version))

    def _commit(self, data):
        if self.read_only or self.status == "protected":
            return False
        try:
            content = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(content.encode("utf-8")) > MAX_BYTES:
                raise OSError("index limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            self.status = "error"
            self.error = "知识索引保存失败，保留旧版本。"
            return False
        self.data = data
        self.status = "ready"
        self.error = ""
        return True

    def merge(self, source, records, fingerprint=""):
        before = len(self.data["records"])
        result = {"ok": False, "before": before, "after": before, "added": 0, "changed": 0, "removed": 0, "version_before": self.data["revision"], "version_after": self.data["revision"]}
        if source not in CATALOG or not isinstance(records, list) or len(records) > LIMIT or not records:
            return result
        try:
            normalized = [_record(row) for row in records]
            incoming = {source + ":" + row["key"]: row for row in normalized}
            if len(incoming) != len(normalized):
                raise ValueError("duplicate record")
            if not fingerprint:
                fingerprint = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise ValueError("invalid source digest")
        except (ValueError, TypeError):
            return result
        next_data = copy.deepcopy(self.data)
        previous = {key: row for key, row in next_data["records"].items() if key.startswith(source + ":")}
        added = len(incoming.keys() - previous.keys())
        removed = len(previous.keys() - incoming.keys())
        changed = sum(incoming[key] != previous[key] for key in incoming.keys() & previous.keys())
        next_data["records"] = {key: row for key, row in next_data["records"].items() if not key.startswith(source + ":")}
        next_data["records"].update(incoming)
        if len(next_data["records"]) > LIMIT:
            return result
        if added or removed or changed:
            next_data["revision"] += 1
        next_data["sources"][source] = {"hash": fingerprint, "version": CATALOG[source]["version"], "updated": time.time()}
        if not self._commit(next_data):
            return result
        return dict(result, ok=True, after=len(next_data["records"]), added=added, changed=changed, removed=removed, version_after=next_data["revision"])

    def snapshot(self):
        counts = {}
        for row in self.data["records"].values():
            counts[row["domain"]] = counts.get(row["domain"], 0) + 1
        return {"status": self.status, "error": self.error, "count": len(self.data["records"]), "revision": self.data["revision"], "domains": counts, "sources": copy.deepcopy(self.data["sources"])}

    def lookup(self, text):
        if not isinstance(text, str) or len(text) > 240:
            return None
        query = "".join(text.lower().split())
        asked = re.search(r"什么|多少|哪里|哪|怎么|含义|意思|区别|告诉我|讲讲|介绍|科普|学到|吗|[?？]", query)
        short_lookup = re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9._-]{1,18}(?:首都|都城|单位|扩展名|格式)", query)
        if not asked and not short_lookup:
            return None
        ranked = []
        for identity, row in self.data["records"].items():
            if row["intent"] == "capital" and not re.search(r"首都|都城|capital", query):
                continue
            if row["intent"] == "unit_name" and not re.search(r"单位|中文|名称|名字|怎么写|翻译|[a-z]{3,}-[a-z]{3,}", query):
                continue
            matches = [len(alias) for alias in row["aliases"] if len(alias) >= 2 and "".join(alias.lower().split()) in query]
            if not matches:
                continue
            score = max(matches) + (6 if identity.startswith("core_curriculum:") else 0)
            if row["intent"] == "capital":
                score += 8
            ranked.append((score, identity, row))
        if not ranked:
            return None
        _, identity, row = max(ranked, key=lambda item: (item[0], item[1]))
        source = identity.split(":", 1)[0]
        url = REFERENCES.get(row.get("reference"), "") or CATALOG[source]["url"]
        return {"text": row["answer"], "topic": row["topic"], "domain": row["domain"], "source": source, "sourceName": CATALOG[source]["name"], "sourceUrl": url, "recordId": identity}

    def example(self, domain="", sequence=0):
        rows = [(identity, row) for identity, row in sorted(self.data["records"].items()) if not domain or row["domain"] == domain]
        if not rows:
            return None
        identity, row = rows[int(sequence) % len(rows)]
        source = identity.split(":", 1)[0]
        return {"text": row["answer"], "topic": row["topic"], "domain": row["domain"], "source": source, "sourceName": CATALOG[source]["name"]}
