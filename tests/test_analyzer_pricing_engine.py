import unittest

from unittest.mock import patch

from src.analyzer import _apply_pricing_engine, _backfill_unknown_pricing_tiers, _step1_facts


class AnalyzerPricingEngineTest(unittest.TestCase):
    def test_backfills_unknown_tier_when_pricing_evidence_exists_but_llm_misses_tiers(self):
        facts = {"pricing_model": {"products": [{"name": "可灵Kling", "tiers": []}]}}
        evidence = [
            {"evidence_id": "SPRICE01", "product": "可灵Kling", "claim_type": "pricing",
             "extracted_snippet": "会员页展示灵感值与付费计划"},
            {"evidence_id": "SFEAT001", "product": "可灵Kling", "claim_type": "feature_existence"},
        ]

        filled = _backfill_unknown_pricing_tiers(facts, evidence, ["可灵Kling"])

        self.assertEqual(filled, 1)
        tier = facts["pricing_model"]["products"][0]["tiers"][0]
        self.assertEqual(tier["tier_name"], "定价信息待结构化")
        self.assertEqual(tier["evidence_ids"], ["SPRICE01"])
        self.assertEqual(tier["billing_options"][0]["amount_status"], "unknown")

    def test_attaches_sparse_seat_subscription_engine_result(self):
        facts = {"pricing_model": {"products": [{
            "name": "Cursor",
            "tiers": [
                {"tier_name": "Hobby", "billing_cycle": "monthly",
                 "price": {"amount": 0, "currency": "USD", "normalized_usd_month": 0},
                 "evidence_ids": ["S0000001"]},
                {"tier_name": "Pro", "billing_cycle": "monthly",
                 "price": {"amount": 20, "currency": "USD", "normalized_usd_month": 20},
                 "evidence_ids": ["S0000002"]},
            ],
        }]}}
        evidence = [
            {"evidence_id": "S0000001", "product": "Cursor", "claim_type": "pricing",
             "extracted_snippet": "Hobby: $0"},
            {"evidence_id": "S0000002", "product": "Cursor", "claim_type": "pricing",
             "extracted_snippet": "Pro: $20 per user per month"},
        ]

        changed = _apply_pricing_engine(facts, evidence)

        self.assertEqual(changed, 1)
        engine = facts["pricing_model"]["products"][0]["pricing_engine"]
        self.assertEqual(engine["archetype"], "Freemium + Subscription")
        self.assertEqual(engine["comparison_axis"], "per_seat")
        self.assertEqual(engine["tiers"][1]["price_per_credit"], None)
        self.assertEqual(engine["tiers"][1]["unit_costs"], [])

    def test_attaches_credit_metered_unit_costs_when_grant_and_consumption_exist(self):
        facts = {"pricing_model": {"products": [{
            "name": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{
                "tier_name": "标准会员",
                "billing_cycle": "monthly",
                "price": {"amount": 199, "currency": "CNY", "normalized_usd_month": None},
                "credit_grant": {"amount": 2210, "unit": "积分"},
                "evidence_ids": ["S0000003"],
            }],
            "consumption": [
                {"capability": "image", "credits_per_output": 1, "unit": "积分/张"},
                {"capability": "video", "credits_per_unit": 8, "unit": "积分/秒"},
            ],
        }]}}
        evidence = [{"evidence_id": "S0000003", "product": "即梦AI", "claim_type": "pricing",
                     "extracted_snippet": "标准会员 ¥199/月, 每月 2210 积分"}]

        changed = _apply_pricing_engine(facts, evidence)

        self.assertEqual(changed, 1)
        engine = facts["pricing_model"]["products"][0]["pricing_engine"]
        self.assertEqual(engine["archetype"], "Freemium + Subscription + Credits")
        self.assertEqual(engine["comparison_axis"], "unit_cost")
        tier = engine["tiers"][0]
        self.assertAlmostEqual(tier["price_per_credit"], 0.09, places=3)
        costs = {c["capability"]: c["value"] for c in tier["unit_costs"]}
        self.assertAlmostEqual(costs["image"], 0.09, places=3)
        self.assertAlmostEqual(costs["video"], 0.72, places=2)

    def test_pricing_engine_exposes_archetype_for_section10(self):
        facts = {"pricing_model": {"products": [{
            "name": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{
                "tier_name": "标准",
                "billing_options": [{
                    "cycle": "monthly",
                    "is_promo": False,
                    "price": {"amount": 199, "currency": "CNY"},
                }],
                "credit_grant": {"amount": 2210, "unit": "积分"},
            }],
            "consumption": [],
        }]}}

        _apply_pricing_engine(facts, [])

        eng = facts["pricing_model"]["products"][0]["pricing_engine"]
        self.assertEqual(eng["archetype"], "Freemium + Subscription + Credits")

    def test_preserves_referral_promotions_in_engine_input(self):
        facts = {"pricing_model": {"products": [{
            "name": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{
                "tier_name": "基础会员",
                "billing_cycle": "monthly",
                "price": {"amount": 69, "currency": "CNY"},
                "credit_grant": {"amount": 725, "unit": "积分"},
            }],
            "promotions": [{
                "kind": "referral_reward",
                "name": "邀请好友送额度",
                "reward": {"amount": 300, "unit": "积分"},
                "condition": "邀请好友注册/完成任务",
                "evidence_ids": ["SREF001"],
            }],
        }]}}
        evidence = [{"evidence_id": "SREF001", "product": "即梦AI", "claim_type": "pricing",
                     "extracted_snippet": "邀请好友送额度"}]

        _apply_pricing_engine(facts, evidence)

        engine = facts["pricing_model"]["products"][0]["pricing_engine"]
        self.assertEqual(engine["promotions"][0]["kind"], "referral_reward")
        self.assertEqual(engine["promotions"][0]["reward"]["amount"], 300)


