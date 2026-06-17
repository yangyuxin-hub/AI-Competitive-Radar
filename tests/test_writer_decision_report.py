import unittest

from src.writer import _render_decision_summary


class DecisionSummaryTest(unittest.TestCase):
    def test_renders_five_questions_with_confidence(self):
        schema = {"decision_summary": {
            "why_success": {"answer": "靠剪映生态导流", "confidence": "medium", "refs": ["S1234567"]},
            "how_monetize": {"answer": "低门槛积分扩规模", "confidence": "high", "refs": ["S7654321"]},
            "moat": {"answer": "生态型候选", "confidence": "medium", "refs": []},
            "what_to_learn": {"answer": "学可灵首尾帧", "confidence": "high", "refs": ["S1111111"]},
            "what_to_avoid": {"answer": "别拼专业运镜", "confidence": "medium", "refs": []},
        }}
        out = _render_decision_summary(schema, {})
        self.assertIn("决策摘要", out)
        for kw in ("为什么成功", "靠什么赚钱", "护城河", "该学什么", "该避开什么"):
            self.assertIn(kw, out)
        self.assertIn("置信度", out)
        self.assertIn("[S1234567]", out)

    def test_missing_summary_degrades_gracefully(self):
        out = _render_decision_summary({}, {})
        self.assertIn("证据不足", out)


if __name__ == "__main__":
    unittest.main()
