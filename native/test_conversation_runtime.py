"""Actual runtime-method regressions with a lightweight host and temporary models.

No installed profile, network, provider, timers, window or background worker is used.
"""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from behavior import BehaviorLearner
from companion_context import CompanionContext
from companion_state import CompanionState
from emotion_model import EmotionModel
from knowledge_store import KnowledgeStore
from learning_hub import LearningHub
from memory_engine import MemoryEngine
from neural_brain import NeuralBrain
from personality_engine import PersonalityEngine
from relationship import RelationshipStore
from retrieval_review import RetrievalReviewStore
from training_journal import TrainingJournal
from runtime.companion_runtime import CompanionRuntime
from runtime.conversation_runtime import ConversationRuntime


CONFIG = {'endpoint': 'https://example.invalid/v1', 'model': 'fake', 'max_tokens': 64}


class FakeDialog:
    def __init__(self):
        self.cloud = dict(CONFIG)
        self.cloud_button = SimpleNamespace(setChecked=mock.Mock())
        self.replies = []
        self.authorized = False
        self.resets = 0

    def isVisible(self):
        return True

    def show_reply(self, text, meta='', error=False, **kwargs):
        self.replies.append({'text': text, 'meta': meta, 'error': error, **kwargs})

    def set_retrieval_available(self, enabled):
        self.retrieval_available = enabled

    def set_cloud_state(self, config):
        self.cloud = dict(config)

    def context_before_current(self):
        return []

    def memory_context_enabled(self):
        return self.authorized

    def reset_local_context(self):
        self.resets += 1
        self.replies.clear()
        self.authorized = False


class Host(ConversationRuntime, CompanionRuntime):
    def __init__(self, root):
        self.conversation = FakeDialog()
        self.conversation_serial = 1
        self.cloud_busy = False
        self.voice_tools = None
        self.voice_engine = None
        self.direct_input_source = ''
        self.growth_dialog = None
        self.chat_open = True
        self.last_proactive = ''
        self.last_user_text = self.last_reply_text = self.last_topic = ''
        self._chat_may_learn = True
        self.learner = BehaviorLearner(root / 'behavior.json')
        self.emotion = EmotionModel(root / 'emotion.json')
        self.personality = PersonalityEngine(root / 'personality.json')
        self.memory = MemoryEngine(root / 'memory.json')
        self.learning = LearningHub(
            NeuralBrain(root / 'brain.json'), self.emotion,
            journal=TrainingJournal(root / 'journal.json'),
            knowledge=KnowledgeStore(root / 'knowledge.json'),
            companion=CompanionState(root / 'companion.json'))
        self.cloud_reply = SimpleNamespace(emit=mock.Mock())
        self._refresh_learning_ui = mock.Mock()
        self._trigger_reaction = mock.Mock()
        self._speak_voice_reply = mock.Mock()
        self._voice_stop = mock.Mock()
        self._show_overlay = mock.Mock()
        self._sync_bubble_size = mock.Mock()
        self.update = mock.Mock()
        self._spawn_hearts = mock.Mock()
        self._start_run = mock.Mock()
        self._start_hop = mock.Mock()
        self._start_spin = mock.Mock()

    def _now_ms(self):
        return 1000

    def _intercept_command(self, text):
        return False

    def _chat_reading(self, text):
        return {'mood': 'neutral'}


class ConversationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # All model constructors have explicit temporary paths. Forbid even an
        # accidental real provider call in tests which do not mock its result.
        self.network_guard = mock.patch('runtime.conversation_runtime.ask_cloud',
                                        side_effect=AssertionError('unexpected provider call'))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.host = Host(Path(self.temp.name))
        self.config_patch = mock.patch('runtime.conversation_runtime.load_config', return_value=dict(CONFIG))
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.thread_patch = mock.patch('runtime.conversation_runtime.threading.Thread')
        self.thread = self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)

    def reply(self):
        return self.host.conversation.replies[-1]

    def dispatch(self, text):
        self.host._conversation_cloud(text)
        self.assertTrue(self.host.cloud_busy)
        kwargs = self.thread.call_args.kwargs
        return kwargs['args']

    def finish(self, args, text='模型回答'):
        with mock.patch('runtime.conversation_runtime.ask_cloud', return_value={'ok': True, 'text': text}):
            self.host._cloud_worker(*args)
        result = self.host.cloud_reply.emit.call_args.args[0]
        self.host._on_cloud_reply(result)
        return result

    def test_native_voice_local_path_initializes_context_without_dialog(self):
        self.host.conversation = None
        self.host.direct_input_source = 'voice_direct'
        self.host._conversation_local('我昨天去了公园')
        self.assertIsInstance(self.host.dialogue_context, CompanionContext)
        self.host._conversation_local('风很舒服')
        self.assertIn('公园的风', self.host.last_reply_text)
        self.assertEqual(len(self.host.dialogue_context.recent_turns()), 4)

    def test_explicit_smart_mode_preference_selects_cloud_without_sending(self):
        self.host.cloud_config = {'prefer_cloud': True, 'endpoint': 'https://example.invalid/v1',
                                  'model': 'fake'}
        self.host._apply_cloud_preference()
        self.host.conversation.cloud_button.setChecked.assert_called_once_with(True)

    def test_relationship_recall_is_answered_locally_before_cloud(self):
        store = RelationshipStore(self.host.memory.path.with_name('relationship.json'))
        context = CompanionContext()
        context.record('我昨天去了公园', '公园那边怎么样？')
        store.remember(context, mood='neutral')
        self.host.relationship_store = store
        self.host._conversation_cloud('上次咱们聊的啥')
        self.assertIn('关系记忆', self.reply()['meta'])
        self.assertIn('公园', self.reply()['text'])
        self.assertFalse(self.host.cloud_busy)
        self.thread.assert_not_called()

    def test_structured_memory_question_not_shadowed_by_topic(self):
        self.host.memory.ingest_state({'diary': [{'text': '公园有一座白桥'}]})
        self.host._conversation_local('我昨天去了公园')
        self.host._conversation_local('你还记得公园吗')
        self.assertIn('记忆引擎', self.reply()['meta'])
        self.assertIn('白桥', self.reply()['text'])
        self.assertTrue(self.reply()['private_context'])

    def test_factual_knowledge_not_shadowed_by_subject_or_old_topic(self):
        self.host.learning.knowledge.merge('core_curriculum', [{
            'key': 'music', 'domain': 'general', 'topic': '音乐', 'aliases': ['音乐'],
            'answer': '音乐是一种声音艺术。', 'intent': 'concept'}])
        self.host._conversation_local('我昨天去了公园')
        self.host._conversation_local('介绍音乐')
        self.assertIn('本地知识', self.reply()['meta'])
        self.assertNotIn('你提到了', self.reply()['text'])
        self.assertFalse(self.reply()['private_context'])

    def test_public_memory_knowledge_not_shadowed(self):
        self.host.memory.ingest_knowledge([
            {'topic': '音乐声学', 'text': '音乐声学研究声音的物理性质', 'source': 'test'}], 1)
        self.host.learning.knowledge.lookup = mock.Mock(return_value=None)
        self.host._conversation_local('介绍音乐声学')
        self.assertIn('公开知识', self.reply()['meta'])
        self.assertIn('物理性质', self.reply()['text'])

    def test_explicit_name_preference_and_actions_preserved(self):
        self.host._conversation_local('我叫小林')
        self.assertEqual(self.host.learner.data['userName'], '小林')
        self.host._conversation_local('我叫什么')
        self.assertIn('小林', self.reply()['text'])
        self.host._conversation_local('我喜欢音乐')
        self.assertIn('记住啦', self.reply()['text'])
        with mock.patch('runtime.conversation_runtime.QTimer.singleShot', side_effect=lambda delay, fn: fn()):
            self.host._conversation_local('跑一圈')
        self.host._start_run.assert_called_once_with(allow_during_chat=True)

    def test_remembered_name_greeting_is_private_but_current_name_input_is_not(self):
        self.host._conversation_local('我叫小林')
        self.assertFalse(self.reply()['private_context'])
        self.host._conversation_local('你好')
        self.assertIn('小林', self.reply()['text'])
        self.assertTrue(self.reply()['private_context'])

    def test_private_reply_provenance_survives_local_followup_and_cloud_repair(self):
        self.host.memory.ingest_state({'diary': [{'text': '公园有一座白桥'}]})
        self.host._conversation_local('你还记得公园吗')
        self.assertTrue(self.reply()['private_context'])
        self.host._conversation_local('然后呢')
        self.assertTrue(self.reply()['private_context'])
        self.host._conversation_cloud('你答非所问')
        self.assertTrue(self.reply()['private_context'])
        self.host._conversation_local('我今天去了图书馆')
        self.assertFalse(self.reply()['private_context'])
        self.host._reset_conversation_context()
        self.assertFalse(self.host._last_reply_private)

    def test_proactive_does_not_override_topic_followup_and_is_consumed(self):
        self.host._conversation_local('我昨天去了公园')
        self.host._conversation_local('风很舒服')
        self.host.last_proactive = '旧主动消息'
        self.host._conversation_local('然后呢')
        self.assertIn('公园', self.reply()['text'])
        self.assertIn('风很舒服', self.reply()['text'])
        self.assertNotIn('基础知识', self.reply()['text'])
        self.assertEqual(self.host.last_proactive, '')

    def test_proactive_does_not_override_legacy_followup_or_repair(self):
        self.host._record_exchange('天气', '只知道这些')
        self.host.last_topic = '天气'
        self.host.last_proactive = '旧主动消息'
        self.host._conversation_local('继续呢')
        self.assertIn('天气', self.reply()['text'])
        self.host.last_proactive = '旧主动消息'
        self.host._conversation_local('你答非所问')
        self.assertIn('抱歉', self.reply()['text'])
        self.assertNotIn('基础知识', self.reply()['text'])

    def test_cloud_memory_short_circuit_initializes_context_and_marks_private(self):
        self.host._conversation_cloud('你还记得公园吗')
        self.assertTrue(self.reply()['private_context'])
        self.assertIn('未发送云端', self.reply()['meta'])
        self.thread.assert_not_called()

    def test_pending_reply_records_original_request_not_intervening_turn(self):
        args = self.dispatch('请谈一个开放的话题')
        self.host._conversation_local('我昨天去了公园')
        self.finish(args)
        turns = self.host.dialogue_context.recent_turns()
        self.assertEqual(turns[-2]['content'], '请谈一个开放的话题')
        self.assertEqual(turns[-1]['content'], '模型回答')
        self.assertIsNone(self.host._pending_model_request)
        self.assertEqual(self.host._pending_model_text, '')

    def test_pending_exchange_keeps_original_mood_not_later_current_emotion(self):
        self.host._chat_reading = lambda text: {'mood': 'sad'}
        args = self.dispatch('开放话题')
        with mock.patch.object(self.host, '_record_exchange', wraps=self.host._record_exchange) as record:
            self.finish(args)
        self.assertEqual(record.call_args.args[2], 'sad')

    def test_late_closed_session_reply_does_not_clear_new_busy_or_pending(self):
        old_args = self.dispatch('旧请求')
        self.host._conversation_closed()
        self.host.conversation = FakeDialog()
        new_args = self.dispatch('新请求')
        pending = dict(self.host._pending_model_request)
        self.finish(old_args, '不该出现的旧回答')
        self.assertTrue(self.host.cloud_busy)
        self.assertEqual(self.host._pending_model_request, pending)
        self.assertEqual(self.host.conversation.replies, [])
        self.finish(new_args, '新回答')
        self.assertFalse(self.host.cloud_busy)
        self.assertEqual(self.host.last_user_text, '新请求')
        self.assertEqual(self.reply()['text'], '新回答')

    def test_duplicate_same_session_reply_does_not_consume_new_request(self):
        first = self.dispatch('第一个话题')
        result = self.finish(first)
        second = self.dispatch('第二个话题')
        pending = dict(self.host._pending_model_request)
        self.host._on_cloud_reply(result)
        self.assertTrue(self.host.cloud_busy)
        self.assertEqual(self.host._pending_model_request, pending)
        self.finish(second)
        self.assertEqual(self.host.last_user_text, '第二个话题')

    def test_cloud_worker_passes_consent_and_preserves_private_provenance(self):
        self.host.memory.ingest_state({'diary': [{'text': '公园有一座白桥'}]})
        self.host.conversation.authorized = True
        args = self.dispatch('公园的白桥')
        with mock.patch('runtime.conversation_runtime.ask_cloud', return_value={'ok': True, 'text': '已答复'}) as provider:
            self.host._cloud_worker(*args)
        self.assertIs(provider.call_args.kwargs['memory_authorized'], True)
        self.assertTrue(provider.call_args.kwargs['memory_context'])
        result = self.host.cloud_reply.emit.call_args.args[0]
        self.assertIs(result['_memory_context_used'], True)
        self.host.conversation.authorized = False
        self.host._on_cloud_reply(result)
        self.assertIs(self.reply()['private_context'], True)

    def test_authorized_private_history_provenance_survives_empty_fresh_retrieval(self):
        self.host.conversation.authorized = True
        self.host.conversation.context_before_current = lambda: [
            {'role': 'assistant', 'content': '先前获准使用的私人记忆回答'}]
        args = self.dispatch('完全不同的新话题')
        self.assertEqual(args[-1]['memories'], [])
        self.finish(args)
        self.assertTrue(self.reply()['private_context'])

    def test_cloud_without_memory_consent_does_not_attach_prior_retrieval(self):
        self.host.memory.ingest_state({'diary': [{'text': '公园有一座白桥'}]})
        self.host._conversation_local('你还记得公园吗')
        args = self.dispatch('另一个开放话题')
        context = args[-1]
        self.assertIs(context['memory_authorized'], False)
        self.assertEqual(context['memories'], [])
        self.finish(args)
        self.assertFalse(self.reply()['private_context'])

    def test_privacy_reset_purges_all_runtime_context_and_invalidates_reply(self):
        self.host._conversation_local('我昨天去了公园')
        self.host.last_proactive = '私人旧主动消息'
        args = self.dispatch('旧请求')
        self.host.conversation.authorized = True
        serial = self.host.conversation_serial
        self.host._reset_conversation_context()
        self.assertEqual(self.host.dialogue_context.recent_turns(), [])
        for attr in ('last_user_text', 'last_reply_text', 'last_topic', 'last_proactive', '_pending_model_text'):
            self.assertEqual(getattr(self.host, attr), '')
        self.assertIsNone(self.host._pending_model_request)
        self.assertIsNone(self.host._latest_retrieval)
        self.assertFalse(self.host.cloud_busy)
        self.assertGreater(self.host.conversation_serial, serial)
        self.assertEqual(self.host.conversation.resets, 1)
        self.assertFalse(self.host.conversation.authorized)
        self.finish(args, '不允许重新进入上下文')
        self.assertEqual(self.host.conversation.replies, [])
        self.assertEqual(self.host.dialogue_context.recent_turns(), [])

    def test_real_privacy_entrypoints_clear_open_context_and_pending_cloud(self):
        reviews = RetrievalReviewStore(Path(self.temp.name) / 'reviews.json')
        for action in ('_forget_memory', '_clear_memory_engine'):
            with self.subTest(action=action):
                self.host._conversation_local('我昨天去了公园')
                args = self.dispatch('正在等待的旧请求')
                self.host.conversation.authorized = True
                with mock.patch('retrieval_review.RetrievalReviewStore', return_value=reviews):
                    getattr(self.host, action)()
                self.assertEqual(self.host.dialogue_context.recent_turns(), [])
                self.assertEqual(self.host.last_user_text, '')
                self.assertEqual(self.host.last_reply_text, '')
                self.assertEqual(self.host.last_topic, '')
                self.assertFalse(self.host.conversation.authorized)
                self.assertIsNone(self.host._pending_model_request)
                self.finish(args, '遗忘后不应出现的回答')
                self.assertEqual(self.host.conversation.replies, [])

    def test_cloud_failure_clears_only_its_pending_request(self):
        args = self.dispatch('开放话题')
        with mock.patch('runtime.conversation_runtime.ask_cloud', return_value={'ok': False, 'error': '失败'}):
            self.host._cloud_worker(*args)
        self.host._on_cloud_reply(self.host.cloud_reply.emit.call_args.args[0])
        self.assertFalse(self.host.cloud_busy)
        self.assertIsNone(self.host._pending_model_request)
        self.assertEqual(self.host._pending_model_text, '')
        self.assertFalse(hasattr(self.host, 'dialogue_context') and self.host.dialogue_context.recent_turns())


if __name__ == '__main__':
    unittest.main()
