"""统一评分配置测试 — src/scoring_config.py + 接入点回归"""
import tempfile
import unittest
from pathlib import Path

from src import scoring_config as SC
from src.quality import score_quality


class ScoringConfigTest(unittest.TestCase):
    def tearDown(self):
        SC.reload()  # 还原默认配置,避免污染其它测试

    def test_default_weights_sum_to_one(self):
        SC.reload()
        # 传空默认,只取 config 里的权重,验证 scoring.yaml 各组和=1
        w = SC.weights("evidence_quality", {})
        self.assertAlmostEqual(sum(w.values()), 1.0, places=2)
        w2 = SC.weights("reviewer_quality", {})
        self.assertAlmostEqual(sum(w2.values()), 1.0, places=2)
        w3 = SC.weights("recommendation_priority", {})
        self.assertAlmostEqual(sum(w3.values()), 1.0, places=2)

    def test_missing_section_falls_back(self):
        self.assertEqual(SC.get("nope", "k", "DEF"), "DEF")
        self.assertEqual(SC.weights("nope", {"a": 0.5}), {"a": 0.5})

    def test_partial_weights_merge_with_default(self):
        tmp = Path(tempfile.mkdtemp()) / "s.yaml"
        tmp.write_text("evidence_quality:\n  weights:\n    specificity: 0.9\n", encoding="utf-8")
        SC.reload(tmp)
        w = SC.weights("evidence_quality", {"specificity": 0.28, "integrity": 0.16})
        self.assertEqual(w["specificity"], 0.9)   # 覆盖
        self.assertEqual(w["integrity"], 0.16)    # 缺失补默认

    def test_reload_changes_score_quality(self):
        ev = {"claim": "x" * 60, "extracted_snippet": "y" * 60,
              "source_reliability": 0.9, "claim_relevance": 0.9}
        base = score_quality(ev)
        tmp = Path(tempfile.mkdtemp()) / "s.yaml"
        tmp.write_text(
            "evidence_quality:\n  weights:\n    specificity: 0.0\n    integrity: 0.0\n"
            "    relevance: 0.0\n    authority: 0.0\n    freshness: 0.0\n",
            encoding="utf-8")
        SC.reload(tmp)
        self.assertLess(score_quality(ev), base)  # 全 0 权重 → 分更低,证明 config 生效

    def test_reliability_and_ttl_helpers(self):
        SC.reload()  # 默认 scoring.yaml
        # 取 yaml 值而非传入默认 → 证明 config 是单一口径
        self.assertEqual(SC.reliability("official_page", 0.0), 0.85)
        self.assertEqual(SC.reliability("hn_comment", 0.0), 0.65)
        self.assertEqual(SC.reliability("v2ex_reply", 0.0), 0.60)
        self.assertEqual(SC.ttl_days("pricing", 999), 7)
        # 未知 key → 回退调用方默认(适配器原硬编码值),绝不 break
        self.assertEqual(SC.reliability("__nope__", 0.42), 0.42)
        self.assertEqual(SC.ttl_days("__nope__", 33), 33)

    def test_config_override_flows_to_adapter(self):
        """改 config 的 source_reliability → collector 官网证据产出随之变(口径拉通契约)。"""
        from src.collector import OfficialPageAdapter
        tmp = Path(tempfile.mkdtemp()) / "s.yaml"
        tmp.write_text("source_reliability:\n  official_page: 0.11\n", encoding="utf-8")
        SC.reload(tmp)
        html = "<html><body><p>" + "Cursor 代码补全体验非常流畅 0.5s 延迟。" * 4 + "</p></body></html>"
        out = OfficialPageAdapter._extract("Cursor", "https://cursor.com", html)
        self.assertTrue(out, "应至少抽到一条官网证据")
        self.assertTrue(all(e["source_reliability"] == 0.11 for e in out))  # 全部取自 config


if __name__ == "__main__":
    unittest.main()
