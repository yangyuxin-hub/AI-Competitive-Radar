"""analysis_intent 透传:intake → analysis_meta → analyzer 取向。

痛点类问题让 analyzer 弱化 0-5 跑分、前端不做「推测」兜底,关掉残余推测。
"""
import unittest

from src.state import build_initial_state
from src.graph import _resolve_run_args


class IntentPropagationTest(unittest.TestCase):
    def test_build_initial_state_carries_intent(self):
        st = build_initial_state("x", "Notion", ["Asana"], ["焦点"],
                                 analysis_intent="pain_attribution")
        self.assertEqual(st["analysis_meta"]["analysis_intent"], "pain_attribution")

    def test_default_intent_is_feature_compare(self):
        st = build_initial_state("x", "A", ["B"], ["f"])
        self.assertEqual(st["analysis_meta"]["analysis_intent"], "feature_compare")

    def test_resolve_args_infers_pain_from_user_input(self):
        *_, intent = _resolve_run_args(
            "Notion", ["Asana"], ["焦点"], "目的",
            "用户为什么从 Notion 流向 Asana 吐槽什么", None)
        self.assertEqual(intent, "pain_attribution")

    def test_resolve_args_infers_feature_compare(self):
        *_, intent = _resolve_run_args(
            "Cursor", ["Windsurf"], ["代码补全"], "目的",
            "对比 Cursor 和 Windsurf 的代码补全差距", None)
        self.assertEqual(intent, "feature_compare")

    def test_explicit_intent_overrides_inference(self):
        # 前端从 intake 带回的 intent 优先,不再二次推断
        *_, intent = _resolve_run_args(
            "Cursor", ["Windsurf"], ["代码补全"], "目的",
            "对比 Cursor 和 Windsurf 的代码补全差距", "selection")
        self.assertEqual(intent, "selection")


if __name__ == "__main__":
    unittest.main()
