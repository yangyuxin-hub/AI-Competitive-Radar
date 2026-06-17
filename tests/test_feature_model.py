import unittest
from src.feature_model import (
    normalize_status, support_score, depth_norm, evidence_level_score,
    domain_coverage, weighted_coverage, feature_winner,
    moat_candidates, whitespace_opportunities,
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


class DiffMatrixTest(unittest.TestCase):
    def test_only_one_product_supported_is_differentiator(self):
        from src.feature_model import differentiation_matrix
        tree = {"domains": [{"id": "A", "name": "a", "weight": 1.0, "modules": [
            {"id": "A1", "name": "a", "points": [
                _leaf("多镜头", Jimeng=_p("unsupported"), Kling=_p("partial"),
                      Runway=_p("supported", 4, diff=True)),
            ]}]}]}
        rows = differentiation_matrix(tree, ["Jimeng", "Kling", "Runway"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product"], "Runway")
        self.assertIn("样本内 3 个产品中", rows[0]["note"])
        self.assertNotIn("独占", rows[0]["note"])


class ArchetypeTest(unittest.TestCase):
    def test_broad_and_strong_is_allrounder(self):
        from src.feature_model import product_archetype
        tree = {"domains": [
            {"id": "A", "name": "a", "weight": 0.5, "modules": [{"id": "A1", "name": "a",
                "points": [_leaf("x", P=_p("supported", 5))]}]},
            {"id": "B", "name": "b", "weight": 0.5, "modules": [{"id": "B1", "name": "b",
                "points": [_leaf("y", P=_p("supported", 4))]}]},
        ]}
        self.assertEqual(product_archetype(tree, "P"), "全能型")

    def test_narrow_strong_is_specialist(self):
        from src.feature_model import product_archetype
        tree = {"domains": [
            {"id": "A", "name": "a", "weight": 0.5, "role": "core", "modules": [{"id": "A1",
                "name": "a", "points": [_leaf("x", P=_p("supported", 5))]}]},
            {"id": "B", "name": "b", "weight": 0.5, "role": "core", "modules": [{"id": "B1",
                "name": "b", "points": [_leaf("y", P=_p("unsupported"))]}]},
        ]}
        self.assertEqual(product_archetype(tree, "P"), "专精型")

    def test_all_unknown_is_insufficient(self):
        from src.feature_model import product_archetype
        tree = {"domains": [{"id": "A", "name": "a", "weight": 1.0, "modules": [{"id": "A1",
            "name": "a", "points": [_leaf("x", P=_p("unknown"))]}]}]}
        self.assertEqual(product_archetype(tree, "P"), "数据不足")


class MoatTest(unittest.TestCase):
    def _tree(self):
        return {"domains": [{"id": "A", "name": "可控性", "weight": 0.3, "modules": [
            {"id": "A1", "name": "运镜", "points": [
                {"id": "运镜控制", "name": "运镜控制", "moat_factors": ["专业用户沉淀"],
                 "products": {"Runway": _p("supported", 5, diff=True),
                              "Jimeng": _p("partial", 2)}},
            ]}]}]}

    def test_moat_needs_factor(self):
        out = moat_candidates(self._tree(), "Runway")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["confidence"], "high")
        self.assertIn("专业用户沉淀", out[0]["factors"])

    def test_strong_feature_without_factor_is_low_confidence(self):
        tree = self._tree()
        tree["domains"][0]["modules"][0]["points"][0]["moat_factors"] = []
        out = moat_candidates(tree, "Runway")  # 仍是候选,但需补难复制因素
        self.assertEqual(out[0]["confidence"], "low")
        self.assertIn("难复制", out[0]["note"])

    def test_migration_cost_adds_factor(self):
        tree = self._tree()
        tree["domains"][0]["modules"][0]["points"][0]["moat_factors"] = []
        out = moat_candidates(tree, "Runway", migration_cost="high")
        self.assertIn("用户习惯迁移成本", out[0]["factors"])

    def test_low_depth_is_not_moat(self):
        tree = self._tree()
        tree["domains"][0]["modules"][0]["points"][0]["products"]["Runway"]["depth_score"] = 2
        self.assertEqual(moat_candidates(tree, "Runway"), [])


class WhitespaceTest(unittest.TestCase):
    def test_high_weight_unmet_need_is_whitespace(self):
        tree = {"domains": [{"id": "A", "name": "可控性", "weight": 0.3, "modules": [
            {"id": "A1", "name": "多镜头", "points": [
                {"id": "多镜头小白化", "name": "多镜头小白化", "barrier": "现有方案学习成本高",
                 "products": {"Jimeng": _p("unsupported"), "Kling": _p("partial", 2)}},
            ]}]}]}
        out = whitespace_opportunities(tree, ["Jimeng", "Kling"])
        self.assertEqual(len(out), 1)
        self.assertIn("学习成本", out[0]["barrier"])

    def test_well_covered_need_is_not_whitespace(self):
        tree = {"domains": [{"id": "A", "name": "a", "weight": 0.3, "modules": [
            {"id": "A1", "name": "x", "points": [
                {"id": "x", "name": "x", "products": {"P": _p("supported", 4)}}]}]}]}
        self.assertEqual(whitespace_opportunities(tree, ["P"]), [])


class ComputeFeatureAnalysisTest(unittest.TestCase):
    def test_end_to_end_shape(self):
        from src.feature_model import compute_feature_analysis
        out = compute_feature_analysis(TREE, ["Jimeng", "Kling"], target="Jimeng")
        self.assertEqual(out["feature_weight_version"], "demo-v1")
        self.assertIn("Jimeng", out["coverage"])
        self.assertIn("Kling", out["coverage"])
        self.assertTrue(all("winner" in w for w in out["winners"]))
        self.assertIn("Jimeng", out["archetypes"])
        # 同输入恒同输出
        out2 = compute_feature_analysis(TREE, ["Jimeng", "Kling"], target="Jimeng")
        self.assertEqual(out, out2)
