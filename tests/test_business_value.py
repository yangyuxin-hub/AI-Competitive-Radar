"""业务价值 vs 人工 指标测试:系统侧实测、需复核占比代理、提速倍数。"""
import unittest

from src.business_value import _review_needed_ratio, business_value_metrics

META = {"target_product": "Cursor", "competitors": ["Windsurf"]}


def _schema():
    return {
        "feature_tree": {"features": [
            {"gap": {"winner": "Cursor", "confidence": 0.7, "gap_type": "performance"}},
            {"gap": {"winner": "unknown", "gap_type": "insufficient_evidence"}},  # 需复核
        ]},
        "recommendations": [
            {"evidence_ids": ["S1"]},
            {"evidence_ids": []},  # 无证据 → 需复核
        ],
    }


class BusinessValueTest(unittest.TestCase):
    def test_review_needed_ratio(self):
        flagged, units = _review_needed_ratio(_schema())
        self.assertEqual((flagged, units), (2, 4))  # 1 低置信gap + 1 无证据rec / 共4单元

    def test_metrics_rows_and_speedup(self):
        ev = [{"source_type": "web_search"}, {"source_type": "hacker_news"}, {"source_type": "web_search"}]
        st = [{"duration_sec": 40}, {"duration_sec": 90}, {"duration_sec": 2}]  # 132s
        bv = business_value_metrics(_schema(), META, st, ev)
        metrics = {r["metric"]: r for r in bv["rows"]}
        self.assertEqual(metrics["信息源类型数"]["system"], "2")  # 去重后 2 类
        self.assertEqual(metrics["采集证据条数"]["system"], "3")
        self.assertIn("符合 Schema", metrics["输出结构一致性"]["system"])
        self.assertIn("50%", metrics["需人工复核占比"]["system"])  # 2/4
        self.assertIn("快", metrics["分析耗时"]["delta"])  # 8h vs 132s → 提速
        self.assertTrue(bv["assumptions"])

    def test_zero_elapsed_no_crash(self):
        bv = business_value_metrics(_schema(), META, [], [])
        self.assertEqual(bv["rows"][0]["delta"], "—")  # 无耗时 → 不算倍数


if __name__ == "__main__":
    unittest.main()
