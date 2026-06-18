import unittest

from src.analyzer import _fallback_decision_summary
from src.writer import (
    _render_caliber_lock,
    _render_decision_summary,
    _render_feature_coverage,
    _render_pricing,
    _render_recommendations,
    _render_tech_capability,
    writer_node,
)


class DecisionSummaryTest(unittest.TestCase):
    def test_renders_decision_brief_with_confidence(self):
        schema = {"decision_summary": {
            "why_success": {"answer": "靠剪映生态导流", "confidence": "medium", "refs": ["S1234567"]},
            "how_monetize": {"answer": "低门槛积分扩规模", "confidence": "high", "refs": ["S7654321"]},
            "moat": {"answer": "生态型候选", "confidence": "medium", "refs": []},
            "what_to_learn": {"answer": "学可灵首尾帧", "confidence": "high", "refs": ["S1111111"]},
            "what_to_avoid": {"answer": "别拼专业运镜", "confidence": "medium", "refs": []},
        }}
        out = _render_decision_summary(schema, {})
        self.assertIn("决策摘要", out)
        for kw in ("关键判断", "定价/变现判断", "可以学习", "需要避开"):
            self.assertIn(kw, out)
        self.assertIn("置信度", out)
        self.assertIn("[S1234567]", out)
        self.assertNotIn("护城河是什么", out)

    def test_missing_summary_degrades_gracefully(self):
        out = _render_decision_summary({}, {})
        self.assertIn("证据不足", out)


class FallbackDecisionSummaryTest(unittest.TestCase):
    def test_derives_from_analysis(self):
        schema = {
            "feature_tree": {"analysis": {
                "archetypes": {"Jimeng": "工具型"},
                "moat_candidates": [
                    {"name": "风格控制", "confidence": "medium", "factors": ["素材生态"]}
                ],
            }},
            "pricing_model": {"products": [{
                "name": "Jimeng",
                "pricing_engine": {"archetype": "Freemium + Subscription + Credits"},
            }]},
            "recommendations": [
                {"action_type": "learn", "action": "学习 Kling 的首尾帧控制",
                 "target_competitor": "Kling", "evidence_refs": ["S1234567"],
                 "priority_score_100": 86},
                {"action_type": "avoid", "action": "避免直接硬拼 Runway 专业运镜",
                 "target_competitor": "Runway", "risk": "证据样本不足"},
            ],
        }
        ds = _fallback_decision_summary(schema, target="Jimeng")
        self.assertIn("moat", ds)
        self.assertIn("how_monetize", ds)
        self.assertIn(ds["moat"]["confidence"], ("high", "medium", "low"))
        self.assertIn("Kling", ds["what_to_learn"]["answer"])
        self.assertIn("Runway", ds["what_to_avoid"]["answer"])


class TechCapabilityTest(unittest.TestCase):
    def test_tech_capability_marks_unknown(self):
        schema = {"tech_capability": {"products": {
            "Jimeng": {"max_resolution": "1080p", "max_duration": None,
                       "gen_speed": "约30s/条", "model_version": None,
                       "source_refs": ["S1234567"]}}}}
        out = _render_tech_capability(schema, ["Jimeng"], {})
        self.assertIn("技术能力", out)
        self.assertIn("1080p", out)
        self.assertIn("unknown", out.lower())

    def test_tech_indicators_adapt_to_meta(self):
        schema = {"tech_capability": {"products": {
            "Cursor": {"completion_latency": "80ms", "context_window": None}}}}
        meta = {"tech_indicators": [{"key": "completion_latency", "label": "补全延迟"},
                                    {"key": "context_window", "label": "上下文窗口"}]}
        out = _render_tech_capability(schema, ["Cursor"], meta)
        self.assertIn("补全延迟", out)
        self.assertIn("80ms", out)
        self.assertIn("上下文窗口", out)

class PricingTopTableTest(unittest.TestCase):
    def test_new_schema_tier_shows_regular_monthly_not_dash(self):
        pm = {"products": [{"name": "即梦AI", "tiers": [
            {"tier_name": "标准会员", "billing_options": [
                {"cycle": "monthly", "is_promo": False,
                 "price": {"amount": 199, "currency": "CNY"}}]}]}]}
        out = _render_pricing(pm, {}, ["即梦AI"])
        self.assertIn("199", out)
        self.assertNotIn("archetype", out.lower())


