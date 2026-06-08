"""StageReport 统一契约测试 — src/stage_report.py（见 docs/design-v3.md §3/§4 · M0）

覆盖:owner 路由全表 / task_key 派生 / status 派生规则 / 四环节收编(三方言→同构) /
契约 schema 完整性。守住"零行为改动 + 统一 schema 落地"的 M0 验收。
"""
import unittest

from src.stage_report import (
    gap_owner, task_key, derive_status, status_to_verdict, to_stage_report,
    aggregate_timeline,
)


class GapOwnerTest(unittest.TestCase):
    def test_official_owned_gaps(self):
        cases = [
            {"claim_type": "feature_existence", "gap_type": "coverage_short"},
            {"claim_type": "pricing", "gap_type": "coverage_short"},
            {"claim_type": "feature_existence", "gap_type": "no_official"},
            {"claim_type": "pricing", "gap_type": "pricing_no_number"},
        ]
        for g in cases:
            self.assertEqual(gap_owner(g), "fetch_official", g)

    def test_community_owned_gaps(self):
        cases = [
            {"claim_type": "user_pain", "gap_type": "coverage_short"},
            {"claim_type": "performance_quality", "gap_type": "coverage_short"},
            {"claim_type": "user_pain", "gap_type": "community_missing"},
            {"claim_type": "user_pain", "gap_type": "community_low_quality"},
            {"claim_type": "user_pain", "gap_type": "bias_all_vendor"},
        ]
        for g in cases:
            self.assertEqual(gap_owner(g), "fetch_community", g)

    def test_total_too_few_to_backfill(self):
        self.assertEqual(gap_owner({"claim_type": None, "gap_type": "total_too_few"}), "backfill")

    def test_explicit_owner_wins(self):
        self.assertEqual(gap_owner({"owner_node": "writer", "gap_type": "coverage_short"}), "writer")

    def test_reject_target_routes_analyzer_writer(self):
        self.assertEqual(gap_owner({"reject_target": "analyzer", "gap_type": "claim_type_mismatch"}), "analyzer")
        self.assertEqual(gap_owner({"reject_target": "writer", "gap_type": "report_chip_not_found"}), "writer")

    def test_unknown_falls_back_to_collector(self):
        self.assertEqual(gap_owner({"gap_type": "mystery"}), "collector")


class TaskKeyTest(unittest.TestCase):
    def test_task_key_shape(self):
        g = {"claim_type": "user_pain", "gap_type": "community_missing", "product": "Linear"}
        self.assertEqual(task_key(g), "fetch_community:Linear:user_pain")

    def test_task_key_none_normalizes_star(self):
        g = {"claim_type": None, "gap_type": "total_too_few", "product": None}
        self.assertEqual(task_key(g), "backfill:*:*")


class DeriveStatusTest(unittest.TestCase):
    def test_error_means_failed(self):
        checks = [{"severity": "error"}, {"severity": "info"}]
        self.assertEqual(derive_status(checks, []), "failed")

    def test_gaps_or_warning_means_degraded(self):
        self.assertEqual(derive_status([{"severity": "info"}], [{"task_key": "x"}]), "degraded")
        self.assertEqual(derive_status([{"severity": "warning"}], []), "degraded")

    def test_clean_means_ok(self):
        self.assertEqual(derive_status([{"severity": "info"}], []), "ok")

    def test_verdict_mapping(self):
        self.assertEqual(status_to_verdict("ok"), "pass")
        self.assertEqual(status_to_verdict("degraded"), "warn")
        self.assertEqual(status_to_verdict("failed"), "fail")


