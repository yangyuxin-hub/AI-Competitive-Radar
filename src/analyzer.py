"""Analyzer 节点(两步式) — 见 docs/design-v2.2.md §六

Step 1: facts (feature_tree + pricing_model + user_persona)
Step 2: derivations (swot + recommendations)

每步带一次 quick_validate 本地自修复。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Optional

from .llm import get_llm, is_mock_mode, load_sample_report
from .state import AgentState


def _is_demo_loop() -> bool:
    return os.environ.get("DEMO_LOOP", "").strip() in ("1", "true", "True")


# ───────────────────────────────────────────────────────────────────────
# 进度回调
# ───────────────────────────────────────────────────────────────────────

_PROGRESS_CALLBACK = None  # type: Optional[callable]


def set_progress_callback(cb) -> None:
    """注册 Analyzer 进度事件回调。事件字典:
    {step: 'facts'|'derivations', phase: 'start'|'done'|'repair', issues?, attempt?}
    回调失败不影响主流程。"""
    global _PROGRESS_CALLBACK
    _PROGRESS_CALLBACK = cb


def _emit_progress(**evt) -> None:
    if _PROGRESS_CALLBACK is None:
        return
    try:
        _PROGRESS_CALLBACK(evt)
    except Exception:
        pass


def _corrupt_facts_for_demo(facts: dict) -> dict:
    """注入一个 R1 错误(evidence_id 不存在)演示打回。深拷贝后修改,不污染原 sample。"""
    out = copy.deepcopy(facts)
    feats = out.get("feature_tree", {}).get("features") or []
    if feats:
        # 在第一个 feature 的 Cursor.support_evidence_ids 末尾塞一个伪造 ID
        cursor = feats[0].get("products", {}).get("Cursor") or {}
        cursor.setdefault("support_evidence_ids", []).append("SDEMOFAK")
    return out


def _corrupt_derivations_for_demo(derivations: dict) -> dict:
    """注入一个 R5 错误(priority_score 公式不一致)演示打回。"""
    out = copy.deepcopy(derivations)
    recs = out.get("recommendations") or []
    if recs:
        ps = recs[0].setdefault("priority_score", {})
        # 把 final_score 改成与公式不符的值
        ps["final_score"] = 9.99
        # 顺便把第二条 rec 的 source refs 清空触发 R4
        if len(recs) > 1:
            recs[1]["source_feature_ids"] = []
            recs[1]["source_pain_ids"] = []
    return out


_ROOT = Path(__file__).resolve().parent.parent
_PROMPTS_DIR = _ROOT / "prompts"


# ────────────────────────────────────────────────────────────────────────────
# Prompt 加载
# ────────────────────────────────────────────────────────────────────────────

_prompt_cache: dict[str, str] = {}


def load_prompt(name: str) -> str:
    if name in _prompt_cache:
        return _prompt_cache[name]
    path = _PROMPTS_DIR / f"{name}.md"
    _prompt_cache[name] = path.read_text(encoding="utf-8")
    return _prompt_cache[name]


# ────────────────────────────────────────────────────────────────────────────
# evidence 引用遍历(Analyzer 自校验 + Reviewer R1 共用)
# ────────────────────────────────────────────────────────────────────────────

def collect_all_evidence_refs(
    schema: dict,
) -> list[tuple[str, list[str], list[str]]]:
    """返回 [(path, evidence_ids, allowed_claim_types), ...]"""
    refs: list[tuple[str, list[str], list[str]]] = []

    # feature_tree
    for f in schema.get("feature_tree", {}).get("features", []):
        fid = f.get("feature_id", "?")
        for pname, pdata in (f.get("products") or {}).items():
            refs.append((
                f"feature_tree.{fid}.{pname}.support_evidence_ids",
                pdata.get("support_evidence_ids") or [],
                ["feature_existence"],
            ))
            qs = pdata.get("quality_score") or {}
            refs.append((
                f"feature_tree.{fid}.{pname}.quality_score.evidence_ids",
                qs.get("evidence_ids") or [],
                ["performance_quality", "user_pain"],
            ))
            agg = qs.get("aggregation") or {}
            refs.append((
                f"feature_tree.{fid}.{pname}.aggregation.representative_evidence_ids",
                agg.get("representative_evidence_ids") or [],
                ["performance_quality", "user_pain", "feature_existence"],
            ))
        refs.append((
            f"feature_tree.{fid}.gap.evidence_ids",
            (f.get("gap") or {}).get("evidence_ids") or [],
            ["feature_existence", "performance_quality", "user_pain"],
        ))

    # pricing_model
    for p in schema.get("pricing_model", {}).get("products", []):
        for i, tier in enumerate(p.get("tiers") or []):
            refs.append((
                f"pricing_model.{p.get('name')}.tiers[{i}].evidence_ids",
                tier.get("evidence_ids") or [],
                ["pricing"],
            ))
    refs.append((
        "pricing_model.pricing_gap.evidence_ids",
        (schema.get("pricing_model", {}).get("pricing_gap") or {}).get("evidence_ids") or [],
        ["pricing", "market_signal"],
    ))

    # user_persona
    for u in schema.get("user_persona", {}).get("user_segments", []):
        refs.append((
            f"user_persona.user_segments.{u.get('segment_id', '?')}.evidence_ids",
            u.get("evidence_ids") or [],
            ["user_pain", "market_signal", "performance_quality"],
        ))
    for pp in schema.get("user_persona", {}).get("pain_points", []):
        refs.append((
            f"user_persona.{pp.get('pain_id', '?')}.frequency.evidence_ids",
            (pp.get("frequency") or {}).get("evidence_ids") or [],
            ["user_pain", "performance_quality"],
        ))

    # recommendations
    for r in schema.get("recommendations", []):
        refs.append((
            f"recommendations.{r.get('rec_id', '?')}.evidence_ids",
            r.get("evidence_ids") or [],
            ["feature_existence", "user_pain", "performance_quality", "pricing", "market_signal"],
        ))

    # swot
    for dim in ("strengths", "weaknesses", "opportunities", "threats"):
        for i, item in enumerate(schema.get("swot", {}).get(dim) or []):
            refs.append((
                f"swot.{dim}[{i}].evidence_ids",
                item.get("evidence_ids") or [],
                ["feature_existence", "pricing", "user_pain", "performance_quality", "market_signal"],
            ))

    return refs


# ────────────────────────────────────────────────────────────────────────────
# quick_validate
# ────────────────────────────────────────────────────────────────────────────

def quick_validate_facts(facts: dict, evidence: list[dict], meta: dict) -> list[str]:
    issues: list[str] = []
    valid_ids = {e["evidence_id"] for e in evidence}

    # (a) evidence_id 必须存在
    for path, eids, _allowed in collect_all_evidence_refs(facts):
        for eid in eids:
            if eid not in valid_ids:
                issues.append(f"{path}: 引用了不存在的 evidence_id {eid}")

    # (b) gap 覆盖检查
    target = meta["target_product"]
    competitors = set(meta["competitors"])
    for feat in facts.get("feature_tree", {}).get("features", []):
        covered = set((feat.get("products") or {}).keys())
        if target not in covered:
            issues.append(f"feature {feat.get('feature_id')}: 未覆盖 target product {target}")
        if not (covered & competitors):
            issues.append(
                f"feature {feat.get('feature_id')}: 未覆盖任何 competitor"
                f"(competitors={sorted(competitors)}, got={sorted(covered)})"
            )
    return issues


def quick_validate_derivations(
    derivations: dict,
    facts: dict,
    evidence: list[dict],
) -> list[str]:
    issues: list[str] = []
    valid_ids = {e["evidence_id"] for e in evidence}
    valid_fids = {
        f["feature_id"] for f in facts.get("feature_tree", {}).get("features", [])
    }
    valid_pids = {
        p["pain_id"] for p in facts.get("user_persona", {}).get("pain_points", [])
    }

    # evidence_id 校验(覆盖 swot + rec)
    for path, eids, _allowed in collect_all_evidence_refs({**facts, **derivations}):
        if not path.startswith(("swot", "recommendations")):
            continue
        for eid in eids:
            if eid not in valid_ids:
                issues.append(f"{path}: 引用了不存在的 evidence_id {eid}")

    for rec in derivations.get("recommendations", []):
        rid = rec.get("rec_id", "?")
        fids = set(rec.get("source_feature_ids") or [])
        pids = set(rec.get("source_pain_ids") or [])

        # (c) 至少 1 个有效引用
        if not (fids & valid_fids) and not (pids & valid_pids):
            issues.append(f"{rid}: 未引用任何有效 feature_id / pain_id")

        # (d) priority_score 公式自洽
        ps = rec.get("priority_score") or {}
        weights = ps.get("weights") or {}
        if weights and "final_score" in ps:
            try:
                expected = sum(
                    float(ps.get(k, 0)) * float(w) for k, w in weights.items()
                )
                if abs(expected - float(ps["final_score"])) > 0.011:
                    issues.append(
                        f"{rid}: priority final_score={ps['final_score']} 与公式"
                        f"={expected:.3f} 不一致"
                    )
            except (TypeError, ValueError):
                issues.append(f"{rid}: priority_score 字段类型异常")
    return issues


# ────────────────────────────────────────────────────────────────────────────
# Node
# ────────────────────────────────────────────────────────────────────────────

def _build_repair_hint(issues: list[str]) -> str:
    issues_text = "\n".join(f"- {i}" for i in issues)
    return (
        "\n\n---\n\n## REPAIR\n\n你上一次输出存在以下问题,请仅修正这些问题后重新输出完整 JSON:\n"
        f"{issues_text}\n\n要求:\n- 单一 JSON 对象,无 markdown 包裹\n- 不要修改无问题的字段\n"
    )


def _step1_facts(evidence: list[dict], meta: dict, analyzer_retry: int = 0) -> dict:
    """Step 1 — 事实层"""
    if is_mock_mode():
        # Mock: 从 sample_report 抽出 facts 部分
        sr = load_sample_report()
        facts = {
            "feature_tree": sr["feature_tree"],
            "pricing_model": sr["pricing_model"],
            "user_persona": sr["user_persona"],
        }
        # DEMO_LOOP: 首轮注入 R1 错误(伪造 evidence_id),retry 后恢复干净
        if _is_demo_loop() and analyzer_retry == 0:
            print("[analyzer] DEMO_LOOP: 注入 R1 错误(SDEMOFAK)到 facts")
            facts = _corrupt_facts_for_demo(facts)
        return facts

    llm = get_llm()
    system = load_prompt("analyzer_facts")
    payload = {"analysis_meta": meta, "raw_evidence": evidence}
    _emit_progress(step="facts", phase="start", attempt=1)
    facts = llm.call_json(system, payload, max_tokens=8192, label="facts")
    _emit_progress(step="facts", phase="done", attempt=1)

    issues = quick_validate_facts(facts, evidence, meta)
    if issues:
        print(f"[analyzer] facts quick_validate found {len(issues)} issues; repairing")
        _emit_progress(step="facts", phase="repair", issues=len(issues))
        facts = llm.call_json(
            system + _build_repair_hint(issues), payload,
            max_tokens=4096, label="facts_repair",
        )
        _emit_progress(step="facts", phase="done", attempt=2)
    return facts


def _step2_derivations(facts: dict, evidence: list[dict], meta: dict, analyzer_retry: int = 0) -> dict:
    """Step 2 — 推导层"""
    if is_mock_mode():
        sr = load_sample_report()
        der = {
            "swot": sr["swot"],
            "recommendations": sr["recommendations"],
        }
        # DEMO_LOOP: 首轮额外注入 R5(priority 公式)和 R4(无 source_refs)错误
        if _is_demo_loop() and analyzer_retry == 0:
            print("[analyzer] DEMO_LOOP: 注入 R5/R4 错误到 derivations")
            der = _corrupt_derivations_for_demo(der)
        return der

    llm = get_llm()
    system = load_prompt("analyzer_derivations")
    payload = {"analysis_meta": meta, "raw_evidence": evidence, "facts": facts}
    _emit_progress(step="derivations", phase="start", attempt=1)
    der = llm.call_json(system, payload, max_tokens=3072, label="derivations")
    _emit_progress(step="derivations", phase="done", attempt=1)

    issues = quick_validate_derivations(der, facts, evidence)
    if issues:
        print(f"[analyzer] derivations quick_validate found {len(issues)} issues; repairing")
        _emit_progress(step="derivations", phase="repair", issues=len(issues))
        der = llm.call_json(
            system + _build_repair_hint(issues), payload,
            max_tokens=3072, label="derivations_repair",
        )
        _emit_progress(step="derivations", phase="done", attempt=2)
    return der


def analyzer_node(state: AgentState) -> AgentState:
    evidence = state["raw_evidence"] or []
    meta = state["analysis_meta"]
    analyzer_retry = (state.get("retry_count") or {}).get("analyzer", 0)

    facts = _step1_facts(evidence, meta, analyzer_retry=analyzer_retry)
    derivations = _step2_derivations(facts, evidence, meta, analyzer_retry=analyzer_retry)

    schema_draft = {
        "analysis_meta": meta,
        **facts,
        **derivations,
    }
    return {**state, "schema_draft": schema_draft}
