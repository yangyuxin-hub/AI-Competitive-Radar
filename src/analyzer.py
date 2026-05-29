"""Analyzer 节点(两步式) — 见 docs/design-v2.2.md §六

Step 1: facts (feature_tree + pricing_model + user_persona)
Step 2: derivations (swot + recommendations)

每步带一次 quick_validate 本地自修复。
"""
from __future__ import annotations

import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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


_CLAIM_LABELS = {
    "feature_existence": "功能具备性",
    "performance_quality": "性能与质量",
    "pricing": "定价信息",
    "user_pain": "用户痛点",
    "market_signal": "市场信号",
}


def _short(text: object, limit: int = 72) -> str:
    s = str(text or "").strip().replace("\n", " ")
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _target_products(meta: dict) -> list[str]:
    products = [meta.get("target_product"), *(meta.get("competitors") or [])]
    return [str(p) for p in products if p]


def _evidence_ids(
    evidence: list[dict],
    claim_types: set[str] | tuple[str, ...] | list[str],
    product: Optional[str] = None,
    limit: int = 4,
) -> list[str]:
    wanted = set(claim_types)
    ids: list[str] = []
    for ev in evidence:
        if product and ev.get("product") != product:
            continue
        if ev.get("claim_type") not in wanted:
            continue
        eid = ev.get("evidence_id")
        if eid and eid not in ids:
            ids.append(eid)
        if len(ids) >= limit:
            break
    return ids


def _evidence_preview(evidence: list[dict], meta: dict) -> dict:
    products = _target_products(meta)
    by_product = [
        {
            "product": product,
            "count": sum(1 for ev in evidence if ev.get("product") == product),
        }
        for product in products
    ]
    by_claim: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for ev in evidence:
        ct = ev.get("claim_type") or "unknown"
        by_claim[ct] = by_claim.get(ct, 0) + 1
        src = ev.get("collection_source") or ev.get("source_type") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    signals: list[str] = []
    for claim_type in ("user_pain", "performance_quality", "feature_existence", "pricing"):
        for ev in evidence:
            if ev.get("claim_type") != claim_type:
                continue
            text = _short(ev.get("claim") or ev.get("extracted_snippet"), 68)
            if not text:
                continue
            signal = f"{ev.get('product', 'unknown')}: {text}"
            if signal not in signals:
                signals.append(signal)
            if len(signals) >= 4:
                break
        if len(signals) >= 4:
            break
    return {
        "kind": "overview",
        "evidence": {
            "products": by_product,
            "claim_types": [
                {"label": _CLAIM_LABELS.get(k, k), "count": v}
                for k, v in sorted(by_claim.items(), key=lambda kv: -kv[1])
            ],
            "sources": [
                {"label": str(k), "count": v}
                for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])[:5]
            ],
        },
        "signals": signals,
    }


def _facts_preview(facts: dict) -> dict:
    pricing = []
    for product in (facts.get("pricing_model") or {}).get("products") or []:
        tiers = product.get("tiers") or []
        if tiers:
            pricing.append(f"{product.get('name')}: {len(tiers)} 个价位")
        else:
            pricing.append(f"{product.get('name')}: 暂无价位")
    return {
        "kind": "facts",
        "features": [
            _short(f.get("name"), 32)
            for f in (facts.get("feature_tree") or {}).get("features", [])
            if f.get("name")
        ][:6],
        "pain_points": [
            _short(p.get("description"), 56)
            for p in (facts.get("user_persona") or {}).get("pain_points", [])
            if p.get("description")
        ][:5],
        "pricing": pricing[:5],
    }


def _derivations_preview(der: dict) -> dict:
    swot = der.get("swot") or {}
    return {
        "kind": "derivations",
        "recommendations": [
            {
                "action": _short(r.get("action"), 72),
                "priority": (r.get("priority_score") or {}).get("priority"),
            }
            for r in (der.get("recommendations") or [])[:5]
        ],
        "swot": {
            "strengths": len(swot.get("strengths") or []),
            "weaknesses": len(swot.get("weaknesses") or []),
            "opportunities": len(swot.get("opportunities") or []),
            "threats": len(swot.get("threats") or []),
        },
    }


