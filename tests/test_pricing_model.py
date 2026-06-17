"""确定性定价引擎 src/pricing_model.py —— 见 docs/pricing-model-jimeng.example.yaml。

铁律(测试守护):
  1. 单位成本只用「续费常规价」做 basis,促销/体验价永不入基准。
  2. unit_cost / price_per_credit 缺数据 → None(诚实 unknown),绝不估算。
  3. 计算确定性:同输入恒同输出,每步可溯源。
"""
import unittest

from src.pricing_model import (
    normalized_monthly, price_per_credit, unit_cost,
    derive_archetype, comparison_axis, compute_product,
)


class NormalizedMonthlyTest(unittest.TestCase):
    def test_monthly_passthrough(self):
        opt = {"cycle": "monthly", "price": {"amount": 199, "currency": "CNY"}}
        self.assertEqual(normalized_monthly(opt), 199.0)

    def test_yearly_divided_by_12(self):
        opt = {"cycle": "yearly", "price": {"amount": 1899, "currency": "CNY"}}
        self.assertAlmostEqual(normalized_monthly(opt), 158.25, places=2)

    def test_quarterly_divided_by_3(self):
        opt = {"cycle": "quarterly", "price": {"amount": 300, "currency": "CNY"}}
        self.assertEqual(normalized_monthly(opt), 100.0)

    def test_unknown_amount_returns_none(self):
        opt = {"cycle": "yearly", "amount_status": "unknown"}
        self.assertIsNone(normalized_monthly(opt))


class PricePerCreditTest(unittest.TestCase):
    def test_uses_regular_monthly(self):
        tier = {"billing_options": [{"cycle": "monthly", "is_promo": False,
                                     "price": {"amount": 199, "currency": "CNY"}}],
                "credit_grant": {"amount": 2210, "unit": "积分"}}
        self.assertAlmostEqual(price_per_credit(tier), 0.090, places=3)

    def test_ignores_promo_price(self):
        # 基础会员:续费常规 ¥69 + 首月促销 ¥41 → 必须用 69,不许用 41
        tier = {"billing_options": [
                    {"cycle": "monthly", "is_promo": False, "price": {"amount": 69, "currency": "CNY"}},
                    {"cycle": "monthly", "is_promo": True, "price": {"amount": 41, "currency": "CNY"}}],
                "credit_grant": {"amount": 725, "unit": "积分"}}
        self.assertAlmostEqual(price_per_credit(tier), 0.0952, places=4)

    def test_adjustable_grant_uses_default(self):
        tier = {"billing_options": [{"cycle": "monthly", "is_promo": False,
                                     "price": {"amount": 998, "currency": "CNY"}}],
                "credit_grant": {"adjustable": True, "default": 12320,
                                 "options": [6200, 12320, 18500, 27700], "unit": "积分"}}
        self.assertAlmostEqual(price_per_credit(tier), 0.081, places=3)

    def test_no_credit_grant_returns_none(self):
        # 席位订阅产品(如 Cursor):无积分概念 → 不算元每积分
        tier = {"billing_options": [{"cycle": "monthly", "is_promo": False,
                                     "price": {"amount": 144, "currency": "USD"}}]}
        self.assertIsNone(price_per_credit(tier))

    def test_no_regular_price_returns_none(self):
        # 只有促销价、无续费常规价 → 无法确立 basis → None
        tier = {"billing_options": [{"cycle": "monthly", "is_promo": True,
                                     "price": {"amount": 41, "currency": "CNY"}}],
                "credit_grant": {"amount": 725, "unit": "积分"}}
        self.assertIsNone(price_per_credit(tier))


class UnitCostTest(unittest.TestCase):
    def setUp(self):
        self.std = {"billing_options": [{"cycle": "monthly", "is_promo": False,
                                         "price": {"amount": 199, "currency": "CNY"}}],
                    "credit_grant": {"amount": 2210, "unit": "积分"}}

    def test_image_per_output(self):
        consumption = {"capability": "image", "credits_per_output": 1}
        self.assertAlmostEqual(unit_cost(self.std, consumption), 0.090, places=3)

    def test_video_per_second(self):
        consumption = {"capability": "video", "credits_per_unit": 8}
        self.assertAlmostEqual(unit_cost(self.std, consumption), 0.72, places=2)

    def test_missing_credit_grant_returns_none(self):
        seat = {"billing_options": [{"cycle": "monthly", "is_promo": False,
                                     "price": {"amount": 144, "currency": "USD"}}]}
        self.assertIsNone(unit_cost(seat, {"capability": "image", "credits_per_output": 1}))

    def test_missing_consumption_rate_returns_none(self):
        # 即梦缺「积分→张图」消耗率的场景:有元每积分,但无耗分 → 诚实 None
        self.assertIsNone(unit_cost(self.std, {"capability": "image"}))


class ArchetypeTest(unittest.TestCase):
    def test_three_layer_credit_product(self):
        # 即梦/可灵:Freemium + Subscription + Credits 三层全开
        structure = {"layers": [
            {"mechanism": "freemium", "present": True},
            {"mechanism": "subscription", "present": True},
            {"mechanism": "credits", "present": True}]}
        self.assertEqual(derive_archetype(structure), "Freemium + Subscription + Credits")

    def test_seat_subscription_product(self):
        # Cursor/Notion:只填免费 + 订阅,credits 层缺席
        structure = {"layers": [
            {"mechanism": "freemium", "present": True},
            {"mechanism": "subscription", "present": True, "unit": "seat"},
            {"mechanism": "credits", "present": False}]}
        self.assertEqual(derive_archetype(structure), "Freemium + Subscription")

    def test_empty_is_unknown(self):
        self.assertEqual(derive_archetype({"layers": []}), "Unknown")


