"""Analyzer 骨架兜底构建器 — facts/derivations 的确定性 fallback 与 demo 注入。

LLM 不可用/解析失败时,用证据直接拼出最小可用 schema(_fallback_*);_corrupt_* 仅 demo 用。
依赖 analyzer_common 的叶子 helper(单向,无环)。analyzer.py re-export 保 back-compat。
"""
from __future__ import annotations

import copy

from .analyzer_common import (
    _evidence_ids,
    _safe_price_tier,
    _short,
    _target_products,
)


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
    from . import scoring_config as _sc
    weights = _sc.weights("recommendation_priority", {
        "pain_frequency": 0.35,
        "business_impact": 0.30,
        "implementation_feasibility": 0.20,
        "evidence_confidence": 0.15,
    })
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