def _safe_price_tier(product: str, evidence_ids: list[str]) -> dict:
    return {
        "tier_name": "待确认价位",
        "billing_cycle": "unknown",
        "price": {"amount": None, "currency": "USD", "normalized_usd_month": None},
        "limits": [],
        "display_limits": "模型调用超时后保守保留定价证据，详情以来源为准",
        "observed_at": "",
        "source_freshness": "unknown",
        "evidence_ids": evidence_ids,
    }


def _fallback_facts(evidence: list[dict], meta: dict, reason: str) -> dict:
    products = _target_products(meta)
    focus = " / ".join(meta.get("analysis_focus") or []) or "核心体验"
    all_feature_ids = _evidence_ids(evidence, ("feature_existence",), limit=5)
    all_quality_ids = _evidence_ids(evidence, ("performance_quality", "user_pain"), limit=5)
    gap_ids = (all_quality_ids or all_feature_ids)[:5]

    feature_products = {}
    for product in products:
        support_ids = _evidence_ids(evidence, ("feature_existence",), product=product, limit=3)
        quality_ids = _evidence_ids(evidence, ("performance_quality", "user_pain"), product=product, limit=3)
        sample_size = len(quality_ids)
        feature_products[product] = {
            "support_status": "supported" if support_ids else "unknown",
            "support_evidence_ids": support_ids,
            "quality_score": {
                "score": 3 if quality_ids else 0,
                "scale": 5,
                "basis": (
                    f"模型请求超时，暂按已采集证据保守汇总；{product} 的质量判断需在报告中结合引用复核"
                    if quality_ids else "模型请求超时，且当前证据不足以判断质量"
                ),
                "aggregation": {
                    "aggregation_type": "timeout_fallback",
                    "positive_mentions": 0,
                    "negative_mentions": 0,
                    "neutral_mentions": sample_size,
                    "sample_size": sample_size,
                    "representative_evidence_ids": quality_ids,
                    "method": f"fallback after analyzer timeout: {reason}",
                },
                "evidence_ids": quality_ids,
            },
        }

    pricing_products = []
    for product in products:
        ids = _evidence_ids(evidence, ("pricing",), product=product, limit=3)
        pricing_products.append({
            "name": product,
            "tiers": [_safe_price_tier(product, ids)] if ids else [],
        })

    persona_ids = _evidence_ids(evidence, ("user_pain", "performance_quality", "market_signal"), limit=4)
    pain_ids = _evidence_ids(evidence, ("user_pain", "performance_quality"), limit=4)
    pain_snippets = [
        _short(ev.get("claim") or ev.get("extracted_snippet"), 80)
        for ev in evidence
        if ev.get("evidence_id") in pain_ids[:2]
    ]
    pain_description = "；".join([p for p in pain_snippets if p]) or "证据不足，暂无法稳定归纳用户痛点"

    return {
        "feature_tree": {
            "category": focus,
            "features": [
                {
                    "feature_id": "F001",
                    "name": focus,
                    "products": feature_products,
                    "gap": {
                        "winner": meta.get("target_product") if products else "unknown",
                        "gap_type": "unknown",
                        "reason": "Analyzer 模型请求超时，当前仅输出基于证据覆盖的保守事实层，暂不做强胜负判断",
                        "evidence_ids": gap_ids,
                        "confidence": 0.35 if gap_ids else 0.0,
                    },
                }
            ],
        },
        "pricing_model": {
            "products": pricing_products,
            "pricing_gap": {
                "target_position": "unknown",
                "summary": "Analyzer 模型请求超时，定价差距需结合引用证据人工复核",
                "evidence_ids": _evidence_ids(evidence, ("pricing", "user_pain", "market_signal"), limit=6),
                "confidence": 0.25,
            },
        },
        "user_persona": {
            "user_segments": [
                {
                    "segment_id": "U001",
                    "name": "待确认用户群",
                    "description": "模型请求超时，暂按已采集用户反馈保守归纳",
                    "evidence_ids": persona_ids,
                    "confidence": 0.35 if persona_ids else 0.0,
                }
            ] if persona_ids else [],
            "pain_points": [
                {
                    "pain_id": "P001",
                    "description": pain_description,
                    "frequency": {
                        "level": "unknown",
                        "count": f"{len(pain_ids)} 条可用反馈证据",
                        "sample_size": len(pain_ids),
                        "evidence_ids": pain_ids,
                    },
                    "affected_products": products,
                    "affected_segments": ["U001"] if persona_ids else [],
                    "user_expectation": "需要更多证据或重跑深度分析后确认",
                    "confidence": 0.35 if pain_ids else 0.0,
                }
            ] if pain_ids else [],
        },
    }


