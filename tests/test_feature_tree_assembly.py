import unittest
from src.analyzer import (
    _apply_feature_engine,
    _build_feature_skeleton,
    _compute_gap,
    _normalize_leaf,
    _standard_ai_coding_feature_spine,
)


class NormalizeLeafTest(unittest.TestCase):
    def test_quality_score_maps_to_depth(self):
        out = _normalize_leaf({"support_status": "partially_supported",
                               "support_evidence_ids": ["S1234567"],
                               "quality_score": {"score": 4, "scale": 5}})
        self.assertEqual(out["support_status"], "partial")
        self.assertEqual(out["depth_score"], 4)
        self.assertEqual(out["evidence_level"], "official")

    def test_zero_score_becomes_null_depth(self):
        out = _normalize_leaf({"support_status": "supported",
                               "support_evidence_ids": [],
                               "quality_score": {"score": 0, "scale": 5}})
        self.assertIsNone(out["depth_score"])
        self.assertEqual(out["evidence_level"], "inferred")
        self.assertFalse(out["differentiator"])

    def test_new_leaf_fields_take_precedence_over_quality_score_fallback(self):
        out = _normalize_leaf({"support_status": "supported",
                               "support_evidence_ids": ["S1234567"],
                               "depth_score": 5,
                               "evidence_level": "third_party",
                               "differentiator": True,
                               "quality_score": {"score": 2, "scale": 5}})
        self.assertEqual(out["depth_score"], 5)
        self.assertEqual(out["evidence_level"], "third_party")
        self.assertTrue(out["differentiator"])

    def test_quality_refs_become_source_refs_for_depth_honesty(self):
        out = _normalize_leaf({"support_status": "supported",
                               "support_evidence_ids": [],
                               "depth_score": 3,
                               "evidence_level": "user_review",
                               "quality_score": {"score": 3, "scale": 5,
                                                 "evidence_ids": ["S7654321"]}})
        self.assertEqual(out["depth_score"], 3)
        self.assertEqual(out["source_refs"], ["S7654321"])

    def test_numeric_quality_score_maps_to_depth(self):
        out = _normalize_leaf({"support_status": "supported",
                               "support_evidence_ids": ["S1234567"],
                               "quality_score": 4.0})
        self.assertEqual(out["depth_score"], 4)

    def test_depth_score_without_refs_is_cleared(self):
        out = _normalize_leaf({"support_status": "supported",
                               "support_evidence_ids": [],
                               "depth_score": 3,
                               "evidence_level": "official",
                               "quality_score": {"score": 0, "scale": 5,
                                                 "evidence_ids": []}})
        self.assertIsNone(out["depth_score"])
        self.assertEqual(out["source_refs"], [])


class GapComputationRobustnessTest(unittest.TestCase):
    def test_numeric_quality_score_supported_in_gap_computation(self):
        gap = _compute_gap(
            "细节还原",
            {
                "Midjourney": {
                    "support_status": "supported",
                    "support_evidence_ids": ["SAAA1111"],
                    "quality_score": 4.0,
                },
                "Nano Banana": {
                    "support_status": "supported",
                    "support_evidence_ids": ["SBBB2222"],
                    "quality_score": 3.0,
                },
            },
            {"target_product": "Midjourney"},
        )

        self.assertEqual(gap["winner"], "Midjourney")


