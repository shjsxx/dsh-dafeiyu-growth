"""Observe the installed pet's audited public curriculum, without training it.

No HTTP, no cloud calls, no fabricated owner self-reports. All model/index/log
objects are read-only; actual work must be performed by the running helper.
"""
import json
import os
import time
from live_online import LiveOnlineState

from companion_state import CompanionState
from knowledge_store import KnowledgeStore
from learning_state import PublicLearningState
from training_journal import TrainingJournal

if os.environ.get('DSH_DAFEIYU_ALLOW_LIVE_PROBE') != '1':
    raise SystemExit('维护者脚本：会读取真实档案并驱动已安装桌宠。设置 DSH_DAFEIYU_ALLOW_LIVE_PROBE=1 后再运行。')


def main():
    deadline = time.monotonic() + 240
    required = {"countries", "cldr_units", "mime_db", "core_curriculum", "stackexchange"}
    require_online = os.environ.get("DSH_DAFEIYU_VERIFY_ONLINE") == "1"
    if require_online:
        required.add("usgs_live")
    while time.monotonic() < deadline:
        state = PublicLearningState()
        if not state.enabled or not state.knowledge_enabled:
            raise RuntimeError("Authorized public curriculum is not enabled")
        journal = TrainingJournal(read_only=True).snapshot()
        if journal["status"] in ("protected", "error"):
            raise RuntimeError("Audit storage is not healthy")
        store = KnowledgeStore(read_only=True)
        rows = journal["events"]
        imported = {row["source"] for row in rows if row["kind"] == "knowledge_index" and row["status"] in ("success", "no_change")}
        active = any(row["status"] in ("queued", "running") and row["source"] in required | {"cldr_zh"} for row in rows)
        if required <= imported and all(store.has_source(source) for source in required) and not active:
            checks = {question: store.lookup(question) for question in ("光年是什么？", "JSON是什么？", "法国首都是什么？", "Python元类是什么？")}
            if not all(checks.values()):
                raise RuntimeError("Local knowledge retrieval checks failed")
            online = LiveOnlineState().snapshot()
            if require_online:
                checks["公开地震监测是什么？"] = store.lookup("公开地震监测是什么？")
                if not checks["公开地震监测是什么？"] or not online["enabled"] or not online["lastSuccess"] or online["errorCode"]:
                    raise RuntimeError("Dynamic source has not committed successfully")
            print(json.dumps({"ok": True, "automaticEnabled": state.enabled, "online": online,
                "knowledgeEnabled": state.knowledge_enabled,
                "knowledge": store.snapshot(), "recordedEvents": journal["retained"],
                "recordedTotals": journal["totals"], "companion": CompanionState(read_only=True).snapshot(),
                "retrievalChecks": {question: value["text"] for question, value in checks.items()},
                "publicRuns": [{"source": row["source"], "status": row["status"], "metrics": row["metrics"]} for row in rows if row["source"] in required | {"cldr_zh"}],
            }, ensure_ascii=False, indent=2))
            return
        if state.failures and not active:
            raise RuntimeError("Some public curriculum sources failed; inspect the recorded events")
        # Bounded observation only; this loop never makes a network request.
        time.sleep(.5)
    raise RuntimeError("The installed helper did not finish the audited curriculum in time")


if __name__ == "__main__":
    main()