def _fallback_derivations(facts: dict, evidence: list[dict], meta: dict, reason: str) -> dict:
    feature_ids = [
        f.get("feature_id")
        for f in (facts.get("feature_tree") or {}).get("features", [])
        if f.get("feature_id")
    ]
    pain_ids = [
        p.get("pain_id")
        for p in (facts.get("user_persona") or {}).get("pain_points", [])
        if p.get("pain_id")
    ]
    rec_eids = _evidence_ids(
        evidence,
        ("user_pain", "performance_quality", "pricing", "feature_existence", "market_signal"),
        limit=6,
    )
    weights = {
        "pain_frequency": 0.35,
        "business_impact": 0.30,
        "implementation_feasibility": 0.20,
        "evidence_confidence": 0.15,
    }
    score_parts = {
        "pain_frequency": 2,
        "business_impact": 3,
        "implementation_feasibility": 4,
        "evidence_confidence": 2 if rec_eids else 1,
    }
    final_score = round(sum(score_parts[k] * weights[k] for k in weights), 2)
    swot_eids = rec_eids[:3]
    return {
        "swot": {
            "target": meta.get("target_product"),
            "note": f"Analyzer 模型请求超时，SWOT 为保守降级视图: {reason}",
            "strengths": [],
            "weaknesses": [
                {
                    "point": "当前证据已采集，但模型推导阶段超时，结论置信度需要标注为部分可信",
                    "evidence_ids": swot_eids,
                    "confidence": 0.25,
                }
            ] if swot_eids else [],
            "opportunities": [],
            "threats": [],
        },
        "recommendations": [
            {
                "rec_id": "R001",
                "action": "先基于已采集证据输出保守报告，并补跑深度推导以确认优先级",
                "rationale": "Analyzer 模型请求超时；为了不中断用户流程，系统保留可溯源证据并输出低置信度建议",
                "source_feature_ids": feature_ids[:1],
                "source_pain_ids": pain_ids[:1],
                "evidence_ids": rec_eids,
                "priority_score": {
                    **score_parts,
                    "weights": weights,
                    "final_score": final_score,
                    "priority": "P2",
                },
            }
        ],
    }


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


_REQUIRED_CT = ("feature_existence", "performance_quality", "pricing", "user_pain")


def _compact_evidence(evidence: list[dict]) -> list[dict]:
    """给 LLM 的精简证据:按 claim_type 取 top-K(按可信度)+ 截短片段 + 只留必要字段。
    防止证据过多时 prompt 爆炸 → 调用超时。全量证据仍用于本地 evidence_id 校验。
    可调:ANALYZER_MAX_EVIDENCE_PER_TYPE(默认8)、ANALYZER_SNIPPET_LEN(默认180)。"""
    per_type = int(os.environ.get("ANALYZER_MAX_EVIDENCE_PER_TYPE", "8"))
    snip = int(os.environ.get("ANALYZER_SNIPPET_LEN", "180"))
    by_ct: dict[str, list[dict]] = {}
    for e in evidence:
        by_ct.setdefault(e.get("claim_type", "?"), []).append(e)
    out: list[dict] = []
    for lst in by_ct.values():
        top = sorted(lst, key=lambda e: e.get("evidence_confidence", 0) or 0, reverse=True)[:per_type]
        for e in top:
            out.append({
                "evidence_id": e.get("evidence_id"),
                "product": e.get("product"),
                "claim_type": e.get("claim_type"),
                "source_bias": e.get("source_bias"),
                "claim": e.get("claim"),
                "extracted_snippet": (e.get("extracted_snippet") or "")[:snip],
            })
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


