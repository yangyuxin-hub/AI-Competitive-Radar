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
            "praise_points": [],
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
                "expected_impact": "避免用户等待失败，同时保留可复核的低置信度结论",
                "success_metric": "超时场景仍能生成报告；后续深度重跑补齐高置信建议",
                "risk": "保守降级建议可能缺少竞争洞察，不能直接作为最终立项依据",
                "time_horizon": "<1 周",
                "validation_method": "对比降级报告与深度重跑报告的一致性，并抽样人工复核证据链",
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
        "competitor_landscape": {
            "direct": [
                {"name": c, "relation": "direct",
                 "reason": "本次分析纳入的对比竞品", "evidence_ids": []}
                for c in (meta.get("competitors") or [])
            ],
            "indirect": [],
            "alternative": [],
            "selection_rationale": f"模型推导阶段超时({reason});竞品格局按本次分析输入的竞品列表保守给出,未做关系细分。",
        },
        "positioning_map": {
            "products": [
                {"name": p, "target_user": "", "core_scenario": "",
                 "value_proposition": "模型推导超时,定位信息待补", "positioning_label": "",
                 "evidence_ids": []}
                for p in _target_products(meta)
            ],
        },
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


def _norm_tokens(text: str) -> set:
    """归一化成 token 集合(小写、仅留字母数字),用于近似去重相似度。"""
    import re
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _near_dup(a: set, b: set, thresh: float = 0.82) -> bool:
    """两段文本的 token Jaccard ≥ thresh 视为近似重复(同一吐槽的不同措辞)。
    不同档位定价('$10/mo Plus' vs '$20/mo Pro')token 差异大,不会被误判。"""
    if not a or not b:
        return False
    union = len(a | b)
    return union > 0 and len(a & b) / union >= thresh


def _compact_evidence(evidence: list[dict]) -> list[dict]:
    """给 LLM 的精简证据:按 (claim_type × 产品) 各取 top-K(按可信度)+ 近似去重 + 截短片段。
    防止证据过多时 prompt 爆炸 → 调用超时。全量证据仍用于本地 evidence_id 校验。

    关键:**按产品分桶**取 top-K,而非按 claim_type 全局取——否则多产品分析里,
    低可信度产品(如官网 pricing 0.65 < 聚合站)会被全局 top-8 挤光,导致该产品整块为空
    (实测 Linear 官网定价 $0/$10/$16 在,却因全局截断没喂给 LLM → 报告定价空)。
    feature_tree 调用前已按产品过滤,分桶天然单产品、行为不变。

    去冗余:每桶先按可信度排序,再做近似去重——8 个槽位装 8 个**不同的点**,
    而非同一吐槽的 8 种措辞,提升喂给 LLM 的信息密度。空片段直接丢。
    可调:ANALYZER_MAX_EVIDENCE_PER_TYPE(默认8,现为每产品每类)、ANALYZER_SNIPPET_LEN(默认180)、
    ANALYZER_NEARDUP_THRESH(默认0.82,设 1.1 等于关闭近似去重)。"""
    per_type = int(os.environ.get("ANALYZER_MAX_EVIDENCE_PER_TYPE", "8"))
    snip = int(os.environ.get("ANALYZER_SNIPPET_LEN", "180"))
    thresh = float(os.environ.get("ANALYZER_NEARDUP_THRESH", "0.82"))
    by_key: dict[tuple, list[dict]] = {}
    for e in evidence:
        by_key.setdefault((e.get("claim_type", "?"), e.get("product")), []).append(e)
    out: list[dict] = []
    for lst in by_key.values():
        ranked = sorted(lst, key=lambda e: e.get("evidence_confidence", 0) or 0, reverse=True)
        kept_tok: list[set] = []
        for e in ranked:
            text = (e.get("extracted_snippet") or e.get("claim") or "").strip()
            if not text:
                continue
            tok = _norm_tokens(text)
            if any(_near_dup(tok, kt, thresh) for kt in kept_tok):
                continue  # 近似重复,已有更高可信度的代表
            kept_tok.append(tok)
            out.append({
                "evidence_id": e.get("evidence_id"),
                "product": e.get("product"),
                "claim_type": e.get("claim_type"),
                "source_bias": e.get("source_bias"),
                "claim": e.get("claim"),
                "extracted_snippet": (e.get("extracted_snippet") or "")[:snip],
            })
            if len(kept_tok) >= per_type:
                break
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
                    "不要输出 feature_tree / user_persona。\n"
                    "定价提取要求:\n"
                    "- **逐档列全**:从免费档到最高付费档,证据里出现的每个**命名套餐**都要单列一条 tier"
                    "(如 Free / Plus / Business / Enterprise),不要只取一档、不要合并。\n"
                    "- **同一套餐的年付/月付算同一档**:price 用**月度归一**值(normalized_usd_month);"
                    "年付价请换算成每月(年付总额/12 或证据直接给的「per month billed annually」数),"
                    "可在 billing_cycle / note 注明另一计费周期价。\n"
                    "- 每档:{tier_name, segment, price:{amount,currency,normalized_usd_month}, evidence_ids}。"
                    "价格只能来自 pricing 证据,拿不准的档宁可不列,**不要编价**。\n"
                    "- **segment(面向哪类用户)**:据定价页的档位定位归类——`个人`(Free/Personal/Individual/Pro 个人版)、"
                    "`团队`(Team/按人计费的中间档)、`企业`(Enterprise/Business/联系销售档)、`通用`(无法判断时)。"
                    "这样定价表能直接回答「不同规模用户各该选哪档」。\n"
                    "- 免费档只列一条(amount=0);不要重复输出同价档位。",
    },
    "user_persona": {
        "claim_types": ("user_pain", "market_signal", "performance_quality"),
        "instruct": "本次任务只输出 `user_persona` 这一个顶层字段"
                    "(user_segments + pain_points + praise_points)。不要输出 feature_tree / pricing_model。\n"
                    "- pain_points:高频负向反馈/痛点(保持原结构);\n"
                    "- praise_points:高频正向反馈/被用户反复称赞的亮点。每条 {praise_id:PR001..,"
                    "description, frequency:{level,count,sample_size,evidence_ids}, affected_products, confidence}。\n"
                    "- praise_points.frequency.evidence_ids 优先用 performance_quality 的正面证据;"
                    "**无正向证据就给空数组,严禁为凑数把痛点/负面证据塞进来**。",
    },
}


_FT_CLAIM_TYPES = ("feature_existence", "performance_quality", "user_pain")


