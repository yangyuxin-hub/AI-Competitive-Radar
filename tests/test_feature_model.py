import unittest
from src.feature_model import (
    normalize_status, support_score, depth_norm, evidence_level_score,
    domain_coverage, weighted_coverage, feature_winner,
)


def _leaf(name, **pdata):
    return {"id": name, "name": name, "products": pdata}


def _p(status, depth=None, lvl="official", diff=False, refs=("S1234567",)):
    return {"support_status": status, "depth_score": depth, "evidence_level": lvl,
            "differentiator": diff, "source_refs": list(refs)}


TREE = {
    "feature_weight_version": "demo-v1",
    "domains": [
        {"id": "A", "name": "视频生成", "weight": 0.6, "role": "core", "modules": [
            {"id": "A1", "name": "生成", "points": [
                _leaf("文生视频", Jimeng=_p("supported", 3), Kling=_p("supported", 4)),
                _leaf("首尾帧", Jimeng=_p("unknown"), Kling=_p("supported", 4)),
            ]},
        ]},
        {"id": "B", "name": "可控性", "weight": 0.4, "role": "core", "modules": [
            {"id": "B1", "name": "运镜", "points": [
                _leaf("运镜控制", Jimeng=_p("partial", 2), Kling=_p("supported", 3)),
            ]},
        ]},
    ],
}


class AtomTest(unittest.TestCase):
    def test_normalize_legacy_status(self):
        self.assertEqual(normalize_status("partially_supported"), "partial")
        self.assertEqual(normalize_status("not_supported"), "unsupported")
        self.assertEqual(normalize_status("supported"), "supported")
        self.assertEqual(normalize_status("garbage"), "unknown")

    def test_support_score_excludes_unknown(self):
        self.assertEqual(support_score("supported"), 1.0)
        self.assertEqual(support_score("partial"), 0.5)
        self.assertEqual(support_score("unsupported"), 0.0)
        self.assertIsNone(support_score("unknown"))

    def test_depth_norm(self):
        self.assertAlmostEqual(depth_norm(4), 0.8)
        self.assertIsNone(depth_norm(None))
        self.assertIsNone(depth_norm(0))
        self.assertIsNone(depth_norm(6))

    def test_evidence_level_score(self):
        self.assertEqual(evidence_level_score("official"), 1.0)
        self.assertEqual(evidence_level_score("third_party"), 0.8)
        self.assertEqual(evidence_level_score("user_review"), 0.6)
        self.assertEqual(evidence_level_score("inferred"), 0.3)
        self.assertEqual(evidence_level_score("anything_else"), 0.3)


class CoverageTest(unittest.TestCase):
    def test_domain_coverage_excludes_unknown(self):
        # 域A Jimeng:文生视频 supported(1),首尾帧 unknown(排除) → score=1.0,evidence_rate=1/2
        cov = domain_coverage(TREE["domains"][0], "Jimeng")
        self.assertAlmostEqual(cov["score"], 1.0)
        self.assertAlmostEqual(cov["evidence_rate"], 0.5)
        self.assertEqual((cov["known"], cov["total"]), (1, 2))

    def test_all_unknown_domain_score_is_none(self):
        d = {"id": "Z", "name": "z", "weight": 1.0, "modules": [
            {"id": "Z1", "name": "z", "points": [_leaf("x", Jimeng=_p("unknown"))]}]}
        cov = domain_coverage(d, "Jimeng")
        self.assertIsNone(cov["score"])
        self.assertEqual(cov["evidence_rate"], 0.0)

    def test_weighted_coverage_known_only(self):
        # Jimeng: 域A score=1.0(w0.6), 域B score=partial0.5(w0.4)
        #   known_only = (0.6*1.0 + 0.4*0.5)/(0.6+0.4) = 0.8
        out = weighted_coverage(TREE, "Jimeng")
        self.assertAlmostEqual(out["coverage_known_only"], 0.8, places=4)
        # evidence: 域A 1/2 项有据(w0.6), 域B 1/1(w0.4) → (0.6*0.5 + 0.4*1.0)/1.0 = 0.7
        self.assertAlmostEqual(out["evidence_coverage_rate"], 0.7, places=4)

    def test_unknown_domain_excluded_from_known_only_denominator(self):
        tree2 = {"domains": [
            {"id": "A", "name": "a", "weight": 0.5, "modules": [
                {"id": "A1", "name": "a", "points": [_leaf("x", P=_p("supported", 3))]}]},
            {"id": "B", "name": "b", "weight": 0.5, "modules": [
                {"id": "B1", "name": "b", "points": [_leaf("y", P=_p("unknown"))]}]},
        ]}
        out = weighted_coverage(tree2, "P")
        # 域B 全 unknown → 不进 known_only 分母 → coverage=1.0; evidence=(0.5*1+0.5*0)/1=0.5
        self.assertAlmostEqual(out["coverage_known_only"], 1.0, places=4)
        self.assertAlmostEqual(out["evidence_coverage_rate"], 0.5, places=4)


class WinnerTest(unittest.TestCase):
    def test_clear_winner_with_depth(self):
        pt = _leaf("运镜", Jimeng=_p("partial", 2), Runway=_p("supported", 5))
        out = feature_winner(pt, ["Jimeng", "Runway"])
        self.assertEqual(out["winner"], "Runway")
        self.assertEqual(out["confidence"], "high")

    def test_no_depth_anywhere_forces_tie(self):
        # 全靠 support_status,无任何 depth → 不允许强判
        pt = _leaf("文生视频", Jimeng=_p("supported", None), Kling=_p("supported", None))
        out = feature_winner(pt, ["Jimeng", "Kling"])
        self.assertIn(out["winner"], ("tie", "unclear"))
        self.assertEqual(out["confidence"], "low")

    def test_all_unknown_is_unclear(self):
        pt = _leaf("x", Jimeng=_p("unknown"), Kling=_p("unknown"))
        out = feature_winner(pt, ["Jimeng", "Kling"])
        self.assertEqual(out["winner"], "unclear")

    def test_close_scores_is_tie(self):
        pt = _leaf("x", Jimeng=_p("supported", 4), Kling=_p("supported", 4))
        out = feature_winner(pt, ["Jimeng", "Kling"])
        self.assertEqual(out["winner"], "tie")
