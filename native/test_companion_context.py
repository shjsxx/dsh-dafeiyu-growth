"""RAM-only context rules tested without UI, user files, providers or training."""
import json
import unittest
from unittest import mock

from companion_context import CompanionContext, MAX_TOPICS, MAX_EPISODES, personality_policy


class CompanionContextTests(unittest.TestCase):
    def exchange(self, context, text, **kwargs):
        plan = context.plan(text, **kwargs)
        context.record(text, plan)
        return plan

    def test_park_wind_and_pronoun_follow_actual_topic_despite_random_mood(self):
        context = CompanionContext()
        first = self.exchange(context, '我昨天去了公园', mood='sad')
        self.assertIn('公园那边怎么样', first['text'])
        second = self.exchange(context, '风很舒服', reply={'text': '你是不是很难过？'}, mood='anger')
        self.assertIn('公园的风', second['text'])
        self.assertNotIn('难过', second['text'])
        third = context.plan('那里怎么样？')
        self.assertIn('公园', third['text'])
        self.assertIn('你刚才说', third['text'])
        self.assertIn('还不知道', third['text'])
        fourth = context.plan('这件事让我想了很久')
        self.assertIn('公园', fourth['text'])
        self.assertEqual(context.episodes()[-1]['details'], ['wind_comfortable', 'pleasant'])

    def test_plan_is_read_only_record_does_not_mine_assistant_claims(self):
        context = CompanionContext()
        for _ in range(3):
            context.plan('我昨天去了公园')
        self.assertEqual(context.recent_turns(), [])
        self.assertEqual(context.topics(), [])
        context.record('你好', '我知道你昨天在海边')
        self.assertEqual(context.topics(), [])
        self.assertIn('哪个地方', context.plan('那里怎么样')['text'])

    def test_correction_replaces_referent_and_repair_never_recursively_quotes(self):
        context = CompanionContext()
        self.exchange(context, '我昨天去了公园')
        correction = self.exchange(context, '不是公园，是海边', mood='joy')
        self.assertEqual(correction['intent'], 'repair')
        self.assertIn('抱歉', correction['text'])
        self.assertNotIn('公园', correction['text'])
        self.assertEqual([row['topic'] for row in context.topics()], ['海边'])
        self.assertEqual([row['topic'] for row in context.episodes()], ['海边'])
        self.assertNotIn('说什么', context.plan('你在说什么')['text'])
        for _ in range(5):
            repair = self.exchange(context, '你答非所问，别敷衍我', reply='开心起来吧', mood='joy')
            self.assertEqual(repair['intent'], 'repair')
            self.assertNotIn('「', repair['text'])
            self.assertNotIn('开心', repair['text'])
            self.assertLess(len(repair['text']), 90)
        self.assertEqual(context.plan('风很舒服')['topic'], '海边')

    def test_substantive_non_place_topic_and_negated_visit_are_not_invented(self):
        context = CompanionContext()
        self.exchange(context, '我今天考试失败了')
        self.assertEqual(context.topics()[-1]['topic'], '考试')
        followup = context.plan('这件事让我很难过')
        self.assertEqual(followup['intent'], 'support')
        self.assertIn('考试', followup['text'])
        negated = context.plan('我没去公园')
        self.assertNotIn('那边怎么样', negated['text'])
        planned = context.plan('我想去公园')
        self.assertNotIn('那边怎么样', planned['text'])

    def test_topic_stack_and_unknown_references(self):
        context = CompanionContext()
        self.assertIn('不想猜', context.plan('这件事后来呢？')['text'])
        for text in ('我昨天去了公园', '我今天去了图书馆', '公园那里呢？'):
            self.exchange(context, text)
        self.assertEqual(context.topics()[-1]['topic'], '公园')
        self.assertEqual(context.plan('那里怎么样')['topic'], '公园')
        for index in range(20):
            self.exchange(context, f'我去了地点{index}')
        self.assertLessEqual(len(context.topics()), MAX_TOPICS)
        self.assertLessEqual(len(context.episodes()), MAX_EPISODES)
        self.assertTrue(all(len(row['topic']) <= 32 for row in context.topics()))

    def test_rows_and_character_budget_are_hard_bounds(self):
        context = CompanionContext(max_turns=100, max_chars=100000)
        for index in range(20):
            context.record('user' + str(index) + '字' * 5000, '回复' * 4000)
            rows = context.recent_turns()
            self.assertLessEqual(len(rows), 6)
            self.assertLessEqual(sum(len(row['content']) for row in rows), 4000)
            self.assertTrue(all(row['role'] in ('user', 'assistant') for row in rows))
        small = CompanionContext(max_turns=3, max_chars=30)
        small.record('甲' * 25, '乙' * 25)
        self.assertEqual(sum(len(row['content']) for row in small.recent_turns()), 30)
        self.assertEqual(small.recent_turns()[-1]['content'], '乙' * 25)

    def test_ephemeral_isolation_copies_and_clear(self):
        with mock.patch('builtins.open', side_effect=AssertionError('unexpected IO')):
            context = CompanionContext()
            self.exchange(context, '我去了公园')
            self.exchange(context, '风很舒服')
            rows = context.recent_turns()
            rows[0]['content'] = 'corruption'
            topics = context.topics()
            topics[0]['details'].clear()
            episodes = context.episodes()
            episodes[0]['details'].clear()
            self.assertNotEqual(context.recent_turns()[0]['content'], 'corruption')
            self.assertTrue(context.topics()[0]['details'])
            self.assertTrue(context.episodes()[0]['details'])
            self.assertEqual(CompanionContext().recent_turns(), [])
            self.assertEqual(CompanionContext().topics(), [])
            context.clear()
            self.assertEqual(context.recent_turns(), [])
            self.assertEqual(context.topics(), [])
            self.assertEqual(context.episodes(), [])

    def test_policy_contains_only_fixed_enums_no_history_hits_mood_or_secrets(self):
        context = CompanionContext()
        secret = 'token=NEVER_SEND_ME'
        self.exchange(context, '我去了公园 ' + secret)
        self.assertEqual(context.topics(), [])
        plan = context.plan('你答非所问 ' + secret,
                            hits=[{'text': 'PRIVATE DURABLE MEMORY'}], mood=secret)
        self.assertNotIn(secret, json.dumps(plan['personality_policy']))
        self.assertNotIn('PRIVATE', json.dumps(plan))
        self.assertNotIn(secret, plan['text'])
        context.record('你答非所问 ' + secret, plan)
        policy = context.personality_policy()
        self.assertEqual(policy['initiative'], 'paused')
        self.assertNotIn('公园', json.dumps(policy, ensure_ascii=False))
        self.assertEqual(policy['values'], ['respect_autonomy', 'honesty', 'low_interrupt'])
        self.assertEqual(personality_policy(secret)['intent'], 'idle_share')

    def test_intents_and_explicit_local_reply_are_preserved(self):
        context = CompanionContext()
        for text, intent in (('我很难过', 'support'), ('这是什么意思', 'clarification'),
                             ('我没听懂你刚才说的', 'clarification'), ('别跑了', 'refusal'),
                             ('别主动打扰我', 'refusal'), ('我要休息', 'rest'),
                             ('你好', 'idle_share')):
            self.assertEqual(context.plan(text)['intent'], intent)
        reply = {'text': '好，我跑一圈给你看。', 'action': 'run', 'name': ''}
        result = context.plan('跑一圈', reply=reply)
        self.assertEqual(result['text'], reply['text'])
        self.assertEqual(result['action'], 'run')
        self.assertFalse(result['handled'])
        self.assertIn('还没完全理解', context.plan('量子纠缠是什么')['text'])


if __name__ == '__main__':
    unittest.main()
