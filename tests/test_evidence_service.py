"""M3 EvidenceService 测试(design-v3-draft §六 M3 验收):

1. Analyst 簇(analyzer*.py)零采集依赖——顶层 import 不得出现 search/httpx/collector/survey_skill
2. fill():回捞优先(mode=pool,不追加)→ 外搜兜底 → 补不到 mode=none
3. back-compat:analyzer/_augment 仍可引到迁移后的执行函数(re-export 链)
"""
import ast
import pathlib
import unittest
from unittest import mock

from src import evidence_service

ROOT = pathlib.Path(__file__).resolve().parent.parent

META = {"target_product": "Alpha", "competitors": ["Beta"], "analysis_focus": ["补全"]}


def _ev(eid, product, ct, snippet):
    return {"evidence_id": eid, "product": product, "claim_type": ct,
            "claim": snippet[:30], "extracted_snippet": snippet}


class AnalystImportHygieneTest(unittest.TestCase):
    """v3 §三:Analyst 禁区"不抓数据"。M3 后 analyzer 簇顶层不得 import 采集/网络模块。"""

    BANNED = ("search", "httpx", "collector", "survey_skill", "requests")
    FILES = ("analyzer.py", "analyzer_common.py", "analyzer_augment.py",
             "analyzer_sanitize.py", "analyzer_fallback.py")

    def test_no_collection_imports_in_analyzer_cluster(self):
        for f in self.FILES:
            tree = ast.parse((ROOT / "src" / f).read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                mods = ([a.name for a in n.names] if isinstance(n, ast.Import)
                        else [n.module] if isinstance(n, ast.ImportFrom) and n.module else [])
                for m in mods:
                    self.assertFalse(
                        any(k in m for k in self.BANNED),
                        f"{f}:{n.lineno} 引入采集层模块 {m}(Analyst 禁区,执行逻辑应在 evidence_service)")


class FillTest(unittest.TestCase):
    def test_pool_recall_first(self):
        pool = [_ev("S0000001", "Beta", "pricing", "Beta Pro $12 per month")]
        gaps = {"unknown_cells": [], "missing_claim_types": [],
                "stale_gap_claim_types": [], "pricing_gap_products": ["Beta"], "dim_en": {}}
        rows, mode = evidence_service.fill(pool, META, gaps, "补全")
        self.assertEqual(mode, "pool")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].get("_recalled"))

    def test_search_mode_appends_only_new(self):
        pool = [_ev("S0000001", "Alpha", "feature_existence", "Alpha tab completion exists")]
        gaps = {"unknown_cells": [("Beta", "多文件编辑")], "missing_claim_types": [],
                "stale_gap_claim_types": [], "pricing_gap_products": [], "dim_en": {}}
        fresh = _ev("S0000002", "Beta", "feature_existence", "Beta multi-file editing supported")
        dup = dict(pool[0])  # 与池内同 id → 必须被去重
        with mock.patch.object(evidence_service, "_gap_targeted_recollect",
                               return_value=[fresh, dup]):
            rows, mode = evidence_service.fill(pool, META, gaps, "补全")
        self.assertEqual(mode, "search")
        self.assertEqual([r["evidence_id"] for r in rows], ["S0000002"])

    def test_none_when_nothing_found(self):
        pool = [_ev("S0000001", "Alpha", "feature_existence", "Alpha tab completion exists")]
        gaps = {"unknown_cells": [("Beta", "多文件编辑")], "missing_claim_types": [],
                "stale_gap_claim_types": [], "pricing_gap_products": [], "dim_en": {}}
        with mock.patch.object(evidence_service, "_gap_targeted_recollect", return_value=[]):
            rows, mode = evidence_service.fill(pool, META, gaps, "补全")
        self.assertEqual((rows, mode), ([], "none"))


class BackCompatReexportTest(unittest.TestCase):
    def test_analyzer_chain_still_exposes_moved_names(self):
        from src import analyzer, analyzer_augment
        for name in ("_gap_targeted_recollect", "_recollect_pricing_official",
                     "_run_survey", "_survey_should_run", "_real_ugc_count"):
            self.assertIs(getattr(analyzer_augment, name), getattr(evidence_service, name))
            self.assertIs(getattr(analyzer, name), getattr(evidence_service, name))


if __name__ == "__main__":
    unittest.main()
