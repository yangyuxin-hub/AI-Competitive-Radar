import unittest

from src.pricing_compare import compare_pricing_products
from src.pricing_model import compute_product


class PricingCompareTest(unittest.TestCase):
    def test_compares_credit_price_and_keeps_incompatible_units_honest(self):
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
            "consumption": [{"capability": "video_second", "credits_per_unit": 8, "unit": "积分/秒"}],
        })
        kling = compute_product({
            "product": "可灵AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True},
            ]},
            "tiers": [
                {"tier_name": "黄金会员",
                 "billing_options": [{"cycle": "monthly", "is_promo": False,
                                      "price": {"amount": 66, "currency": "CNY"}}],
                 "credit_grant": {"amount": 660, "unit": "灵感值"}},
                {"tier_name": "钻石会员",
                 "billing_options": [{"cycle": "monthly", "is_promo": False,
                                      "price": {"amount": 666, "currency": "CNY"}}],
                 "credit_grant": {"amount": 8000, "unit": "灵感值"}},
            ],
            "consumption": [{"capability": "standard_video", "credits_per_output": 10,
                             "unit": "灵感值/标准视频"}],
        })

        out = compare_pricing_products([
            {"name": "即梦AI", "pricing_engine": jimeng},
            {"name": "可灵AI", "pricing_engine": kling},
        ])

        self.assertEqual(out["credit_price_winner"]["product"], "可灵AI")
        self.assertAlmostEqual(out["credit_price_winner"]["value"], 0.0833, places=4)
        self.assertTrue(any(g["kind"] == "unit_cost_axis_mismatch" for g in out["gaps"]))
        self.assertTrue(any("同为积分制" in i for i in out["insights"]))
        self.assertTrue(any("不等同" in i for i in out["insights"]))


if __name__ == "__main__":
    unittest.main()