def sanitize_derivations(derivations: dict, facts: dict, evidence: list[dict]) -> tuple[dict, int]:
    """确定性修复 derivations(替代昂贵的 LLM 重跑,facts 已用同款策略):
    - 删除 swot/rec 中不存在的 evidence_id
    - 删除 rec 中不在 facts 的 source_feature_ids / source_pain_ids
    - 按 weights 重算 priority_score.final_score,保证 R5 公式自洽
    无法凭空补的(如 rec 一个有效引用都没有)留给 Reviewer 判定。"""
    valid_ids = {e["evidence_id"] for e in evidence if e.get("evidence_id")}
    valid_fids = {
        f["feature_id"] for f in facts.get("feature_tree", {}).get("features", []) if f.get("feature_id")
    }
    valid_pids = {
        p["pain_id"] for p in facts.get("user_persona", {}).get("pain_points", []) if p.get("pain_id")
    }
    dropped = 0

    def _keep(ids: list[str] | None, allowed: set[str]) -> list[str]:
        nonlocal dropped
        src = ids or []
        kept = [x for x in src if x in allowed]
        dropped += len(src) - len(kept)
        return kept

    swot = derivations.get("swot") or {}
    for dim in ("strengths", "weaknesses", "opportunities", "threats"):
        for item in swot.get(dim) or []:
            item["evidence_ids"] = _keep(item.get("evidence_ids"), valid_ids)

    for rec in derivations.get("recommendations") or []:
        rec["evidence_ids"] = _keep(rec.get("evidence_ids"), valid_ids)
        rec["source_feature_ids"] = _keep(rec.get("source_feature_ids"), valid_fids)
        rec["source_pain_ids"] = _keep(rec.get("source_pain_ids"), valid_pids)
        ps = rec.get("priority_score") or {}
        weights = ps.get("weights") or {}
        if weights:
            try:
                ps["final_score"] = round(
                    sum(float(ps.get(k, 0)) * float(w) for k, w in weights.items()), 2
                )
            except (TypeError, ValueError):
                pass
    return derivations, dropped


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

    if not derivations.get("recommendations"):
        issues.append("recommendations: 至少输出 3 条可执行建议；证据极少时也至少 2 条")

    swot = derivations.get("swot") or {}
    swot_count = sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats"))
    if swot_count == 0:
        issues.append("swot: SWOT 四象限不能全部为空，至少输出 target 的优势/劣势/机会/威胁线索")

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


# facts 三个顶层 section 各自独立 → 拆成并行子问答,每个只喂相关 claim_type、只输出该 section,
# 输出量天然减半/三分,既躲开"顶满 max_tokens 被截断 → 残缺 JSON"悬崖,又并行更快。
# 注意:feature_tree 是跨产品对比(gap.winner),所以按 section 拆而不是按产品拆,保对比结构完整。
_FACTS_SECTIONS = {
    "feature_tree": {
        "claim_types": ("feature_existence", "performance_quality", "user_pain"),
        "instruct": "本次任务只输出 `feature_tree` 这一个顶层字段(覆盖所有有证据的功能,每个 feature 必须覆盖 "
                    "target + ≥1 competitor)。不要输出 pricing_model / user_persona。",
    },
    "pricing_model": {
        "claim_types": ("pricing", "user_pain", "market_signal"),
        "instruct": "本次任务只输出 `pricing_model` 这一个顶层字段(覆盖所有有定价证据的产品 + pricing_gap)。"
                    "不要输出 feature_tree / user_persona。",
    },
    "user_persona": {
        "claim_types": ("user_pain", "market_signal", "performance_quality"),
        "instruct": "本次任务只输出 `user_persona` 这一个顶层字段(user_segments + pain_points)。"
                    "不要输出 feature_tree / pricing_model。",
    },
}


_FT_CLAIM_TYPES = ("feature_existence", "performance_quality", "user_pain")


def _feature_tree_split_enabled() -> bool:
    return os.environ.get("ANALYZER_FEATURE_TREE_SPLIT", "1").strip() not in ("0", "false", "False")


