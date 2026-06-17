import unittest
from src.analyzer import _build_feature_skeleton, _normalize_leaf


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