class CollectorReportTest(unittest.TestCase):
    def _state(self, gaps, evidence):
        return {
            "raw_evidence": evidence,
            "collection_meta": {"quality_audit": {
                "avg_quality": 0.6,
                "quantity_gaps": [g for g in gaps if g["gap_type"] in ("coverage_short", "total_too_few", "no_official")],
                "quality_gaps": [g for g in gaps if g["gap_type"] in ("pricing_no_number", "bias_all_vendor", "community_missing", "community_low_quality")],
                "gaps": gaps,
                "passed": not gaps,
            }},
            "retry_count": {"collector": 0},
        }

    def test_community_gap_routes_to_community(self):
        gaps = [{"product": "Linear", "claim_type": "user_pain", "gap_type": "community_missing",
                 "fix": {"source_hint": ["reddit.com"]}}]
        st = self._state(gaps, [{"evidence_id": "S0000001", "product": "Linear"}])
        rep = to_stage_report("collector", st, run_id="r1")
        self.assertEqual(len(rep["gaps"]), 1)
        self.assertEqual(rep["gaps"][0]["owner_node"], "fetch_community")
        self.assertEqual(rep["gaps"][0]["task_key"], "fetch_community:Linear:user_pain")
        self.assertEqual(rep["status"], "degraded")
        self.assertEqual(rep["produced"]["evidence"], 1)

    def test_clean_collector_is_ok(self):
        st = self._state([], [{"evidence_id": "S0000001", "product": "X"}])
        rep = to_stage_report("collector", st, run_id="r1")
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["gaps"], [])

    def test_no_evidence_is_failed(self):
        st = self._state([], [])
        rep = to_stage_report("collector", st, run_id="r1")
        self.assertEqual(rep["status"], "failed")  # has_evidence check 是 error


class AnalyzerReportTest(unittest.TestCase):
    def test_hallucination_fails(self):
        st = {
            "raw_evidence": [{"evidence_id": "S0000001"}],
            "schema_draft": {"feature_tree": {"features": [
                {"feature_id": "F1", "products": {"X": {"support_evidence_ids": ["S9999999"]}}}]}},
            "retry_count": {"analyzer": 1},
        }
        rep = to_stage_report("analyzer", st, run_id="r1")
        integrity = [c for c in rep["checks"] if c["check_id"] == "evidence_ref_integrity"][0]
        self.assertEqual(integrity["verdict"], "fail")
        self.assertEqual(integrity["severity"], "error")
        self.assertEqual(rep["status"], "failed")
        self.assertEqual(rep["produced"]["hallucination_count"], 1)
        self.assertEqual(rep["attempt"], 2)  # retry_count 1 + 1

    def test_clean_analyzer_ok(self):
        st = {
            "raw_evidence": [{"evidence_id": "S0000001"}],
            "schema_draft": {"feature_tree": {"features": [
                {"feature_id": "F1", "products": {"X": {"support_evidence_ids": ["S0000001"]}}}]}},
            "retry_count": {},
        }
        rep = to_stage_report("analyzer", st, run_id="r1")
        self.assertEqual(rep["status"], "ok")


class WriterReportTest(unittest.TestCase):
    def test_empty_report_failed(self):
        rep = to_stage_report("writer", {"report_draft": ""}, run_id="r1")
        self.assertEqual(rep["status"], "failed")

    def test_score_leak_degraded(self):
        rep = to_stage_report("writer", {"report_draft": "## 报告\n含 quality_score 字样 [S0000001]"}, run_id="r1")
        leak = [c for c in rep["checks"] if c["check_id"] == "no_score_leak"][0]
        self.assertEqual(leak["verdict"], "fail")
        self.assertEqual(rep["status"], "degraded")
        self.assertEqual(rep["produced"]["chips"], 1)

    def test_clean_report_ok(self):
        rep = to_stage_report("writer", {"report_draft": "## 报告\n正文 [S0000001]"}, run_id="r1")
        self.assertEqual(rep["status"], "ok")


class ReviewerReportTest(unittest.TestCase):
    def test_r1_error_failed_and_gap_routed(self):
        st = {"status": "running", "quality_report": {
            "quality_score": 70,
            "failed_rules": ["R0"], "warning_rules": [],
            "errors": [{"rule": "R0", "issue_type": "missing_claim_type_coverage",
                        "location": "collection_meta.products.Linear", "detail": "缺 user_pain",
                        "reject_target": "collector", "claim_type": "user_pain", "gap_type": "coverage_short"}],
            "warnings": [],
        }}
        rep = to_stage_report("reviewer", st, run_id="r1")
        self.assertEqual(rep["status"], "failed")
        self.assertEqual(rep["checks"][0]["check_id"], "R0")
        self.assertEqual(len(rep["gaps"]), 1)
        self.assertEqual(rep["gaps"][0]["owner_node"], "fetch_community")

    def test_clean_reviewer_ok(self):
        st = {"status": "passed", "quality_report": {
            "quality_score": 92, "failed_rules": [], "warning_rules": [], "errors": [], "warnings": []}}
        rep = to_stage_report("reviewer", st, run_id="r1")
        self.assertEqual(rep["status"], "ok")