def _compute_gap(name: str, products_block: dict, meta: dict) -> dict:
    """确定性算 gap.winner/gap_type/confidence,不再让 LLM 生成(R5/R1 天然自洽)。
    winner = quality_score 最高的产品;证据取 winner 的 support + quality evidence_ids。"""
    target = meta.get("target_product")
    scored = []  # (product, score, pdata)
    for product, pdata in products_block.items():
        score = (pdata.get("quality_score") or {}).get("score") or 0
        scored.append((product, score, pdata))
    if not scored:
        return {"winner": "unknown", "gap_type": "unknown", "reason": "证据不足，暂无法判断胜负",
                "evidence_ids": [], "confidence": 0.0}
    # 分数降序;同分时让 target 优先(避免无谓判负自身)
    scored.sort(key=lambda x: (x[1], x[0] == target), reverse=True)
    winner, top, win_data = scored[0]
    second = scored[1][1] if len(scored) > 1 else 0
    spread = top - second
    eids = ((win_data.get("support_evidence_ids") or [])
            + ((win_data.get("quality_score") or {}).get("evidence_ids") or []))[:4]
    any_missing = any((d.get("support_status") == "not_supported") for _, _, d in scored)
    gap_type = "feature_completeness" if any_missing else ("performance" if spread > 0 else "usability")
    if spread > 0:
        reason = f"{winner} 在「{name}」上质量评分领先（{top}/5 vs 次优 {second}/5）"
    else:
        reason = f"各产品在「{name}」上质量相近（均 {top}/5），差距主要在支持范围"
    conf = round(min(0.85, 0.35 + 0.1 * spread + 0.05 * len(eids)), 2)
    return {"winner": winner, "gap_type": gap_type, "reason": reason,
            "evidence_ids": eids, "confidence": conf}


def _feature_tree_call(system_base: str, evidence: list[dict], meta: dict) -> dict:
    """feature_tree 2 段式(替代单次 ~127s 大调用):
    段1 骨架(只要 4-6 个对比功能 id+name,小输出) → 段2 按产品并行填充 → 段3 确定性 gap。"""
    products = _target_products(meta)
    target = meta.get("target_product")
    focus = " / ".join(meta.get("analysis_focus") or []) or "核心体验"
    timeout = float(os.environ.get("ANALYZER_FACTS_TIMEOUT", "90"))
    ft_ev = [e for e in evidence if e.get("claim_type") in _FT_CLAIM_TYPES]

    # ── 段1:功能骨架 ──
    spine_instruct = (
        f"本次只做一件事:基于证据,列出 {focus} 维度下 4-6 个**适合跨产品横向对比**的功能点。\n"
        '只输出 JSON: {"features":[{"feature_id":"F001","name":"功能名"}]}。\n'
        "feature_id 用 F001/F002…;name 尽量简洁(≤10字);不要输出 products / gap / quality 等其它字段。"
    )
    spine = get_llm().call_json(
        f"{system_base}\n\n## 本次任务范围(重要)\n{spine_instruct}",
        {"analysis_meta": meta, "raw_evidence": _compact_evidence(ft_ev)},
        label="facts:feature_spine", timeout=timeout,
    )
    feats = (spine.get("features") if isinstance(spine, dict) else None) or []
    feats = [f for f in feats if f.get("feature_id") and f.get("name")][:6]
    if not feats:
        feats = [{"feature_id": "F001", "name": focus}]
    _emit_progress(step="facts", phase="section_progress", section="feature_tree",
                   note=f"功能骨架就绪（{len(feats)} 项），按产品并行填充")

    # ── 段2:按产品并行填充 ──
    def _fill(product: str) -> tuple[str, dict]:
        prod_ev = _compact_evidence(
            [e for e in ft_ev if e.get("product") == product]
        )
        fill_instruct = (
            f"对产品「{product}」,针对下面 feature_list 中每个功能逐一评估其支持度与质量。\n"
            '只输出 JSON: {"products":{"F001":{"support_status":"supported|partially_supported|'
            'not_supported|unknown","support_evidence_ids":["..."],"quality_score":{"score":0-5,'
            '"scale":5,"basis":"一句话依据","evidence_ids":["..."]}}}}。\n'
            "support_evidence_ids 只能用 feature_existence 证据;quality_score.evidence_ids 只能用 "
            "performance_quality / user_pain 证据。证据不足填 support_status:unknown、score:0。"
            "严禁编造 evidence_id。"
        )
        out = get_llm().call_json(
            f"{system_base}\n\n## 本次任务范围(重要)\n{fill_instruct}",
            {"analysis_meta": meta, "feature_list": feats, "raw_evidence": prod_ev},
            label=f"facts:feature_fill:{product}", timeout=timeout,
        )
        block = out.get("products") if isinstance(out, dict) else None
        return product, (block or {})

    per_product: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(products))) as ex:
        futs = {ex.submit(_fill, p): p for p in products}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                prod, block = fut.result()
                per_product[prod] = block
                _emit_progress(step="facts", phase="section_progress", section="feature_tree",
                               note=f"{prod} 功能填充完成")
            except Exception as e:  # noqa: BLE001
                print(f"[analyzer] feature_fill '{p}' failed: {type(e).__name__}: {e}")
                per_product[p] = {}

    # ── 段3:组装 + 确定性 gap;缺失产品补 unknown 保证覆盖(过 quick_validate 覆盖检查) ──
    def _unknown_pdata() -> dict:
        return {"support_status": "unknown", "support_evidence_ids": [],
                "quality_score": {"score": 0, "scale": 5, "basis": "证据不足", "evidence_ids": []}}

    features_out = []
    for f in feats:
        fid = f["feature_id"]
        block = {}
        for product in products:
            pdata = (per_product.get(product) or {}).get(fid)
            block[product] = pdata if isinstance(pdata, dict) and pdata else _unknown_pdata()
        features_out.append({
            "feature_id": fid, "name": f["name"], "products": block,
            "gap": _compute_gap(f["name"], block, meta),
        })
    return {"category": focus, "features": features_out}


