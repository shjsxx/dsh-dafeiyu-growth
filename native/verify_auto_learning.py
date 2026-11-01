"""Observe the installed pet's first automatic update; performs no HTTP itself."""
import json
import sys
import time

from learning_state import PublicLearningState
from emotion_model import EmotionModel


def main():
    # Cold pet launch plus two bounded source transports can exceed 55s.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = PublicLearningState()
        if not state.enabled:
            raise RuntimeError("Automatic public reading is not enabled")
        if state.last_success > 0:
            model = EmotionModel()
            print(json.dumps({
                "ok": True, "automaticEnabled": True,
                "lastSuccess": state.last_success, "nextCheck": state.next_check,
                "newFeatures": state.last_added, "publicCues": model.snapshot()["lexicon"],
                "sources": state.sources,
            }, ensure_ascii=False, indent=2))
            return 0
        if state.last_attempt and state.error:
            raise RuntimeError(state.error)
        # Bounded observer of our own state file, not a repeated network request.
        time.sleep(.5)
    raise RuntimeError("The pet has not completed its first scheduled public update")


if __name__ == "__main__":
    sys.exit(main())
