"""交付清单测试 — src/checklist.py（design-v3 M1 决策视角）"""
import unittest

from src.checklist import build_checklist


def _hq_community(product, ct):
    """高质量社区证据（满足 coverage + community + bias 三门）。"""
    return {
        "product": product, "claim_type": ct,
        "claim": "用户实测 500k 行项目补全延迟约 1.5 秒,相比竞品慢",
        "extracted_snippet": "Reddit 用户实测 500k 行项目补全延迟约 1.5 秒,相比竞品慢,并提到大文件切换时偶发卡顿。",
        "source_type": "reddit", "source_bias": "user_generated",
        "source_url": "https://www.reddit.com/r/test/comments/1",
        "claim_relevance": 0.9, "source_reliability": 0.8,
    }


def _report():
    return {
        "report_id": "CR-TEST-1",
        "status": "degraded",
        "meta": {
            "target_product": "Solid", "competitors": ["Ghost"],
            "analysis_intent": "pain_attribution",  # required = user_pain + performance_quality
        },
        "raw_evidence": [
            _hq_community("Solid", "user_pain"),
            _hq_community("Solid", "user_pain"),
            _hq_community("Solid", "performance_quality"),
            _hq_community("Solid", "performance_quality"),
            # Ghost: 完全没有证据 → 两类都打回
        ],
        "schema_draft": {
            "feature_tree": {"features": [{"feature_id": "F1"}]},
            "pricing_model": {"products": [{"name": "Solid", "tiers": [{}]}]},
            "user_persona": {"pain_points": [{"pain_id": "P1"}]},
            "swot": {},  # 缺 → 打回 analyzer
            "recommendations": [{"rec_id": "R1"}, {"rec_id": "R2"}],
        },
        "quality_report": {
            "passed_rules": ["R1", "R9"],
            "failed_rules": ["R4"],
            "warning_rules": ["R7"],
            "errors": [{"rule": "R4", "reject_target": "analyzer", "issue_type": "broken_reasoning_chain"}],
        },
    }


class ChecklistShapeTest(unittest.TestCase):
    def setUp(self):
        self.cl = build_checklist(_report())

    def test_three_layers(self):
        self.assertEqual([g["layer"] for g in self.cl["groups"]], ["采集", "分析", "质检"])

    def test_collection_grid_size(self):
        collect = self.cl["groups"][0]
        # 2 产品 × 2 必需类型(pain_attribution) = 4 项
        self.assertEqual(collect["total"], 4)
        self.assertEqual(collect["done"], 2)  # Solid 两类达标

    def test_solid_covered_ghost_missing_with_owner(self):
        collect = self.cl["groups"][0]
        by_label = {it["label"]: it for it in collect["items"]}
        self.assertEqual(by_label["Solid · 用户痛点证据"]["status"], "ok")
        self.assertEqual(by_label["Ghost · 用户痛点证据"]["status"], "missing")
        self.assertEqual(by_label["Ghost · 用户痛点证据"]["owner"], "fetch_community")
        self.assertEqual(by_label["Ghost · 体验/性能证据"]["owner"], "fetch_community")

    def test_analysis_swot_missing_routes_analyzer(self):
        analysis = self.cl["groups"][1]
        by_label = {it["label"]: it for it in analysis["items"]}
        self.assertEqual(by_label["SWOT 推导"]["status"], "missing")
        self.assertEqual(by_label["SWOT 推导"]["owner"], "analyzer")
        self.assertEqual(by_label["功能对比矩阵"]["status"], "ok")
        self.assertEqual(analysis["done"], 4)

    def test_qc_layer_maps_rules(self):
        qc = self.cl["groups"][2]
        by_label = {it["label"].split()[0]: it for it in qc["items"]}
        self.assertEqual(by_label["R1"]["status"], "ok")
        self.assertEqual(by_label["R4"]["status"], "missing")
        self.assertEqual(by_label["R4"]["owner"], "analyzer")
        self.assertEqual(by_label["R7"]["status"], "warn")

    def test_summary_math_and_repairs_by_owner(self):
        s = self.cl["summary"]
        self.assertEqual(s["total"], 13)   # 采集4 + 分析5 + 质检4(R1/R9/R4/R7)
        self.assertEqual(s["done"], 8)     # 采集2 + 分析4 + 质检2(R1/R9)
        self.assertEqual(s["open_repairs"], 4)  # Ghost×2 + SWOT + R4
        self.assertEqual(s["repairs_by_owner"], {"fetch_community": 2, "analyzer": 2})

    def test_target_and_competitors_passthrough(self):
        self.assertEqual(self.cl["target"], "Solid")
        self.assertEqual(self.cl["competitors"], ["Ghost"])


if __name__ == "__main__":
    unittest.main()
