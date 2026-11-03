"""Native action runtime: capture decision features before UI/motion side effects."""
import random
from motion_model import plan_run


class ActionRuntime:
    def _maybe_talk(self, force: bool = False, neural_context=None) -> None:
        now_ms = self._now_ms()
        if not force and now_ms < self.next_talk_ms:
            return
        if not self._can_talk():
            self.next_talk_ms = now_ms + 15000
            return
        context = dict(neural_context) if isinstance(neural_context, dict) else self._neural_context()
        text = self._talk_text()
        if not text:
            self.next_talk_ms = now_ms + 60000
            return
        self._ensure_conversation()
        self.last_proactive = text
        was_visible = self.conversation.isVisible()
        self._refresh_online_ui()
        self.conversation.show_proactive(text, "云岚主动搭话 · 可直接回复")
        if not was_visible:
            self._place_conversation(self.conversation)
        self.chat_open = True
        self.last_neural = (context, "talk")
        self.last_neural_ms = now_ms
        if self._idle_only() and not self.tasks:
            self._show_overlay(text, "云岚 · 自己跑来说话", "IDLE", 4800)
        self.learner.note_greeting()
        self._sync_bubble_size()
        self.update()
        mood = self.emotion.current().get("label", "neutral")
        pace = 1.25 - 0.5 * self.personality.initiative(mood, bool(self.memory.recent(1)))
        self.next_talk_ms = now_ms + int((random.randint(120000, 240000) if self._idle_only() else random.randint(300000, 600000)) * pace)


    def _choose_neural_action(self) -> None:
        allowed = ["rest"]
        if self._can_move():
            allowed.extend(("run", "hop"))
        if self._can_talk():
            allowed.append("talk")
        if len(allowed) == 1:
            self._schedule_next_motion()
            return
        context = self._neural_context()
        try:
            action = self.brain.choose(context, allowed, explore=0.15)
        except (ValueError, TypeError, KeyError, OverflowError):
            action = "rest"
        if action == "rest":
            self._schedule_next_motion()
            return
        if action == "talk":
            self._maybe_talk(force=True, neural_context=context)
            return
        ok = self._start_run(neural_context=context) if action == "run" else self._start_hop(neural_context=context)
        if not ok:
            self._schedule_next_motion()


    def _start_autonomous_action(self) -> None:
        self._choose_neural_action()


    def _start_run(
        self,
        distance: int | None = None,
        direction: int | None = None,
        force_fall: bool | None = None,
        speed: float | None = None,
        allow_during_chat: bool = False,
        neural_context=None,
    ) -> bool:
        if self.motion or not self.isVisible() or self.reduced_motion or self.dragging or self.menu_open or self.tasks or self.model.base_state != "IDLE" or (self._conversation_engaged() and not allow_during_chat):
            return False
        pet_width, _ = self._pet_size()
        geometry = self._screen_geometry_at(self.pet_x + pet_width // 2, self.pet_y)
        if geometry is None:
            return False
        direction = direction if direction in (-1, 1) else random.choice((-1, 1))
        plan = plan_run(self.pet_x, self.pet_y, geometry.x() + 8,
                        geometry.x() + geometry.width() - pet_width - 8,
                        int(distance if distance is not None else random.randint(180, 480)),
                        direction, speed or self.learner.run_speed(),
                        self.learner.fall_chance(), force_fall, random)
        if plan is None:
            return False
        context = dict(neural_context) if isinstance(neural_context, dict) else self._neural_context()
        plan['t0'] = self._now_ms()
        self.motion = plan
        self.pending_neural = (context, "run")
        self.fatigue = min(1.0, self.fatigue + 0.25)
        self.update()
        return True


    def _start_hop(self, height: int | None = None, allow_during_chat: bool = False, neural_context=None) -> bool:
        if self.motion or not self.isVisible() or self.reduced_motion or self.dragging or self.menu_open or self.tasks or self.model.base_state != "IDLE" or (self._conversation_engaged() and not allow_during_chat):
            return False
        height = int(height if height is not None else random.randint(34, 54))
        context = dict(neural_context) if isinstance(neural_context, dict) else self._neural_context()
        self.motion = {
            "kind": "hop",
            "x": self.pet_x,
            "base_y": self.pet_y,
            "t0": self._now_ms(),
            "dur": random.randint(560, 760),
            "height": height,
        }
        self.pending_neural = (context, "hop")
        self.fatigue = min(1.0, self.fatigue + 0.12)
        return True

