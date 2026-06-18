import unittest

from src.evidence_plan import (
    build_evidence_plan,
    evidence_planner_node,
    required_claim_types_for_meta,
)
from src import source_planner as sp


class EvidencePlanTest(unittest.TestCase):
    def _meta(self, intent="feature_compare", focus=None):
        return {
            "target_product": "Suno",
            "competitors": ["Udio", "StableAudio"],
            "analysis_focus": focus or ["AI 音乐生成质量"],
            "analysis_intent": intent,
        }

    def test_feature_compare_defaults_to_four_core_claims(self):
        plan = build_evidence_plan(self._meta())
        self.assertEqual(
            plan["required_claim_types"],
            ["feature_existence", "performance_quality", "pricing", "user_pain"],
        )
        self.assertIn("market_signal", plan["optional_claim_types"])

    def test_pain_intent_still_requires_pricing_for_product_analysis(self):
        plan = build_evidence_plan(self._meta(intent="pain_attribution"))
        self.assertEqual(plan["required_claim_types"], ["user_pain", "performance_quality", "pricing"])

    def test_market_keywords_promote_market_signal_to_required(self):
        plan = build_evidence_plan(
            self._meta(),
            user_input="分析 Suno 和 Udio 的用户量、融资、收入和公司状况",
        )
        self.assertIn("market_signal", plan["required_claim_types"])
        market_tasks = [t for t in plan["evidence_tasks"] if t["claim_type"] == "market_signal"]
        self.assertTrue(market_tasks)

    def test_planner_node_attaches_plan_to_state_and_meta(self):
        state = {
            "user_input": "分析 Suno 和 Udio 的用户量和融资",
            "analysis_meta": self._meta(),
        }
        out = evidence_planner_node(state)
        self.assertIn("evidence_plan", out)
        self.assertIn("market_signal", out["analysis_meta"]["evidence_plan"]["required_claim_types"])
        self.assertEqual(
            required_claim_types_for_meta(out["analysis_meta"]),
            out["evidence_plan"]["required_claim_types"],
        )

    def test_source_planner_uses_evidence_plan_claims(self):
        plan = build_evidence_plan(
            self._meta(),
            user_input="分析 Suno 和 Udio 的用户量、融资、估值",
        )
        queries = sp.plan_sources(
            product="Suno",
            competitors=["Udio"],
            analysis_focus=["AI 音乐生成质量"],
            evidence_plan=plan,
        )
        self.assertIn("market_signal", {q["claim_type"] for q in queries})
        self.assertTrue(any("funding" in q["query"] or "融资" in q["query"] for q in queries))


if __name__ == "__main__":
    unittest.main()
