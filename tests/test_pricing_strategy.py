import unittest

from src.pricing_compare import compare_pricing_products
from src.pricing_model import compute_product
from src.pricing_strategy import build_pricing_strategy_analysis


class PricingStrategyAnalysisTest(unittest.TestCase):
    def test_splits_business_model_and_value_for_money(self):
        jimeng = compute_product({
            "product": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{"tier_name": "基础会员",
                       "billing_options": [{"cycle": "monthly", "is_promo": False,
                                            "price": {"amount": 69, "currency": "CNY"}}],
                       "credit_grant": {"amount": 725, "unit": "积分"}}],
            "consumption": [{"capability": "video_second", "credits_per_unit": 8,
                             "unit": "积分/秒"}],
        })
        kling = compute_product({
            "product": "可灵AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{"tier_name": "黄金会员",
                       "billing_options": [{"cycle": "monthly", "is_promo": False,
                                            "price": {"amount": 66, "currency": "CNY"}}],
                       "credit_grant": {"amount": 660, "unit": "灵感值"}}],
            "consumption": [{"capability": "standard_video", "credits_per_output": 10,
                             "unit": "灵感值/标准视频"}],
        })
        products = [
            {"name": "即梦AI", "pricing_engine": jimeng},
            {"name": "可灵AI", "pricing_engine": kling},
        ]
        comparison = compare_pricing_products(products)

        out = build_pricing_strategy_analysis(products, comparison)

        self.assertIn("pricing_model_analysis", out)
        self.assertIn("value_for_money_analysis", out)
        model = out["pricing_model_analysis"]
        value = out["value_for_money_analysis"]
        self.assertEqual(len(model["products"]), 2)
        self.assertTrue(any("免费" in lever for lever in model["products"][0]["conversion_levers"]))
        self.assertTrue(any("积分" in lever for lever in model["products"][0]["conversion_levers"]))
        self.assertTrue(any(s["scenario"] == "稳定日常使用" for s in value["scenario_baskets"]))
        self.assertTrue(value["caveats"])

    def test_scenario_baskets_are_industry_agnostic_and_focus_driven(self):
        """场景篮子必须行业泛化:不含任何垂直名词(短视频/首尾帧…),
        且把 analysis_focus 织进话术。"""
        engine = compute_product({
            "product": "X",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{"tier_name": "Pro",
                       "billing_options": [{"cycle": "monthly", "is_promo": False,
                                            "price": {"amount": 20, "currency": "USD"}}]}],
        })
        products = [{"name": "X", "pricing_engine": engine}]
        out = build_pricing_strategy_analysis(
            products, compare_pricing_products(products), focus=["代码补全体验"])
        baskets = out["value_for_money_analysis"]["scenario_baskets"]
        blob = str(baskets)
        for vertical in ("短视频", "首尾帧", "运镜", "商业视频", "图片"):
            self.assertNotIn(vertical, blob)
        # focus 提炼后(去「体验」后缀)应织入场景产出话术
        self.assertIn("代码补全", blob)

    def test_outputs_entitlements_monetization_and_cautious_value_claims(self):
        jimeng = compute_product({
            "product": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{"tier_name": "标准会员",
                       "billing_options": [{"cycle": "monthly", "is_promo": False,
                                            "price": {"amount": 199, "currency": "CNY"}}],
                       "credit_grant": {"amount": 2210, "unit": "积分"}}],
            "consumption": [{"capability": "video_second", "credits_per_unit": 8,
                             "unit": "积分/秒"}],
        })
        kling = compute_product({
            "product": "可灵AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{"tier_name": "钻石会员",
                       "billing_options": [{"cycle": "monthly", "is_promo": False,
                                            "price": {"amount": 666, "currency": "CNY"}}],
                       "credit_grant": {"amount": 8000, "unit": "灵感值"}}],
            "consumption": [{"capability": "standard_video", "credits_per_output": 10,
                             "unit": "灵感值/标准视频"}],
        })
        products = [
            {"name": "即梦AI", "pricing_engine": jimeng},
            {"name": "可灵AI", "pricing_engine": kling},
        ]

        out = build_pricing_strategy_analysis(products, compare_pricing_products(products))
        product = out["pricing_model_analysis"]["products"][0]
        scenario = next(s for s in out["value_for_money_analysis"]["scenario_baskets"]
                        if s["scenario"] == "稳定日常使用")

        self.assertIn("entitlement_design", product)
        self.assertIn("monetization_hypothesis", product)
        self.assertIn("free_layer", product["entitlement_design"])
        self.assertIn("monthly_budget", scenario)
        self.assertIn("required_capabilities", scenario)
        self.assertIn("不能等同", scenario["best_for"])
        self.assertTrue(any("单位不同" in c for c in out["value_for_money_analysis"]["caveats"]))

    def test_referral_promotion_is_treated_as_acquisition_lever(self):
        jimeng = compute_product({
            "product": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [{
                "tier_name": "基础会员",
                "billing_options": [{"cycle": "monthly", "is_promo": False,
                                     "price": {"amount": 69, "currency": "CNY"}}],
                "credit_grant": {"amount": 725, "unit": "积分"},
            }],
            "promotions": [{
                "kind": "referral_reward",
                "name": "邀请好友送额度",
                "reward": {"amount": 300, "unit": "积分"},
                "condition": "邀请好友注册/完成任务",
            }],
        })
        out = build_pricing_strategy_analysis(
            [{"name": "即梦AI", "pricing_engine": jimeng}],
            {"products": [{"product": "即梦AI"}]},
        )
        product = out["pricing_model_analysis"]["products"][0]
        rewards = product["entitlement_design"]["promotional_rewards"]
        self.assertTrue(any("邀请好友送额度" in r and "300积分" in r for r in rewards), rewards)
        self.assertTrue(any("邀请" in lever for lever in product["conversion_levers"]))


if __name__ == "__main__":
    unittest.main()
