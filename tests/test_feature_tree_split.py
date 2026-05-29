"""Tier2: feature_tree 2 段式拆分测试。

用 stub LLM 模拟「骨架 + 按产品填充」两段输出,验证组装结果:
- 覆盖 target + 每个 competitor(过 quick_validate 覆盖检查)
- 只引用存在的 evidence_id、claim_type 对齐
- gap.winner 由 quality_score 确定性算出
"""
import unittest

from src import analyzer
from src.analyzer import _compute_gap, _feature_tree_call, quick_validate_facts


META = {
    "target_product": "Cursor",
    "competitors": ["Windsurf"],
    "analysis_focus": ["代码补全体验"],
}

EVIDENCE = [
    {"evidence_id": "SFEAT001", "claim_type": "feature_existence", "product": "Cursor",
     "claim": "Cursor Tab 补全", "extracted_snippet": "Tab", "evidence_confidence": 0.9},
    {"evidence_id": "SFEAT002", "claim_type": "feature_existence", "product": "Windsurf",
     "claim": "Windsurf Tab 补全", "extracted_snippet": "Tab", "evidence_confidence": 0.8},
    {"evidence_id": "SPERF001", "claim_type": "performance_quality", "product": "Cursor",
     "claim": "Cursor 补全快", "extracted_snippet": "快", "evidence_confidence": 0.7},
    {"evidence_id": "SPAIN001", "claim_type": "user_pain", "product": "Windsurf",
     "claim": "Windsurf 偶尔卡", "extracted_snippet": "卡", "evidence_confidence": 0.6},
]


class _StubLLM:
    """按 label 返回 spine / fill 响应。"""

    def call_json(self, system, payload, label="call", **kw):
        if label == "facts:feature_spine":
            return {"features": [{"feature_id": "F001", "name": "Tab 补全"}]}
        if label.startswith("facts:feature_fill:"):
            product = label.split(":")[-1]
            if product == "Cursor":
                return {"products": {"F001": {
                    "support_status": "supported",
                    "support_evidence_ids": ["SFEAT001"],
                    "quality_score": {"score": 4, "scale": 5, "basis": "快",
                                      "evidence_ids": ["SPERF001"]},
                }}}
            if product == "Windsurf":
                return {"products": {"F001": {
                    "support_status": "supported",
                    "support_evidence_ids": ["SFEAT002"],
                    "quality_score": {"score": 3, "scale": 5, "basis": "偶尔卡",
                                      "evidence_ids": ["SPAIN001"]},
                }}}
        return {}


class FeatureTreeSplitTest(unittest.TestCase):
    def setUp(self):
        self._orig = analyzer.get_llm
        analyzer.get_llm = lambda: _StubLLM()

    def tearDown(self):
        analyzer.get_llm = self._orig

    def test_assembled_tree_passes_quick_validate(self):
        ft = _feature_tree_call("SYS", EVIDENCE, META)
        facts = {"feature_tree": ft, "pricing_model": {"products": []},
                 "user_persona": {"user_segments": [], "pain_points": []}}
        issues = quick_validate_facts(facts, EVIDENCE, META)
        self.assertEqual(issues, [], f"组装后的 feature_tree 不应有校验问题: {issues}")

    def test_gap_winner_is_higher_score(self):
        ft = _feature_tree_call("SYS", EVIDENCE, META)
        gap = ft["features"][0]["gap"]
        self.assertEqual(gap["winner"], "Cursor")  # score 4 > 3
        self.assertTrue(gap["evidence_ids"])

    def test_missing_product_filled_unknown(self):
        # 只有 Cursor 返回,Windsurf 缺 → 补 unknown,仍覆盖 competitor
        class _PartialLLM(_StubLLM):
            def call_json(self, system, payload, label="call", **kw):
                if label == "facts:feature_fill:Windsurf":
                    return {}
                return super().call_json(system, payload, label=label, **kw)

        analyzer.get_llm = lambda: _PartialLLM()
        ft = _feature_tree_call("SYS", EVIDENCE, META)
        block = ft["features"][0]["products"]
        self.assertIn("Windsurf", block)
        self.assertEqual(block["Windsurf"]["support_status"], "unknown")


class ComputeGapTest(unittest.TestCase):
    def test_tie_prefers_target(self):
        block = {
            "Cursor": {"support_status": "supported", "support_evidence_ids": ["SFEAT001"],
                       "quality_score": {"score": 3, "evidence_ids": ["SPERF001"]}},
            "Windsurf": {"support_status": "supported", "support_evidence_ids": [],
                         "quality_score": {"score": 3, "evidence_ids": ["SPAIN001"]}},
        }
        gap = _compute_gap("Tab 补全", block, META)
        self.assertEqual(gap["winner"], "Cursor")

    def test_all_unscored_is_unknown_not_tie(self):
        # 全是证据不足(unknown/0 无证据)→ winner unknown,不能宣称"打平"
        block = {
            "Cursor": {"support_status": "unknown",
                       "quality_score": {"score": 0, "evidence_ids": []}},
            "Windsurf": {"support_status": "unknown",
                         "quality_score": {"score": 0, "evidence_ids": []}},
        }
        gap = _compute_gap("跨文件补全", block, META)
        self.assertEqual(gap["winner"], "unknown")
        self.assertEqual(gap["confidence"], 0.0)

    def test_single_rated_no_false_lead(self):
        # 只有 Cursor 有真实证据,对手是 0/unknown → 不宣称"4 vs 0 领先"
        block = {
            "Cursor": {"support_status": "supported", "support_evidence_ids": ["SFEAT001"],
                       "quality_score": {"score": 4, "evidence_ids": ["SPERF001"]}},
            "Windsurf": {"support_status": "unknown",
                         "quality_score": {"score": 0, "evidence_ids": []}},
        }
        gap = _compute_gap("补全响应速度", block, META)
        self.assertEqual(gap["winner"], "Cursor")
        self.assertEqual(gap["gap_type"], "insufficient_evidence")
        self.assertLessEqual(gap["confidence"], 0.3)
        self.assertNotIn("vs 次优", gap["reason"])


if __name__ == "__main__":
    unittest.main()
