"""P0 收尾:合成问卷兜底 + 结论标注 + 质量分按意图调整。

防的是"满屏推测"残余:合成数据污染结论、诚实弃权反被扣分、痛点问题被功能跑分口径误评。
"""
import os
import unittest

from src import analyzer, reviewer, writer


class SurveyFallbackTest(unittest.TestCase):
    def _ugc(self, n, synthetic=False):
        return [{
            "evidence_id": f"S{i:07d}", "source_bias": "user_generated",
            "source_url": ("synthetic://survey/x" if synthetic else "https://reddit.com/x"),
            "claim_type": "user_pain",
        } for i in range(n)]

    def test_real_ugc_counts_only_real(self):
        ev = self._ugc(5) + self._ugc(10, synthetic=True)
        self.assertEqual(analyzer._real_ugc_count(ev), 5)

    def test_survey_skips_when_real_ugc_plenty(self):
        os.environ["SURVEY_MIN_REAL_UGC"] = "8"
        try:
            # 10 条真实 UGC ≥ 8 → 不跑合成
            self.assertFalse(analyzer._survey_should_run(self._ugc(10), {}))
            # 3 条 < 8 → 合成兜底
            self.assertTrue(analyzer._survey_should_run(self._ugc(3), {}))
        finally:
            os.environ.pop("SURVEY_MIN_REAL_UGC", None)


class TraceabilityTest(unittest.TestCase):
    def test_unknown_cells_not_penalized(self):
        # 一个有结论(带引用)、一个 unknown(无引用)→ unknown 不该算进分母
        schema = {"feature_tree": {"features": [{
            "feature_id": "F001", "name": "x", "gap": {"evidence_ids": ["S1234567"]},
            "products": {
                "A": {"support_status": "supported", "support_evidence_ids": ["S1234567"],
                      "quality_score": {"evidence_ids": ["S1234567"]}},
                "B": {"support_status": "unknown", "support_evidence_ids": [],
                      "quality_score": {"evidence_ids": []}},
            },
        }]}}
        with_ev, total = reviewer._schema_refs_with_coverage(schema)
        # gap(1) + A 的 support/quality(2) = 3 条都带引用;B 的 unknown 两格被跳过
        self.assertEqual((with_ev, total), (3, 3))


class QualityIntentTest(unittest.TestCase):
    def _schema(self):
        return {"feature_tree": {"features": []}, "user_persona": {"pain_points": []},
                "pricing_model": {"products": []}, "swot": {}, "recommendations": []}

    def test_pain_intent_uses_pain_focused_coverage(self):
        ev = [{"product": "Notion", "claim_type": "user_pain"},
              {"product": "Notion", "claim_type": "performance_quality"},
              {"product": "Notion", "claim_type": "pricing"}]
        meta = {"target_product": "Notion", "competitors": [], "analysis_intent": "pain_attribution"}
        dims, _ = reviewer._quality_dimensions(self._schema(), ev, {}, meta, [], [])
        # 痛点分析也内化定价:痛点+体验+定价都覆盖 → 满分
        self.assertEqual(dims["evidence_coverage"]["score"], 100)
        self.assertIn("痛点", dims["evidence_coverage"]["note"])

    def test_feature_intent_still_requires_four_types(self):
        ev = [{"product": "Notion", "claim_type": "user_pain"},
              {"product": "Notion", "claim_type": "performance_quality"}]
        meta = {"target_product": "Notion", "competitors": [], "analysis_intent": "feature_compare"}
        dims, _ = reviewer._quality_dimensions(self._schema(), ev, {}, meta, [], [])
        # 缺 feature_existence / pricing → 覆盖度只 50%
        self.assertEqual(dims["evidence_coverage"]["score"], 50)


class WriterSyntheticLabelTest(unittest.TestCase):
    def test_synthetic_only_pain_marked(self):
        ev = [{"evidence_id": "S1111111", "source_url": "synthetic://survey/1"},
              {"evidence_id": "S2222222", "source_url": "https://reddit.com/x"}]
        persona = {"pain_points": [
            {"pain_id": "P001", "description": "纯合成痛点",
             "frequency": {"level": "high", "evidence_ids": ["S1111111"]}},
            {"pain_id": "P002", "description": "真实痛点",
             "frequency": {"level": "high", "evidence_ids": ["S2222222"]}},
        ]}
        md = writer._render_personas(persona, ev)
        self.assertIn("【模拟】纯合成痛点", md)
        self.assertNotIn("【模拟】真实痛点", md)


if __name__ == "__main__":
    unittest.main()