def _facts_section_call(section: str, system_base: str, evidence: list[dict], meta: dict) -> tuple[str, dict]:
    """单个 section 的子问答:只喂相关 claim_type 的证据、只要求输出该 section。"""
    # feature_tree 走 2 段式拆分(可用 ANALYZER_FEATURE_TREE_SPLIT=0 回退单调用)
    if section == "feature_tree" and _feature_tree_split_enabled():
        return section, _feature_tree_call(system_base, evidence, meta)
    cfg = _FACTS_SECTIONS[section]
    sub_ev = _compact_evidence([e for e in evidence if e.get("claim_type") in cfg["claim_types"]])
    system = f"{system_base}\n\n## 本次任务范围(重要)\n{cfg['instruct']}\n仍需遵守上面所有 HARD CONSTRAINTS。"
    payload = {"analysis_meta": meta, "raw_evidence": sub_ev}
    # facts 三 section 并行,任意一个 hang 不该拖到共享 client 的 200s 才降级。
    # 单独给一个更短超时(ANALYZER_FACTS_TIMEOUT,默认 90s),超时即抛 → 上层用兜底填充。
    timeout = float(os.environ.get("ANALYZER_FACTS_TIMEOUT", "90"))
    out = get_llm().call_json(system, payload, label=f"facts:{section}", timeout=timeout)  # 不设 max_tokens,杜绝截断
    return section, _extract_section(out, section)


def _extract_section(out: object, section: str) -> object:
    """LLM 可能回 {section: <data>} 也可能直接回 <data>(尤其 recommendations 数组)。"""
    if isinstance(out, dict):
        return out.get(section, out)
    return out


# derivations 的 swot / recommendations 在给定 facts 后彼此独立(都只引用 facts + evidence,
# 互不依赖)→ 拆成两个并行子调用,每个输出量减半,总耗时从 ~87s 降到 ~max(swot, rec)。
_DERIV_SECTIONS = {
    "swot": "本次任务只输出 `swot` 这一个顶层字段(target 的四象限,每条可定位到 facts 依据)。"
            "不要输出 recommendations。",
    "recommendations": "本次任务只输出 `recommendations` 这一个顶层字段(可执行、带 priority_score、"
                       "引用 facts 的 source_feature_ids / source_pain_ids)。不要输出 swot。",
}