def _feature_tree_split_enabled() -> bool:
    return os.environ.get("ANALYZER_FEATURE_TREE_SPLIT", "1").strip() not in ("0", "false", "False")


def _real_score(pdata: dict) -> Optional[float]:
    """真实质量分;证据不足(unknown / 0 分且无质量证据)→ None,不参与胜负与均分。"""
    qs = pdata.get("quality_score") or {}
    if (pdata.get("support_status") or "").lower() == "unknown":
        return None
    try:
        f = float(qs.get("score"))
    except (TypeError, ValueError):
        return None
    has_ev = bool(qs.get("evidence_ids") or (qs.get("aggregation") or {}).get("representative_evidence_ids"))
    if f <= 0 and not has_ev:
        return None
    return f


def _compute_gap(name: str, products_block: dict, meta: dict) -> dict:
    """确定性算 gap.winner/gap_type/confidence(R5/R1 天然自洽)。
    关键:只在**有真实质量证据**的产品间判胜负 —— 不把"证据不足(0/unknown)"
    当成真实低分,避免"4 vs 0"式的伪领先和"均 0/5 打平"式的错误结论。"""
    target = meta.get("target_product")
    rated = []  # (product, score, pdata) —— 仅含有真实分的产品
    for product, pdata in products_block.items():
        s = _real_score(pdata)
        if s is not None:
            rated.append((product, s, pdata))

    def _eids(pdata: dict) -> list[str]:
        return ((pdata.get("support_evidence_ids") or [])
                + ((pdata.get("quality_score") or {}).get("evidence_ids") or []))[:4]

    if not rated:
        # 全员都没质量分,但若官网已确认各产品都具备 → 是「能力对等、未评分」,不是「没证据」
        supported = [(p, d) for p, d in products_block.items()
                     if (d.get("support_status") or "").lower() in ("supported", "partially_supported")]
        if len(supported) >= 2:
            sids: list[str] = []
            for _, d in supported:
                sids += (d.get("support_evidence_ids") or [])
            return {"winner": "unknown", "gap_type": "parity_unrated",
                    "reason": f"各产品均具备「{name}」能力(官网/证据确认),但缺少用户质量证据,暂不分优劣",
                    "evidence_ids": sids[:4], "confidence": 0.2}
        return {"winner": "unknown", "gap_type": "unknown",
                "reason": f"各产品在「{name}」上均缺少证据，暂不判断胜负",
                "evidence_ids": [], "confidence": 0.0}

    if len(rated) == 1:
        # 只有一个产品有证据 → 不与"没数据"的对手强行比;如实标注
        winner, score, win_data = rated[0]
        return {"winner": winner, "gap_type": "insufficient_evidence",
                "reason": f"仅 {winner} 在「{name}」上有足够质量证据（{score:.0f}/5），"
                          "其余产品证据不足，暂不作强对比",
                "evidence_ids": _eids(win_data), "confidence": 0.3}

    # ≥2 个产品有真实分:在它们之间判胜负
    rated.sort(key=lambda x: (x[1], x[0] == target), reverse=True)
    winner, top, win_data = rated[0]
    second = rated[1][1]
    spread = top - second
    any_missing = any((d.get("support_status") == "not_supported") for _, _, d in rated)
    gap_type = "feature_completeness" if any_missing else ("performance" if spread > 0 else "usability")
    if spread > 0:
        reason = f"{winner} 在「{name}」上质量评分领先（{top:.0f}/5 vs 次优 {second:.0f}/5）"
    else:
        reason = f"已评分产品在「{name}」上质量相近（均 {top:.0f}/5），差距主要在支持范围"
    conf = round(min(0.85, 0.35 + 0.1 * spread + 0.05 * len(_eids(win_data))), 2)
    return {"winner": winner, "gap_type": gap_type, "reason": reason,
            "evidence_ids": _eids(win_data), "confidence": conf}


def _feature_spine(system_base: str, evidence: list[dict], meta: dict) -> list[dict]:
    """段1:从证据里抽 4-6 个适合跨产品对比的功能点(只要 id+name,小输出)。"""
    focus = " / ".join(meta.get("analysis_focus") or []) or "核心体验"
    timeout = float(os.environ.get("ANALYZER_FACTS_TIMEOUT", "90"))
    ft_ev = [e for e in evidence if e.get("claim_type") in _FT_CLAIM_TYPES]
    # 喂给 spine:每个产品有多少 feature 类证据 → 引导它只挑「多数产品都接得住」的能力维度
    cov_by_prod: dict[str, int] = {}
    for e in ft_ev:
        p = e.get("product") or "?"
        cov_by_prod[p] = cov_by_prod.get(p, 0) + 1
    spine_instruct = (
        f"本次只做一件事:基于证据,列出 {focus} 维度下 4-6 个**适合跨产品横向对比**的功能点。\n"
        "要求:\n"
        "1. **产品中立**:是该品类的通用对比维度,不能是某个产品的专有叫法/卖点,这样每个产品都能在同一维度被公平评估。\n"
        "2. **粒度必须是「产品能力级」,不是「工程实现细节级」**:维度应是用户能感知、**官网产品介绍/功能页里会专门描述、"
        "各产品都可能具备**的能力模块。**绝不要**把一个能力拆成实现细节——那种维度官网不会逐条写、证据极稀疏,"
        "会导致整列全空、矩阵塌方。\n"
        "   - ✅ 好维度(官网会写、可对比):实时多人协同 / 组件与设计系统 / 原型与交互 / 开发者交付 / 版本管理\n"
        "   - ❌ 坏维度(太细、官网不会逐条写、证据接不住):实时光标同步 / 操作同步延迟 / 编辑冲突处理 / 断网重连恢复\n"
        "3. **严禁把「计划档位 / 资源配额 / 访问权限 / 价格细节」当功能维度**——那是定价分析的范畴,放进功能矩阵必然整列空、塌方。\n"
        "   - ❌ 坏维度(计划/配额/权限,绝不要):免费版权益配置 / 团队成员上限 / 离线使用权限 / 各档位资源配额 / AI功能使用权限 / 存储空间上限\n"
        "   - ✅ 对应应改成的能力维度:协作与权限管理(整体能力)/ AI与自动化能力 / 离线与跨端可用性 —— 用「能力」表述,不要用「档位/配额/权益」。\n"
        "4. **维度必须能被现有证据接住**:优先选 raw_evidence 里反复出现、且 **target 和至少一个 competitor 都涉及**的能力;"
        "参考下方 `feature_evidence_count_by_product`——别选只有一个产品有证据的维度。宁可少给两个扎实维度,也不要凑数造细维度。\n"
        '只输出 JSON: {"features":[{"feature_id":"F001","name":"功能名"}]}。\n'
        "feature_id 用 F001/F002…;name 用产品能力级短语(≤12字);不要输出 products / gap / quality 等其它字段。"
    )
    spine = get_llm().call_json(
        f"{system_base}\n\n## 本次任务范围(重要)\n{spine_instruct}",
        {"analysis_meta": meta, "feature_evidence_count_by_product": cov_by_prod,
         "raw_evidence": _compact_evidence(ft_ev)},
        label="facts:feature_spine", timeout=timeout,
    )
    feats = (spine.get("features") if isinstance(spine, dict) else None) or []
    feats = [f for f in feats if f.get("feature_id") and f.get("name")][:6]
    return feats or [{"feature_id": "F001", "name": focus}]


