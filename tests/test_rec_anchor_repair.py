"""R4 锚点修复(repair_rec_anchors)+ source_pricing 第三锚 的回归测试。

覆盖 ai_assistant full 严审跑出的 degraded 根因:定价类建议 R001 锚不到
feature/pain → R4 broken_reasoning_chain。修复后:重锚现有 feature / 定价锚 / 仍断链上报。
"""
import unittest

from src.analyzer import quick_validate_derivations
from src.analyzer_sanitize import repair_rec_anchors
from src.reviewer import check_reasoning_chain


def _facts() -> dict:
    return {
        "feature_tree": {
            "features": [
                {"feature_id": "F001", "name": "基础对话理解", "name_en": "Basic conversational understanding"},
                {"feature_id": "F002", "name": "长上下文处理", "name_en": "Long context processing"},
            ],
        },
        "user_persona": {
            "pain_points": [
                {"pain_id": "P001", "description": "服务不稳定，频繁中断"},
            ],
        },
        "pricing_model": {"products": [{"product": "ChatGPT", "tiers": [{"price": {"normalized_usd_month": 20}}]}]},
    }


def _rec(rid, **kw):
    base = {"rec_id": rid, "source_feature_ids": [], "source_pain_ids": []}
    base.update(kw)
    return base


class TestRepairRecAnchors(unittest.TestCase):
    def test_pricing_rec_gets_source_pricing(self):
        """定价类建议锚不到 feature/pain → 打 source_pricing(R001 的真实场景)。"""
        der = {"recommendations": [
            _rec("R001", title="推出Plus档位年付订阅折扣", action="年付192美元/年比月付优惠20%"),
        ]}
        der, stats = repair_rec_anchors(der, _facts())
        self.assertEqual(stats["pricing_anchored"], 1)
        self.assertTrue(der["recommendations"][0]["source_pricing"])
        self.assertEqual(stats["still_broken"], [])

    def test_reanchor_by_keyword_to_existing_feature(self):
        """rec 文本含 feature 关键词但 LLM 漏填 source_id → 确定性重锚到 F002。"""
        der = {"recommendations": [
            _rec("R002", title="优化长上下文窗口召回", action="对标竞品长上下文体验，目标支持百万 token"),
        ]}
        der, stats = repair_rec_anchors(der, _facts())
        self.assertEqual(stats["reanchored"], 1)
        self.assertIn("F002", der["recommendations"][0]["source_feature_ids"])

    def test_truly_unanchorable_reported(self):
        """既非定价、又匹配不到任何 feature 的 rec → 收进 still_broken,不强行造锚。"""
        der = {"recommendations": [
            _rec("R009", title="开拓东南亚线下渠道", action="设立海外办公室拓展本地合作伙伴"),
        ]}
        der, stats = repair_rec_anchors(der, _facts())
        self.assertEqual(stats["still_broken"], ["R009"])
        self.assertFalse(der["recommendations"][0].get("source_pricing"))

    def test_already_anchored_skipped_idempotent(self):
        """已有有效锚的 rec 不动;重复调用幂等。"""
        der = {"recommendations": [_rec("R003", source_feature_ids=["F001"])]}
        der, stats1 = repair_rec_anchors(der, _facts())
        der, stats2 = repair_rec_anchors(der, _facts())
        self.assertEqual(stats1["reanchored"] + stats1["pricing_anchored"], 0)
        self.assertEqual(stats2["still_broken"], [])
        self.assertEqual(der["recommendations"][0]["source_feature_ids"], ["F001"])

    def test_pricing_anchor_needs_pricing_model(self):
        """无 pricing_model 数据时,定价类建议不能凭空打定价锚 → 仍断链。"""
        facts = _facts()
        facts["pricing_model"] = {"products": []}
        der = {"recommendations": [_rec("R001", title="推出年付订阅折扣")]}
        der, stats = repair_rec_anchors(der, facts)
        self.assertEqual(stats["pricing_anchored"], 0)
        self.assertEqual(stats["still_broken"], ["R001"])


class TestSourcePricingAcceptedAsAnchor(unittest.TestCase):
    """R4(reviewer)与 quick_validate_derivations 都必须把 source_pricing 当合法锚。"""

    def _schema(self):
        return {
            **_facts(),
            "swot": {"strengths": [{"point": "x", "evidence_ids": []}]},
            "recommendations": [
                _rec("R001", title="年付订阅折扣", source_pricing=True),
                _rec("R002", source_feature_ids=["F002"]),
            ],
        }

    def test_reviewer_r4_accepts_source_pricing(self):
        issues = check_reasoning_chain(self._schema(), [])
        r4_rec = [i for i in issues if i.get("location", "").startswith("recommendations.R")]
        self.assertEqual(r4_rec, [], f"source_pricing 应被接受为合法锚,但仍报 R4: {r4_rec}")

    def test_quick_validate_accepts_source_pricing(self):
        facts = _facts()
        der = {
            "swot": {"strengths": [{"point": "x", "evidence_ids": []}]},
            "recommendations": [_rec("R001", title="年付订阅折扣", source_pricing=True)],
        }
        issues = quick_validate_derivations(der, facts, [])
        anchor_issues = [m for m in issues if "未引用任何有效" in m]
        self.assertEqual(anchor_issues, [])


if __name__ == "__main__":
    unittest.main()