class SkeletonTest(unittest.TestCase):
    def test_wraps_flat_features_into_domains(self):
        meta = {"analysis_focus": ["视频生成质量与可控性"]}
        flat = [{"feature_id": "F001", "name": "文生视频"},
                {"feature_id": "F002", "name": "运镜控制"}]
        tree = _build_feature_skeleton(meta, flat)
        self.assertTrue(tree["domains"])
        self.assertEqual(tree["feature_weight_version"], "demo-v1")
        # 所有 flat feature 都落进了某个 domain 的某个 module 的 points
        ids = [pt["id"] for d in tree["domains"] for m in d["modules"] for pt in m["points"]]
        self.assertCountEqual(ids, ["F001", "F002"])

    def test_meta_feature_weights_take_precedence_over_config(self):
        # Phase 7 intake 产出的权重优先于 config 种子
        meta = {"analysis_focus": ["视频生成质量与可控性"],
                "feature_weights": [
                    {"id": "X", "name": "自适应域", "weight": 1.0, "role": "core"}],
                "feature_weight_version": "intake-proposed"}
        tree = _build_feature_skeleton(meta, [{"feature_id": "F001", "name": "x"}])
        self.assertEqual([d["id"] for d in tree["domains"]], ["X"])

    def test_ai_coding_standard_spine_uses_developer_workflow(self):
        meta = {"analysis_focus": ["代码补全体验"], "target_product": "Cursor",
                "competitors": ["GitHubCopilot", "Cline"]}
        spine = _standard_ai_coding_feature_spine(meta)
        self.assertIsNotNone(spine)
        self.assertEqual([f["name"] for f in spine[:6]], [
            "代码理解", "代码生成", "代码修改与重构", "Agent 自动执行", "调试与测试", "上下文管理"
        ])
        self.assertEqual(len(spine), 10)
        self.assertEqual(spine[0]["source_skill"], "ai_coding")

    def test_ai_coding_skeleton_maps_features_to_matching_domains(self):
        meta = {"analysis_focus": ["代码补全体验"]}
        flat = [
            {"feature_id": "F001", "name": "代码理解"},
            {"feature_id": "F002", "name": "代码生成"},
            {"feature_id": "F009", "name": "安全权限"},
        ]
        tree = _build_feature_skeleton(meta, flat)
        by_domain = {
            d["name"]: [pt["name"] for m in d["modules"] for pt in m["points"]]
            for d in tree["domains"]
        }
        self.assertIn("代码理解", by_domain["代码理解"])
        self.assertIn("代码生成", by_domain["代码生成"])
        self.assertIn("安全权限", by_domain["安全权限"])
        self.assertEqual(tree["source_skill"], "ai_coding")
        self.assertEqual(tree["generation_mode"], "skill_llm_hybrid_ready")

    def test_unknown_focus_uses_dynamic_domains_not_video_default(self):
        meta = {"analysis_focus": ["知识库协作效率"], "target_product": "Notion",
                "competitors": ["Coda"]}
        flat = [
            {"feature_id": "F001", "name": "知识组织"},
            {"feature_id": "F002", "name": "协作权限"},
        ]
        tree = _build_feature_skeleton(meta, flat)
        self.assertEqual([d["name"] for d in tree["domains"]], ["知识组织", "协作权限"])
        self.assertEqual(tree["feature_weight_version"], "llm_dynamic@unversioned")


class ApplyFeatureEngineTest(unittest.TestCase):
    def test_attaches_tree_and_analysis(self):
        facts = {"feature_tree": {"features": [
            {"feature_id": "F001", "name": "文生视频", "products": {
                "Jimeng": {"support_status": "supported",
                           "support_evidence_ids": ["S1234567"],
                           "quality_score": {"score": 3, "scale": 5}},
                "Kling": {"support_status": "supported",
                          "support_evidence_ids": ["S7654321"],
                          "quality_score": {"score": 4, "scale": 5}}}},
        ]}}
        meta = {"analysis_focus": ["视频生成质量与可控性"],
                "target_product": "Jimeng", "competitors": ["Kling"]}
        n = _apply_feature_engine(facts, meta)
        self.assertEqual(n, 1)
        ft = facts["feature_tree"]
        self.assertIn("tree", ft)
        self.assertIn("analysis", ft)
        self.assertIn("Jimeng", ft["analysis"]["coverage"])
        # 叶子已是新 schema
        pt = ft["tree"]["domains"][0]["modules"][0]["points"][0]
        self.assertEqual(pt["products"]["Kling"]["depth_score"], 4)