class FeatureCoverageTest(unittest.TestCase):
    def test_renders_two_coverage_numbers_and_winner(self):
        ft = {"analysis": {
            "coverage": {"Jimeng": {"coverage_known_only": 0.62,
                                    "evidence_coverage_rate": 0.76,
                                    "by_domain": []}},
            "winners": [{"feature_id": "F1", "name": "运镜控制", "winner": "Runway",
                         "reason": "领先", "confidence": "high"},
                        {"feature_id": "F2", "name": "文生视频", "winner": "tie",
                         "reason": "无深度", "confidence": "low"}],
            "differentiation_matrix": [{"feature_id": "F3", "name": "多镜头",
                                        "product": "Runway",
                                        "note": "样本内 3 个产品中,仅 Runway 做到位"}]}}
        out = _render_feature_coverage(ft, ["Jimeng"])
        self.assertIn("功能覆盖率", out)
        self.assertIn("62%", out)
        self.assertIn("证据覆盖率", out)
        self.assertIn("76%", out)
        self.assertIn("Runway", out)
        self.assertIn("样本内 3 个产品中", out)
        self.assertNotIn("独占", out)


class LockTest(unittest.TestCase):
    def test_caliber_lock_has_all_fields(self):
        schema = {"feature_tree": {"analysis": {"feature_weight_version": "demo-v1"}}}
        meta = {"analysis_focus": ["视频质量"], "competitors": ["Kling"],
                "target_product": "Jimeng", "generated_at": "2026-06-17T00:00:00Z"}
        out = _render_caliber_lock(schema, meta)
        for kw in ("口径锁定", "demo-v1", "unknown", "元/积分", "Kling"):
            self.assertIn(kw, out)


class FourLayerOrderTest(unittest.TestCase):
    def test_section_order_matches_decision_layers(self):
        state = {
            "schema_draft": {
                "decision_summary": {},
                "competitor_landscape": {},
                "positioning_map": {},
                "feature_tree": {"features": [{
                    "feature_id": "F1",
                    "name": "文生视频",
                    "products": {
                        "Jimeng": {"support_status": "supported",
                                   "quality_score": {"score": 3, "scale": 5, "evidence_ids": []}},
                        "Kling": {"support_status": "supported",
                                  "quality_score": {"score": 3, "scale": 5, "evidence_ids": []}},
                    },
                }], "analysis": {
                    "coverage": {},
                    "winners": [],
                    "differentiation_matrix": [],
                    "archetypes": {"Jimeng": "工具型", "Kling": "专精型"},
                    "moat_candidates": [],
                    "whitespace": [],
                    "feature_weight_version": "demo-v1",
                }},
                "pricing_model": {"products": []},
                "user_persona": {},
                "tech_capability": {"products": {}},
                "swot": {},
                "recommendations": [
                    {"action_type": "learn", "action": "优先补齐首尾帧控制", "target_competitor": "Kling"}
                ],
            },
            "analysis_meta": {"target_product": "Jimeng", "competitors": ["Kling"],
                              "analysis_focus": ["视频质量"], "generated_at": "2026-06-17"},
            "raw_evidence": [],
            "collection_meta": {},
        }
        out = writer_node(state)["report_draft"]
        i_summary = out.find("决策摘要")
        i_recs = out.find("优先级建议")
        i_facts = out.find("多维度评分总览")
        i_coverage = out.find("功能覆盖与差距")
        i_lock = out.find("口径锁定表")
        self.assertTrue(0 <= i_summary < i_facts < i_coverage < i_recs < i_lock)
        self.assertNotIn("功能定位 · 产品形态", out)
        self.assertNotIn("商业模式逻辑", out)


class RecommendationsTriBlockTest(unittest.TestCase):
    def test_groups_into_learn_avoid_attack(self):
        recs = [
            {"action_type": "learn", "action": "学首尾帧", "target_competitor": "Kling",
             "evidence_refs": ["S1234567"], "priority_score": 80, "rationale": "高权重", "risk": "投入大"},
            {"action_type": "attack", "action": "做多镜头小白化", "target_competitor": "Runway",
             "evidence_refs": [], "priority_score": 65, "rationale": "蓝海", "risk": "教育成本"},
        ]
        out = _render_recommendations(recs, {})
        self.assertIn("Learn", out)
        self.assertIn("Avoid", out)
        self.assertIn("Attack", out)
        self.assertIn("学首尾帧", out)
        self.assertIn("[S1234567]", out)
        self.assertIn("风险", out)


if __name__ == "__main__":
    unittest.main()
