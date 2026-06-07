"""意图↔焦点对齐:痛点/流失类问题的焦点要以痛点维度打头,而非功能跑分。

这是"满屏推测"的根因之一——把"用户为什么流失/吐槽"做成了逐功能 0-5 跑分。
"""
import os
import unittest
from unittest import mock

# 强制走启发式(不依赖 LLM key)。注意:仅 pop env 不够 —— 其他测试模块 import src.graph 会
# load_dotenv(.env) 把 ARK_API_KEY 重新灌回 os.environ,导致本测试在全量套件里 propose() 误打真实 LLM
# (非确定性输出 → flaky 失败)。所以调 propose() 的用例额外在 setUp 里把 _llm_available pat 成 False,
# 与 env 状态/测试顺序解耦。
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("ARK_API_KEY", None)

from src import intake


class IntentDetectionTest(unittest.TestCase):
    def test_pain_attribution(self):
        for t in ("用户为什么从 Notion 流向 Asana 吐槽什么",
                  "大家为何弃用 Cursor", "Notion 有哪些槽点和短板"):
            self.assertEqual(intake._detect_intent(t), "pain_attribution", t)

    def test_selection(self):
        self.assertEqual(intake._detect_intent("帮我在这几个里选型"), "selection")

    def test_pricing(self):
        self.assertEqual(intake._detect_intent("对比它们的定价和性价比"), "pricing")

    def test_feature_compare_default(self):
        self.assertEqual(intake._detect_intent("分析 Cursor 和 Windsurf 的代码补全差距"),
                         "feature_compare")


class IntentFocusAlignmentTest(unittest.TestCase):
    def setUp(self):
        # 锁定启发式路径:无论 .env 是否被别的模块重新加载,本类的 propose() 都不打真实 LLM。
        self._p = mock.patch.object(intake, "_llm_available", return_value=False)
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_pain_question_leads_with_pain_focus(self):
        out = intake.propose("用户为什么从 Notion 流向 Asana 或 Linear，吐槽什么")
        self.assertEqual(out.get("analysis_intent"), "pain_attribution")
        # 焦点建议必须是痛点维度,不能是功能跑分维度
        self.assertIn(out.get("focus_suggested"), intake._PAIN_FOCUS)
        # 痛点维度排在候选最前
        self.assertEqual(out.get("focus_candidates")[:3], intake._PAIN_FOCUS)
        # 目的转向流失归因
        self.assertEqual(out.get("purpose_suggested"), "理解用户流失原因与核心痛点")

    def test_feature_question_keeps_feature_focus(self):
        out = intake.propose("分析 Cursor 和 Windsurf 在代码补全体验上的差距")
        self.assertEqual(out.get("analysis_intent"), "feature_compare")
        # 不应被痛点维度污染
        self.assertNotIn(out.get("focus_suggested"), intake._PAIN_FOCUS)


if __name__ == "__main__":
    unittest.main()
