import unittest

from src.reviewer import (
    check_differentiator_wording,
    check_feature_depth_honesty,
    check_winner_conservatism,
)


def _tree_leaf(depth, refs):
    return {"feature_tree": {"tree": {"domains": [{"id": "A", "name": "a", "weight": 1.0,
        "modules": [{"id": "A1", "name": "a", "points": [
            {"id": "F1", "name": "x", "products": {
                "P": {"support_status": "supported", "depth_score": depth,
                      "evidence_level": "official", "differentiator": False,
                      "source_refs": refs}}}]}]}]}}}


class DepthHonestyTest(unittest.TestCase):
    def test_depth_without_refs_is_error(self):
        issues = check_feature_depth_honesty(_tree_leaf(4, []), [])
        self.assertTrue(any(i["rule"] == "R11" for i in issues))

    def test_depth_with_refs_ok(self):
        self.assertEqual(check_feature_depth_honesty(_tree_leaf(4, ["S1234567"]), []), [])

    def test_null_depth_ok(self):
        self.assertEqual(check_feature_depth_honesty(_tree_leaf(None, []), []), [])


class WordingTest(unittest.TestCase):
    def test_absolute_wording_flagged(self):
        schema = {"feature_tree": {"analysis": {"differentiation_matrix": [
            {"name": "x", "product": "P", "note": "P 独占该能力"}]}}}
        issues = check_differentiator_wording(schema, [])
        self.assertTrue(any(i["rule"] == "R12" for i in issues))


class WinnerConservatismTest(unittest.TestCase):
    def test_winner_without_any_depth_is_error(self):
        schema = {"feature_tree": {
            "tree": {"domains": [{"id": "A", "name": "a", "weight": 1.0, "modules": [
                {"id": "A1", "name": "a", "points": [{"id": "F1", "name": "x", "products": {
                    "P": {"support_status": "supported", "depth_score": None},
                    "Q": {"support_status": "supported", "depth_score": None}}}]}]}]},
            "analysis": {"winners": [{"feature_id": "F1", "name": "x", "winner": "P",
                                      "confidence": "high"}]}}}
        issues = check_winner_conservatism(schema, [])
        self.assertTrue(any(i["rule"] == "R13" for i in issues))


if __name__ == "__main__":
    unittest.main()
