"""R9/R10 writer 自检规则测试 — src/reviewer.py"""
import unittest

from src.reviewer import check_report_chip_traceability, check_report_no_score_leak
from src.writer import _render_data_availability, _render_feature_gaps, _render_pricing

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


class PricingEngineRenderTest(unittest.TestCase):
    def test_pricing_engine_summary_and_unit_costs_render(self):
        pricing = {"products": [{
            "name": "即梦AI",
            "tiers": [{"tier_name": "标准会员",
                       "price": {"amount": 199, "currency": "CNY", "normalized_usd_month": None},
                       "evidence_ids": ["S1234ABC"]}],
            "pricing_engine": {
                "archetype": "Freemium + Subscription + Credits",
                "comparison_axis": "unit_cost",
                "tiers": [{"tier_name": "标准会员", "price_per_credit": 0.09,
                           "unit_costs": [
                               {"capability": "image", "value": 0.09, "unit": "积分/张"},
                               {"capability": "video", "value": 0.72, "unit": "积分/秒"},
                           ]}],
            },
        }], "engine_comparison": {
            "insights": ["同为积分制产品时,可灵AI的最低积分单价最低,约 0.0833 / 积分。"],
            "gaps": [{"kind": "unit_cost_axis_mismatch",
                      "note": "各产品的单位成本口径不同,不能直接比较"}],
        }, "pricing_strategy_analysis": {
            "pricing_model_analysis": {
                "summary": "定价应拆成免费获客、会员转化、积分增购三层来看。",
                "products": [{
                    "product": "即梦AI",
                    "archetype": "Freemium + Subscription + Credits",
                    "conversion_levers": ["免费额度/免费档降低试用门槛", "积分消耗把高频使用转成持续增购"],
                    "entitlement_design": {
                        "free_layer": ["用免费额度/免费档承接尝鲜用户"],
                        "subscription_entitlements": ["按会员档位打包月度额度"],
                        "premium_capabilities": ["高阶模型", "高清输出", "首尾帧/运镜控制"],
                        "topup_rules": ["积分/灵感值用完后的增购与消耗规则是复购关键"],
                        "promotional_rewards": ["邀请好友送额度(300积分)"],
                        "conversion_trigger": "免费额度不足、去水印/高清/高阶控制需求、积分耗尽",
                    },
                    "monetization_hypothesis": {
                        "target_paid_user": "持续生成图片/视频的创作者",
                        "upsell_path": "免费试用 -> 会员订阅 -> 高额度档位/积分增购",
                        "retention_mechanism": "月度额度和会员权益形成持续使用理由",
                        "revenue_expansion": "高频生成通过积分消耗带来二次变现",
                    },
                    "business_logic": "用免费额度拉新,用会员权益转化,再用积分消耗承接重度创作需求。",
                }],
            },
            "value_for_money_analysis": {
                "scenario_baskets": [{
                    "scenario": "稳定短视频产出",
                    "monthly_budget": "100-300 CNY",
                    "expected_outputs": "每月 30-100 条标准短视频",
                    "required_capabilities": ["标准视频消耗率", "生成成功率"],
                    "decision_basis": "看主力会员每月额度、积分单价、标准视频消耗率",
                    "best_for": "可灵AI在额度/积分单价口径上更低,但不能等同于同规格视频成本优势",
                }],
                "caveats": ["单位不同:image / standard_video / video_second 不能直接换算"],
            },
        }}

        md = _render_pricing(pricing, {"features": []}, ["即梦AI"])

        self.assertIn("单位成本归一化", md)
        self.assertNotIn("Freemium + Subscription + Credits", md)
        self.assertIn("标准会员", md)
        self.assertIn("0.09", md)
        self.assertIn("0.72", md)
        self.assertIn("¥", md)
        self.assertIn("最低积分单价", md)
        self.assertIn("不能直接比较", md)
        self.assertIn("价格与性价比:场景判断", md)
        self.assertIn("稳定短视频产出", md)
        self.assertIn("100-300 CNY", md)
        self.assertIn("标准视频消耗率", md)
        self.assertIn("不能等同", md)
        self.assertNotIn("未抓到付费档价格数值", md)


class WriterRobustnessTest(unittest.TestCase):
    def test_numeric_quality_score_does_not_crash(self):
        feature_tree = {
            "category": "图像生成质量",
            "features": [{
                "feature_id": "F001",
                "name": "细节还原",
                "products": {
                    "Midjourney": {
                        "support_status": "supported",
                        "support_evidence_ids": ["S1234ABC"],
                        "quality_score": 4.0,
                    }
                },
                "gap": {"winner": "Midjourney", "evidence_ids": ["S1234ABC"]},
            }],
        }

        md = _render_feature_gaps(feature_tree, _EV)

        self.assertIn("Midjourney", md)
        self.assertIn("4/5", md)


if __name__ == "__main__":
    unittest.main()
