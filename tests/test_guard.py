"""M2 Guard 测试(design-v3-draft §六 M2 验收):

1. G1 强对比对账:winner 与次优各 ≥1 条 quality/pain 证据,否则降级安全措辞
2. G2 basis 对账:声称"仅确认具备"需 ≥1 条 feature_existence 证据,否则改 unknown
3. apply 幂等:二次套用零变化(确定性、纯函数)
"""
import copy
import unittest

from src.guard import apply, enforce_basis_support, enforce_comparison_evidence


def _ev(eid, product, ct):
    return {"evidence_id": eid, "product": product, "claim_type": ct,
            "extracted_snippet": f"{product} {ct} evidence", "claim": "c"}


def _schema(gap, alpha_q_ids, beta_q_ids, alpha_basis="第三方实测延迟低",
            alpha_support_ids=("SAAA0001",)):
    return {
        "feature_tree": {"features": [{
            "feature_id": "F001", "name": "Tab 补全",
            "products": {
                "Alpha": {
                    "support_status": "supported",
                    "support_evidence_ids": list(alpha_support_ids),
                    "quality_score": {"score": 4, "scale": 5, "basis": alpha_basis,
                                      "evidence_ids": list(alpha_q_ids)},
                },
                "Beta": {
                    "support_status": "supported",
                    "support_evidence_ids": [],
                    "quality_score": {"score": 2, "scale": 5, "basis": "评价一般",
                                      "evidence_ids": list(beta_q_ids)},
                },
            },
            "gap": dict(gap),
        }]},
    }


POOL = [
    _ev("SAAA0001", "Alpha", "feature_existence"),
    _ev("SQQQ0001", "Alpha", "performance_quality"),
    _ev("SQQQ0002", "Beta", "user_pain"),
]

STRONG_GAP = {"winner": "Alpha", "gap_type": "performance",
              "reason": "Alpha 体验明显更优", "evidence_ids": ["SQQQ0001"], "confidence": 0.6}


class ComparisonEvidenceTest(unittest.TestCase):
    def test_keeps_winner_when_both_sides_have_quality_evidence(self):
        schema = _schema(STRONG_GAP, ["SQQQ0001"], ["SQQQ0002"])
        self.assertEqual(enforce_comparison_evidence(schema, POOL), 0)
        self.assertEqual(schema["feature_tree"]["features"][0]["gap"]["winner"], "Alpha")

    def test_downgrades_when_runner_up_has_no_quality_evidence(self):
        schema = _schema(STRONG_GAP, ["SQQQ0001"], [])
        self.assertEqual(enforce_comparison_evidence(schema, POOL), 1)
        gap = schema["feature_tree"]["features"][0]["gap"]
        self.assertEqual(gap["winner"], "unknown")
        self.assertEqual(gap["gap_type"], "insufficient_evidence")
        self.assertIn("暂不作强对比", gap["reason"])

    def test_downgrades_when_winner_cites_only_feature_evidence(self):
        """winner 的质量分引用全是 feature_existence(非 quality/pain)→ 同样降级。"""
        schema = _schema(STRONG_GAP, ["SAAA0001"], ["SQQQ0002"])
        self.assertEqual(enforce_comparison_evidence(schema, POOL), 1)
        gap = schema["feature_tree"]["features"][0]["gap"]
        self.assertEqual(gap["winner"], "unknown")

    def test_weak_gap_types_untouched(self):
        weak = {"winner": "Alpha", "gap_type": "insufficient_evidence",
                "reason": "已是安全措辞", "evidence_ids": [], "confidence": 0.3}
        schema = _schema(weak, [], [])
        self.assertEqual(enforce_comparison_evidence(schema, POOL), 0)


class BasisSupportTest(unittest.TestCase):
    def test_only_confirmed_claim_without_feature_evidence_goes_unknown(self):
        schema = _schema(STRONG_GAP, ["SQQQ0001"], ["SQQQ0002"],
                         alpha_basis="官网功能页确认具备该能力，但无用户质量证据",
                         alpha_support_ids=())
        self.assertEqual(enforce_basis_support(schema, POOL), 1)
        pdata = schema["feature_tree"]["features"][0]["products"]["Alpha"]
        self.assertEqual(pdata["support_status"], "unknown")
        self.assertEqual(pdata["quality_score"]["score"], 0)
        self.assertNotIn("确认具备", pdata["quality_score"]["basis"])

    def test_only_confirmed_claim_with_feature_evidence_kept(self):
        schema = _schema(STRONG_GAP, ["SQQQ0001"], ["SQQQ0002"],
                         alpha_basis="仅确认具备，无质量证据",
                         alpha_support_ids=("SAAA0001",))
        self.assertEqual(enforce_basis_support(schema, POOL), 0)

    def test_normal_basis_untouched(self):
        schema = _schema(STRONG_GAP, ["SQQQ0001"], ["SQQQ0002"])
        self.assertEqual(enforce_basis_support(schema, POOL), 0)


class ApplyIdempotencyTest(unittest.TestCase):
    def test_second_apply_is_noop(self):
        schema = _schema(STRONG_GAP, ["SQQQ0001"], [],
                         alpha_basis="大量用户反馈官网确认具备该能力",
                         alpha_support_ids=())
        # 混入一条幻觉引用,验证 dropped 也幂等
        schema["feature_tree"]["features"][0]["gap"]["evidence_ids"].append("SFAKE999")
        out1, rep1 = apply(copy.deepcopy(schema), POOL)
        self.assertGreater(rep1["changes_total"], 0)
        out2, rep2 = apply(copy.deepcopy(out1), POOL)
        self.assertEqual(rep2["changes_total"], 0, f"二次套用必须零变化: {rep2}")
        self.assertEqual(out1, out2)


if __name__ == "__main__":
    unittest.main()
