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


def _facts_summary(facts: dict) -> str:
    feats = [f.get("name") for f in (facts.get("feature_tree") or {}).get("features", []) if f.get("name")]
    pains = (facts.get("user_persona") or {}).get("pain_points") or []
    parts = []
    if feats:
        parts.append("功能维度：" + "、".join(feats[:4]))
    if pains:
        parts.append(f"识别 {len(pains)} 个用户痛点")
    return "；".join(parts)


def _der_summary(der: dict) -> str:
    recs = der.get("recommendations") or []
    swot = der.get("swot") or {}
    n_s = len(swot.get("strengths") or [])
    n_w = len(swot.get("weaknesses") or [])
    top = recs[0].get("action") if recs else ""
    parts = [f"SWOT：{n_s} 优势 / {n_w} 劣势", f"{len(recs)} 条改进建议"]
    if top:
        parts.append(f"首要：{top[:30]}")
    return "；".join(parts)


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
        ["pricing", "market_signal", "user_pain"],
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
    by_id = {e["evidence_id"]: e for e in evidence}

    # (a) evidence_id 必须存在,且 claim_type 必须匹配字段语义。
    for path, eids, allowed in collect_all_evidence_refs(facts):
        for eid in eids:
            ev = by_id.get(eid)
            if not ev:
                issues.append(f"{path}: 引用了不存在的 evidence_id {eid}")
                continue
            ct = ev.get("claim_type")
            if allowed and ct not in allowed:
                issues.append(
                    f"{path}: evidence_id {eid} claim_type={ct} 不在允许集 {allowed}; "
                    "请替换为允许 claim_type 的证据,或把该 evidence_id 移到匹配字段"
                )

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


def _filter_evidence_ids(
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict],
    allowed_claim_types: set[str],
) -> tuple[list[str], int]:
    kept: list[str] = []
    dropped = 0
    for eid in evidence_ids or []:
        ev = evidence_by_id.get(eid)
        if ev and ev.get("claim_type") in allowed_claim_types:
            kept.append(eid)
        else:
            dropped += 1
    return kept, dropped


def sanitize_schema_evidence_refs(schema: dict, evidence: list[dict]) -> tuple[dict, int]:
    """Remove evidence IDs that do not exist in the current raw_evidence packet."""
    valid_ids = {e["evidence_id"] for e in evidence if e.get("evidence_id")}
    dropped = 0
    ref_keys = {"evidence_ids", "support_evidence_ids", "representative_evidence_ids"}

    def walk(obj) -> None:
        nonlocal dropped
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                if key in ref_keys and isinstance(value, list):
                    filtered = [eid for eid in value if eid in valid_ids]
                    dropped += len(value) - len(filtered)
                    obj[key] = filtered
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(schema)
    return schema, dropped


