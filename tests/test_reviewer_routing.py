"""Reviewer 打回路由测试(minimal 模式)。

保护点:打回/降级的状态机是性能重构最易回归的地方。
- 无 error → status=passed,reject_target=None
- 有 hard_gate error 且未超配额 → status=running,reject_target 命中,retry_count 自增
- 配额用尽 → status=degraded,不再打回
"""
import unittest

from src.reviewer import make_reviewer_node
from src.state import build_initial_state


def _state_with(schema: dict, evidence: list[dict], retry_count=None):
    st = build_initial_state(
        user_input="x",
        target_product="Cursor",
        competitors=["Windsurf"],
        analysis_focus=["代码补全体验"],
    )
    st["schema_draft"] = schema
    st["raw_evidence"] = evidence
    if retry_count is not None:
        st["retry_count"] = retry_count
    return st


# 引用了不存在 evidence_id 的 feature gap → R1(evidence_id_not_found → collector)
_BAD_SCHEMA = {
    "feature_tree": {
        "features": [
            {
                "feature_id": "F001",
                "name": "Tab 补全",
                "products": {},
                "gap": {"winner": "Cursor", "evidence_ids": ["SNOTEXIST"], "confidence": 0.5},
            }
        ]
    }
}


class ReviewerRoutingTest(unittest.TestCase):
    def setUp(self):
        self.review = make_reviewer_node(mode="minimal")

    def test_passed_when_no_errors(self):
        out = self.review(_state_with({}, []))
        self.assertEqual(out["status"], "passed")
        self.assertIsNone(out["reject_target"])

    def test_running_and_increments_retry(self):
        out = self.review(_state_with(_BAD_SCHEMA, []))
        self.assertEqual(out["status"], "running")
        self.assertEqual(out["reject_target"], "collector")
        self.assertEqual(out["retry_count"]["collector"], 1)
        self.assertIn("R1", out["quality_report"]["failed_rules"])

    def test_degraded_when_quota_exhausted(self):
        # collector 配额默认 1,先把 retry_count 顶到上限
        out = self.review(
            _state_with(_BAD_SCHEMA, [], retry_count={"collector": 1, "analyzer": 0, "writer": 0})
        )
        self.assertEqual(out["status"], "degraded")
        self.assertIsNone(out["reject_target"])


if __name__ == "__main__":
    unittest.main()
