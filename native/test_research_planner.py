"""The planner is local, allowlist-only, and only returns an editable proposal."""
import unittest

from evolution_kernel import PolicyGate
from research_planner import ResearchPlanner


class ResearchPlannerTests(unittest.TestCase):
    def setUp(self):
        self.gate = PolicyGate({
            "docs.python.org", "developer.mozilla.org",
            "github.com", "raw.githubusercontent.com",
        })
        self.planner = ResearchPlanner(self.gate)

    def test_python_goal_selects_python_docs_without_network(self):
        plan = self.planner.plan("查清 Python asyncio 的官方说明")
        self.assertTrue(plan["urls"])
        self.assertIn("https://docs.python.org/3/", plan["urls"])
        self.assertTrue(plan["matched"])
        self.assertIn("未提出", plan["note"])

    def test_frontend_goal_selects_mdn(self):
        plan = self.planner.plan("JavaScript DOM 浏览器文档")
        self.assertTrue(plan["urls"])
        self.assertIn("https://developer.mozilla.org/en-US/docs/Web", plan["urls"])
        self.assertTrue(all(url.startswith("https://") for url in plan["urls"]))

    def test_explicit_allowlisted_url_is_kept(self):
        goal = "读 https://docs.python.org/3/library/asyncio.html，总结任务模型"
        plan = self.planner.plan(goal)
        self.assertEqual(plan["urls"][0], "https://docs.python.org/3/library/asyncio.html")
        self.assertIn("目标中的公开 URL", [row["title"] for row in plan["sources"]])

    def test_disallowed_or_unknown_goal_returns_no_candidate(self):
        planner = ResearchPlanner(PolicyGate({"docs.python.org"}))
        plan = planner.plan("浏览器 CSS 布局")
        self.assertEqual(plan["urls"], [])
        self.assertFalse(plan["matched"])
        self.assertIn("手动填写", plan["note"])

    def test_non_https_and_foreign_hosts_are_never_planned(self):
        plan = self.planner.plan("看 http://example.com/a 和 https://evil.example/b")
        self.assertEqual(plan["urls"], [])

    def test_plan_limit_is_bounded(self):
        plan = self.planner.plan("Python JavaScript 文档 和 MDN", max_sources=1)
        self.assertEqual(len(plan["urls"]), 1)


if __name__ == "__main__":
    unittest.main()