def sanitize_facts_evidence_refs(facts: dict, evidence: list[dict]) -> tuple[dict, int]:
    """Drop evidence refs whose claim_type cannot satisfy the target schema field.

    This is a deterministic last line of defense for Reviewer R2. LLM repair gets
    the first chance to replace IDs with better ones; this function removes any
    remaining incompatible IDs so full mode does not fail on avoidable type drift.
    """
    evidence_by_id = {e["evidence_id"]: e for e in evidence}
    dropped = 0

    def apply(obj: dict, key: str, allowed: set[str]) -> None:
        nonlocal dropped
        filtered, n = _filter_evidence_ids(obj.get(key) or [], evidence_by_id, allowed)
        obj[key] = filtered
        dropped += n

    for feat in facts.get("feature_tree", {}).get("features", []):
        for pdata in (feat.get("products") or {}).values():
            apply(pdata, "support_evidence_ids", {"feature_existence"})
            qs = pdata.get("quality_score") or {}
            apply(qs, "evidence_ids", {"performance_quality", "user_pain"})
            agg = qs.get("aggregation") or {}
            apply(agg, "representative_evidence_ids", {
                "performance_quality", "user_pain", "feature_existence",
            })
        gap = feat.get("gap") or {}
        apply(gap, "evidence_ids", {"feature_existence", "performance_quality", "user_pain"})

    pricing = facts.get("pricing_model") or {}
    for product in pricing.get("products", []):
        for tier in product.get("tiers") or []:
            apply(tier, "evidence_ids", {"pricing"})
    apply(pricing.get("pricing_gap") or {}, "evidence_ids", {"pricing", "user_pain", "market_signal"})

    persona = facts.get("user_persona") or {}
    for segment in persona.get("user_segments", []):
        apply(segment, "evidence_ids", {"user_pain", "market_signal", "performance_quality"})
    for pain in persona.get("pain_points", []):
        freq = pain.get("frequency") or {}
        apply(freq, "evidence_ids", {"user_pain", "performance_quality"})

    return facts, dropped


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
        f"{issues_text}\n\n"
        "常见修复指引:\n"
        "- 若 evidence_id 不存在:从 raw_evidence 中找一条 claim 最匹配的合法 ID 替换\n"
        "- 若 gap 未覆盖 target 或 competitor:补充对应的 products 条目,证据不足用 support_status: unknown\n"
        "- 若 aggregation.sample_size 不等于 pos+neg+neu:重新核算并修正 sample_size\n"
        "- 若 support_status 使用了未定义的值:改为 supported/partially_supported/not_supported/unknown 之一\n"
        "- 若 claim_type 不在允许集:不要为了保留 evidence_id 硬塞字段;按下面字段规则移动或删除该 ID\n"
        "  * support_evidence_ids 只允许 feature_existence\n"
        "  * quality_score.evidence_ids 只允许 performance_quality / user_pain\n"
        "  * pricing_model.products[].tiers[].evidence_ids 只允许 pricing\n"
        "  * pricing_gap.evidence_ids 允许 pricing / user_pain / market_signal\n"
        "  * user_segments.evidence_ids 只允许 user_pain / market_signal / performance_quality;不要用官方功能页或定价页推断用户画像\n"
        "- 若 source_feature_ids 中某个 ID 不在 facts.feature_tree:删掉无效 ID\n"
        "- 若 final_score 与公式不一致:重新计算 sum(评分项 * weights),保留两位小数\n\n"
        "要求:\n- 单一 JSON 对象,无 markdown 包裹\n- 不要修改无问题的字段\n"
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
    _emit_progress(step="facts", phase="done", attempt=1, summary=_facts_summary(facts))

    # issue 过多时 LLM 重跑(~80s)既慢又难全修 → 直接走确定性 sanitize(秒级)
    _MAX_LLM_REPAIR_ISSUES = int(os.environ.get("ANALYZER_FACTS_REPAIR_THRESHOLD", "6"))
    issues = quick_validate_facts(facts, evidence, meta)
    if issues and len(issues) > _MAX_LLM_REPAIR_ISSUES:
        print(f"[analyzer] facts found {len(issues)} issues (> {_MAX_LLM_REPAIR_ISSUES}); 跳过 LLM 重跑，直接 sanitize")
        _emit_progress(step="facts", phase="repair", issues=len(issues))
        facts, dropped = sanitize_facts_evidence_refs(facts, evidence)
        print(f"[analyzer] facts deterministic sanitize dropped {dropped} invalid evidence refs")
    elif issues:
        print(f"[analyzer] facts quick_validate found {len(issues)} issues; repairing")
        _emit_progress(step="facts", phase="repair", issues=len(issues))
        try:
            facts = llm.call_json(
                system + _build_repair_hint(issues), payload,
                max_tokens=8192, label="facts_repair",
            )
            _emit_progress(step="facts", phase="done", attempt=2, summary=_facts_summary(facts))
        except Exception as e:
            print(f"[analyzer] facts repair failed; applying deterministic sanitize: {e}")

        remaining = quick_validate_facts(facts, evidence, meta)
        if remaining:
            facts, dropped = sanitize_facts_evidence_refs(facts, evidence)
            if dropped:
                print(f"[analyzer] facts deterministic sanitize dropped {dropped} invalid evidence refs")
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
    _emit_progress(step="derivations", phase="done", attempt=1, summary=_der_summary(der))

    issues = quick_validate_derivations(der, facts, evidence)
    if issues:
        print(f"[analyzer] derivations quick_validate found {len(issues)} issues; repairing")
        _emit_progress(step="derivations", phase="repair", issues=len(issues))
        try:
            der = llm.call_json(
                system + _build_repair_hint(issues), payload,
                max_tokens=3072, label="derivations_repair",
            )
            _emit_progress(step="derivations", phase="done", attempt=2, summary=_der_summary(der))
        except Exception as e:
            print(f"[analyzer] derivations repair failed; reviewer will handle remaining issues: {e}")
    return der


def analyzer_node(state: AgentState) -> AgentState:
    evidence = state["raw_evidence"] or []
    print(f"\n[analyzer] received {len(evidence)} raw_evidence")
    if evidence:
        by_source = {}
        for e in evidence:
            s = e.get("source_type", "?")
            by_source[s] = by_source.get(s, 0) + 1
        print(f"[analyzer] by source_type: {by_source}")
    meta = state["analysis_meta"]
    analyzer_retry = (state.get("retry_count") or {}).get("analyzer", 0)

    facts = _step1_facts(evidence, meta, analyzer_retry=analyzer_retry)
    derivations = _step2_derivations(facts, evidence, meta, analyzer_retry=analyzer_retry)

    schema_draft = {
        "analysis_meta": meta,
        **facts,
        **derivations,
    }
    if is_mock_mode():
        schema_draft, dropped = sanitize_schema_evidence_refs(schema_draft, evidence)
        if dropped:
            print(f"[analyzer] mock schema sanitize dropped {dropped} invalid evidence refs")
    return {**state, "schema_draft": schema_draft}