def _deriv_section_call(section: str, system_base: str, payload: dict) -> tuple[str, object]:
    system = (
        f"{system_base}\n\n## 本次任务范围(重要)\n{_DERIV_SECTIONS[section]}\n"
        "仍需遵守上面所有约束。"
    )
    timeout = float(os.environ.get("ANALYZER_DERIV_TIMEOUT", "90"))
    out = get_llm().call_json(system, payload, label=f"derivations:{section}", timeout=timeout)
    return section, _extract_section(out, section)


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
        _emit_progress(step="facts", phase="done", attempt=1, summary=_facts_summary(facts), preview=_facts_preview(facts))
        return facts

    system = load_prompt("analyzer_facts")
    _emit_progress(step="facts", phase="start", attempt=1,
                   summary=f"并行抽取 {len(_FACTS_SECTIONS)} 个事实 section")
    # 三个 section 并行子问答,各自只输出一个顶层字段 → 不会顶满被截断
    facts: dict = {}
    fb = None  # 懒构造的兜底(整套 facts)
    with ThreadPoolExecutor(max_workers=len(_FACTS_SECTIONS)) as ex:
        futs = {ex.submit(_facts_section_call, s, system, evidence, meta): s
                for s in _FACTS_SECTIONS}
        for fut in as_completed(futs):
            section = futs[fut]
            try:
                sec, data = fut.result()
                facts[sec] = data
                _emit_progress(step="facts", phase="section_done", section=sec)
            except Exception as e:  # noqa: BLE001
                reason = f"{type(e).__name__}: {e}"
                print(f"[analyzer] facts section '{section}' failed; 用兜底填充: {reason}")
                if fb is None:
                    fb = _fallback_facts(evidence, meta, reason)
                facts[section] = fb.get(section, {})
                _emit_progress(step="facts", phase="section_fallback", section=section, note=reason)

    _emit_progress(step="facts", phase="done", attempt=1,
                   summary=_facts_summary(facts), preview=_facts_preview(facts))

    # 拆分后不再走 LLM 重跑(那会退回大调用);引用问题统一用确定性 sanitize(秒级)。
    issues = quick_validate_facts(facts, evidence, meta)
    if issues:
        print(f"[analyzer] facts quick_validate found {len(issues)} issues; 确定性 sanitize")
        _emit_progress(step="facts", phase="repair", issues=len(issues))
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
        _emit_progress(step="derivations", phase="done", attempt=1, summary=_der_summary(der), preview=_derivations_preview(der))
        return der

    system = load_prompt("analyzer_derivations")
    # derivations 主要基于 facts;证据用精简版即可(不再重复塞全量,防 prompt 爆炸)
    payload = {"analysis_meta": meta, "raw_evidence": _compact_evidence(evidence), "facts": facts}
    _emit_progress(step="derivations", phase="start", attempt=1, preview=_facts_preview(facts))

    # swot ‖ recommendations 并行子调用;任一失败用兜底对应字段填充
    der: dict = {}
    fb = None
    with ThreadPoolExecutor(max_workers=len(_DERIV_SECTIONS)) as ex:
        futs = {ex.submit(_deriv_section_call, s, system, payload): s for s in _DERIV_SECTIONS}
        for fut in as_completed(futs):
            section = futs[fut]
            try:
                sec, data = fut.result()
                der[sec] = data
                _emit_progress(step="derivations", phase="section_done", section=sec)
            except Exception as e:  # noqa: BLE001
                reason = f"{type(e).__name__}: {e}"
                print(f"[analyzer] derivations section '{section}' failed; 用兜底填充: {reason}")
                if fb is None:
                    fb = _fallback_derivations(facts, evidence, meta, reason)
                der[section] = fb.get(section)
                _emit_progress(step="derivations", phase="section_fallback", section=section, note=reason)

    _emit_progress(step="derivations", phase="done", attempt=1,
                   summary=_der_summary(der), preview=_derivations_preview(der))

    # 拆分后不再走 LLM 重跑(那会退回 ~130s 大调用);引用/公式问题统一确定性 sanitize(秒级)。
    issues = quick_validate_derivations(der, facts, evidence)
    if issues:
        print(f"[analyzer] derivations quick_validate found {len(issues)} issues; 确定性 sanitize")
        _emit_progress(step="derivations", phase="repair", issues=len(issues))
        der, dropped = sanitize_derivations(der, facts, evidence)
        if dropped:
            print(f"[analyzer] derivations deterministic sanitize dropped {dropped} invalid refs")
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

    _emit_progress(
        step="overview",
        phase="ready",
        summary=f"已读取 {len(evidence)} 条证据，开始抽取事实层",
        preview=_evidence_preview(evidence, meta),
    )
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
