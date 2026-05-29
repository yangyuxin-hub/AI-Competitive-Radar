"""问卷/访谈采集 Skill 测试:问卷→访谈→证据,且合成数据透明标注。"""
import os
import unittest

from src import survey_skill
from src.survey_skill import SurveySkill


class _StubLLM:
    def call_json(self, system, payload, label="", **kw):
        if label.startswith("survey"):  # 合并为单次调用
            return {
                "questions": [
                    {"id": "Q1", "text": "补全速度如何?", "claim_type": "performance_quality"},
                    {"id": "Q2", "text": "最影响体验的痛点?", "claim_type": "user_pain"},
                ],
                "findings": [
                    {"persona": "独立开发者", "question_id": "Q1",
                     "claim_type": "performance_quality", "finding": "补全延迟约 1 秒,偏慢",
                     "expectation": "100ms 内"},
                    {"persona": "企业团队", "question_id": "Q2",
                     "claim_type": "user_pain", "finding": "大项目里偶尔补全错乱", "expectation": ""},
                    {"persona": "学生", "question_id": "Q2", "claim_type": "bogus_type",
                     "finding": "免费额度不够用", "expectation": "更多免费额度"},  # 非法 claim_type → 归一
                ],
            }
        return {}


class SurveySkillTest(unittest.TestCase):
    def setUp(self):
        self._orig = survey_skill.SurveySkill
        os.environ["ARK_API_KEY"] = os.environ.get("ARK_API_KEY", "test")
        os.environ.pop("SURVEY_SKILL", None)
        os.environ.pop("ANALYZER_MOCK", None)
        # 注入 stub LLM
        import src.llm as llm
        self._orig_get = llm.get_llm
        self._orig_mock = llm.is_mock_mode
        llm.get_llm = lambda: _StubLLM()
        llm.is_mock_mode = lambda: False

    def tearDown(self):
        import src.llm as llm
        llm.get_llm = self._orig_get
        llm.is_mock_mode = self._orig_mock

    def test_produces_labeled_synthetic_evidence(self):
        evs, meta = SurveySkill().execute([], product="Cursor", focus="代码补全体验")
        self.assertEqual(len(evs), 3)
        for e in evs:
            self.assertTrue(e["evidence_id"].startswith("S") and len(e["evidence_id"]) == 8)
            self.assertEqual(e["source_bias"], "synthetic")
            self.assertEqual(e["source_type"], "simulated_interview")
            self.assertTrue(e["synthetic"])
            self.assertIn(e["claim_type"], {"user_pain", "performance_quality", "feature_existence"})
            self.assertTrue(e["claim"].startswith("[模拟访谈]"))
            self.assertLess(e["source_reliability"], 0.6)  # 合成数据可信度低
        # 非法 claim_type 被归一为 user_pain
        self.assertEqual(evs[2]["claim_type"], "user_pain")
        # 问卷随 meta 返回供溯源 + 合规免责声明
        self.assertEqual(meta["n_questions"], 2)
        self.assertTrue(meta["synthetic"])
        self.assertIn("disclaimer", meta)

    def test_disabled_by_env(self):
        os.environ["SURVEY_SKILL"] = "0"
        try:
            self.assertFalse(SurveySkill().can_execute([], product="Cursor"))
        finally:
            del os.environ["SURVEY_SKILL"]

    def test_skipped_in_mock(self):
        import src.llm as llm
        llm.is_mock_mode = lambda: True
        self.assertFalse(SurveySkill().can_execute([], product="Cursor"))

    def test_analyzer_run_survey_builds_research_method(self):
        from src import analyzer
        meta = {"target_product": "Cursor", "competitors": ["Windsurf"], "analysis_focus": ["代码补全体验"]}
        merged, rm = analyzer._run_survey([], meta)
        self.assertTrue(len(merged) > 0)  # 合成访谈证据合并进来
        self.assertIsNotNone(rm)
        self.assertEqual(len(rm["questions"]), 2)
        self.assertTrue(rm["synthetic"])
        self.assertIn("独立开发者", rm["personas"])
        self.assertEqual(rm["n_findings"], len(merged))


if __name__ == "__main__":
    unittest.main()
