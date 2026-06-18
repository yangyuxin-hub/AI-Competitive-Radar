"""Tier2: feature_tree 2 段式拆分测试。

用 stub LLM 模拟「骨架 + 按产品填充」两段输出,验证组装结果:
- 覆盖 target + 每个 competitor(过 quick_validate 覆盖检查)
- 只引用存在的 evidence_id、claim_type 对齐
- gap.winner 由 quality_score 确定性算出
"""
import unittest

from src import analyzer
from src.analyzer import _apply_feature_engine, _compute_gap, _feature_tree_call, quick_validate_facts


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


class _NoSpineLLM(_StubLLM):
    """Skill 命中时不应再调用 facts:feature_spine。"""

    def call_json(self, system, payload, label="call", **kw):
        if label == "facts:feature_spine":
            raise AssertionError("AI coding skill should provide the feature spine")
        return super().call_json(system, payload, label=label, **kw)


class _NoSkillLLM:
    """无行业 skill 时模拟 LLM 生成骨架 + 按产品填充。"""

    def __init__(self):
        self.labels = []

    def call_json(self, system, payload, label="call", **kw):
        self.labels.append(label)
        if label == "facts:feature_spine":
            return {"features": [
                {"feature_id": "F001", "name": "知识组织"},
                {"feature_id": "F002", "name": "协作权限"},
            ]}
        if label == "facts:feature_fill:Notion":
            return {"products": {
                "F001": {"support_status": "supported", "support_evidence_ids": ["SNOTE01"],
                         "quality_score": {"score": 4, "scale": 5, "basis": "知识组织能力较强",
                                           "evidence_ids": ["SPERF11"]}},
                "F002": {"support_status": "supported", "support_evidence_ids": ["SNOTE02"],
                         "quality_score": {"score": 3, "scale": 5, "basis": "权限能力可用",
                                           "evidence_ids": ["SPERF12"]}},
            }}
        if label == "facts:feature_fill:Coda":
            return {"products": {
                "F001": {"support_status": "supported", "support_evidence_ids": ["SCODA01"],
                         "quality_score": {"score": 3, "scale": 5, "basis": "知识组织能力可用",
                                           "evidence_ids": ["SPERF21"]}},
                "F002": {"support_status": "partial", "support_evidence_ids": ["SCODA02"],
                         "quality_score": {"score": 2, "scale": 5, "basis": "权限能力较弱",
                                           "evidence_ids": ["SPERF22"]}},
            }}
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

    def test_ai_coding_generation_uses_skill_spine_not_llm_spine(self):
        analyzer.get_llm = lambda: _NoSpineLLM()
        ft = _feature_tree_call("SYS", EVIDENCE, META)
        self.assertEqual(ft["source_skill"], "ai_coding")
        self.assertEqual(ft["generation_mode"], "skill_llm_hybrid_ready")
        self.assertEqual(ft["features"][0]["name"], "代码理解")
        self.assertEqual(len(ft["features"]), 10)

    def test_no_skill_generation_uses_llm_spine_then_dynamic_domains(self):
        meta = {"target_product": "Notion", "competitors": ["Coda"],
                "analysis_focus": ["知识库协作效率"], "category": "协作文档"}
        evidence = [
            {"evidence_id": "SNOTE01", "claim_type": "feature_existence", "product": "Notion",
             "claim": "Notion knowledge organization", "extracted_snippet": "knowledge"},
            {"evidence_id": "SNOTE02", "claim_type": "feature_existence", "product": "Notion",
             "claim": "Notion permissions", "extracted_snippet": "permissions"},
            {"evidence_id": "SCODA01", "claim_type": "feature_existence", "product": "Coda",
             "claim": "Coda docs organization", "extracted_snippet": "docs"},
            {"evidence_id": "SCODA02", "claim_type": "feature_existence", "product": "Coda",
             "claim": "Coda permissions", "extracted_snippet": "permissions"},
            {"evidence_id": "SPERF11", "claim_type": "performance_quality", "product": "Notion",
             "claim": "Notion organization works well", "extracted_snippet": "well"},
            {"evidence_id": "SPERF12", "claim_type": "performance_quality", "product": "Notion",
             "claim": "Notion permissions are usable", "extracted_snippet": "usable"},
            {"evidence_id": "SPERF21", "claim_type": "performance_quality", "product": "Coda",
             "claim": "Coda organization works", "extracted_snippet": "works"},
            {"evidence_id": "SPERF22", "claim_type": "performance_quality", "product": "Coda",
             "claim": "Coda permissions are limited", "extracted_snippet": "limited"},
        ]
        stub = _NoSkillLLM()
        analyzer.get_llm = lambda: stub
        ft = _feature_tree_call("SYS", evidence, meta)
        self.assertIn("facts:feature_spine", stub.labels)
        self.assertIsNone(ft.get("source_skill"))
        self.assertEqual([f["name"] for f in ft["features"]], ["知识组织", "协作权限"])

        facts = {"feature_tree": ft}
        changed = _apply_feature_engine(facts, meta)
        self.assertEqual(changed, 2)
        tree = facts["feature_tree"]["tree"]
        self.assertEqual(tree["feature_weight_version"], "llm_dynamic@unversioned")
        self.assertEqual([d["name"] for d in tree["domains"]], ["知识组织", "协作权限"])


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