def _feature_enrich_enabled() -> bool:
    return os.environ.get("ANALYZER_FEATURE_ENRICH", "1").strip() not in ("0", "false", "False")


def _enrich_evidence_by_features(
    evidence: list[dict], meta: dict, system_base: str,
) -> tuple[list[dict], Optional[list[dict]]]:
    """按 feature 骨架做针对性补采:先抽 spine,再对每个产品按功能名补搜证据,
    合并去重回 evidence。返回 (合并后的 evidence, spine);spine 供 facts 复用免重抽。
    需要 Tavily;失败则原样返回。"""
    from . import search  # 采集层

    if not search.tavily_available():
        return evidence, None
    try:
        spine = _feature_spine(system_base, evidence, meta)
    except Exception as e:  # noqa: BLE001
        print(f"[analyzer] enrich: spine 生成失败,跳过补采: {e}")
        return evidence, None
    feat_names = [f["name"] for f in spine if f.get("name")]
    products = _target_products(meta)
    focus = (meta.get("analysis_focus") or [""])[0] if meta.get("analysis_focus") else ""
    if not feat_names or not products:
        return evidence, spine

    _emit_progress(step="facts", phase="enrich_start",
                   summary=f"按 {len(feat_names)} 个功能为 {len(products)} 个产品针对性补采证据")
    existing_ids = {e.get("evidence_id") for e in evidence}
    added: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, len(products))) as ex:
        futs = {
            ex.submit(search.feature_targeted_evidence, p, feat_names, focus): p
            for p in products
        }
        for fut in as_completed(futs):
            product = futs[fut]
            try:
                for ev in fut.result():
                    eid = ev.get("evidence_id")
                    if eid and eid not in existing_ids:
                        existing_ids.add(eid)
                        added.append(ev)
            except Exception as e:  # noqa: BLE001
                print(f"[analyzer] enrich '{product}' 补采失败(忽略): {e}")
    print(f"[analyzer] feature-targeted enrich added {len(added)} new evidence")
    _emit_progress(step="facts", phase="enrich_done",
                   summary=f"针对性补采新增 {len(added)} 条证据")
    return evidence + added, spine


