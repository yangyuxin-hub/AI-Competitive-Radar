"""Collector 纯函数契约测试(此前盲区):evidence_id 格式 / dedupe / patch_by_requirements。
锁定 CLAUDE.md §5 的关键契约,防回归。"""
import unittest

from src.collector import (
    dedupe_evidence,
    generate_evidence_id,
    patch_by_requirements,
)


class EvidenceIdTest(unittest.TestCase):
    def test_format_is_S_plus_7_upper_hex(self):
        eid = generate_evidence_id("Cursor", "https://x.com", "claim")
        self.assertEqual(len(eid), 8)
        self.assertTrue(eid.startswith("S"))
        self.assertEqual(eid[1:], eid[1:].upper())

    def test_deterministic(self):
        a = generate_evidence_id("Cursor", "u", "c")
        b = generate_evidence_id("Cursor", "u", "c")
        self.assertEqual(a, b)

    def test_distinct_inputs_distinct_ids(self):
        a = generate_evidence_id("Cursor", "u", "c")
        b = generate_evidence_id("Windsurf", "u", "c")
        self.assertNotEqual(a, b)


class DedupeTest(unittest.TestCase):
    def test_dedupe_by_evidence_id_keeps_last(self):
        evs = [
            {"evidence_id": "S1", "claim": "a"},
            {"evidence_id": "S1", "claim": "b"},
            {"evidence_id": "S2", "claim": "c"},
        ]
        out = dedupe_evidence(evs)
        self.assertEqual(len(out), 2)
        by_id = {e["evidence_id"]: e for e in out}
        self.assertEqual(by_id["S1"]["claim"], "b")  # 后者覆盖


class PatchByRequirementsTest(unittest.TestCase):
    def _ev(self, eid, ct):
        return {"evidence_id": eid, "claim_type": ct}

    def test_no_collector_req_keeps_all_new_deduped(self):
        existing = [self._ev("S1", "pricing")]
        new = [self._ev("S1", "pricing"), self._ev("S2", "user_pain")]
        out = patch_by_requirements(existing, new, requirements=[])
        ids = {e["evidence_id"] for e in out}
        self.assertEqual(ids, {"S1", "S2"})  # S1 不重复

    def test_expands_all_required_claim_types(self):
        # 契约:展开所有 required_claim_types(不是只取第一个)
        existing = []
        new = [self._ev("S1", "pricing"), self._ev("S2", "user_pain"), self._ev("S3", "feature_existence")]
        reqs = [{"reject_target": "collector", "required_claim_types": ["pricing", "user_pain"]}]
        out = patch_by_requirements(existing, new, reqs)
        ids = {e["evidence_id"] for e in out}
        self.assertEqual(ids, {"S1", "S2"})  # feature_existence 不在需求内 → 不补

    def test_non_collector_req_ignored(self):
        existing = []
        new = [self._ev("S1", "pricing")]
        reqs = [{"reject_target": "analyzer", "required_claim_types": ["pricing"]}]
        out = patch_by_requirements(existing, new, reqs)
        # 没有 collector 需求 → 走"保留全部新证据"分支
        self.assertEqual({e["evidence_id"] for e in out}, {"S1"})


if __name__ == "__main__":
    unittest.main()
