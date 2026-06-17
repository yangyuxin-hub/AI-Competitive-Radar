import unittest

from src.reviewer import _collect_semantic_claims, check_semantic_grounding, make_reviewer_node


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def call_json(self, system_prompt, user_payload, max_tokens=4096, label="call", **kwargs):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_payload": user_payload,
            "max_tokens": max_tokens,
            "label": label,
            **kwargs,
        })
        return self.response


def _schema():
    return {
        "feature_tree": {
            "features": [
                {
                    "feature_id": "F001",
                    "name": "跨文件补全",
                    "products": {
                        "Cursor": {
                            "support_status": "supported",
                            "support_evidence_ids": ["SABC001"],
                            "quality_score": {
                                "score": 4,
                                "scale": 5,
                                "basis": "Cursor 在所有大型企业单体仓库里都显著领先",
                                "evidence_ids": ["SABC002"],
                            },
                        },
                        "Windsurf": {
                            "support_status": "supported",
                            "support_evidence_ids": ["SABC001"],
                        },
                    },
                    "gap": {
                        "winner": "Cursor",
                        "gap_type": "accuracy",
                        "reason": "Cursor 在所有大型企业单体仓库里都显著领先",
                        "evidence_ids": ["SABC002"],
                        "confidence": 0.8,
                    },
                }
            ]
        },
        "pricing_model": {
            "products": [
                {"name": "Cursor", "tiers": [{"tier_name": "Pro", "price": "$20/mo"}]},
                {"name": "Windsurf", "tiers": [{"tier_name": "Free", "price": "$0"}]},
            ],
            "pricing_gap": {},
        },
        "user_persona": {
            "user_segments": [],
            "pain_points": [
                {
                    "pain_id": "P001",
                    "description": "跨文件补全不稳定",
                    "frequency": {
                        "level": "medium",
                        "count": "1 条评论",
                        "evidence_ids": ["SABC002"],
                    },
                }
            ],
        },
        "recommendations": [
            {
                "rec_id": "R001",
                "action": "强化大型仓库补全",
                "rationale": "因为 Cursor 在所有大型企业单体仓库里都显著领先",
                "source_feature_ids": ["F001"],
                "source_pain_ids": ["P001"],
                "evidence_ids": ["SABC002"],
                "priority_score": {
                    "pain_frequency": 4,
                    "business_impact": 4,
                    "implementation_feasibility": 3,
                    "evidence_confidence": 4,
                    "weights": {
                        "pain_frequency": 0.35,
                        "business_impact": 0.30,
                        "implementation_feasibility": 0.20,
                        "evidence_confidence": 0.15,
                    },
                    "final_score": 3.8,
                    "priority": "P1",
                },
            },
            {
                "rec_id": "R002",
                "action": "补充跨文件召回失败时的可解释提示",
                "rationale": "P001 显示跨文件补全不稳定，提示缺失上下文可减少用户误判",
                "source_feature_ids": ["F001"],
                "source_pain_ids": ["P001"],
                "evidence_ids": ["SABC002"],
                "priority_score": {
                    "pain_frequency": 3,
                    "business_impact": 3,
                    "implementation_feasibility": 4,
                    "evidence_confidence": 3,
                    "weights": {
                        "pain_frequency": 0.35,
                        "business_impact": 0.30,
                        "implementation_feasibility": 0.20,
                        "evidence_confidence": 0.15,
                    },
                    "final_score": 3.2,
                    "priority": "P2",
                },
            }
        ],
        "swot": {
            "target": "Cursor",
            "strengths": [
                {"point": "支持跨文件编辑", "evidence_ids": ["SABC001"], "confidence": 0.7}
            ],
            "weaknesses": [],
            "opportunities": [],
            "threats": [],
        },
    }


def _evidence():
    return [
        {
            "evidence_id": "SABC001",
            "product": "Cursor",
            "claim_type": "feature_existence",
            "source_bias": "vendor_claim",
            "extracted_snippet": "Cursor supports multi-line edits across your codebase.",
        },
        {
            "evidence_id": "SABC002",
            "product": "Cursor",
            "claim_type": "user_pain",
            "source_bias": "user_generated",
            "extracted_snippet": "On my 200k LOC monorepo, Cursor sometimes misses obvious files.",
        },
    ]


