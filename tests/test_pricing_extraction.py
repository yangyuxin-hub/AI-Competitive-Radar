"""定价页提取:真实档位价藏在短 <span>$10</span> 或内嵌 JSON,旧版 40 字符门槛会漏。
验证 _price_snippets 两条路径(可见 DOM 卡片 + 内嵌 JSON)都能把价捞回。"""
import unittest

from bs4 import BeautifulSoup

from src.collector import OfficialPageAdapter


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

    def test_extract_pricing_url_prepends_prices(self):
        # 定价 URL → 价格片段应置顶进 pricing 证据
        html = '<main><div><h3>Pro</h3><span>$16</span><p>per user/month</p></div></main>'
        evs = OfficialPageAdapter._extract("X", "https://x.com/pricing", html)
        pricing = [e for e in evs if e["claim_type"] == "pricing"]
        self.assertTrue(pricing)
        self.assertTrue(any("$16" in e["extracted_snippet"] for e in pricing))


if __name__ == "__main__":
    unittest.main()