def _feature_tree_call(system_base: str, evidence: list[dict], meta: dict,
                       spine: Optional[list[dict]] = None,
                       only_products: Optional[list[str]] = None,
                       prev_tree: Optional[dict] = None) -> dict:
    """feature_tree 2 段式(替代单次 ~127s 大调用):
    段1 骨架(只要 4-6 个对比功能 id+name,小输出) → 段2 按产品并行填充 → 段3 确定性 gap。
    spine 可由上游(补采阶段)预生成并传入,避免重复调用。
    only_products 非空时只重填这些产品(缺口补采用),其余产品从 prev_tree 复用——
    避免 gap-refill 把没缺口的产品也整套重跑(省 ~2/3 LLM 调用)。"""
    products = _target_products(meta)
    fill_products = [p for p in (only_products or products) if p in products]
    prev_by_fid = {f.get("feature_id"): (f.get("products") or {})
                   for f in ((prev_tree or {}).get("features") or [])}
    target = meta.get("target_product")
    focus = " / ".join(meta.get("analysis_focus") or []) or "核心体验"
    timeout = float(os.environ.get("ANALYZER_FACTS_TIMEOUT", "90"))
    ft_ev = [e for e in evidence if e.get("claim_type") in _FT_CLAIM_TYPES]

    # ── 段1:功能骨架(已有则复用) ──
    feats = spine or _feature_spine(system_base, evidence, meta)
    _emit_progress(step="facts", phase="section_progress", section="feature_tree",
                   note=f"功能骨架就绪（{len(feats)} 项），按产品并行填充")

    # ── 段2:按产品并行填充 ──
    # 痛点/流失归因类问题:质量分是次要的,重点是痛点与支持度。
    # 弱化"务必拉开评分"的压力,避免对定性痛点证据硬凑 0-5 分 → 减少残余「推测」。
    pain_intent = meta.get("analysis_intent") == "pain_attribution"
    pain_note = (
        "## 本次是痛点归因分析(重点不是打分)\n"
        "本次目标是定位用户痛点与流失动因,质量分仅作辅助。**缺质量证据时坦然给 "
        "unknown+score 0,绝不要为『拉开区分度』硬凑分**;support_status 与痛点证据优先。\n\n"
        if pain_intent else ""
    )

    def _fill(product: str) -> tuple[str, dict]:
        prod_ev = _compact_evidence(
            [e for e in ft_ev if e.get("product") == product]
        )
        fill_instruct = (
            pain_note
            + f"对产品「{product}」,针对下面 feature_list 中每个功能逐一评估其支持度与质量。\n"
            '只输出 JSON: {"products":{"F001":{"support_status":"supported|partially_supported|'
            'not_supported|unknown","support_evidence_ids":["..."],"quality_score":{"score":0-5,'
            '"scale":5,"basis":"一句话依据","evidence_ids":["..."]}}}}。\n'
            "support_evidence_ids 只能用 feature_existence 证据;quality_score.evidence_ids 只能用 "
            "performance_quality / user_pain 证据。\n"
            "## 关键:支持度 与 质量分 分开判,各用各的证据(不要绑死)\n"
            "### support_status —— 该产品**是否具备**这个能力(优先用 feature_existence / 官网 vendor_claim 证据)\n"
            "- supported=官网功能页/产品介绍明确描述了该能力;partially_supported=只部分具备或明显受限;\n"
            "- not_supported=证据明确表明没有;unknown=**连官网都没有任何相关介绍**(真的一点线索都没有时才用)。\n"
            "- **核心:官网产品介绍能证明『具备』,就给 supported,哪怕完全没有用户体验数据**——"
            "绝不要因为缺质量证据,就把本可由官网确认的支持度也写成 unknown。这是矩阵不塌方的关键。\n"
            "### quality_score —— 该能力**好不好**(只用 performance_quality / user_pain 证据,严禁用官网营销话术补分)\n"
            "- 5=多条用户/第三方证据一致称业界领先;4=明确优于同类;3=可用/评价不一;2=明显短板;1=几乎不可用;\n"
            "- 0=**没有任何质量证据** → score 0,basis 写『仅确认具备,无质量证据』;"
            "**这条只代表没评分,不要因此改动上面的 support_status**。\n"
            "## 纪律\n"
            "- support_status 可采信官网/feature_existence;quality_score **只**采信 user_generated/third_party;\n"
            "- basis 写成**可对比**的一句话(点出快/慢、准/糙),或在无质量证据时如实写『仅确认具备』;\n"
            "- 严禁编造 evidence_id。\n"
            "## 微示例\n"
            '有官网无评价(常见,务必照此填): {"support_status":"supported","support_evidence_ids":["SAAA1111"],'
            '"quality_score":{"score":0,"scale":5,"basis":"官网功能页确认具备该能力,但无用户质量证据","evidence_ids":[]}}\n'
            '有评价: {"support_status":"supported","support_evidence_ids":["SAAA1111"],'
            '"quality_score":{"score":4,"scale":5,"basis":"第三方实测延迟100-200ms,优于多数同类","evidence_ids":["SBBB2222"]}}\n'
            '真无任何线索: {"support_status":"unknown","support_evidence_ids":[],'
            '"quality_score":{"score":0,"scale":5,"basis":"未检索到该能力的任何证据","evidence_ids":[]}}'
        )
        out = get_llm().call_json(
            f"{system_base}\n\n## 本次任务范围(重要)\n{fill_instruct}",
            {"analysis_meta": meta, "feature_list": feats, "raw_evidence": prod_ev},
            label=f"facts:feature_fill:{product}", timeout=timeout,
        )
        block = out.get("products") if isinstance(out, dict) else None
        return product, (block or {})

    per_product: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(fill_products))) as ex:
        futs = {ex.submit(_fill, p): p for p in fill_products}
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
            if product in fill_products:
                pdata = (per_product.get(product) or {}).get(fid)
            else:
                # 没缺口的产品:复用上一轮的填充,不重跑 LLM
                pdata = (prev_by_fid.get(fid) or {}).get(product)
            block[product] = pdata if isinstance(pdata, dict) and pdata else _unknown_pdata()
        features_out.append({
            "feature_id": fid, "name": f["name"], "products": block,
            "gap": _compute_gap(f["name"], block, meta),
        })
    # 剪枝:整行所有产品都是 support unknown(矩阵里全"—")= 取不到任何证据的过细/跑偏维度(常是定价细节
    # 被当功能,如「团队成员上限」)。这种行搜了也填不上,是噪声 → 移出矩阵,展示有据可依的维度更可信;
    # 同时让下一轮 _coverage_gaps 不再把它们算作 unknown_cells 去空转补采。至少保留 3 行避免空表。
    grounded = [
        f for f in features_out
        if any((f["products"].get(p) or {}).get("support_status") != "unknown" for p in products)
    ]
    # floor=2:整行全"—"的噪声行一律剪掉,只要还剩 ≥2 行。薄而诚实的矩阵 > 补一堆空行的"伪塌方"。
    # 剩 <2 行说明证据太稀(集采问题),此时保留全部(含空行)给读者看"尝试过哪些维度"。
    if len(grounded) >= 2 and len(grounded) < len(features_out):
        print(f"[analyzer] 功能矩阵剪枝:移除 {len(features_out) - len(grounded)} 个全无证据维度(全'—'行)")
        features_out = grounded
    return {"category": focus, "features": features_out}


def _normalize_pricing_tiers(facts: dict) -> int:
    """确定性整理 pricing_model.tiers:同价档去重(尤其 LLM 偶发重复输出免费档)+ 按月度价升序。
    去重键优先用 normalized_usd_month,缺失回退 tier_name;保留先出现且信息更全(evidence 多)的那条。
    返回去重掉的档数。纯确定性,不调 LLM。"""
    pm = facts.get("pricing_model") or {}
    removed = 0

    def _month(t: dict) -> Optional[float]:
        pr = t.get("price") or {}
        v = pr.get("normalized_usd_month", pr.get("amount"))
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for prod in pm.get("products") or []:
        tiers = prod.get("tiers") or []
        best: dict = {}  # key -> tier(保留 evidence 更多的)
        order: list = []
        for t in tiers:
            m = _month(t)
            key = round(m, 2) if m is not None else (t.get("tier_name") or "").strip().lower()
            if key not in best:
                best[key] = t
                order.append(key)
            else:
                removed += 1
                cur = best[key]
                if len((t.get("evidence_ids") or [])) > len((cur.get("evidence_ids") or [])):
                    best[key] = t  # 替换为信息更全的
        deduped = [best[k] for k in order]
        deduped.sort(key=lambda t: (_month(t) is None, _month(t) if _month(t) is not None else 0))
        prod["tiers"] = deduped
    return removed


