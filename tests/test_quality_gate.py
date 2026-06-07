"""采集质量门测试 — src/quality.py（见 docs/reviewer-reject-design.md §3.6）"""
import unittest

from src.quality import (
    score_quality, has_real_price,
    audit_pricing_substance, audit_bias_balance, annotate_and_audit,
)


class ScoreQualityTest(unittest.TestCase):
    def test_concrete_beats_fluff(self):
        good = {"claim": "Cursor 大代码库补全延迟约 1.5 秒，相比 Copilot 慢，实测 50万行项目卡顿",
                "claim_relevance": 0.9, "source_reliability": 0.85,
                "source_freshness": "current", "source_bias": "user_generated"}
        fluff = {"claim": "革命性的无缝体验，极致流畅，领先行业的强大能力",
                 "extracted_snippet": "革命性无缝极致领先", "source_bias": "vendor_claim"}
        self.assertGreater(score_quality(good), 0.6)
        self.assertLess(score_quality(fluff), 0.3)
        self.assertGreater(score_quality(good), score_quality(fluff))

    def test_empty_snippet_low(self):
        self.assertLess(score_quality({"claim": "好"}), 0.5)


class PriceDetectTest(unittest.TestCase):
    def test_real_price(self):
        self.assertTrue(has_real_price({"claim": "Pro plan $20/month"}))
        self.assertTrue(has_real_price({"claim": "Premium 10.99 美元/seat"}))

    def test_free_tier_counts(self):
        self.assertTrue(has_real_price({"claim": "有免费 Free 档"}))

    def test_contact_sales_not_substance(self):
        self.assertFalse(has_real_price({"claim": "企业版请联系销售获取定制报价"}))


class HardGateTest(unittest.TestCase):
    def test_pricing_no_number_fires(self):
        ev = [{"product": "Asana", "claim_type": "pricing", "claim": "企业版联系销售", "source_bias": "vendor_claim"},
              {"product": "Asana", "claim_type": "pricing", "claim": "Premium 定制报价", "source_bias": "vendor_claim"}]
        gaps = audit_pricing_substance(ev, ["Asana"])
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["gap_type"], "pricing_no_number")

    def test_pricing_with_number_passes(self):
        ev = [{"product": "Asana", "claim_type": "pricing", "claim": "Premium $10.99/user/month", "source_bias": "vendor_claim"}]
        self.assertEqual(audit_pricing_substance(ev, ["Asana"]), [])

    def test_bias_all_vendor_fires(self):
        ev = [{"product": "Kling", "claim_type": "user_pain", "claim": "官方宣称无任何问题", "source_bias": "vendor_claim"}]
        gaps = audit_bias_balance(ev, ["Kling"])
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["gap_type"], "bias_all_vendor")

    def test_bias_with_ugc_passes(self):
        ev = [{"product": "Kling", "claim_type": "user_pain", "claim": "reddit 用户吐槽", "source_bias": "user_generated"}]
        self.assertEqual(audit_bias_balance(ev, ["Kling"]), [])

    def test_no_pricing_evidence_no_gap(self):
        # 0 条定价 = 数量门的事，质量门不报
        ev = [{"product": "X", "claim_type": "feature_existence", "claim": "支持补全", "source_bias": "vendor_claim"}]
        self.assertEqual(audit_pricing_substance(ev, ["X"]), [])


class AnnotateAuditTest(unittest.TestCase):
    def test_annotates_quality_score(self):
        ev = [{"product": "X", "claim_type": "pricing", "claim": "X Pro $9/mo", "source_bias": "vendor_claim"}]
        rep = annotate_and_audit(ev, {"target_product": "X", "competitors": []})
        self.assertIn("quality_score", ev[0])
        # 单条证据:质量门(定价含金量)过(有 $9/mo),但数量门必挂(覆盖/总量不足)
        self.assertEqual(rep["quality_gaps"], [])
        self.assertTrue(len(rep["quantity_gaps"]) > 0)
        self.assertFalse(rep["passed"])
        self.assertEqual(rep["gaps"], rep["quantity_gaps"] + rep["quality_gaps"])


if __name__ == "__main__":
    unittest.main()