class AnalyzerPricingFlowTest(unittest.TestCase):
    def test_step1_facts_attaches_pricing_engine(self):
        evidence = [
            {"evidence_id": "S0000001", "product": "Cursor", "claim_type": "pricing",
             "extracted_snippet": "Hobby: $0"},
            {"evidence_id": "S0000002", "product": "Cursor", "claim_type": "pricing",
             "extracted_snippet": "Pro: $20 per user per month"},
        ]
        meta = {"target_product": "Cursor", "competitors": ["Windsurf"], "analysis_focus": ["定价"]}
        fake_sections = {
            "feature_tree": {"category": "定价", "features": []},
            "pricing_model": {"products": [{
                "name": "Cursor",
                "tiers": [{"tier_name": "Pro", "billing_cycle": "monthly",
                           "price": {"amount": 20, "currency": "USD", "normalized_usd_month": 20},
                           "evidence_ids": ["S0000002"]}],
            }]},
            "user_persona": {"user_segments": [], "pain_points": []},
        }

        def fake_call(section, *_args, **_kwargs):
            return section, fake_sections[section]

        with patch("src.analyzer.is_mock_mode", return_value=False), \
             patch("src.analyzer._facts_section_call", side_effect=fake_call):
            facts = _step1_facts(evidence, meta)

        product = facts["pricing_model"]["products"][0]
        self.assertEqual(product["pricing_engine"]["comparison_axis"], "per_seat")
        self.assertIn("pricing_strategy_analysis", facts["pricing_model"])
        strategy = facts["pricing_model"]["pricing_strategy_analysis"]
        self.assertIn("pricing_model_analysis", strategy)
        self.assertIn("value_for_money_analysis", strategy)

    def test_apply_pricing_engine_attaches_cross_product_comparison(self):
        facts = {"pricing_model": {"products": [
            {"name": "即梦AI", "pricing_structure": {"layers": [
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]}, "tiers": [{"tier_name": "标准会员", "billing_cycle": "monthly",
                            "price": {"amount": 199, "currency": "CNY"},
                            "credit_grant": {"amount": 2210, "unit": "积分"},
                            "evidence_ids": ["S0000001"]}]},
            {"name": "可灵AI", "pricing_structure": {"layers": [
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]}, "tiers": [{"tier_name": "钻石会员", "billing_cycle": "monthly",
                            "price": {"amount": 666, "currency": "CNY"},
                            "credit_grant": {"amount": 8000, "unit": "灵感值"},
                            "evidence_ids": ["S0000002"]}]},
        ]}}
        evidence = [
            {"evidence_id": "S0000001", "product": "即梦AI", "claim_type": "pricing",
             "extracted_snippet": "标准会员 ¥199/月, 2210 积分"},
            {"evidence_id": "S0000002", "product": "可灵AI", "claim_type": "pricing",
             "extracted_snippet": "钻石会员 666元/月, 8000 灵感值"},
        ]

        _apply_pricing_engine(facts, evidence)

        cmp = facts["pricing_model"]["engine_comparison"]
        self.assertEqual(cmp["credit_price_winner"]["product"], "可灵AI")
        strategy = facts["pricing_model"]["pricing_strategy_analysis"]
        self.assertIn("pricing_model_analysis", strategy)
        self.assertIn("value_for_money_analysis", strategy)


if __name__ == "__main__":
    unittest.main()