def _facts_section_call(section: str, system_base: str, evidence: list[dict], meta: dict,
                        spine: Optional[list[dict]] = None,
                        only_products: Optional[list[str]] = None,
                        prev_section: Optional[dict] = None) -> tuple[str, dict]:
    """单个 section 的子问答:只喂相关 claim_type 的证据、只要求输出该 section。"""
    # feature_tree 走 2 段式拆分(可用 ANALYZER_FEATURE_TREE_SPLIT=0 回退单调用)
    if section == "feature_tree" and _feature_tree_split_enabled():
        return section, _feature_tree_call(system_base, evidence, meta, spine=spine,
                                           only_products=only_products, prev_tree=prev_section)
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
    "recommendations": "本次任务只输出 `recommendations` 这一个顶层字段,按优先级从高到低排序。不要输出 swot。\n"
                       "每条建议必须引用 facts 的 source_feature_ids / source_pain_ids,且**必须包含 "
                       "`priority_score`**:{pain_frequency,business_impact,implementation_feasibility,"
                       "evidence_confidence 各 1-5 整数, weights 用 {0.35,0.30,0.20,0.15}, "
                       "final_score=各项×权重之和(保留两位小数), priority 为 P0/P1/P2}。\n"
                       "另尽量补齐可落地字段(让 PM 能直接立项):`expected_impact`(预期收益)、"
                       "`success_metric`(验收指标)、`risk`(主要风险)、`time_horizon`(周期)。无把握的字段可省略,不要编造。",
    "competitor_landscape": "本次任务只输出 `competitor_landscape` 一个顶层字段。把 analysis_meta.competitors "
                            "及你从证据中识别到的相关玩家,按竞争关系分三类:direct(直接竞品)/indirect(间接竞品)/"
                            "alternative(替代方案),每类是数组,元素 {name, relation, reason(为何纳入,一句话), "
                            "evidence_ids(可空)}。再给一句 selection_rationale(纳入与筛选标准)。\n"
                            "analysis_meta.competitors 里的产品默认归 direct;间接/替代可补充同品类相邻方案。"
                            "evidence_ids 只引用 raw_evidence 里真实存在的 ID,没有就给空数组,**严禁编造**。",
    "positioning_map": "本次任务只输出 `positioning_map` 一个顶层字段:{products:[{name, target_user, core_scenario, "
                       "value_proposition(官方价值主张/定位摘要), positioning_label(≤6字定位标签,如 'AI IDE'), "
                       "evidence_ids(可空)}]},覆盖 target + 各 competitor 共 N 个产品。\n"
                       "evidence_ids 优先引用该产品的 feature_existence / vendor_claim 证据,只引用真实存在的 ID,"
                       "没有就空数组,**严禁编造**。",
}


_PRIORITY_WEIGHTS = {
    "pain_frequency": 0.35,
    "business_impact": 0.30,
    "implementation_feasibility": 0.20,
    "evidence_confidence": 0.15,
}


def _ensure_priority_scores(der: dict) -> int:
    """兜底:LLM 偶尔漏 priority_score(尤其拆分后)。缺失时按 LLM 给出的排序补一个
    R5 自洽的 priority_score(越靠前分越高),保证「优先级建议」这一核心交付不缺。
    已有合法 priority_score 的不动。返回补了几条。"""
    recs = der.get("recommendations") or []
    filled = 0
    for idx, rec in enumerate(recs):
        ps = rec.get("priority_score")
        if isinstance(ps, dict) and ps.get("final_score") is not None and ps.get("priority"):
            continue
        base = max(1, 5 - idx)  # 第1条=5,依次递减,最低 1
        parts = {
            "pain_frequency": base,
            "business_impact": base,
            "implementation_feasibility": max(2, base - 1),
            "evidence_confidence": 3 if rec.get("evidence_ids") else 1,
        }
        final = round(sum(parts[k] * _PRIORITY_WEIGHTS[k] for k in _PRIORITY_WEIGHTS), 2)
        priority = "P0" if idx == 0 else ("P1" if idx <= 2 else "P2")
        rec["priority_score"] = {**parts, "weights": dict(_PRIORITY_WEIGHTS),
                                 "final_score": final, "priority": priority}
        filled += 1
    return filled


def _deriv_section_call(section: str, system_base: str, payload: dict) -> tuple[str, object]:
    system = (
        f"{system_base}\n\n## 本次任务范围(重要)\n{_DERIV_SECTIONS[section]}\n"
        "仍需遵守上面所有约束。"
    )
    timeout = float(os.environ.get("ANALYZER_DERIV_TIMEOUT", "90"))
    out = get_llm().call_json(system, payload, label=f"derivations:{section}", timeout=timeout)
    return section, _extract_section(out, section)


def _step1_facts(evidence: list[dict], meta: dict, analyzer_retry: int = 0,
                 spine: Optional[list[dict]] = None,
                 only_sections: Optional[list[str]] = None,
                 only_products: Optional[list[str]] = None,
                 prev_facts: Optional[dict] = None) -> dict:
    """Step 1 — 事实层。only_sections 非空时只重跑这些 section(用于缺口补采后的局部重出,
    避免把没缺口的 pricing/persona 也整套重算),返回的 dict 只含这些 section,由调用方合并。
    only_products 非空时,feature_tree 只重填这些产品(其余从 prev_facts 复用)。"""
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

    sections = only_sections or _FACTS_SECTIONS
    is_partial = only_sections is not None
    system = load_prompt("analyzer_facts")
    if not is_partial:
        _emit_progress(step="facts", phase="start", attempt=1,
                       summary=f"并行梳理 {len(sections)} 个事实板块")
    # 各 section 并行子问答,各自只输出一个顶层字段 → 不会顶满被截断
    facts: dict = {}
    fb = None  # 懒构造的兜底(整套 facts)
    prev_ft = (prev_facts or {}).get("feature_tree")
    with ThreadPoolExecutor(max_workers=len(sections)) as ex:
        futs = {ex.submit(_facts_section_call, s, system, evidence, meta,
                          spine if s == "feature_tree" else None,
                          only_products if s == "feature_tree" else None,
                          prev_ft if s == "feature_tree" else None): s
                for s in sections}
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

    if not is_partial:
        _emit_progress(step="facts", phase="done", attempt=1,
                       summary=_facts_summary(facts), preview=_facts_preview(facts))

    # 确定性整理定价档位(去重同价档 + 升序),纯本地不调 LLM。
    if "pricing_model" in facts:
        n = _normalize_pricing_tiers(facts)
        if n:
            print(f"[analyzer] pricing 去重 {n} 个重复档位")

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
            "competitor_landscape": sr.get("competitor_landscape", {}),
            "positioning_map": sr.get("positioning_map", {}),
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

    # 兜底补缺失的 priority_score(LLM 拆分后偶发漏填,保「优先级建议」不缺)
    n_filled = _ensure_priority_scores(der)
    if n_filled:
        print(f"[analyzer] backfilled priority_score for {n_filled} recommendation(s)")

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


def _gap_refill_enabled() -> bool:
    return os.environ.get("ANALYZER_GAP_REFILL", "1").strip() not in ("0", "false", "False")


