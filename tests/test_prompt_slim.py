# -*- coding: utf-8 -*-
"""ANALYZER_PROMPT_SLIM payload 瘦身的行为契约。

- flag 关(默认):llm_meta 原样透传、derivations 走共享旧口径 → 零行为变化
- flag 开:meta 白名单;derivations 按 section 过滤 claim_type + 更紧 top-K/片段
- 过滤产物永远是全量证据的子集 → 引用合法性(R1/R9)不受影响
"""
import pytest

from src.analyzer_common import (
    _LLM_META_KEYS,
    _compact_evidence,
    compact_evidence_for_deriv,
    llm_meta,
    prompt_slim_enabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ANALYZER_PROMPT_SLIM", "ANALYZER_DERIV_MAX_PER_TYPE",
              "ANALYZER_DERIV_SNIPPET_LEN"):
        monkeypatch.delenv(k, raising=False)


_META = {
    "report_id": "CR-1", "target_product": "Cursor", "competitors": ["Windsurf"],
    "analysis_focus": ["代码补全"], "analysis_purpose": "学习", "analysis_intent": "feature_compare",
    "data_cutoff": "2026-06-11",
    # 以下是不该进 LLM prompt 的内务字段
    "evidence_plan": {"evidence_tasks": ["..."] * 7}, "agent_trace_id": "trace_x",
    "runtime_profile": "deep", "generated_at": "...", "schema_version": "2.1",
}


def _ev(eid, product, ct, text="某证据片段" * 40):
    return {"evidence_id": eid, "product": product, "claim_type": ct,
            "extracted_snippet": text, "quality_score": 0.8}


def test_flag_off_meta_passthrough():
    assert not prompt_slim_enabled()
    assert llm_meta(_META) is _META  # 原对象原样透传


def test_flag_on_meta_whitelist(monkeypatch):
    monkeypatch.setenv("ANALYZER_PROMPT_SLIM", "1")
    out = llm_meta(_META)
    assert set(out) == set(_LLM_META_KEYS)
    assert "evidence_plan" not in out and "agent_trace_id" not in out
    assert out["target_product"] == "Cursor"


def test_deriv_section_filter_excludes_irrelevant_types():
    ev = [_ev("S0000001", "Cursor", "user_pain"),
          _ev("S0000002", "Cursor", "pricing", "完全不同的定价内容" * 30),
          _ev("S0000003", "Cursor", "market_signal", "另一段市场信号文本" * 30)]
    pm = compact_evidence_for_deriv(ev, "positioning_map")
    assert {e["claim_type"] for e in pm} == {"pricing"}  # user_pain/market_signal 被过滤
    swot = compact_evidence_for_deriv(ev, "swot")
    assert {e["claim_type"] for e in swot} == {"user_pain", "pricing", "market_signal"}  # 全保留


def test_deriv_is_subset_of_full_pool():
    ev = [_ev(f"S{i:07d}", "Cursor", "pricing", f"第{i}种互不相同的定价表述内容文本段落" * 10)
          for i in range(12)]
    full_ids = {e["evidence_id"] for e in ev}
    out = compact_evidence_for_deriv(ev, "recommendations")
    assert {e["evidence_id"] for e in out} <= full_ids
    assert len(out) <= 5  # 默认 ANALYZER_DERIV_MAX_PER_TYPE=5


def test_deriv_snippet_tighter():
    ev = [_ev("S0000001", "Cursor", "pricing", "长" * 500)]
    out = compact_evidence_for_deriv(ev, "swot")
    assert len(out[0]["extracted_snippet"]) <= 140


def test_compact_evidence_param_override_vs_default():
    ev = [_ev(f"S{i:07d}", "Cursor", "pricing", f"互不重复的表述编号{i}的独立内容段" * 12)
          for i in range(10)]
    assert len(_compact_evidence(ev, per_type=2)) == 2
    assert len(_compact_evidence(ev)) == 8  # 默认口径不变
