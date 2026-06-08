"""R9/R10 writer 自检规则测试 — src/reviewer.py"""
import unittest

from src.reviewer import check_report_chip_traceability, check_report_no_score_leak
from src.writer import _render_data_availability

_EV = [{"evidence_id": "S1234ABC"}, {"evidence_id": "SABCDEF1"}]


class R9ChipTraceTest(unittest.TestCase):
    def test_bad_chip_fires(self):
        report = "Cursor 补全快 [S1234ABC]。Windsurf 慢 [SDEADBEF]。"  # 后者不存在
        issues = check_report_chip_traceability(report, _EV)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue_type"], "report_chip_not_found")
        self.assertEqual(issues[0]["reject_target"], "writer")

    def test_all_valid_passes(self):
        report = "结论一 [S1234ABC]。结论二 [SABCDEF1]。"
        self.assertEqual(check_report_chip_traceability(report, _EV), [])

    def test_missing_chip_fires_when_evidence_exists(self):
        issues = check_report_chip_traceability("Cursor 补全更快。", _EV)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue_type"], "report_chip_missing")
        self.assertEqual(issues[0]["reject_target"], "writer")

    def test_empty_report_no_issue(self):
        self.assertEqual(check_report_chip_traceability("", _EV), [])


class R10ScoreLeakTest(unittest.TestCase):
    def test_leak_fires(self):
        issues = check_report_no_score_leak("综合 quality_score 为 0.8", _EV)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["reject_target"], "writer")

    def test_clean_passes(self):
        self.assertEqual(check_report_no_score_leak("Cursor 综合领先 [S1234ABC]", _EV), [])

    def test_dimensional_score_not_flagged(self):
        # 各维度证据评分(/5)是正文合法内容,不应触发 R10(修复真实 deep 跑出的误杀)
        body = "Cursor 在「基础内联代码补全」上质量评分领先（4/5 vs 次优 3/5） [S1234ABC]"
        self.assertEqual(check_report_no_score_leak(body, _EV), [])

    def test_report_level_badge_fires(self):
        # 报告级 /100 徽章才是真正要拦的泄漏
        issues = check_report_no_score_leak("本报告质检 73/100，通过。", _EV)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["reject_target"], "writer")


class DataAvailabilityTest(unittest.TestCase):
    def test_unfilled_pricing_shown_honestly(self):
        md = _render_data_availability(
            {"gaps": [{"product": "可灵Kling", "claim_type": "pricing",
                       "gap_type": "pricing_no_number", "reason": "x"}]})
        self.assertIn("数据可得性说明", md)
        self.assertIn("可灵Kling", md)
        self.assertIn("积分制", md)        # 诚实标注原因,而非空着

    def test_no_gaps_renders_empty(self):
        self.assertEqual(_render_data_availability({"gaps": []}), "")
        self.assertEqual(_render_data_availability({}), "")


if __name__ == "__main__":
    unittest.main()