def _coverage_gaps(facts: dict, meta: dict, evidence: list[dict]) -> dict:
    """扫描 facts 暴露的缺口:未评分的 (产品×功能) 格子 + 缺失的必需 claim_type。"""
    products = _target_products(meta)
    unknown_cells: list[tuple[str, str]] = []
    for feat in (facts.get("feature_tree") or {}).get("features", []):
        name = feat.get("name")
        if not name:
            continue
        for p in products:
            d = (feat.get("products") or {}).get(p) or {}
            if (d.get("support_status") or "").lower() == "unknown":
                unknown_cells.append((p, name))
    present_ct = {e.get("claim_type") for e in evidence}
    missing_ct = [ct for ct in _REQUIRED_CT if ct not in present_ct]

    # 定价抽到了但整张表没有任何可用价格数值(全 $0/None)→ 抽取失败,当作缺口触发定向重搜定价。
    # 只在「全表无正价」时触发(强信号),避免把某个真免费档误判成缺口。
    pm_products = (facts.get("pricing_model") or {}).get("products") or []
    tiers_by_prod: dict[str, list] = {p.get("product"): (p.get("tiers") or []) for p in pm_products}
    all_tiers = [t for ts in tiers_by_prod.values() for t in ts]

    def _amt(t):
        return (t.get("price") or {}).get("normalized_usd_month")

    if all_tiers and "pricing" not in missing_ct:
        if not any(isinstance(_amt(t), (int, float)) and _amt(t) > 0 for t in all_tiers):
            missing_ct.append("pricing")
            print("[analyzer] pricing 全表无可用价格,标记为缺口 → 定向重搜定价")

    # per-product 定价缺口:某 target 产品在 pricing_model 里一档都没有,而至少一个别的产品有正价
    # → 多半是该产品定价抽取/抓取漏了(而非它真免费),定向补回。比「全表空」的强信号更细。
    pricing_gap_products: list[str] = []
    any_priced = any(isinstance(_amt(t), (int, float)) and _amt(t) > 0 for t in all_tiers)
    if any_priced and "pricing" not in missing_ct:
        for p in products:
            if not tiers_by_prod.get(p):  # 该产品缺席 pricing_model 或 0 档
                pricing_gap_products.append(p)
        if pricing_gap_products:
            print(f"[analyzer] per-product 定价缺口: {pricing_gap_products} 无档位而别家有 → 定向补采")

    return {
        "unknown_cells": unknown_cells,
        "missing_claim_types": missing_ct,
        "pricing_gap_products": pricing_gap_products,
    }


def _gap_affected_sections(gaps: dict) -> list[str]:
    """把缺口映射到需要重跑的 facts section(局部重跑用,避免整套重算)。"""
    secs: set = set()
    if gaps.get("unknown_cells"):
        secs.add("feature_tree")
    if gaps.get("pricing_gap_products"):
        secs.add("pricing_model")
    for ct in gaps.get("missing_claim_types") or []:
        if ct == "pricing":
            secs.add("pricing_model")
        elif ct == "user_pain":
            secs.add("user_persona")
        else:  # feature_existence / performance_quality 都在 feature_tree 里
            secs.add("feature_tree")
    return [s for s in _FACTS_SECTIONS if s in secs]


def _recollect_pricing_official(products: list[str], focus: str) -> list[dict]:
    """定价缺口治本:对配了 pricing_pages/official_pages 的产品,直接走官网定价页 +
    Playwright 渲染(SPA 档位价的权威出处),比 web_search 命中准、能拿到真实档位。
    未配 URL 的产品由调用方的 web_search 兜底。"""
    if not products:
        return []
    try:
        from .collector import OfficialPageAdapter
    except Exception:  # noqa: BLE001
        return []
    official = OfficialPageAdapter()
    out: list[dict] = []
    for product in products:
        if not official.can_fetch(product):
            continue
        try:
            evs = official.fetch(product, focus)
            priced = [e for e in evs if e.get("claim_type") == "pricing"]
            out.extend(priced)
            print(f"[analyzer] 官网定价补采 '{product}': {len(priced)} 条 pricing 证据")
        except Exception as e:  # noqa: BLE001
            print(f"[analyzer] 官网定价补采 '{product}' failed: {type(e).__name__}: {e}")
    return out


def _gap_targeted_recollect(meta: dict, gaps: dict, focus: str, round_idx: int = 0) -> list[dict]:
    """对缺口做定向搜索:每个空缺 (产品×功能) 一条查询 + 每个产品对缺失 claim_type 一条查询 +
    per-product 定价缺口走官网渲染。比盲目全量补采更省额度、命中更准。

    query/site 构造复用 source_planner 的语言一致构造器 + 站点锚定,杜绝旧版
    `{英文产品} {中文功能} {中文焦点}` 中英混搭 + site="" 裸搜捞同名页/学术站的问题。
    round_idx≥1(多轮升级):提高 results_per_query 并放开站点锚定(全网兜底),扩大召回。"""
    from collections import defaultdict

    from . import search, source_planner as sp

    added: list[dict] = []
    # 定价缺口走官网渲染(不依赖搜索额度,SPA 档位价最权威),两种触发:
    #   - per-product 缺口(有的产品有价、有的没);
    #   - 全表无价(pricing 整类缺失;per-product 因 any_priced=False 不触发,这里兜住——治"AI编程定价全空")。
    pricing_gap_products = gaps.get("pricing_gap_products") or []
    pricing_targets = list(pricing_gap_products)
    if "pricing" in (gaps.get("missing_claim_types") or []):
        for p in _target_products(meta):
            if p not in pricing_targets:
                pricing_targets.append(p)
    if pricing_targets:
        added.extend(_recollect_pricing_official(pricing_targets, focus))

    if not search.tavily_available():
        return added
    domain = os.environ.get("DOMAIN", "").strip()
    cat_en, cat_cn = sp._domain_category(domain)
    by_ct = sp.load_sources_config().get("by_claim_type") or {}
    max_cells = int(os.environ.get("ANALYZER_GAP_MAX_QUERIES", "10"))
    max_sites = int(os.environ.get("ANALYZER_GAP_MAX_SITES", "2"))
    # 多轮升级:第 2 轮起放开站点锚定 + 多取结果,把第一轮没补到的缺口用更宽的网捞
    widen = round_idx >= 1
    rpq = 3 if round_idx == 0 else 5
    plans: dict[str, list[dict]] = defaultdict(list)

    def _emit(product: str, ct: str, focus_kw: str) -> None:
        """按 claim_type 锚定权威源各发一条;widen/无 site 时给一条全网兜底(相关性门兜底过滤)。"""
        base_q = sp._build_query(product, focus_kw, ct, cat_en, cat_cn)
        sites = [] if widen else sp._sites_for_claim(product, ct, by_ct)[:max_sites]
        if sites:
            for site, st, bias in sites:
                plans[product].append({
                    "query": base_q, "claim_type": ct, "site": site,
                    "source_type": st, "bias": bias,
                })
        else:
            plans[product].append({
                "query": base_q, "claim_type": ct, "site": "",
                "source_type": "web_search",
                "bias": "vendor_claim" if ct in ("pricing", "feature_existence") else "third_party",
            })

    # 空白格 (产品×功能,support_status=unknown):缺的是「该产品到底有没有这个能力」=feature_existence,
    # 官网/文档是权威出处(和定价同理)。旧版搜 performance_quality(UGC质量)填不了「—」格,导致矩阵塌陷。
    # 同时补一条质量搜索:若该能力确实存在,顺带捞 UGC 评价(命中则连质量分一起补上)。
    for product, fname in gaps["unknown_cells"][:max_cells]:
        _emit(product, "feature_existence", fname or focus)
        _emit(product, "performance_quality", fname or focus)
    # 整类缺失:每个产品对缺失 claim_type 各补一条(焦点回退到分析焦点)
    for product in _target_products(meta):
        for ct in gaps["missing_claim_types"]:
            _emit(product, ct, focus)
    # per-product 定价缺口:除官网外,再补一条 pricing 搜索(覆盖未配官网 URL 的产品)
    for product in pricing_gap_products:
        _emit(product, "pricing", focus)

    for product, plan in plans.items():
        try:
            evs, _ = search.search_plan_to_evidence(product, plan, results_per_query=rpq)
            added.extend(evs)
        except Exception as e:  # noqa: BLE001
            print(f"[analyzer] gap recollect '{product}' failed: {type(e).__name__}: {e}")
    return added


