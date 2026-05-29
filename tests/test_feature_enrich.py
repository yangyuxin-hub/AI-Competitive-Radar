"""按 feature 骨架针对性补采:plan 构造 + 合并去重 + spine 复用。"""
import unittest

from src import analyzer, search


META = {"target_product": "Cursor", "competitors": ["Windsurf"], "analysis_focus": ["代码补全体验"]}
EVIDENCE = [
    {"evidence_id": "S0001", "claim_type": "feature_existence", "product": "Cursor",
     "claim": "x", "extracted_snippet": "x", "evidence_confidence": 0.9},
]


class TargetedSearchTest(unittest.TestCase):
    def test_no_key_returns_empty(self):
        orig = search.tavily_available
        search.tavily_available = lambda: False
        try:
            self.assertEqual(search.feature_targeted_evidence("Cursor", ["Tab 补全"], "focus"), [])
        finally:
            search.tavily_available = orig

    def test_builds_two_angles_per_feature(self):
        captured = {}

        def _stub_plan(product, plan, results_per_query=5):
            captured["plan"] = plan
            return [], []

        orig_avail, orig_plan = search.tavily_available, search.search_plan_to_evidence
        search.tavily_available = lambda: True
        search.search_plan_to_evidence = _stub_plan
        try:
            search.feature_targeted_evidence("Cursor", ["Tab 补全", "跨文件"], "代码补全")
        finally:
            search.tavily_available, search.search_plan_to_evidence = orig_avail, orig_plan
        plan = captured["plan"]
        self.assertEqual(len(plan), 4)  # 2 功能 × 2 角度
        cts = {q["claim_type"] for q in plan}
        self.assertEqual(cts, {"performance_quality", "user_pain"})


class EnrichMergeTest(unittest.TestCase):
    def test_merges_and_dedupes(self):
        orig_get, orig_avail, orig_targeted = analyzer.get_llm, search.tavily_available, search.feature_targeted_evidence

        class _SpineLLM:
            def call_json(self, *a, **k):
                return {"features": [{"feature_id": "F001", "name": "Tab 补全"}]}

        analyzer.get_llm = lambda: _SpineLLM()
        search.tavily_available = lambda: True
        # 返回一条新证据 + 一条与已有 id 重复的
        search.feature_targeted_evidence = lambda p, names, focus="": [
            {"evidence_id": "SNEW1", "claim_type": "user_pain", "product": p},
            {"evidence_id": "S0001", "claim_type": "feature_existence", "product": p},  # 重复
        ]
        try:
            merged, spine = analyzer._enrich_evidence_by_features(EVIDENCE, META, "SYS")
        finally:
            analyzer.get_llm, search.tavily_available, search.feature_targeted_evidence = orig_get, orig_avail, orig_targeted

        ids = [e["evidence_id"] for e in merged]
        self.assertIn("SNEW1", ids)
        self.assertEqual(ids.count("S0001"), 1)  # 去重,不重复添加
        self.assertEqual(spine, [{"feature_id": "F001", "name": "Tab 补全"}])

    def test_no_tavily_returns_original(self):
        orig_avail = search.tavily_available
        search.tavily_available = lambda: False
        try:
            merged, spine = analyzer._enrich_evidence_by_features(EVIDENCE, META, "SYS")
        finally:
            search.tavily_available = orig_avail
        self.assertEqual(merged, EVIDENCE)
        self.assertIsNone(spine)


class CoverageGapsTest(unittest.TestCase):
    def test_detects_unknown_cells_and_missing_claim_types(self):
        from src.analyzer import _coverage_gaps
        facts = {"feature_tree": {"features": [
            {"name": "F1", "products": {"Cursor": {"support_status": "supported"},
                                         "Windsurf": {"support_status": "unknown"}}},
        ]}}
        ev = [{"claim_type": "user_pain"}, {"claim_type": "performance_quality"}]  # 缺 pricing/feature_existence
        g = _coverage_gaps(facts, META, ev)
        self.assertIn(("Windsurf", "F1"), g["unknown_cells"])
        self.assertEqual(set(g["missing_claim_types"]), {"pricing", "feature_existence"})

    def test_no_gaps_when_full(self):
        from src.analyzer import _coverage_gaps
        facts = {"feature_tree": {"features": [
            {"name": "F1", "products": {"Cursor": {"support_status": "supported"},
                                         "Windsurf": {"support_status": "supported"}}},
        ]}}
        ev = [{"claim_type": ct} for ct in ("feature_existence", "performance_quality", "pricing", "user_pain")]
        g = _coverage_gaps(facts, META, ev)
        self.assertEqual(g["unknown_cells"], [])
        self.assertEqual(g["missing_claim_types"], [])


if __name__ == "__main__":
    unittest.main()