class ReviewerR6Test(unittest.TestCase):
    def test_semantic_claim_collection_skips_unrated_quality_basis(self):
        schema = {
            "feature_tree": {"features": [{
                "feature_id": "F001",
                "name": "文本驱动视频生成",
                "products": {
                    "即梦AI": {
                        "support_status": "supported",
                        "support_evidence_ids": ["SABC001"],
                        "quality_score": {
                            "score": 0,
                            "scale": 5,
                            "basis": "无用户/第三方质量评价证据，暂不评分",
                            "evidence_ids": [],
                        },
                    }
                },
                "gap": {},
            }]},
        }

        claims = _collect_semantic_claims(schema)

        self.assertEqual(claims, [])

    def test_semantic_claim_collection_uses_recommendation_evidence_refs(self):
        schema = {"recommendations": [{
            "rec_id": "R001",
            "action": "补齐高清输出",
            "rationale": "对标竞品公开规格",
            "evidence_refs": ["SABC002"],
        }]}

        claims = _collect_semantic_claims(schema)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["location"], "recommendations.R001")
        self.assertEqual(claims[0]["evidence_ids"], ["SABC002"])

    def test_semantic_grounding_converts_llm_issue(self):
        llm = FakeLLM({
            "issues": [
                {
                    "location": "recommendations.R001",
                    "issue_type": "overclaim",
                    "severity": "error",
                    "detail": "单条用户评论不能支撑所有大型企业单体仓库都领先",
                    "unsupported_text": "所有大型企业单体仓库",
                    "evidence_ids": ["SABC002"],
                    "confidence": 0.9,
                }
            ]
        })

        issues = check_semantic_grounding(_schema(), _evidence(), llm)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["rule"], "R6")
        self.assertEqual(issues[0]["issue_type"], "semantic_grounding_fail")
        self.assertEqual(issues[0]["severity"], "error")
        self.assertEqual(issues[0]["reject_target"], "analyzer")
        self.assertEqual(llm.calls[0]["label"], "reviewer_r6")

    def test_full_reviewer_uses_r6_to_reject_analyzer(self):
        llm = FakeLLM({
            "issues": [
                {
                    "location": "recommendations.R001",
                    "issue_type": "unsupported",
                    "severity": "error",
                    "detail": "证据不支撑该建议依据",
                    "evidence_ids": ["SABC002"],
                    "confidence": 0.95,
                }
            ]
        })
        state = {
            "user_input": "分析 Cursor",
            "analysis_meta": {
                "target_product": "Cursor",
                "competitors": ["Windsurf"],
                "analysis_focus": ["代码补全体验"],
            },
            "raw_evidence": _evidence(),
            "schema_draft": _schema(),
            "report_draft": "",
            "quality_report": None,
            "collection_meta": None,
            "reject_target": None,
            "reject_requirements": None,
            "retry_count": {"collector": 0, "analyzer": 0, "writer": 0},
            "max_retries_per_target": {"collector": 1, "analyzer": 2, "writer": 1},
            "status": "running",
        }

        out = make_reviewer_node(llm=llm, mode="full")(state)

        # M4b:state 级打回字段已删;R6 error 仍判 running,issue 级归因到 analyzer
        self.assertEqual(out["status"], "running")
        self.assertIn("R6", out["quality_report"]["failed_rules"])
        r6_errors = [e for e in out["quality_report"]["errors"] if e.get("rule") == "R6"]
        self.assertTrue(r6_errors)
        self.assertEqual(r6_errors[0].get("reject_target"), "analyzer")

    def test_full_reviewer_marks_r6_skipped_when_structural_errors_exist(self):
        schema = _schema()
        schema["feature_tree"]["features"][0]["products"]["Cursor"]["support_evidence_ids"] = ["SABC002"]
        state = {
            "user_input": "分析 Cursor",
            "analysis_meta": {
                "target_product": "Cursor",
                "competitors": ["Windsurf"],
                "analysis_focus": ["代码补全体验"],
            },
            "raw_evidence": _evidence(),
            "schema_draft": schema,
            "report_draft": "",
            "quality_report": None,
            "collection_meta": None,
            "reject_target": None,
            "reject_requirements": None,
            "retry_count": {"collector": 0, "analyzer": 0, "writer": 0},
            "max_retries_per_target": {"collector": 1, "analyzer": 2, "writer": 1},
            "status": "running",
        }

        out = make_reviewer_node(llm=FakeLLM({"issues": []}), mode="full")(state)
        qr = out["quality_report"]

        self.assertIn("R2", qr["failed_rules"])
        self.assertIn("R6", qr["skipped_rules"])
        self.assertNotIn("R6", qr["passed_rules"])


if __name__ == "__main__":
    unittest.main()
