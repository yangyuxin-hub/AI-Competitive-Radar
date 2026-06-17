import unittest
from src.feature_model import (
    normalize_status, support_score, depth_norm, evidence_level_score,
)


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
