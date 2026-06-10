"""端到端评估回归基线 — 全 Mock 跑一遍 graph,锁定关键产物的"不退化"边界。

不追求精确数值(LLM/采样会漂移),只守住结构性底线:
- pipeline 能跑完并产出非降级/非失败状态
- schema_draft 覆盖 REQUIRED_CLAIM_TYPES 对应的核心字段
- report_draft 含可溯源 chip,且不泄漏 quality_score
- 每个执行过的节点都在 logs/stage_quality.jsonl 写入合法 StageReport 记录
  (status/checks/produced/cost 字段齐全),供 to_stage_report 契约与本测试互相印证。
"""
import json

import pytest


@pytest.fixture
def mock_run(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYZER_MOCK", "1")
    monkeypatch.setenv("DISABLE_LIVE_FETCH", "1")
    log_path = tmp_path / "stage_quality.jsonl"
    monkeypatch.setattr("src.stage_eval._LOG", log_path)

    from src.graph import run_demo

    final = run_demo(runtime_profile="fast")
    return final, log_path


def test_pipeline_reaches_non_failed_status(mock_run):
    final, _ = mock_run
    assert final.get("status") in ("passed", "degraded")


def test_schema_covers_required_claim_types(mock_run):
    final, _ = mock_run
    schema = final.get("schema_draft") or {}

    feats = (schema.get("feature_tree") or {}).get("features") or []
    assert feats, "feature_tree.features 不应为空"
    for feat in feats:
        assert feat.get("products"), f"{feat.get('feature_id')} 缺少 products"

    assert schema.get("pricing_model"), "pricing_model 不应为空"
    assert schema.get("user_persona") or schema.get("personas"), "user_persona 不应为空"


def test_report_has_chips_and_no_score_leak(mock_run):
    final, _ = mock_run
    report = final.get("report_draft") or ""
    assert report, "report_draft 不应为空"
    assert "[S" in report, "报告应含可溯源 chip [SXXXXXXX]"
    assert "quality_score" not in report, "正文不应泄漏 quality_score"


def test_stage_quality_log_has_valid_records_per_node(mock_run):
    final, log_path = mock_run
    assert log_path.exists(), "logs/stage_quality.jsonl 应有写入"

    rid = (final.get("analysis_meta") or {}).get("agent_trace_id")
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r.get("run_id") == rid]
    assert rows, "本次 run 应至少写入一条 StageReport 记录"

    for r in rows:
        for key in ("stage", "status", "checks", "gaps", "produced", "cost", "verdict"):
            assert key in r, f"记录缺少 {key}: {r}"
        assert r["status"] in ("ok", "degraded", "failed")
        assert r["verdict"] in ("pass", "warn", "fail")

    nodes = {r.get("node") or r.get("stage") for r in rows}
    assert "writer" in nodes
    assert "reviewer" in nodes or "degraded_writer" in nodes
