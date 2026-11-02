"""Audit-first coordination of local learners, public indexes and expression.

No HTTP or GUI in this coordinator. Downloaded data can only reach a bounded
lexicon/index; it never becomes an executable command or permission decision.
"""
from __future__ import annotations

import math

from companion_state import CompanionState
from core_curriculum import VERSION as CORE_VERSION, records as core_records
from knowledge_sources import CATALOG, platform_catalog_snapshot
from knowledge_store import KnowledgeStore
from neural_brain import ACTIONS, SOURCE_WEIGHTS
from training_journal import TrainingJournal


class LearningHub:
    def __init__(self, brain, emotion, journal=None, knowledge=None, companion=None):
        self.brain = brain
        self.emotion = emotion
        self.journal = journal if journal is not None else TrainingJournal()
        self.knowledge = knowledge if knowledge is not None else KnowledgeStore()
        self.companion = companion if companion is not None else CompanionState()
        self.audit_blocked = False
        self._bootstrap()

    def _finish(self, event_id, status, metrics=None, labels=None, error=""):
        ok = self.journal.finish(event_id, status, metrics, labels, error)
        if not ok:
            # A model may already have committed. Do not silently repeat or claim
            # it failed; leave the pending row for commit-unknown recovery.
            self.audit_blocked = True
        return ok

    def _begin(self, kind, domain, source, queued=False):
        if self.audit_blocked:
            return None
        return self.journal.begin(kind, domain, source, queued)

    def _bootstrap(self):
        if self.knowledge.has_source("core_curriculum", CORE_VERSION):
            return
        event_id = self._begin("knowledge_index", "general", "core_curriculum")
        if event_id is None:
            return
        result = self.knowledge.merge("core_curriculum", core_records())
        self._finish(event_id, "success" if result["ok"] else "failed", self._index_metrics(result), error="" if result["ok"] else "storage")

    @staticmethod
    def _index_metrics(result):
        return {"records_before": result["before"], "records_after": result["after"], "added": result["added"], "changed": result["changed"], "removed": result["removed"], "version_before": result["version_before"], "version_after": result["version_after"], "trained": 0}

    @staticmethod
    def _objective(probabilities, action, reward, source):
        strength = abs(reward) * SOURCE_WEIGHTS[source]
        if strength == 0:
            return None
        if reward > 0:
            return -strength * math.log(max(1e-12, probabilities[action]))
        return -strength * sum(math.log(max(1e-12, probabilities[other])) for other in ACTIONS if other != action) / (len(ACTIONS) - 1)

    def observe_action(self, context, action, reward, source="outcome"):
        if not isinstance(action, str) or action not in ACTIONS or not isinstance(source, str) or source not in SOURCE_WEIGHTS or isinstance(reward, bool) or not isinstance(reward, (int, float)):
            return False
        try:
            reward = float(reward)
        except (OverflowError, ValueError):
            return False
        if not math.isfinite(reward):
            return False
        reward = max(-1, min(1, reward))
        event_id = self._begin("action_fit", "action", "local_feedback" if source == "explicit" else "local_outcome")
        if event_id is None:
            return False
        before = self.brain.info()
        probabilities_before = self.brain.predict(context)
        ok = self.brain.observe(context, action, reward, source)
        after = self.brain.info()
        metrics = {"reward": reward, "samples_before": before["samples"], "samples_after": after["samples"], "updates_before": before["updates"], "updates_after": after["updates"], "version_before": before["version"], "version_after": after["version"], "weight_steps": max(0, after["updates"] - before["updates"]), "trained": int(ok and after["updates"] > before["updates"])}
        if ok:
            probabilities_after = self.brain.predict(context)
            metrics.update(probability_before=probabilities_before[action], probability_after=probabilities_after[action])
            loss_before = self._objective(probabilities_before, action, reward, source)
            if loss_before is not None:
                metrics.update(loss_before=loss_before, loss_after=self._objective(probabilities_after, action, reward, source))
        logged = self._finish(event_id, "success" if ok else "failed", metrics, {"action": action}, "" if ok else "model_rejected")
        return bool(ok and logged)

    def observe_emotion(self, text, source="local_chat"):
        if source not in ('local_chat', 'local_voice'):
            return self.emotion.classify(text)
        explicit = self.emotion.detect_self_label(text)
        event_id = self._begin("emotion_fit" if explicit else "emotion_read", "emotion", source)
        if event_id is None:
            result = self.emotion.classify(text)
            return dict(result, mood="neutral", learned=False, audit={"persisted": False})
        result = self.emotion.observe(text)
        audit = result.get("audit", {})
        committed = audit.get("persisted") is True
        metrics = {key: value for key, value in audit.items() if key != "persisted" and isinstance(value, (int, float)) and not isinstance(value, bool)}
        if "changed_weights" in audit:
            metrics["changed"] = audit["changed_weights"]
        metrics["trained"] = int(result.get("learned") is True)
        label = result.get("mood", "neutral")
        if not committed:
            status, error = "failed", "storage" if self.emotion.status == "error" else "protected"
        elif explicit:
            status, error = ("success", "") if result.get("learned") else ("no_change", "no_new_data")
        else:
            status, error = "observed", "no_label"
        self._finish(event_id, status, metrics, {"emotion": label}, error)
        return result

    def adapt(self, event, user_mood="neutral", source="local_touch"):
        event_id = self._begin("companion_adapt", "emotion", source)
        if event_id is None:
            return False
        result = self.companion.apply(event, user_mood)
        metrics = {}
        if result.get("ok"):
            for key in ("valence", "energy", "trust", "curiosity"):
                metrics[key + "_before"] = result["before"][key]
                metrics[key + "_after"] = result["after"][key]
            metrics["event_count"] = result["after"]["events"]
        return self._finish(event_id, "success" if result.get("ok") else "failed", metrics,
                            {"own_mood": result.get("after", {}).get("mood", "relaxed")}, "" if result.get("ok") else "storage")

    def prepare_public(self, include_knowledge=False):
        tasks = []
        for source in ["cldr_zh"] + (["countries", "cldr_units", "mime_db", "stackexchange"] if include_knowledge else []):
            kind = "public_lexicon" if source == "cldr_zh" else "knowledge_index"
            domain = "emotion" if source == "cldr_zh" else CATALOG[source]["domain"]
            event_id = self._begin(kind, domain, source, queued=True)
            if event_id is None:
                for task in tasks:
                    self._finish(task["id"], "cancelled", error="protected")
                return []
            tasks.append({"id": event_id, "source": source})
        return tasks

    def prepare_live(self):
        """Reserve a durable row before the separately paced dynamic fetch."""
        event_id = self._begin("knowledge_index", "geography", "usgs_live", queued=True)
        return {"id": event_id, "source": "usgs_live"} if event_id is not None else None

    def apply_public(self, task, result, cancelled=False):
        event_id, source = task["id"], task["source"]
        metrics = {key: result[key] for key in ("bytes", "validated", "rejected", "duration_ms") if isinstance(result.get(key), (int, float)) and not isinstance(result.get(key), bool)}
        metrics["trained"] = 0
        if cancelled:
            self._finish(event_id, "cancelled", metrics, error="disabled")
            return {"ok": False, "added": 0}
        if self.audit_blocked:
            return {"ok": False, "added": 0}
        if not result.get("ok"):
            error = result.get("errorCode") if result.get("errorCode") in ("schema", "protected", "storage", "rate_limit") else "network"
            self._finish(event_id, "failed", metrics, error=error)
            return {"ok": False, "added": 0}
        if source == "cldr_zh":
            before = self.emotion.snapshot()
            self.emotion.merge_lexicon(result.get("mapping", {}), "unicode-org/cldr-json")
            after = self.emotion.snapshot()
            imported = self.emotion.last_import
            ok = imported.get("persisted") is True
            added = max(0, after["lexicon"] - before["lexicon"]) if ok else 0
            metrics.update(public_cues_before=before["lexicon"], public_cues_after=after["lexicon"], features_before=before.get("features", 0), features_after=after.get("features", 0), added=added, validated=result.get("count", 0))
            changed = after.get("features", 0) != before.get("features", 0)
        else:
            merged = self.knowledge.merge(source, result.get("records", []), result.get("hash", ""))
            metrics.update(self._index_metrics(merged))
            ok, added = merged["ok"], merged["added"]
            changed = merged["changed"] or merged["removed"]
        status = "success" if ok and (added or changed) else "no_change" if ok else "failed"
        logged = self._finish(event_id, status, metrics, error="" if status == "success" else "no_new_data" if ok else "storage")
        if ok and logged and (added or changed):
            self.adapt("learn", source="local_outcome")
        return {"ok": bool(ok and logged), "added": added if ok else 0}

    def control(self, action):
        if action not in ("rollback", "reset"):
            return False
        event_id = self._begin("control", "action", "owner_control")
        if event_id is None:
            return False
        before = self.brain.info()
        ok = self.brain.rollback() if action == "rollback" else self.brain.reset()
        after = self.brain.info()
        metrics = {"version_before": before["version"], "version_after": after["version"], "samples_before": before["samples"], "samples_after": after["samples"], "updates_before": before["updates"], "updates_after": after["updates"]}
        logged = self._finish(event_id, "success" if ok else "failed", metrics, error=action if ok else "model_rejected")
        return bool(ok and logged)

    def companion_reply(self, text, reply, user_mood="neutral"):
        """Compose local expression/retrieval without storing the query."""
        if reply.get("action") or reply.get("name") or reply.get("like"):
            return reply, "本地陪伴"
        raw = "".join(str(text).split())[:240]
        state = self.companion.snapshot()
        answer = None
        meta = "本地陪伴"
        if any(word in raw for word in ("你有感情吗", "你有情感吗", "你有意识吗", "你真的有感情", "你会真的伤心")):
            answer = "我会把互动积累成自己的心情状态，让陪伴的语气和动作有变化。不过这是情感表达的模拟，不是真实意识。你可以在成长卡看见这些变化。"
        elif any(word in raw for word in ("你什么心情", "你的心情", "你现在心情", "你开心吗", "你现在怎么样", "你累吗")):
            answer = f"这会儿{state['name']}，{state['reason']}。" + self.companion.line()
        elif any(word in raw for word in ("你学到了什么", "你学会了什么", "学到什么知识", "你会哪些知识")):
            info = self.knowledge.snapshot()
            example = self.knowledge.example(sequence=state["events"])
            answer = f"我已经收录 {info['count']} 条基础知识，分在 {len(info['domains'])} 个领域里。"
            if example:
                answer += "比如这一条：" + example["text"]
            answer += "每次更新的来处和结果，都在成长卡的训练页。"
        elif user_mood == "neutral":
            found = self.knowledge.lookup(raw)
            if found:
                answer = "这条我有收录，一起看看：\n" + found["text"]
                meta = "本地知识 · " + found["sourceName"]
        # A default reply is handed back to the caller for an honest contextual
        # fallback; never replace a specific emotional reply with a random line.
        if answer is not None:
            reply = dict(reply, text=answer, action=None)
        return reply, meta

    @staticmethod
    def source_catalog(knowledge, journal, emotion):
        """Pure presentation snapshot, also usable by read-only renderers."""
        catalog = []
        for source in ("cldr_zh", "core_curriculum", "countries", "cldr_units", "mime_db", "stackexchange"):
            recent = next((row for row in journal["events"] if row["source"] == source and row["kind"] in ("public_lexicon", "knowledge_index")), None)
            status = "available"
            if source in knowledge["sources"] or (source == "cldr_zh" and emotion.get("lexicon", 0) > 0):
                status = "indexed"
            if recent and recent["status"] == "failed":
                status = "error"
            elif recent and recent["status"] in ("queued", "running"):
                status = "pending"
            catalog.append({"id": source, "status": status})
        return catalog

    def snapshot(self, active_source=""):
        journal = self.journal.snapshot()
        if self.audit_blocked:
            journal.update(status="error", error="训练结束状态未完整保存；模型提交结果待核对，新训练已暂停。")
        knowledge = self.knowledge.snapshot()
        catalog = self.source_catalog(knowledge, journal, self.emotion.snapshot())
        return {"journal": journal, "knowledge": knowledge, "brain": self.brain.info(), "companion": self.companion.snapshot(), "activeSource": active_source,
                "sourceCatalog": catalog, "platformSources": platform_catalog_snapshot()}
