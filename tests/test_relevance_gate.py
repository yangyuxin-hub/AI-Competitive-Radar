"""P0 相关性门:检索结果必须真的在讲这个产品,挡同名碰撞/语义漂移。

覆盖真实跑批里暴露的坏 case:产品名是常见词("Linear"→线性回归)时,
搜索引擎返回大量含关键词但与产品无关的页面。
"""
import unittest

from src import search


class RelevanceGateTest(unittest.TestCase):
    def setUp(self):
        # 这些产品在 config/products.yaml 里有别名,直接用真实别名表
        self.linear = search._product_aliases("Linear")
        self.notion = search._product_aliases("Notion")

    # ── 同名碰撞:必须拦掉 ───────────────────────────────────────────
    def test_linear_regression_blocked(self):
        # "Linear" 撞「线性回归」,中性域名 + 无软件锚点 → 离题
        self.assertFalse(
            search._is_on_topic("How To Perform Simple Linear Regression by Hand", self.linear, trusted=False)
        )

    def test_offtopic_no_mention_blocked(self):
        # 沙漠卖水定价:连产品名都没提 → 离题
        self.assertFalse(
            search._is_on_topic("一瓶水卖多少钱 从沙漠卖水看懂创业定价", self.linear, trusted=False)
        )

    def test_academic_pdf_blocked(self):
        self.assertFalse(
            search._is_on_topic("Technical Program for Wednesday - IFAC Papercept", self.notion, trusted=False)
        )

    # ── 真讲产品:必须保留 ───────────────────────────────────────────
    def test_linear_app_pricing_kept(self):
        # 提到 Linear + 软件锚点(pricing/app)→ 命中
        self.assertTrue(
            search._is_on_topic("Linear app pricing plans for teams", self.linear, trusted=False)
        )

    def test_trusted_source_lenient(self):
        # 可信源(reddit 等):提到产品名即可,无需软件锚点
        self.assertTrue(
            search._is_on_topic("Linear is so slow lately", self.linear, trusted=True)
        )

    def test_notion_review_kept(self):
        self.assertTrue(
            search._is_on_topic("I Analysed 366+ Notion Complaints from users", self.notion, trusted=False)
        )

    # ── _result_to_evidence 端到端:离题结果被丢弃 ────────────────────
    def test_result_to_evidence_drops_offtopic(self):
        q = {"claim_type": "performance_quality", "bias": "third_party"}
        off = {"title": "Simple Linear Regression by Hand", "url": "https://youtube.com/x", "content": "math tutorial"}
        self.assertIsNone(search._result_to_evidence("Linear", off, q))

    def test_result_to_evidence_keeps_ontopic(self):
        q = {"claim_type": "pricing", "bias": "vendor_claim"}
        on = {"title": "Linear pricing", "url": "https://linear.app/pricing",
              "content": "Linear pricing plans for teams: Free, Standard, Plus"}
        ev = search._result_to_evidence("Linear", on, q)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["product"], "Linear")
        # 官网域名 → 权威源,无 score 时 relevance 兜底为 0.9
        self.assertEqual(ev["claim_relevance"], 0.9)


class FarmFilterTest(unittest.TestCase):
    """P2 农场黑名单:SEO 聚合/导航站即使在讲产品也丢弃。"""

    def test_farm_domains_detected(self):
        for u in ("https://toolradar.com/linear", "https://www.utilio.io/x",
                  "https://aitoolbriefing.com/notion", "https://blog.csdn.net/x"):
            self.assertTrue(search._is_farm_domain(u), f"{u} 应判为农场")

    def test_real_domain_not_farm(self):
        for u in ("https://reddit.com/r/Notion", "https://linear.app/pricing"):
            self.assertFalse(search._is_farm_domain(u))

    def test_farm_dropped_even_if_on_topic(self):
        # 农场页确实在讲 Linear + 有锚点,但仍因黑名单被丢
        q = {"claim_type": "feature_existence", "bias": "third_party"}
        farm = {"title": "Linear app review 2026", "url": "https://toolradar.com/linear",
                "content": "Linear is a project management tool with great features and pricing"}
        self.assertIsNone(search._result_to_evidence("Linear", farm, q))


if __name__ == "__main__":
    unittest.main()
