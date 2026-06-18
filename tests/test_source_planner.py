"""source_planner 启发式计划:每个 claim_type 锁定对应权威源 + 全网兜底。

回归点:旧实现对所有 claim_type 共用 recs[:N](前几条恰是 feature 的无 site
官网页)→ 所有查询 site 都空 → 全网裸搜捞回学术站/同名页(Linear→线性回归)。
"""
import unittest

from src import source_planner as sp


class HeuristicPlanTest(unittest.TestCase):
    def _plan(self):
        return sp.plan_sources(
            product="Linear",
            competitors=["Notion", "Asana"],
            analysis_focus=["加载速度与性能"],
            domain="pm",
        )

    def test_each_claim_type_covered(self):
        plan = self._plan()
        cts = {q["claim_type"] for q in plan}
        self.assertEqual(cts, set(sp.REQUIRED_CLAIM_TYPES))

    def test_not_all_sites_empty(self):
        # 核心回归:绝不能再出现"所有查询 site 都空"的裸搜
        plan = self._plan()
        pinned = [q for q in plan if q.get("site")]
        self.assertTrue(pinned, "至少应有权威源定向查询,不能全是全网裸搜")

    def test_feature_pricing_pinned_to_official(self):
        plan = self._plan()
        for ct in ("feature_existence", "pricing"):
            sites = {q["site"] for q in plan if q["claim_type"] == ct}
            self.assertIn("linear.app", sites, f"{ct} 应锁定官网 linear.app")

    def test_pain_perf_pinned_to_feedback_sources(self):
        plan = self._plan()
        perf_sites = {q["site"] for q in plan if q["claim_type"] == "performance_quality"}
        pain_sites = {q["site"] for q in plan if q["claim_type"] == "user_pain"}
        self.assertIn("reddit.com", perf_sites)
        self.assertIn("reddit.com", pain_sites)

    def test_each_claim_has_fullweb_fallback(self):
        # 每个 claim_type 都保留一条全网兜底(由相关性门过滤)
        plan = self._plan()
        for ct in sp.REQUIRED_CLAIM_TYPES:
            empty = [q for q in plan if q["claim_type"] == ct and not q["site"]]
            self.assertTrue(empty, f"{ct} 应有一条全网兜底查询")


class QueryDisambiguationTest(unittest.TestCase):
    """检索词消歧:产品名拼品类锚定 + 杜绝中英混搭。"""

    def test_english_product_drops_chinese_focus(self):
        # 英文产品 + 中文焦点 → query 全英文,丢中文焦点(避免 "Linear 加载速度" 混搭)
        q = sp._build_query("Linear", "加载速度与性能", "performance_quality",
                            "project management", "项目协作工具")
        self.assertNotIn("加载", q)
        self.assertIn("project management", q)  # 品类锚定,挡线性回归
        self.assertTrue(all(ord(c) < 128 for c in q), f"英文产品 query 不应含中文: {q}")

    def test_chinese_product_keeps_chinese(self):
        # 中文产品 + 中文焦点 → 全中文 coherent
        q = sp._build_query("通义灵码", "代码补全体验", "user_pain",
                            "AI coding assistant", "AI 编程工具")
        self.assertIn("代码补全体验", q)
        self.assertIn("AI 编程工具", q)
        self.assertNotIn("complaints", q)  # 不混入英文关键词

    def test_category_anchors_product(self):
        # 品类词必须出现,这是消歧的关键
        q = sp._build_query("Linear", "", "feature_existence", "project management", "项目协作工具")
        self.assertIn("project management", q)


class ChineseCommunityPlanTest(unittest.TestCase):
    def _plan(self):
        return sp.plan_sources(
            product="飞书项目",
            competitors=["Notion", "Linear", "Todoist"],
            analysis_focus=["任务全生命周期流转能力"],
            missing_claim_types=["performance_quality", "user_pain"],
            domain="pm",
        )

    def test_chinese_pm_product_prefers_chinese_feedback_sources(self):
        plan = self._plan()
        perf_sites = [q["site"] for q in plan if q["claim_type"] == "performance_quality" and q["site"]]
        pain_sites = [q["site"] for q in plan if q["claim_type"] == "user_pain" and q["site"]]

        for sites in (perf_sites, pain_sites):
            self.assertIn("zhihu.com", sites)
            self.assertIn("sspai.com", sites)
            self.assertIn("v2ex.com", sites)
            self.assertIn("juejin.cn", sites)
            self.assertIn("community.feishu.cn", sites)

    def test_chinese_pm_queries_expand_product_aliases(self):
        plan = self._plan()
        pinned = [q for q in plan if q["claim_type"] == "performance_quality" and q["site"] == "zhihu.com"]
        self.assertTrue(pinned)
        query = pinned[0]["query"]

        self.assertIn("飞书项目", query)
        self.assertIn("Meego", query)
        self.assertIn("Lark Project", query)
        self.assertIn("任务全生命周期流转能力", query)
        self.assertIn("评测 体验", query)

    def test_chinese_product_uses_chinese_sources_without_domain(self):
        plan = sp.plan_sources(
            product="飞书项目",
            competitors=["Notion", "Linear", "Todoist"],
            analysis_focus=["任务全生命周期流转能力"],
            missing_claim_types=["performance_quality"],
            domain=None,
        )
        sites = [q["site"] for q in plan if q["site"]]

        self.assertIn("community.feishu.cn", sites)
        self.assertIn("zhihu.com", sites)
        self.assertIn("sspai.com", sites)
        self.assertNotIn("reddit.com", sites[:3])

    def test_english_feedback_site_uses_english_alias_query(self):
        q = sp._build_query_for_site(
            "飞书项目",
            "任务全生命周期流转能力",
            "user_pain",
            "project management",
            "项目协作工具",
            "g2.com",
        )

        self.assertIn("Lark Project", q)
        self.assertIn("Meego", q)
        self.assertIn("project management", q)
        self.assertIn("complaints problems", q)
        self.assertNotIn("任务全生命周期", q)


if __name__ == "__main__":
    unittest.main()
