"""M4 直线控制流测试(design-v3-draft §四/§六 M4 验收):

1. guard_revise_node:消费 Auditor 发现 → 确定性修订 → 重渲染;终态规则诚实
   (running+有修订→passed / running+零修订→degraded / passed 原样)
2. 图拓扑:无条件边,reviewer 后恒为 guard_revise → END;degraded_writer 不在图中
"""
import unittest
from unittest import mock

from src.guard import guard_revise_node


def _ev(eid, product, ct):
    return {"evidence_id": eid, "product": product, "claim_type": ct,
            "claim": "c", "extracted_snippet": f"{product} {ct}"}


POOL = [_ev("SREAL001", "Alpha", "performance_quality")]


def _state(status, schema, errors=()):
    return {
        "status": status,
        "schema_draft": schema,
        "raw_evidence": list(POOL),
        "report_draft": "old report",
        "quality_report": {"errors": list(errors), "warnings": []},
        "retry_count": {"collector": 0, "analyzer": 0, "writer": 0},
    }


def _schema_with_fake_ref():
    return {"swot": {"strengths": [
        {"point": "示例优势", "evidence_ids": ["SFAKE999", "SREAL001"]}]}}


class GuardReviseNodeTest(unittest.TestCase):
    def test_running_with_revision_becomes_passed_and_rerenders(self):
        st = _state("running", _schema_with_fake_ref(),
                    errors=[{"rule": "R1", "location": "swot", "detail": "幻觉引用"}])
        with mock.patch("src.writer.writer_node",
                        side_effect=lambda s: {**s, "report_draft": "re-rendered"}) as w:
            out = guard_revise_node(st)
        self.assertEqual(out["status"], "passed")
        self.assertEqual(out["report_draft"], "re-rendered")
        w.assert_called_once()
        rev = out["guard_revision"]
        self.assertGreater(rev["changes_total"], 0)
        self.assertEqual(rev["findings_consumed"], 1)
        # 幻觉引用确实被修掉
        ids = out["schema_draft"]["swot"]["strengths"][0]["evidence_ids"]
        self.assertEqual(ids, ["SREAL001"])

    def test_running_with_zero_revision_degrades_honestly(self):
        clean = {"swot": {"strengths": [{"point": "示例优势", "evidence_ids": ["SREAL001"]}]}}
        st = _state("running", clean,
                    errors=[{"rule": "R5", "location": "recommendations", "detail": "公式错"}])
        out = guard_revise_node(st)
        self.assertEqual(out["status"], "degraded",
                         "审出问题但 Guard 修不了 → 诚实降级,不假装 passed")
        # 零修订不重渲染,但降级出货包分层说明(原 degraded_writer 措辞,Guard 收编)
        self.assertIn("old report", out["report_draft"])
        self.assertIn("部分置信", out["report_draft"])
        self.assertIn("不建议直接采纳的结论", out["report_draft"])

    def test_passed_clean_run_is_noop_shipping(self):
        clean = {"swot": {"strengths": [{"point": "示例优势", "evidence_ids": ["SREAL001"]}]}}
        out = guard_revise_node(_state("passed", clean))
        self.assertEqual(out["status"], "passed")
        self.assertEqual(out["report_draft"], "old report")

    def test_missing_schema_degrades(self):
        out = guard_revise_node(_state("running", None))
        self.assertEqual(out["status"], "degraded")


class LinearTopologyTest(unittest.TestCase):
    def test_graph_is_linear_with_guard_revise_terminal(self):
        from src.graph import build_app
        app = build_app(llm=None, reviewer_mode="minimal")
        g = app.get_graph()
        nodes = set(g.nodes)
        self.assertIn("guard_revise", nodes)
        self.assertNotIn("degraded_writer", nodes, "degraded_writer 必须已删")
        edges = {(e.source, e.target) for e in g.edges}
        self.assertIn(("reviewer", "guard_revise"), edges)
        # reviewer 只有一条出边(无条件路由、无回边)
        self.assertEqual(sum(1 for s, _ in edges if s == "reviewer"), 1)
        for src in ("collector", "analyzer", "writer"):
            self.assertNotIn(("reviewer", src), edges, f"打回回边 reviewer→{src} 必须已删")


if __name__ == "__main__":
    unittest.main()
