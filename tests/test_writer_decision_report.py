import unittest

from src.writer import (
    _render_business_model,
    _render_decision_summary,
    _render_feature_coverage,
    _render_pricing,
    _render_tech_capability,
)


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


class TechAndBizTest(unittest.TestCase):
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

    def test_business_model_carries_archetype(self):
        pm = {"products": [{"name": "即梦AI", "pricing_engine": {
            "archetype": "Freemium + Subscription + Credits"}}],
            "pricing_strategy_analysis": {"pricing_model_analysis": {
                "summary": "定价应拆成三层看", "products": []}}}
        out = _render_business_model(pm)
        self.assertIn("商业模式逻辑", out)
        self.assertIn("Freemium + Subscription + Credits", out)


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


if __name__ == "__main__":
    unittest.main()
