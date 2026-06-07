"""Source Ledger 测试 — src/source_ledger.py（见 docs §8.6）"""
import json
import tempfile
import unittest
from pathlib import Path

from src import source_ledger as SL


def _ev(product, source_type, url, q, bias=None):
    e = {"product": product, "source_type": source_type, "source_url": url, "quality_score": q}
    if bias:
        e["source_bias"] = bias
    return e


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "ledger.json"

    def test_records_only_high_quality(self):
        ev = [
            _ev("可灵Kling", "official_page", "https://klingai.com/features", 0.82),
            _ev("可灵Kling", "reddit", "https://reddit.com/r/KlingAI", 0.90, "user_generated"),
            _ev("可灵Kling", "web_search", "https://lowq.example.com/x", 0.40),   # 低质,不记
        ]
        SL.record(ev, category="ai_video", products=["可灵Kling"], path=self.tmp)
        led = json.loads(self.tmp.read_text(encoding="utf-8"))
        cat = led["by_category"]["ai_video"]
        self.assertIn("klingai.com", [x["domain"] for x in cat["official"]])
        self.assertIn("reddit.com", [x["domain"] for x in cat["community"]])
        # 低质 domain 不入台账
        self.assertNotIn("lowq.example.com", [x["domain"] for b in cat.values() for x in b])

    def test_skips_synthetic(self):
        ev = [_ev("X", "simulated_interview", "synthetic://survey/X/Q1", 0.9, "synthetic")]
        SL.record(ev, category="c", products=["X"], path=self.tmp)
        led = json.loads(self.tmp.read_text(encoding="utf-8"))
        self.assertEqual(led["by_category"].get("c", {}), {})

    def test_ewma_and_hits(self):
        SL.record([_ev("X", "official_page", "https://x.com/a", 0.7)], "c", ["X"], path=self.tmp)
        SL.record([_ev("X", "official_page", "https://x.com/b", 0.9)], "c", ["X"], path=self.tmp)
        led = json.loads(self.tmp.read_text(encoding="utf-8"))
        entry = led["by_category"]["c"]["official"][0]
        self.assertEqual(entry["domain"], "x.com")
        self.assertEqual(entry["hits"], 2)
        self.assertTrue(0.7 < entry["q"] < 0.9)  # EWMA 介于两者

    def test_query_reuse(self):
        SL.record([_ev("可灵Kling", "official_page", "https://klingai.com/p", 0.8)],
                  "ai_video", ["可灵Kling"], path=self.tmp)
        self.assertIn("klingai.com", SL.top_official("ai_video", "可灵Kling", path=self.tmp))
        SL.record([_ev("可灵Kling", "reddit", "https://reddit.com/r/k", 0.9, "user_generated")],
                  "ai_video", ["可灵Kling"], path=self.tmp)
        self.assertIn("reddit.com", SL.top_sites("ai_video", "community", path=self.tmp))

    def test_topk_cap(self):
        ev = [_ev("X", "reddit", f"https://d{i}.com/x", 0.7 + i * 0.01, "user_generated") for i in range(10)]
        SL.record(ev, "c", ["X"], path=self.tmp)
        led = json.loads(self.tmp.read_text(encoding="utf-8"))
        self.assertLessEqual(len(led["by_category"]["c"]["community"]), SL.TOPK)


if __name__ == "__main__":
    unittest.main()
