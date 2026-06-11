"""claim_type 出处锚定:定价页(pricing_pages)来源的证据 claim_type 锚死 pricing。

档位权益描述满是 collaborate/integrate/support 这类功能词,关键词打分层(infer_claim_type)
和收尾 LLM 精分类层都会把它改判成 feature_existence——定价证据被改没了,Analyzer 端
触发「pricing 假缺口」→ 整轮无效补采(2026-06-11 run 实测:规则层 29% 误标是缺口假信号源)。
出处规则 > 文本规则:analyzer prompt 自己规定「档位/配额/权益属于定价范畴」,采集端同口径。
"""
import unittest
from unittest.mock import patch

from src import collector_common
from src.collector import OfficialPageAdapter


class PricingPageProvenanceTest(unittest.TestCase):
    def test_pricing_url_chunks_pinned_to_pricing(self):
        # 档位权益段:全是功能词、无价格 token → 旧逻辑被 infer_claim_type 改判 feature_existence
        tier_benefits = (
            "Collaborate with your whole team, integrate all your favorite tools, "
            "automate workflows and create unlimited projects with priority support included"
        )
        html = f"<main><p>{tier_benefits}</p></main>"
        evs = OfficialPageAdapter._extract("X", "https://x.com/pricing", html)
        self.assertTrue(evs)
        for e in evs:
            self.assertEqual(e["claim_type"], "pricing",
                             f"定价页证据被改判: {e['claim_type']}: {e['extracted_snippet'][:60]}")

    def test_non_pricing_url_still_inferred(self):
        # 非定价页不受影响:功能页照常走关键词推断
        text = ("Collaborate with your whole team, integrate all your favorite tools, "
                "automate workflows and create unlimited projects together easily")
        html = f"<main><p>{text}</p></main>"
        evs = OfficialPageAdapter._extract("X", "https://x.com/features", html)
        self.assertTrue(evs)
        self.assertEqual(evs[0]["claim_type"], "feature_existence")

    def test_reclassify_skips_pricing_page_evidence(self):
        evidence = [
            {"claim_type": "pricing", "source_type": "official_page",
             "source_url": "https://x.com/pricing",
             "extracted_snippet": "Pro plan: unlimited projects, priority support, SSO"},
            {"claim_type": "feature_existence", "source_type": "official_page",
             "source_url": "https://x.com/features",
             "extracted_snippet": "blazing fast and reliable code completion"},
        ]
        with patch.object(collector_common, "_claim_llm_enabled", return_value=True), \
             patch.object(collector_common, "classify_claim_types_llm",
                          side_effect=lambda snips: ["performance_quality"] * len(snips)):
            changed = collector_common._reclassify_official_claim_types(evidence)
        # 定价页出处锚定:LLM 想改也不送去分类
        self.assertEqual(evidence[0]["claim_type"], "pricing")
        # 非定价页照常被 LLM 修正
        self.assertEqual(evidence[1]["claim_type"], "performance_quality")
        self.assertEqual(changed, 1)


if __name__ == "__main__":
    unittest.main()