class SchemaShapeTest(unittest.TestCase):
    def test_all_keys_present(self):
        rep = to_stage_report("writer", {"report_draft": "## x"}, run_id="r1", elapsed_sec=1.234)
        for k in ("stage", "node", "run_id", "attempt", "status", "produced", "checks", "gaps", "cost"):
            self.assertIn(k, rep)
        self.assertEqual(rep["cost"]["elapsed_sec"], 1.2)

    def test_unknown_stage_returns_none(self):
        self.assertIsNone(to_stage_report("mystery", {}, run_id="r1"))


class AggregateTimelineTest(unittest.TestCase):
    def _rows(self):
        # 两次 run 混在一个文件里;run B 含一次 collector 重试
        return [
            {"run_id": "A", "stage": "collector", "status": "ok", "elapsed_sec": 5,
             "checks": [{"verdict": "pass", "severity": "info"}], "gaps": [], "produced": {"evidence": 10}},
            {"run_id": "A", "stage": "analyzer", "status": "ok", "elapsed_sec": 30, "checks": [], "gaps": []},
            {"run_id": "B", "stage": "collector", "status": "degraded", "elapsed_sec": 6,
             "checks": [{"verdict": "fail", "severity": "warning"}],
             "gaps": [{"task_key": "fetch_community:Linear:user_pain", "owner_node": "fetch_community",
                       "gap_type": "community_missing", "product": "Linear", "claim_type": "user_pain", "fixable": True}]},
            {"run_id": "B", "stage": "collector", "status": "ok", "elapsed_sec": 4, "checks": [], "gaps": []},
            {"run_id": "B", "stage": "analyzer", "status": "failed", "elapsed_sec": 40,
             "checks": [{"verdict": "fail", "severity": "error"}], "gaps": []},
        ]

    def test_defaults_to_latest_run(self):
        tl = aggregate_timeline(self._rows())
        self.assertEqual(tl["run_id"], "B")
        self.assertEqual(tl["summary"]["nodes"], 3)  # collector, collector(retry), analyzer

    def test_explicit_run_id(self):
        tl = aggregate_timeline(self._rows(), run_id="A")
        self.assertEqual([e["stage"] for e in tl["entries"]], ["collector", "analyzer"])
        self.assertEqual(tl["summary"]["total_elapsed_sec"], 35.0)
        self.assertEqual(tl["summary"]["worst_status"], "ok")

    def test_retry_is_separate_entry_and_order_preserved(self):
        tl = aggregate_timeline(self._rows(), run_id="B")
        self.assertEqual([e["stage"] for e in tl["entries"]], ["collector", "collector", "analyzer"])
        self.assertEqual([e["seq"] for e in tl["entries"]], [1, 2, 3])

    def test_gaps_and_worst_status_surfaced(self):
        tl = aggregate_timeline(self._rows(), run_id="B")
        self.assertEqual(tl["summary"]["worst_status"], "failed")
        self.assertEqual(tl["summary"]["open_gaps"], 1)
        self.assertEqual(tl["entries"][0]["gaps"][0]["owner_node"], "fetch_community")
        self.assertEqual(tl["entries"][0]["checks_summary"], {"pass": 0, "warn": 1, "fail": 0})

    def test_legacy_rows_fallback_to_verdict_and_metrics(self):
        rows = [{"run_id": "X", "stage": "writer", "verdict": "warn", "elapsed_sec": 2,
                 "metrics": {"report_chars": 100}}]
        tl = aggregate_timeline(rows)
        self.assertEqual(tl["entries"][0]["status"], "degraded")
        self.assertEqual(tl["entries"][0]["produced"], {"report_chars": 100})

    def test_empty_rows(self):
        tl = aggregate_timeline([])
        self.assertEqual(tl["entries"], [])
        self.assertIsNone(tl["summary"]["worst_status"])


if __name__ == "__main__":
    unittest.main()