class ComparisonAxisTest(unittest.TestCase):
    def test_credit_product_uses_unit_cost(self):
        structure = {"layers": [
            {"mechanism": "subscription", "present": True},
            {"mechanism": "credits", "present": True}]}
        self.assertEqual(comparison_axis(structure), "unit_cost")

    def test_seat_product_uses_per_seat(self):
        structure = {"layers": [
            {"mechanism": "subscription", "present": True, "unit": "seat"}]}
        self.assertEqual(comparison_axis(structure), "per_seat")

    def test_flat_tier_product_uses_per_tier_monthly(self):
        structure = {"layers": [{"mechanism": "subscription", "present": True}]}
        self.assertEqual(comparison_axis(structure), "per_tier_monthly")


class ComputeProductTest(unittest.TestCase):
    """端到端:整份即梦模型 → archetype + 比较轴 + 各档单位成本(真实数据验收)。"""

    def setUp(self):
        self.jimeng = {
            "product": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True}]},
            "tiers": [
                {"tier_name": "基础会员",
                 "billing_options": [{"cycle": "monthly", "is_promo": False, "price": {"amount": 69, "currency": "CNY"}},
                                     {"cycle": "monthly", "is_promo": True, "price": {"amount": 41, "currency": "CNY"}}],
                 "credit_grant": {"amount": 725, "unit": "积分"}},
                {"tier_name": "标准会员",
                 "billing_options": [{"cycle": "monthly", "is_promo": False, "price": {"amount": 199, "currency": "CNY"}}],
                 "credit_grant": {"amount": 2210, "unit": "积分"}},
            ],
            "consumption": [
                {"capability": "image", "credits_per_output": 1},
                {"capability": "video", "credits_per_unit": 8},
            ],
        }

    def test_archetype_and_axis(self):
        out = compute_product(self.jimeng)
        self.assertEqual(out["archetype"], "Freemium + Subscription + Credits")
        self.assertEqual(out["comparison_axis"], "unit_cost")

    def test_per_tier_unit_costs(self):
        out = compute_product(self.jimeng)
        std = next(t for t in out["tiers"] if t["tier_name"] == "标准会员")
        self.assertAlmostEqual(std["price_per_credit"], 0.090, places=3)
        costs = {c["capability"]: c["value"] for c in std["unit_costs"]}
        self.assertAlmostEqual(costs["image"], 0.090, places=3)
        self.assertAlmostEqual(costs["video"], 0.72, places=2)

    def test_base_tier_ignores_promo(self):
        out = compute_product(self.jimeng)
        base = next(t for t in out["tiers"] if t["tier_name"] == "基础会员")
        self.assertAlmostEqual(base["price_per_credit"], 0.0952, places=4)

    def test_seat_product_has_no_unit_costs(self):
        cursor = {"product": "Cursor",
                  "pricing_structure": {"layers": [
                      {"mechanism": "freemium", "present": True},
                      {"mechanism": "subscription", "present": True, "unit": "seat"}]},
                  "tiers": [{"tier_name": "Pro",
                             "billing_options": [{"cycle": "monthly", "is_promo": False,
                                                  "price": {"amount": 20, "currency": "USD"}}]}],
                  "consumption": []}
        out = compute_product(cursor)
        self.assertEqual(out["comparison_axis"], "per_seat")
        pro = out["tiers"][0]
        self.assertIsNone(pro["price_per_credit"])
        self.assertEqual(pro["unit_costs"], [])

    def test_unmodeled_pricing_shape_is_preserved_with_gap(self):
        gpu_auction = {"product": "GPUCloudX",
                       "pricing_structure": {"layers": [
                           {"mechanism": "gpu_hour_auction", "present": True}]},
                       "raw_billing_items": [
                           {"label": "A100 spot auction", "detail": "按实时竞价 GPU 小时计费"}],
                       "tiers": [],
                       "consumption": []}
        out = compute_product(gpu_auction)
        self.assertEqual(out["archetype"], "custom/unknown")
        self.assertEqual(out["comparison_axis"], "unknown")
        self.assertEqual(out["raw_billing_items"], gpu_auction["raw_billing_items"])
        self.assertEqual(out["unit_costs"]["status"], "not_applicable")
        self.assertTrue(any(g.get("kind") == "pricing_shape_unmodeled"
                            for g in out["gaps"]))

    def test_promotions_are_preserved_but_not_used_as_cost_basis(self):
        jimeng = {
            "product": "即梦AI",
            "pricing_structure": {"layers": [
                {"mechanism": "freemium", "present": True},
                {"mechanism": "subscription", "present": True},
                {"mechanism": "credits", "present": True}]},
            "tiers": [{
                "tier_name": "基础会员",
                "billing_options": [{"cycle": "monthly", "is_promo": False,
                                     "price": {"amount": 69, "currency": "CNY"}}],
                "credit_grant": {"amount": 725, "unit": "积分"},
            }],
            "consumption": [{"capability": "video", "credits_per_unit": 8}],
            "promotions": [{
                "kind": "referral_reward",
                "name": "邀请好友送积分",
                "reward": {"amount": 300, "unit": "积分"},
                "condition": "邀请好友注册/完成任务",
                "is_recurring": False,
                "evidence_ids": ["SREF001"],
            }],
        }
        out = compute_product(jimeng)
        self.assertEqual(out["promotions"][0]["kind"], "referral_reward")
        tier = out["tiers"][0]
        # 仍然只用常规订阅价/常规月额度,不把邀请奖励摊进单位成本。
        self.assertAlmostEqual(tier["price_per_credit"], 0.0952, places=4)


if __name__ == "__main__":
    unittest.main()