def _survey_enabled() -> bool:
    return os.environ.get("ANALYZER_SURVEY", "1").strip() not in ("0", "false", "False")


def _real_ugc_count(evidence: list[dict]) -> int:
    """真实用户侧证据数(非合成):reddit/hn/v2ex/UGC 搜索。"""
    return sum(
        1 for e in evidence
        if e.get("source_bias") == "user_generated"
        and not str(e.get("source_url") or "").startswith("synthetic")
    )


def _survey_should_run(evidence: list[dict], meta: dict) -> bool:
    """合成问卷只作兜底:真实 UGC 充足时不跑(省时 + 避免合成数据污染结论)。
    SURVEY_MIN_REAL_UGC(默认 8)条真实用户证据以上即跳过;不足则用合成兜底(已标注)。"""
    if not _survey_enabled():
        return False
    threshold = int(os.environ.get("SURVEY_MIN_REAL_UGC", "8"))
    real = _real_ugc_count(evidence)
    if real >= threshold:
        print(f"[analyzer] survey 跳过:已有 {real} 条真实 UGC(≥{threshold}),不用合成兜底")
        return False
    return True


def _run_survey(evidence: list[dict], meta: dict) -> tuple[list[dict], Optional[dict]]:
    """问卷/用户访谈采集 Agent(合成,已标注):对每个产品设计问卷+模拟访谈→证据。
    在 analyzer 跑(不受采集超时限制、默认全档生效)。返回 (合并 evidence, research_method)。"""
    from .survey_skill import SurveySkill
    sk = SurveySkill()
    products = _target_products(meta)
    focus = (meta.get("analysis_focus") or [""])[0] if meta.get("analysis_focus") else ""
    existing = {e.get("evidence_id") for e in evidence}
    added: list[dict] = []
    questions: list[dict] = []
    personas: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, len(products))) as ex:
        futs = {ex.submit(sk.execute, [], product=p, focus=focus): p for p in products}
        for fut in as_completed(futs):
            try:
                evs, m = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[analyzer] survey '{futs[fut]}' failed: {type(e).__name__}: {e}")
                continue
            if not questions and m.get("questionnaire"):
                questions = m["questionnaire"]
            for e in evs:
                eid = e.get("evidence_id")
                if eid and eid not in existing:
                    existing.add(eid)
                    added.append(e)
                    persona = (e.get("metadata") or {}).get("persona")
                    if persona:
                        personas.add(persona)
    if not added:
        return evidence, None
    # 访谈回答原文:从合成证据里还原 persona/问题/反馈/期望,供报告「调研方法」卡逐条展示
    findings_list: list[dict] = []
    for e in added:
        md = e.get("metadata") or {}
        finding_text = (e.get("claim") or "").replace("[模拟访谈]", "").strip()
        if not finding_text:
            continue
        findings_list.append({
            "product": e.get("product") or "",
            "persona": md.get("persona") or "匿名受访者",
            "question_id": md.get("question_id") or "",
            "claim_type": e.get("claim_type") or "",
            "finding": finding_text,
            "expectation": md.get("expectation") or "",
            "evidence_id": e.get("evidence_id") or "",
        })
    research_method = {
        "method": "LLM 模拟问卷调研 + 用户访谈(合成数据,非真实用户,已脱敏)",
        "synthetic": True,
        "questions": [{"id": q.get("id"), "text": q.get("text")} for q in questions if q.get("text")][:6],
        "n_findings": len(added),
        "personas": sorted(personas)[:8],
        "findings": findings_list[:16],  # 控量:逐条访谈回答,前端可展开
    }
    return evidence + added, research_method


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
        summary=f"已读取 {len(evidence)} 条证据，开始梳理事实",
        preview=_evidence_preview(evidence, meta),
    )

    focus = (meta.get("analysis_focus") or [""])[0] if meta.get("analysis_focus") else ""

    # 问卷/访谈采集 Agent(合成,已标注):仅作兜底——真实 UGC 不足时才跑,避免合成数据污染结论
    research_method: Optional[dict] = None
    if (_survey_should_run(evidence, meta) and not is_mock_mode()
            and (os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY"))):
        try:
            _emit_progress(step="facts", phase="survey_start", summary="设计问卷并模拟用户访谈采集")
            evidence, research_method = _run_survey(evidence, meta)
            if research_method:
                print(f"[analyzer] survey added {research_method['n_findings']} synthetic interview evidence")
                _emit_progress(step="facts", phase="survey_done",
                               summary=f"问卷调研完成：{len(research_method['questions'])} 题 / "
                                       f"{research_method['n_findings']} 条模拟访谈")
        except Exception as e:  # noqa: BLE001
            print(f"[analyzer] survey 失败(忽略): {e}")

    # 先生成功能骨架一次(供两遍 facts 复用,免重复抽取)
    spine: Optional[list[dict]] = None
    if not is_mock_mode() and _feature_tree_split_enabled():
        try:
            spine = _feature_spine(load_prompt("analyzer_facts"), evidence, meta)
        except Exception as e:  # noqa: BLE001
            print(f"[analyzer] spine 生成失败(忽略): {e}")

    facts = _step1_facts(evidence, meta, analyzer_retry=analyzer_retry, spine=spine)

    # #13 缺口定向补采(治本):facts 暴露哪些 (产品×功能) 空缺、缺哪类 claim_type(如定价),
    # 就**只对这些缺口**定向搜索 → 合并写回 evidence → 重出一遍 facts。比盲目全量补采省额度、命中准。
    # 同时充当覆盖兜底:缺失的必需 claim_type 会被主动补回。ANALYZER_GAP_REFILL=0 可关。
    if _gap_refill_enabled() and not is_mock_mode():
        # 多轮补采:每轮自检缺口 → 定向补 → 局部重出 facts → 再自检。直到无缺口 / 补不到新证据 /
        # 用完轮数。第 2 轮起 _gap_targeted_recollect 自动放开站点锚定 + 多取结果(韧性升级)。
        max_rounds = max(1, int(os.environ.get("ANALYZER_GAP_MAX_ROUNDS", "2")))
        _ct_cn = {"feature_existence": "功能", "performance_quality": "体验",
                  "pricing": "定价", "user_pain": "痛点"}
        _sec_cn = {"feature_tree": "功能对比", "pricing_model": "定价", "user_persona": "用户画像"}
        exhausted_pricing: set = set()   # 已尽力仍抓不到价的产品(如 Asana SPA),不再触发 pricing_model 重算
        prev_pgap: set = set()
        for round_idx in range(max_rounds):
            gaps = _coverage_gaps(facts, meta, evidence)
            # per-product 定价已尽力剔除:上一轮补过、这一轮仍缺的产品 = 抓不动 → 标记后不再触发,
            # 避免昂贵的 pricing_model 为补不上的缺口空转重算(实测旧版 4×/183s 的元凶)。
            cur_pgap = set(gaps.get("pricing_gap_products") or [])
            newly = cur_pgap & prev_pgap
            if newly:
                exhausted_pricing |= newly
                print(f"[analyzer] 定价已尽力仍缺,停止重试: {sorted(newly)}")
            prev_pgap = cur_pgap
            gaps["pricing_gap_products"] = sorted(p for p in cur_pgap if p not in exhausted_pricing)
            pgap = gaps["pricing_gap_products"]
            if not (gaps["unknown_cells"] or gaps["missing_claim_types"] or pgap):
                break  # 无缺口(或剩下的都已尽力),收敛
            _miss = "、".join(_ct_cn.get(c, c) for c in gaps["missing_claim_types"])
            _tail = f"，并缺 {_miss}证据" if _miss else ""
            _ptail = f"，{len(pgap)} 个产品定价缺失" if pgap else ""
            _rtag = f"第{round_idx + 1}轮：" if round_idx else ""
            _emit_progress(step="facts", phase="gap_refill_start",
                           summary=f"{_rtag}发现 {len(gaps['unknown_cells'])} 个空白项{_tail}{_ptail}，定向补采")
            try:
                new_ev = _gap_targeted_recollect(meta, gaps, focus, round_idx=round_idx)
            except Exception as e:  # noqa: BLE001
                print(f"[analyzer] gap refill 失败(忽略): {e}")
                new_ev = []
            existing_ids = {e.get("evidence_id") for e in evidence}
            new_ev = [e for e in new_ev if e.get("evidence_id") not in existing_ids]
            if not new_ev:
                print(f"[analyzer] gap refill round {round_idx + 1}: 没补到新证据,停止")
                break  # 补不到新证据 → 再循环也白搭
            evidence = evidence + new_ev
            # 成本闸门:只重跑「这一轮真补到了对应类型新证据」的 section。
            # 否则像 Asana 付费档这种客观补不上的缺口,会让昂贵的 pricing_model 每轮空转重算(实测 4×/183s)。
            new_cts = {e.get("claim_type") for e in new_ev}
            sec_for_ct = {
                "feature_tree": {"feature_existence", "performance_quality"},
                "pricing_model": {"pricing"},
                "user_persona": {"user_pain"},
            }
            gap_secs = _gap_affected_sections(gaps) or list(_FACTS_SECTIONS)
            affected = [s for s in gap_secs if (sec_for_ct.get(s, set()) & new_cts)]
            if not affected:
                print(f"[analyzer] gap refill round {round_idx + 1}: 补到的证据类型与缺口 section 不匹配"
                      f"(new={sorted(c for c in new_cts if c)}),跳过重算,停止")
                break  # 补到的不是缺口要的类型 → 重算也不会变,停
            # 再按产品收窄:feature_tree 只重填有空白格的产品,其余从上一轮复用(省 ~2/3 调用)
            gap_products = sorted({p for p, _ in gaps["unknown_cells"]})
            if gaps["missing_claim_types"]:
                gap_products = _target_products(meta)  # 缺整类证据 → 各产品都可能受影响
            _names = "、".join(_sec_cn.get(s, s) for s in affected)
            _ponly = f"(仅 {len(gap_products)}/{len(_target_products(meta))} 个产品)" if gap_products and "feature_tree" in affected else ""
            print(f"[analyzer] gap refill round {round_idx + 1} added {len(new_ev)} evidence; 只重出: {affected} 产品: {gap_products or '全部'}")
            _emit_progress(step="facts", phase="gap_refill_done",
                           summary=f"{_rtag}定向补采 {len(new_ev)} 条，重新梳理{_names}{_ponly}")
            partial = _step1_facts(evidence, meta, analyzer_retry=analyzer_retry,
                                   spine=spine, only_sections=affected,
                                   only_products=gap_products or None, prev_facts=facts)
            facts.update(partial)

    derivations = _step2_derivations(facts, evidence, meta, analyzer_retry=analyzer_retry)

    schema_draft = {
        "analysis_meta": meta,
        **facts,
        **derivations,
    }
    if research_method:
        schema_draft["research_method"] = research_method  # 供报告「调研方法」卡展示
    # 收尾确定性安全网(幂等):清掉任何不存在的 evidence 引用。覆盖新模块
    # competitor_landscape / positioning_map / praise_points —— 它们不在 collect_all_evidence_refs
    # 校验范围内,live 模式下靠这层兜住,杜绝幻觉 ID 漏进 writer chip。
    schema_draft, dropped = sanitize_schema_evidence_refs(schema_draft, evidence)
    if dropped:
        print(f"[analyzer] schema sanitize dropped {dropped} invalid evidence refs")
    # 补采可能扩充了 evidence → 写回 state,保证 writer/reviewer 看到的引用都真实存在
    return {**state, "raw_evidence": evidence, "schema_draft": schema_draft}
