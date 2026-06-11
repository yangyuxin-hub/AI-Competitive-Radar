"""Reviewer(Auditor)判定测试(minimal 模式)。

v3 M4b 后 Reviewer 只审不修:打回 target 选择/retry 配额/reject_requirements 写回已删。
保护点:
- 无 error → status=passed
- 有 hard_gate error → status=running(交给 guard_revise 一次修订定终态),
  issue 级 reject_target 归因保留在 quality_report.errors(checklist/gap_owner 消费)
"""
import copy
import os
import unittest

from src.reviewer import make_reviewer_node
from src.state import build_initial_state


def _state_with(schema: dict, evidence: list[dict]):
    st = build_initial_state(
        user_input="x",
        target_product="Cursor",
        competitors=["Windsurf"],
        analysis_focus=["代码补全体验"],
    )
    st["schema_draft"] = schema
    st["raw_evidence"] = evidence
    return st


_EVIDENCE = [
    {"evidence_id": "SFEAT001", "claim_type": "feature_existence"},
    {"evidence_id": "SPAIN001", "claim_type": "user_pain"},
]


_GOOD_SCHEMA = {
    "feature_tree": {
        "features": [
            {
                "feature_id": "F001",
                "name": "Tab 补全",
                "products": {
                    "Cursor": {
                        "support_status": "supported",
                        "support_evidence_ids": ["SFEAT001"],
                    },
                    "Windsurf": {
                        "support_status": "supported",
                        "support_evidence_ids": ["SFEAT001"],
                    },
                },
                "gap": {"winner": "Cursor", "evidence_ids": ["SFEAT001"], "confidence": 0.5},
            }
        ]
    },
    # R8 内容门要求每产品有定价档位(后加的门,fixture 需补全才能隔离出被测规则)
    "pricing_model": {
        "products": [
            {"name": "Cursor", "tiers": [{"tier_name": "Pro", "price": "$20/mo"}]},
            {"name": "Windsurf", "tiers": [{"tier_name": "Free", "price": "$0"}]},
        ],
        "pricing_gap": {},
    },
    "user_persona": {
        "pain_points": [
            {
                "pain_id": "P001",
                "description": "补全不稳",
                "frequency": {"evidence_ids": ["SPAIN001"]},
            }
        ],
    },
    "recommendations": [
        {
            "rec_id": "R001",
            "source_feature_ids": ["F001"],
            "source_pain_ids": ["P001"],
            "evidence_ids": ["SPAIN001"],
        },
        {
            "rec_id": "R002",
            "source_feature_ids": ["F001"],
            "source_pain_ids": [],
            "evidence_ids": ["SFEAT001"],
        },
    ],
    "swot": {
        "strengths": [{"point": "补全能力明确", "evidence_ids": ["SFEAT001"]}],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    }
}


def _bad_schema() -> dict:
    schema = copy.deepcopy(_GOOD_SCHEMA)
    schema["feature_tree"]["features"][0]["gap"]["evidence_ids"] = ["SNOTEXIST"]
    return schema


class ReviewerVerdictTest(unittest.TestCase):
    def setUp(self):
        self.review = make_reviewer_node(mode="minimal")

    def test_passed_when_no_errors(self):
        out = self.review(_state_with(_GOOD_SCHEMA, _EVIDENCE))
        self.assertEqual(out["status"], "passed")

    def test_running_with_issue_level_attribution(self):
        out = self.review(_state_with(_bad_schema(), _EVIDENCE))
        self.assertEqual(out["status"], "running",
                         "有 error → running,终态由 guard_revise 决定")
        self.assertIn("R1", out["quality_report"]["failed_rules"])
        # issue 级 reject_target 归因必须保留(checklist/stage_report.gap_owner 消费)
        errors = out["quality_report"]["errors"]
        self.assertTrue(errors)
        for e in errors:
            self.assertIn(e.get("reject_target"), ("collector", "analyzer", "writer"))

    def test_no_state_level_reject_machinery(self):
        """M4b:state 级打回字段不再写回(直线控制流无消费者)。"""
        out = self.review(_state_with(_bad_schema(), _EVIDENCE))
        for key in ("reject_target", "reject_requirements", "retry_count",
                    "max_retries_per_target"):
            self.assertNotIn(key, out, f"state 级 {key} 应已随打回机器删除")


class _DummyLLM:
    def call_json(self, *a, **k):
        return {"issues": []}  # 无语义问题


class TerminalR6Test(unittest.TestCase):
    """minimal 模式终轮 R6:注入 llm 时执行(软校验),REVIEWER_R6_FINAL=0 时跳过。"""

    def setUp(self):
        os.environ.pop("REVIEWER_R6_FINAL", None)

    def tearDown(self):
        os.environ.pop("REVIEWER_R6_FINAL", None)

    def test_r6_runs_in_minimal_when_llm_injected(self):
        review = make_reviewer_node(llm=_DummyLLM(), mode="minimal")
        out = review(_state_with(_GOOD_SCHEMA, _EVIDENCE))  # 结构干净 → R6 终轮执行
        qr = out["quality_report"]
        self.assertIn("R6", qr["passed_rules"])  # 执行且无 R6 错误
        self.assertNotIn("R6", qr["skipped_rules"])
        self.assertEqual(out["status"], "passed")

    def test_r6_skipped_when_disabled(self):
        os.environ["REVIEWER_R6_FINAL"] = "0"
        review = make_reviewer_node(llm=_DummyLLM(), mode="minimal")
        out = review(_state_with(_GOOD_SCHEMA, _EVIDENCE))
        self.assertIn("R6", out["quality_report"]["skipped_rules"])

    def test_r6_skipped_when_no_llm(self):
        review = make_reviewer_node(mode="minimal")  # 无 llm
        out = review(_state_with(_GOOD_SCHEMA, _EVIDENCE))
        self.assertIn("R6", out["quality_report"]["skipped_rules"])


class R5UnknownWinnerTest(unittest.TestCase):
    """winner='unknown'(证据不足,诚实不判胜负)不应被 R5 判为结构矛盾。"""

    def test_unknown_winner_not_contradiction(self):
        from src.reviewer import check_structured_contradiction

        schema = {"feature_tree": {"features": [
            {"feature_id": "F001", "name": "x",
             "products": {"Cursor": {}, "Windsurf": {}},
             "gap": {"winner": "unknown"}},
        ]}}
        issues = check_structured_contradiction(schema, [])
        self.assertEqual([i for i in issues if "gap.winner" in i["location"]], [])

    def test_winner_not_in_products_still_flagged(self):
        from src.reviewer import check_structured_contradiction

        schema = {"feature_tree": {"features": [
            {"feature_id": "F001", "name": "x",
             "products": {"Cursor": {}, "Windsurf": {}},
             "gap": {"winner": "GhostProduct"}},  # 不在 products → 真矛盾
        ]}}
        issues = check_structured_contradiction(schema, [])
        self.assertTrue([i for i in issues if "gap.winner" in i["location"]])


if __name__ == "__main__":
    unittest.main()
