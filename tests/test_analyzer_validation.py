import unittest

from src.analyzer import quick_validate_derivations, quick_validate_facts, sanitize_facts_evidence_refs
from src.reviewer import check_reasoning_chain


META = {
    "target_product": "Cursor",
    "competitors": ["Windsurf"],
    "analysis_focus": ["代码补全体验"],
}

EVIDENCE = [
    {"evidence_id": "SFEAT001", "claim_type": "feature_existence"},
    {"evidence_id": "SPERF001", "claim_type": "performance_quality"},
    {"evidence_id": "SPRICE01", "claim_type": "pricing"},
    {"evidence_id": "SPAIN001", "claim_type": "user_pain"},
]


def _base_facts() -> dict:
    return {
        "feature_tree": {
            "category": "代码补全体验",
            "features": [
                {
                    "feature_id": "F001",
                    "name": "Tab 补全",
                    "products": {
                        "Cursor": {
                            "support_status": "supported",
                            "support_evidence_ids": ["SFEAT001"],
                            "quality_score": {
                                "score": 3,
                                "scale": 5,
                                "basis": "用户反馈一般",
                                "aggregation": {
                                    "aggregation_type": "sampled_evidence",
                                    "positive_mentions": 0,
                                    "negative_mentions": 1,
                                    "neutral_mentions": 0,
                                    "sample_size": 1,
                                    "representative_evidence_ids": ["SPAIN001"],
                                    "method": "manual sample",
                                },
                                "evidence_ids": ["SPAIN001"],
                            },
                        },
                        "Windsurf": {
                            "support_status": "supported",
                            "support_evidence_ids": ["SFEAT001"],
                            "quality_score": {
                                "score": 3,
                                "scale": 5,
                                "basis": "用户反馈一般",
                                "aggregation": {
                                    "aggregation_type": "sampled_evidence",
                                    "positive_mentions": 1,
                                    "negative_mentions": 0,
                                    "neutral_mentions": 0,
                                    "sample_size": 1,
                                    "representative_evidence_ids": ["SPERF001"],
                                    "method": "manual sample",
                                },
                                "evidence_ids": ["SPERF001"],
                            },
                        },
                    },
                    "gap": {
                        "winner": "Cursor",
                        "gap_type": "accuracy",
                        "reason": "Cursor 略优",
                        "evidence_ids": ["SPAIN001"],
                        "confidence": 0.7,
                    },
                }
            ],
        },
        "pricing_model": {
            "products": [
                {
                    "name": "Cursor",
                    "tiers": [
                        {
                            "tier_name": "Pro",
                            "billing_cycle": "monthly",
                            "price": {"amount": 20, "currency": "USD", "normalized_usd_month": 20},
                            "limits": [],
                            "display_limits": "Pro",
                            "observed_at": "2026-05-27",
                            "source_freshness": "current",
                            "evidence_ids": ["SPRICE01"],
                        }
                    ],
                }
            ],
            "pricing_gap": {
                "target_position": "more_expensive",
                "summary": "Cursor 更贵",
                "evidence_ids": ["SPRICE01", "SPAIN001"],
                "confidence": 0.7,
            },
        },
        "user_persona": {
            "user_segments": [
                {
                    "segment_id": "U001",
                    "name": "独立开发者",
                    "description": "价格敏感",
                    "evidence_ids": ["SPAIN001"],
                    "confidence": 0.7,
                }
            ],
            "pain_points": [
                {
                    "pain_id": "P001",
                    "description": "补全不稳",
                    "frequency": {
                        "level": "medium",
                        "count": "1 条",
                        "sample_size": 1,
                        "evidence_ids": ["SPAIN001"],
                    },
                    "affected_products": ["Cursor"],
                    "affected_segments": ["U001"],
                    "user_expectation": "更稳定",
                    "confidence": 0.7,
                }
            ],
        },
    }


class AnalyzerFactsValidationTest(unittest.TestCase):
    def assert_has_claim_type_issue(self, facts: dict, path_fragment: str) -> None:
        issues = quick_validate_facts(facts, EVIDENCE, META)
        matching = [i for i in issues if path_fragment in i and "claim_type" in i]
        self.assertTrue(matching, f"expected claim_type issue for {path_fragment}, got {issues}")

    def test_support_evidence_ids_reject_pricing(self):
        facts = _base_facts()
        facts["feature_tree"]["features"][0]["products"]["Cursor"]["support_evidence_ids"] = ["SPRICE01"]

        self.assert_has_claim_type_issue(facts, "support_evidence_ids")

    def test_quality_score_evidence_ids_reject_feature_existence(self):
        facts = _base_facts()
        facts["feature_tree"]["features"][0]["products"]["Cursor"]["quality_score"]["evidence_ids"] = ["SFEAT001"]

        self.assert_has_claim_type_issue(facts, "quality_score.evidence_ids")

    def test_pricing_tier_evidence_ids_reject_user_pain(self):
        facts = _base_facts()
        facts["pricing_model"]["products"][0]["tiers"][0]["evidence_ids"] = ["SPAIN001"]

        self.assert_has_claim_type_issue(facts, "tiers[0].evidence_ids")

    def test_sanitize_facts_drops_invalid_claim_type_refs(self):
        facts = _base_facts()
        facts["feature_tree"]["features"][0]["products"]["Cursor"]["support_evidence_ids"] = ["SPRICE01"]
        facts["feature_tree"]["features"][0]["products"]["Cursor"]["quality_score"]["evidence_ids"] = ["SFEAT001"]
        facts["pricing_model"]["products"][0]["tiers"][0]["evidence_ids"] = ["SPAIN001"]

        sanitized, dropped = sanitize_facts_evidence_refs(facts, EVIDENCE)
        issues = quick_validate_facts(sanitized, EVIDENCE, META)

        self.assertEqual(dropped, 3)
        self.assertFalse([i for i in issues if "claim_type" in i], issues)

    def test_derivations_reject_empty_recommendations_and_swot(self):
        issues = quick_validate_derivations({"recommendations": [], "swot": {}}, _base_facts(), EVIDENCE)

        self.assertTrue([i for i in issues if i.startswith("recommendations:")], issues)
        self.assertTrue([i for i in issues if i.startswith("swot:")], issues)

    def test_reviewer_rejects_report_without_recommendations_and_swot(self):
        schema = {**_base_facts(), "recommendations": [], "swot": {}}
        issues = check_reasoning_chain(schema, EVIDENCE)
        locations = {i["location"] for i in issues}

        self.assertIn("recommendations", locations)
        self.assertIn("swot", locations)


if __name__ == "__main__":
    unittest.main()
