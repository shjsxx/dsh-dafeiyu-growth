"""Real native action methods; fake presentation, no disk/model updates."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent / 'runtime'))
from action_runtime import ActionRuntime


class Host(ActionRuntime):
    def __init__(self):
        self.motion = None
        self.chat_open = False
        self.tasks = []
        self.reduced_motion = self.dragging = self.menu_open = False
        self.model = SimpleNamespace(base_state='IDLE')
        self.pet_x, self.pet_y = 300, 500
        self.fatigue = 0.0
        self.next_talk_ms = 0
        self.last_neural = self.pending_neural = None
        self.context_calls = 0
        self.scheduled = 0
        self.line = 'short line'
        self.conversation = None
        self.emotion = SimpleNamespace(current=lambda:{'label':'neutral'})
        self.personality = SimpleNamespace(initiative=lambda *a:0.5)
        self.memory = SimpleNamespace(recent=lambda n:[])
        self.learner = SimpleNamespace(note_greeting=lambda:None, run_speed=lambda:200,
                                       fall_chance=lambda:0)
        self.brain = SimpleNamespace(choose=lambda context, allowed, **kw:'talk')

    def _neural_context(self):
        self.context_calls += 1
        return {'chat_open':self.chat_open, 'motion':bool(self.motion), 'fatigue':self.fatigue,
                'sequence':self.context_calls}
    def _now_ms(self): return 10000
    def _can_talk(self): return True
    def _can_move(self): return True
    def _talk_text(self): return self.line
    def _conversation_engaged(self): return False
    def isVisible(self): return True
    def _pet_size(self): return (100, 100)
    def _screen_geometry_at(self, *a): return SimpleNamespace(x=lambda:0, width=lambda:1920)
    def _ensure_conversation(self):
        self.chat_open = True
        self.conversation = SimpleNamespace(isVisible=lambda:False, show_proactive=lambda *a:None)

    def _invite_to_chat(self, text):
        self._ensure_conversation()
        self.conversation.show_proactive(text, '云岚主动搭话 · 可直接回复')

    def _refresh_online_ui(self): pass
    def _place_conversation(self, *a): pass
    def _idle_only(self): return True
    def _show_overlay(self, *a): pass
    def _sync_bubble_size(self): pass
    def update(self): pass
    def _schedule_next_motion(self): self.scheduled += 1


class ActionRuntimeTests(unittest.TestCase):
    def test_direct_proactive_captures_before_opening_chat(self):
        host = Host()
        host._maybe_talk(force=True)
        context, action = host.last_neural
        self.assertFalse(context['chat_open'])
        self.assertTrue(host.chat_open)
        self.assertEqual(host.context_calls, 1)
        self.assertEqual(action, 'talk')

    def test_chosen_talk_reuses_exact_decision_context(self):
        host = Host()
        host._choose_neural_action()
        self.assertEqual(host.context_calls, 1)
        self.assertFalse(host.last_neural[0]['chat_open'])

    def test_chosen_motion_reuses_exact_decision_context(self):
        for action in ('run', 'hop'):
            with self.subTest(action=action):
                host = Host()
                host.brain.choose = lambda *args, **kwargs: action
                host._choose_neural_action()
                self.assertEqual(host.context_calls, 1)
                self.assertEqual(host.pending_neural[1], action)
                self.assertFalse(host.pending_neural[0]['motion'])
                self.assertEqual(host.pending_neural[0]['fatigue'], 0)
                self.assertIsNotNone(host.motion)
                self.assertGreater(host.fatigue, 0)

    def test_direct_hop_captures_before_side_effects(self):
        host = Host()
        self.assertTrue(host._start_hop())
        self.assertFalse(host.pending_neural[0]['motion'])
        self.assertEqual(host.pending_neural[0]['fatigue'], 0)

    def test_direct_run_captures_before_side_effects(self):
        host = Host()
        self.assertTrue(host._start_run(direction=1, force_fall=False))
        self.assertFalse(host.pending_neural[0]['motion'])
        self.assertEqual(host.pending_neural[0]['fatigue'], 0)

    def test_paused_proactive_never_creates_dialogue_or_reward(self):
        host = Host()
        host.line = ''
        host._maybe_talk(force=True)
        self.assertIsNone(host.conversation)
        self.assertIsNone(host.last_neural)
        self.assertGreater(host.next_talk_ms, host._now_ms())

    def test_blocked_motion_does_not_create_training_candidate(self):
        host = Host()
        host.reduced_motion = True
        self.assertFalse(host._start_run())
        self.assertFalse(host._start_hop())
        self.assertEqual(host.context_calls, 0)
        self.assertIsNone(host.pending_neural)


if __name__ == '__main__': unittest.main()
