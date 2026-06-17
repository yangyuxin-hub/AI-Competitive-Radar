"""定价页提取:真实档位价藏在短 <span>$10</span> 或内嵌 JSON,旧版 40 字符门槛会漏。
验证 _price_snippets 两条路径(可见 DOM 卡片 + 内嵌 JSON)都能把价捞回。"""
import unittest

from bs4 import BeautifulSoup

from src.collector import OfficialPageAdapter
from src.collector_common import is_pricing_url


class PriceSnippetTest(unittest.TestCase):
    def _snips(self, html):
        return OfficialPageAdapter._price_snippets(BeautifulSoup(html, "html.parser"), html)

    def test_short_price_span_captured(self):
        # 旧逻辑:<span>$10</span> 仅 3 字符,被 40 字符门槛滤掉
        html = """
        <div class="card"><h3>Plus</h3><span>$10</span><p>per user / month</p></div>
        <div class="card"><h3>Free</h3><span>$0</span></div>
        """
        snips = self._snips(html)
        joined = " ".join(snips)
        self.assertIn("$10", joined)
        self.assertIn("$0", joined)
        # 卡片级上溯应带上 plan 名
        self.assertTrue(any("Plus" in s for s in snips))

    def test_embedded_json_price_captured(self):
        # Notion/Asana 把价放 __NEXT_DATA__ JSON 里,DOM 无可见价
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {"plans":[{"name":"Business","price":"18","priceCurrency":"USD"}]}
        </script>
        """
        snips = self._snips(html)
        self.assertTrue(any("Business" in s and "18" in s for s in snips),
                        f"未从内嵌 JSON 提到 Business/$18: {snips}")

    def test_embedded_price_array_captured(self):
        # Kling 等开发者定价页把价格放在 prices/points/specs 并行数组里
        html = """
        <script>
        {"points":["0.6","0.8"],"prices":["$0.084","$0.112"],
         "specs":["std x 1s","pro x 1s"]}
        </script>
        """
        snips = self._snips(html)
        joined = " ".join(snips)
        self.assertIn("$0.084", joined)
        self.assertIn("std x 1s", joined)

    def test_membership_url_is_pricing_url(self):
        self.assertTrue(is_pricing_url("https://app.klingai.com/global/membership/spirit-unit"))

    def test_extract_pricing_url_prepends_prices(self):
        # 定价 URL → 价格片段应置顶进 pricing 证据
        html = '<main><div><h3>Pro</h3><span>$16</span><p>per user/month</p></div></main>'
        evs = OfficialPageAdapter._extract("X", "https://x.com/pricing", html)
        pricing = [e for e in evs if e["claim_type"] == "pricing"]
        self.assertTrue(pricing)
        self.assertTrue(any("$16" in e["extracted_snippet"] for e in pricing))


class CompactEvidenceFairnessTest(unittest.TestCase):
    def test_low_confidence_product_not_starved(self):
        """多产品分析:低可信度产品(官网定价)不应被全局 top-K 挤光。"""
        from src.analyzer import _compact_evidence
        ev = []
        # 高可信度聚合站:Notion/Asana 各 10 条 pricing(挤占全局名额)
        for prod in ("Notion", "Asana"):
            for i in range(10):
                ev.append({"evidence_id": f"S{prod[:1]}{i:06d}", "product": prod,
                           "claim_type": "pricing", "evidence_confidence": 0.9,
                           "extracted_snippet": f"{prod} aggregator ${i}"})
        # 低可信度官网:Linear 3 条(0.65),旧逻辑会被全局 top-8 全挤掉
        for i, price in enumerate(("$0", "$10", "$16")):
            ev.append({"evidence_id": f"SL{i:06d}", "product": "Linear",
                       "claim_type": "pricing", "evidence_confidence": 0.65,
                       "extracted_snippet": f"Linear {price} per user/month"})
        out = _compact_evidence(ev)
        linear = [e for e in out if e["product"] == "Linear"]
        self.assertEqual(len(linear), 3, f"Linear 定价被压缩饿死了: {out}")


class NormalizePricingTiersTest(unittest.TestCase):
    def test_dedupe_same_price_and_sort(self):
        from src.analyzer import _normalize_pricing_tiers
        facts = {"pricing_model": {"products": [{"name": "Notion", "tiers": [
            {"tier_name": "Business", "price": {"normalized_usd_month": 18}, "evidence_ids": ["S1"]},
            {"tier_name": "Free", "price": {"normalized_usd_month": 0}, "evidence_ids": ["S2"]},
            {"tier_name": "Plus", "price": {"normalized_usd_month": 12}, "evidence_ids": ["S3"]},
            {"tier_name": "Free trial", "price": {"normalized_usd_month": 0}, "evidence_ids": []},  # 重复免费档
        ]}]}}
        removed = _normalize_pricing_tiers(facts)
        tiers = facts["pricing_model"]["products"][0]["tiers"]
        prices = [(t.get("price") or {}).get("normalized_usd_month") for t in tiers]
        self.assertEqual(removed, 1)
        self.assertEqual(prices, [0, 12, 18])  # 去重 + 升序
        # 去重保留 evidence 更全的免费档
        self.assertEqual(tiers[0]["evidence_ids"], ["S2"])


if __name__ == "__main__":
    unittest.main()
