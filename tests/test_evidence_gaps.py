"""M1 统一缺口口径对账测试(design-v3-draft §六 M1 验收):

1. find_gaps = 两套旧口径(quality.audit_coverage 系 + analyzer_augment._coverage_gaps)的并集
2. Gap 归一化:owner_node/task_key 确定性派生,按 (task_key, gap_type) 去重
3. 池内回捞:命中标记 _recalled、只回捞一次(循环收敛)、整类缺失不回捞
4. _compact_evidence 对 _recalled 行:顶进 top-K + 放宽截断
"""
import unittest

from src.analyzer_augment import _coverage_gaps
from src.analyzer_common import _compact_evidence
from src.evidence_gaps import find_gaps, recall_from_pool
from src.quality import annotate_and_audit


META = {
    "target_product": "Alpha",
    "competitors": ["Beta"],
    "analysis_focus": ["代码补全体验"],
}


def _ev(product, ct, snippet, *, eid=None, source_type="web_search", quality=0.8):
    return {
        "evidence_id": eid or f"S{abs(hash((product, ct, snippet))) % 10**7:07d}",
        "product": product,
        "claim_type": ct,
        "source_type": source_type,
        "source_bias": "third_party",
        "source_freshness": "current",
        "claim": snippet[:40],
        "extracted_snippet": snippet,
        "quality_score": quality,
    }


def _sparse_evidence():
    """Alpha 只有 1 条 feature 证据 → audit_coverage 必报多类 coverage_short。"""
    return [_ev("Alpha", "feature_existence", "Alpha supports tab completion")]


def _facts_with_gaps():
    """unknown 格子 + Beta 定价缺席(Alpha 有正价) → _coverage_gaps 报 facts 级缺口。"""
    return {
        "feature_tree": {"features": [{
            "name": "Tab 补全", "name_en": "tab completion",
            "products": {"Alpha": {"support_status": "supported"},
                         "Beta": {"support_status": "unknown"}},
        }]},
        "pricing_model": {"products": [
            {"name": "Alpha", "tiers": [{"price": {"normalized_usd_month": 10}}]},
        ]},
        "user_persona": {},
    }


class FindGapsUnionTest(unittest.TestCase):
    def test_superset_of_collector_audit(self):
        ev = _sparse_evidence()
        audit = annotate_and_audit(ev, META)
        unified = find_gaps(ev, META, audit=audit)
        uni_keys = {(g["product"], g["claim_type"], g["gap_type"]) for g in unified}
        for old in audit["gaps"]:
            self.assertIn((old.get("product"), old.get("claim_type"), old["gap_type"]),
                          uni_keys, f"旧 collector 口径 gap 丢失: {old}")

    def test_superset_of_analyzer_coverage_gaps(self):
        # 需要一条 pricing 证据让 "pricing" 不进 missing_ct,才能触发 per-product 定价缺口分支
        ev = _sparse_evidence() + [_ev("Alpha", "pricing", "Alpha Pro is $10 per month")]
        facts = _facts_with_gaps()
        legacy = _coverage_gaps(facts, META, ev)
        unified = find_gaps(ev, META, facts=facts)
        uni = {(g["product"], g["claim_type"], g["gap_type"]) for g in unified}
        # unknown 格子(Beta×Tab补全) → unknown_cells gap
        self.assertIn(("Beta", "feature_existence", "unknown_cells"), uni)
        # Beta 定价缺席而 Alpha 有价 → pricing_extraction_missing
        self.assertIn("Beta", legacy["pricing_gap_products"])
        self.assertIn(("Beta", "pricing", "pricing_extraction_missing"), uni)
        # 整类缺失逐一映射
        for ct in legacy["missing_claim_types"]:
            self.assertTrue(
                any(g["claim_type"] == ct and g["gap_type"] in
                    ("missing_claim_type", "stale_claim_type") for g in unified),
                f"旧 analyzer 口径 missing_ct 丢失: {ct}")

    def test_gap_shape_and_dedupe(self):
        ev = _sparse_evidence()
        unified = find_gaps(ev, META, facts=_facts_with_gaps())
        keys = [(g["task_key"], g["gap_type"]) for g in unified]
        self.assertEqual(len(keys), len(set(keys)), "(task_key, gap_type) 必须去重")
        for g in unified:
            self.assertTrue(g["owner_node"], f"owner_node 必须派生: {g}")
            self.assertEqual(g["task_key"].count(":"), 2, f"task_key 形如 owner:product:ct: {g}")
            self.assertIn("fixable", g)


class RecallFromPoolTest(unittest.TestCase):
    def test_pricing_gap_recalls_pool_pricing(self):
        pool = [
            _ev("Beta", "pricing", "Beta Pro plan costs $12 per month billed annually"),
            _ev("Beta", "user_pain", "Beta is slow sometimes"),
        ]
        gaps = {"unknown_cells": [], "missing_claim_types": [],
                "stale_gap_claim_types": [], "pricing_gap_products": ["Beta"], "dim_en": {}}
        recalled = recall_from_pool(pool, gaps)
        self.assertEqual(len(recalled), 1)
        self.assertEqual(recalled[0]["claim_type"], "pricing")
        self.assertTrue(recalled[0].get("_recalled"))
        # 第二次调用不再返回(每行只回捞一次,保证 refill 循环收敛)
        self.assertEqual(recall_from_pool(pool, gaps), [])

    def test_unknown_cell_recalls_dim_matched_rows_only(self):
        pool = [
            _ev("Beta", "feature_existence", "Beta tab completion works across files"),
            _ev("Beta", "feature_existence", "Beta has a dark theme"),
        ]
        gaps = {"unknown_cells": [("Beta", "Tab 补全")], "missing_claim_types": [],
                "stale_gap_claim_types": [], "pricing_gap_products": [],
                "dim_en": {"Tab 补全": "tab completion"}}
        recalled = recall_from_pool(pool, gaps)
        self.assertEqual(len(recalled), 1)
        self.assertIn("tab completion", recalled[0]["extracted_snippet"])

    def test_missing_claim_type_not_recalled(self):
        """整类缺失=池里本来没有 → 不回捞(只能外搜);不误捞其他类型。"""
        pool = [_ev("Alpha", "feature_existence", "Alpha supports tab completion")]
        gaps = {"unknown_cells": [], "missing_claim_types": ["user_pain"],
                "stale_gap_claim_types": [], "pricing_gap_products": [], "dim_en": {}}
        self.assertEqual(recall_from_pool(pool, gaps), [])


class CompactRecallVisibilityTest(unittest.TestCase):
    def test_recalled_row_enters_topk_with_relaxed_truncation(self):
        long_text = "Beta pricing detail " * 30  # ~600 字符,默认 180 截断必丢尾部
        pool = [_ev("Beta", "pricing", f"high quality filler row number {i} with text", quality=0.9)
                for i in range(10)]
        recalled_row = _ev("Beta", "pricing", long_text, quality=0.1)  # 低分,默认必被 top-8 挤掉
        recalled_row["_recalled"] = True
        pool.append(recalled_row)
        out = _compact_evidence(pool, per_type=8, snip=180)
        hit = [o for o in out if o["evidence_id"] == recalled_row["evidence_id"]]
        self.assertEqual(len(hit), 1, "_recalled 行必须进 top-K 视野")
        self.assertGreater(len(hit[0]["extracted_snippet"]), 200, "回捞行截断必须放宽(>180)")


if __name__ == "__main__":
    unittest.main()
