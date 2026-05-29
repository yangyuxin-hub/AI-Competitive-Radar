"""Tier3: 确定性完成度指标测试。"""
import unittest

from src.judge import completeness_metrics


META = {"target_product": "Cursor", "competitors": ["Windsurf"], "analysis_purpose": "default"}


def _schema(decided=True):
    return {
        "feature_tree": {"features": [
            {
                "name": "Tab 补全",
                "products": {
                    "Cursor": {"support_status": "supported",
                               "quality_score": {"score": 4, "evidence_ids": ["SX"]}},
                    "Windsurf": {"support_status": "supported",
                                 "quality_score": {"score": 3, "evidence_ids": ["SY"]}},
                },
                "gap": {"winner": "Cursor" if decided else "unknown"},
            }
        ]},
        "pricing_model": {"products": [{"name": "Cursor", "tiers": [{"evidence_ids": ["SP"]}]}]},
        "user_persona": {"pain_points": [{"pain_id": "P001"}]},
        "recommendations": [{"rec_id": "R001", "priority_score": {"final_score": 3},
                             "evidence_ids": ["SX"], "expected_impact": "x", "success_metric": "y"}],
        "swot": {"strengths": [{"point": "a"}], "weaknesses": [], "opportunities": [], "threats": []},
    }


class CompletenessTest(unittest.TestCase):
    def test_overall_in_range_and_aspects(self):
        m = completeness_metrics(_schema(), META)
        self.assertTrue(0 <= m["overall"] <= 100)
        keys = {a["key"] for a in m["aspects"]}
        self.assertEqual(keys, {"feature_coverage", "evidence", "gap_decisiveness",
                                "rec_priority", "rec_actionable"})
        self.assertEqual(m["counts"]["products"], 2)

    def test_decisiveness_drops_when_winner_unknown(self):
        decided = completeness_metrics(_schema(decided=True), META)["overall"]
        undecided = completeness_metrics(_schema(decided=False), META)["overall"]
        self.assertGreater(decided, undecided)


if __name__ == "__main__":
    unittest.main()
