"""Reviewer 节点 — 见 docs/design-v2.2.md §八

Demo 默认 REVIEWER_MODE=minimal:
- hard_gate(error,会打回): R1(引用完整) / R4(推理链) / R5(结构冲突)
- soft(只 warning,不打回): R0 / R2 / R3 / R7
- R6(LLM 语义校验): 关
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Optional

from .analyzer import collect_all_evidence_refs
from .judge import completeness_metrics
from .state import AgentState


REVIEWER_RULES = {
    "R0": "evidence_coverage_gate",
    "R1": "evidence_reference_integrity",
    "R2": "claim_type_compatibility",
    "R3": "aggregation_integrity",
    "R4": "reasoning_chain_integrity",
    "R5": "structured_contradiction",
    "R6": "semantic_grounding",
    "R7": "freshness_and_confidence",
}

MODE_CONFIG = {
    "minimal": {
        "hard_gate": {"R1", "R4", "R5"},
        "soft": {"R0", "R2", "R3", "R7"},
        "llm": False,
    },
    "full": {
        "hard_gate": {"R0", "R1", "R2", "R3", "R4", "R5"},
        "soft": {"R7"},
        "llm": True,
    },
}

ISSUE_TYPE_TO_TARGET = {
    "evidence_id_not_found": "collector",
    "missing_evidence_ids": "analyzer",
    "claim_type_mismatch": "analyzer",
    "aggregation_sum_mismatch": "analyzer",
    "aggregation_method_missing": "analyzer",
    "broken_reasoning_chain": "analyzer",
    "structured_contradiction": "analyzer",
    "semantic_grounding_fail": "analyzer",
    "semantic_grounding_unavailable": "analyzer",
    "freshness_stale": "collector",
    "missing_product_evidence": "collector",
    "missing_claim_type_coverage": "collector",
}


# ────────────────────────────────────────────────────────────────────────────
# Rule runners — 返回 list[issue dict]
# issue: {rule, issue_type, severity(后由 mode 重写), location, detail, reject_target, required_claim_types}
# ────────────────────────────────────────────────────────────────────────────

def _mk_issue(rule: str, issue_type: str, location: str, detail: str,
              required_claim_types: Optional[list[str]] = None) -> dict:
    return {
        "rule": rule,
        "issue_type": issue_type,
        "severity": "error",  # 占位,后续按 mode 重写
        "location": location,
        "detail": detail,
        "reject_target": ISSUE_TYPE_TO_TARGET.get(issue_type, "analyzer"),
        "required_claim_types": required_claim_types or [],
    }


def check_evidence_reference_integrity(schema: dict, evidence: list[dict]) -> list[dict]:
    valid_ids = {e["evidence_id"] for e in evidence}
    issues = []
    for path, eids, allowed in collect_all_evidence_refs(schema):
        if not eids and path.endswith((".support_evidence_ids", ".evidence_ids")):
            # 空 evidence_ids — Analyzer 通常该有,但某些字段允许空,这里不报
            continue
        for eid in eids:
            if eid not in valid_ids:
                issues.append(_mk_issue(
                    "R1", "evidence_id_not_found", path,
                    f"evidence_id {eid} 不存在于 raw_evidence",
                    required_claim_types=allowed,
                ))
    return issues


def check_claim_type_compatibility(schema: dict, evidence: list[dict]) -> list[dict]:
    by_id = {e["evidence_id"]: e for e in evidence}
    issues = []
    for path, eids, allowed in collect_all_evidence_refs(schema):
        if not allowed:
            continue
        for eid in eids:
            ev = by_id.get(eid)
            if not ev:
                continue  # R1 已处理
            ct = ev.get("claim_type")
            if ct not in allowed:
                issues.append(_mk_issue(
                    "R2", "claim_type_mismatch", path,
                    f"evidence {eid} claim_type={ct},不在允许集 {allowed}",
                ))
    return issues


def check_aggregation_integrity(schema: dict, evidence: list[dict]) -> list[dict]:
    issues = []
    for feat in schema.get("feature_tree", {}).get("features", []):
        fid = feat.get("feature_id", "?")
        for pname, pdata in (feat.get("products") or {}).items():
            agg = ((pdata.get("quality_score") or {}).get("aggregation") or {})
            if not agg:
                continue
            loc = f"feature_tree.{fid}.{pname}.aggregation"
            ss = agg.get("sample_size", 0) or 0
            pos = agg.get("positive_mentions", 0) or 0
            neg = agg.get("negative_mentions", 0) or 0
            neu = agg.get("neutral_mentions", 0) or 0
            if ss != pos + neg + neu:
                issues.append(_mk_issue(
                    "R3", "aggregation_sum_mismatch", loc,
                    f"sample_size={ss} != pos+neg+neu={pos+neg+neu}",
                ))
            if not agg.get("method"):
                issues.append(_mk_issue(
                    "R3", "aggregation_method_missing", loc,
                    "aggregation.method 字段缺失,代表模式必须说明采样来源",
                ))
    return issues


def check_reasoning_chain(schema: dict, evidence: list[dict]) -> list[dict]:
    issues = []
    valid_fids = {f["feature_id"] for f in schema.get("feature_tree", {}).get("features", [])}
    valid_pids = {p["pain_id"] for p in schema.get("user_persona", {}).get("pain_points", [])}
    recs = schema.get("recommendations") or []
    if len(recs) < 2:
        issues.append(_mk_issue(
            "R4", "broken_reasoning_chain", "recommendations",
            "recommendations 为空或数量不足；竞品报告必须给出可执行建议",
        ))

    swot = schema.get("swot") or {}
    swot_count = sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats"))
    if swot_count == 0:
        issues.append(_mk_issue(
            "R4", "broken_reasoning_chain", "swot",
            "SWOT 四象限全部为空；缺少从事实层到结论层的推导",
        ))

    for r in recs:
        rid = r.get("rec_id", "?")
        fids = set(r.get("source_feature_ids") or [])
        pids = set(r.get("source_pain_ids") or [])
        if not (fids & valid_fids) and not (pids & valid_pids):
            issues.append(_mk_issue(
                "R4", "broken_reasoning_chain", f"recommendations.{rid}",
                "未引用任何有效 feature_id / pain_id",
            ))
    return issues


def check_structured_contradiction(schema: dict, evidence: list[dict]) -> list[dict]:
    """v1: 简单检测 priority_score 公式自洽 + gap.winner 必须在 products 列表里"""
    issues = []
    for r in schema.get("recommendations", []):
        rid = r.get("rec_id", "?")
        ps = r.get("priority_score") or {}
        weights = ps.get("weights") or {}
        if weights and "final_score" in ps:
            try:
                expected = sum(float(ps.get(k, 0)) * float(w) for k, w in weights.items())
                if abs(expected - float(ps["final_score"])) > 0.011:
                    issues.append(_mk_issue(
                        "R5", "structured_contradiction",
                        f"recommendations.{rid}.priority_score",
                        f"final_score={ps['final_score']} 与公式计算值={expected:.3f} 不一致",
                    ))
            except (TypeError, ValueError):
                issues.append(_mk_issue(
                    "R5", "structured_contradiction",
                    f"recommendations.{rid}.priority_score",
                    "priority_score 字段类型异常",
                ))

    for feat in schema.get("feature_tree", {}).get("features", []):
        fid = feat.get("feature_id", "?")
        gap = feat.get("gap") or {}
        winner = gap.get("winner")
        products = list((feat.get("products") or {}).keys())
        # "unknown" 是合法的"证据不足、暂不判胜负"状态,不算结构矛盾;
        # 只有 winner 指向一个不存在于 products 的具体产品名才是真矛盾。
        if winner and winner != "unknown" and winner not in products:
            issues.append(_mk_issue(
                "R5", "structured_contradiction",
                f"feature_tree.{fid}.gap.winner",
                f"winner={winner} 不在 products 列表 {products} 中",
            ))
    return issues


def check_freshness_and_confidence(schema: dict, evidence: list[dict]) -> list[dict]:
    issues = []
    by_id = {e["evidence_id"]: e for e in evidence}
    for _path, eids, _ in collect_all_evidence_refs(schema):
        for eid in eids:
            ev = by_id.get(eid)
            if not ev:
                continue
            if ev.get("source_freshness") == "stale":
                issues.append(_mk_issue(
                    "R7", "freshness_stale", f"evidence.{eid}",
                    f"evidence {eid} 已过期({ev.get('observed_at', '?')})",
                ))
    return issues


def check_collection_coverage(collection_meta: dict, analysis_meta: dict) -> list[dict]:
    """R0: every requested product needs all required claim types before analysis is trusted."""
    issues: list[dict] = []
    products = [analysis_meta.get("target_product")] + list(analysis_meta.get("competitors") or [])
    products = [p for p in products if p]
    by_product = collection_meta.get("products") or {}
    if not by_product:
        return []

    for product in products:
        info = by_product.get(product) or {}
        coverage = info.get("coverage") or {}
        missing = [
            ct for ct in ("feature_existence", "performance_quality", "pricing", "user_pain")
            if int(coverage.get(ct) or 0) <= 0
        ]
        if len(missing) == 4:
            issues.append(_mk_issue(
                "R0",
                "missing_product_evidence",
                f"collection_meta.products.{product}",
                f"{product} 没有采集到任何必需证据; attempts={info.get('aliases_tried') or []}",
                required_claim_types=missing,
            ))
        elif missing:
            issues.append(_mk_issue(
                "R0",
                "missing_claim_type_coverage",
                f"collection_meta.products.{product}.coverage",
                f"{product} 缺少 claim_type 覆盖: {missing}",
                required_claim_types=missing,
            ))
    return issues


RULE_RUNNERS = {
    "R1": check_evidence_reference_integrity,
    "R2": check_claim_type_compatibility,
    "R3": check_aggregation_integrity,
    "R4": check_reasoning_chain,
    "R5": check_structured_contradiction,
    "R7": check_freshness_and_confidence,
}


# R6 is intentionally one LLM call over a compact claim/evidence packet.
# It runs only after deterministic structural checks pass.
_R6_MAX_CLAIMS = 28
_R6_MAX_CLAIM_CHARS = 520
_R6_MAX_SNIPPET_CHARS = 420


def _clip(text: object, limit: int) -> str:
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit - 3].rstrip() + "..."


def _norm_eids(evidence_ids) -> list[str]:
    if not evidence_ids:
        return []
    return [str(e) for e in evidence_ids if e]


def _collect_semantic_claims(schema: dict) -> list[dict]:
    """Collect high-value claims for R6 semantic grounding.

    R6 focuses on interpretive or user-facing conclusions, not every schema field.
    Deterministic R1-R5 already handle existence, type compatibility and formulas.
    """
    claims: list[dict] = []

    def add(location: str, claim_kind: str, claim_text: str, evidence_ids) -> None:
        text = _clip(claim_text, _R6_MAX_CLAIM_CHARS)
        if not text:
            return
        claims.append({
            "location": location,
            "claim_kind": claim_kind,
            "claim_text": text,
            "evidence_ids": _norm_eids(evidence_ids),
        })

    # Feature quality and gaps.
    for feat in schema.get("feature_tree", {}).get("features", []):
        fid = feat.get("feature_id", "?")
        fname = feat.get("name", "")
        for pname, pdata in (feat.get("products") or {}).items():
            qs = pdata.get("quality_score") or {}
            basis = qs.get("basis")
            if basis:
                add(
                    f"feature_tree.{fid}.{pname}.quality_score.basis",
                    "feature_quality",
                    f"{pname} / {fname}: {basis}",
                    qs.get("evidence_ids") or [],
                )
        gap = feat.get("gap") or {}
        if gap.get("reason"):
            add(
                f"feature_tree.{fid}.gap",
                "feature_gap",
                (
                    f"{fname}: winner={gap.get('winner', 'unknown')}; "
                    f"gap_type={gap.get('gap_type', 'unknown')}; "
                    f"reason={gap.get('reason')}"
                ),
                gap.get("evidence_ids") or [],
            )

    # Pricing interpretation.
    pricing_gap = (schema.get("pricing_model") or {}).get("pricing_gap") or {}
    if pricing_gap.get("summary"):
        add(
            "pricing_model.pricing_gap",
            "pricing_gap",
            (
                f"target_position={pricing_gap.get('target_position', 'unknown')}; "
                f"summary={pricing_gap.get('summary')}"
            ),
            pricing_gap.get("evidence_ids") or [],
        )

    # Personas and pain points.
    for seg in (schema.get("user_persona") or {}).get("user_segments", []):
        sid = seg.get("segment_id", "?")
        add(
            f"user_persona.user_segments.{sid}",
            "user_segment",
            f"{seg.get('name', '')}: {seg.get('description', '')}",
            seg.get("evidence_ids") or [],
        )
    for pain in (schema.get("user_persona") or {}).get("pain_points", []):
        pid = pain.get("pain_id", "?")
        freq = pain.get("frequency") or {}
        add(
            f"user_persona.{pid}",
            "pain_point",
            (
                f"{pain.get('description', '')}; frequency={freq.get('level', 'unknown')} "
                f"({freq.get('count', '')}); affected={pain.get('affected_products', [])}"
            ),
            freq.get("evidence_ids") or [],
        )

    # Recommendations.
    for rec in schema.get("recommendations", []):
        rid = rec.get("rec_id", "?")
        add(
            f"recommendations.{rid}",
            "recommendation",
            f"action={rec.get('action', '')}; rationale={rec.get('rationale', '')}",
            rec.get("evidence_ids") or [],
        )

    # SWOT.
    for dim in ("strengths", "weaknesses", "opportunities", "threats"):
        for i, item in enumerate((schema.get("swot") or {}).get(dim) or []):
            add(
                f"swot.{dim}[{i}]",
                f"swot_{dim}",
                item.get("point", ""),
                item.get("evidence_ids") or [],
            )

    return claims


def _r6_system_prompt() -> str:
    return """你是竞品分析系统的 Reviewer R6: semantic grounding。

任务:判断每条 claim_text 是否被它列出的 evidence_ids 的 extracted_snippet 直接支撑。

严格规则:
1. 只能使用输入中的 evidence.snippet / claim_type / source_bias,不能使用外部知识。
2. 若证据只支持较弱结论,但 claim 写成强比较、全称判断、精确数字或因果判断,判为 overclaim。
3. vendor_claim 可以支撑功能存在和定价,但不能单独支撑真实体验质量或用户痛点。
4. user_generated 可以支撑用户痛点或个体体验,但不能支撑市场总体结论,除非 snippet 明确给出样本或统计。
5. 证据之间相互矛盾且 claim 没有解释冲突时,判为 contradicted。
6. 没问题的 claim 不要输出。

只返回 JSON:
{
  "issues": [
    {
      "location": "必须等于输入 claims[].location",
      "issue_type": "unsupported|overclaim|contradicted",
      "severity": "error|warning",
      "detail": "简短说明为什么证据不支撑",
      "unsupported_text": "claim 中不被支撑的最小片段",
      "evidence_ids": ["SXXXXXXX"],
      "confidence": 0.0
    }
  ]
}
"""


def _resolve_r6_location(location: str, valid_locations: set[str]) -> Optional[str]:
    if location in valid_locations:
        return location
    matches = [loc for loc in valid_locations if loc.endswith(location) or location in loc]
    return matches[0] if len(matches) == 1 else None


def _r6_unavailable(detail: str) -> list[dict]:
    issue = _mk_issue(
        "R6",
        "semantic_grounding_unavailable",
        "reviewer.R6",
        detail,
    )
    issue["severity"] = "warning"
    return [issue]


def check_semantic_grounding(schema: dict, evidence: list[dict], llm) -> list[dict]:
    """R6: LLM checks whether extracted snippets really ground schema claims."""
    if llm is None:
        return _r6_unavailable("R6 已启用,但未注入 LLM client;跳过语义落地校验")

    claims = _collect_semantic_claims(schema)
    if not claims:
        return []

    selected_claims = claims[:_R6_MAX_CLAIMS]
    by_id = {e.get("evidence_id"): e for e in evidence if e.get("evidence_id")}
    needed_ids: list[str] = []
    for c in selected_claims:
        for eid in c["evidence_ids"]:
            if eid in by_id and eid not in needed_ids:
                needed_ids.append(eid)

    evidence_pack = [
        {
            "evidence_id": eid,
            "product": by_id[eid].get("product"),
            "claim_type": by_id[eid].get("claim_type"),
            "source_bias": by_id[eid].get("source_bias"),
            "claim": _clip(by_id[eid].get("claim"), 180),
            "snippet": _clip(by_id[eid].get("extracted_snippet"), _R6_MAX_SNIPPET_CHARS),
        }
        for eid in needed_ids
    ]

    payload = {
        "claims": selected_claims,
        "evidence": evidence_pack,
        "review_policy": {
            "return_only_issues": True,
            "location_must_match_claims": True,
            "error_means_reject_target_analyzer": True,
        },
    }

    try:
        raw = llm.call_json(
            _r6_system_prompt(),
            payload,
            max_tokens=3072,
            label="reviewer_r6",
        )
    except Exception as e:
        return _r6_unavailable(f"R6 LLM 调用失败:{type(e).__name__}: {e}")

    if isinstance(raw, list):
        raw_issues = raw
    elif isinstance(raw, dict):
        raw_issues = raw.get("issues") or []
    else:
        return _r6_unavailable("R6 LLM 返回格式不是 JSON object/list")

    valid_locations = {c["location"] for c in selected_claims}
    valid_eids = set(by_id)
    issues: list[dict] = []

    for item in raw_issues[:20]:
        if not isinstance(item, dict):
            continue
        loc = _resolve_r6_location(str(item.get("location", "")).strip(), valid_locations)
        if not loc:
            continue

        detail = str(item.get("detail") or item.get("reason") or "claim 未被证据充分支撑").strip()
        unsupported = str(item.get("unsupported_text") or "").strip()
        if unsupported:
            detail = f"{detail}; unsupported_text={unsupported}"

        issue = _mk_issue("R6", "semantic_grounding_fail", loc, detail)
        issue["semantic_issue_type"] = item.get("issue_type") or "unsupported"
        issue["evidence_ids"] = [eid for eid in _norm_eids(item.get("evidence_ids")) if eid in valid_eids]

        try:
            conf = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 1.0
        issue["confidence"] = max(0.0, min(1.0, conf))

        sev = str(item.get("severity") or "error").lower()
        # Low-confidence semantic findings should inform, not block.
        issue["severity"] = "warning" if sev == "warning" or issue["confidence"] < 0.65 else "error"
        issues.append(issue)

    return issues


def _clamp_score(v: float) -> int:
    return int(round(max(0, min(100, v))))


def _schema_refs_with_coverage(schema: dict) -> tuple[int, int]:
    """Return (claims_with_evidence, total_claims) for user-facing report quality."""
    total = 0
    with_ev = 0

    def add(eids) -> None:
        nonlocal total, with_ev
        total += 1
        if eids:
            with_ev += 1

    for feat in (schema.get("feature_tree") or {}).get("features", []):
        add((feat.get("gap") or {}).get("evidence_ids"))
        for pdata in (feat.get("products") or {}).values():
            add(pdata.get("support_evidence_ids"))
            add((pdata.get("quality_score") or {}).get("evidence_ids"))
    for product in (schema.get("pricing_model") or {}).get("products", []):
        for tier in product.get("tiers") or []:
            add(tier.get("evidence_ids"))
    if (schema.get("pricing_model") or {}).get("pricing_gap"):
        add(((schema.get("pricing_model") or {}).get("pricing_gap") or {}).get("evidence_ids"))
    for pain in (schema.get("user_persona") or {}).get("pain_points", []):
        add((pain.get("frequency") or {}).get("evidence_ids") or pain.get("evidence_ids"))
    for rec in schema.get("recommendations") or []:
        add(rec.get("evidence_ids"))
    for dim in ("strengths", "weaknesses", "opportunities", "threats"):
        for item in (schema.get("swot") or {}).get(dim) or []:
            add(item.get("evidence_ids"))
    return with_ev, total


def _quality_dimensions(
    schema: dict,
    evidence: list[dict],
    collection_meta: dict,
    analysis_meta: dict,
    errors: list[dict],
    warnings: list[dict],
) -> tuple[dict, int]:
    """Deterministic user-facing quality dimensions.

    R1-R7 answer "is this structurally safe"; these dimensions answer
    "how useful/trustworthy is this report for a PM".
    """
    products = [analysis_meta.get("target_product"), *list(analysis_meta.get("competitors") or [])]
    products = [p for p in products if p]
    required = ("feature_existence", "performance_quality", "pricing", "user_pain")

    coverage_scores: list[float] = []
    by_product = (collection_meta.get("products") or {}) if collection_meta else {}
    if by_product:
        for product in products:
            cov = (by_product.get(product) or {}).get("coverage") or {}
            coverage_scores.append(sum(1 for ct in required if int(cov.get(ct) or 0) > 0) / len(required))
    elif products:
        for product in products:
            seen = {e.get("claim_type") for e in evidence if e.get("product") == product}
            coverage_scores.append(sum(1 for ct in required if ct in seen) / len(required))
    evidence_coverage = _clamp_score((sum(coverage_scores) / len(coverage_scores)) * 100 if coverage_scores else 0)

    bias_counts = Counter(e.get("source_bias") or "unknown" for e in evidence)
    source_types = {e.get("source_type") for e in evidence if e.get("source_type")}
    has_user = bias_counts.get("user_generated", 0) > 0
    has_third = bias_counts.get("third_party", 0) > 0
    vendor_only = bool(evidence) and set(bias_counts) <= {"vendor_claim"}
    source_trust = 45 + min(len(source_types), 4) * 7 + (18 if has_user else 0) + (14 if has_third else 0)
    if vendor_only:
        source_trust -= 22
    source_credibility = _clamp_score(source_trust)

    refs_with, refs_total = _schema_refs_with_coverage(schema)
    traceability = _clamp_score((refs_with / refs_total) * 100 if refs_total else 0)

    stale = sum(1 for e in evidence if e.get("source_freshness") == "stale")
    freshness = _clamp_score(((len(evidence) - stale) / len(evidence)) * 100 if evidence else 0)

    recs = schema.get("recommendations") or []
    swot = schema.get("swot") or {}
    swot_count = sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats"))
    # 完成度统一走 judge.completeness_metrics(单一计算来源,与 API 附带的 report.completeness 一致)
    # 失败时退回粗粒度布尔检查兜底,保证评分不中断。
    try:
        report_completeness = _clamp_score(completeness_metrics(schema, analysis_meta).get("overall") or 0)
    except Exception:  # noqa: BLE001
        _checks = [
            bool((schema.get("feature_tree") or {}).get("features")),
            bool((schema.get("pricing_model") or {}).get("products")),
            bool((schema.get("user_persona") or {}).get("pain_points")),
            len(recs) >= 2,
            swot_count > 0,
        ]
        report_completeness = _clamp_score(sum(1 for x in _checks if x) / len(_checks) * 100)

    conflict_handling = 70
    if has_user and has_third:
        conflict_handling += 15
    elif has_user or has_third:
        conflict_handling += 8
    if vendor_only:
        conflict_handling -= 18
    if not recs or swot_count == 0:
        conflict_handling -= 12
    conflict_handling = _clamp_score(conflict_handling)

    dimensions = {
        "evidence_coverage": {
            "label": "证据覆盖度",
            "score": evidence_coverage,
            "note": "目标产品与竞品是否覆盖功能、质量、定价、痛点四类证据",
        },
        "source_credibility": {
            "label": "来源可信度",
            "score": source_credibility,
            "note": "综合来源类型、厂商/第三方/用户侧占比；vendor-only 会降权",
        },
        "traceability": {
            "label": "结论可追溯性",
            "score": traceability,
            "note": f"{refs_with}/{refs_total} 个结构化结论带证据引用",
        },
        "freshness": {
            "label": "时效性",
            "score": freshness,
            "note": f"{stale} 条证据超过 TTL",
        },
        "report_completeness": {
            "label": "报告完整度",
            "score": report_completeness,
            "note": "功能、定价、痛点、建议、SWOT 是否齐全",
        },
        "conflict_handling": {
            "label": "冲突处理",
            "score": conflict_handling,
            "note": "是否有用户/第三方证据帮助校验厂商叙事",
        },
    }
    weights = {
        "evidence_coverage": 0.22,
        "source_credibility": 0.18,
        "traceability": 0.22,
        "freshness": 0.10,
        "report_completeness": 0.18,
        "conflict_handling": 0.10,
    }
    dimensional_score = _clamp_score(sum(dimensions[k]["score"] * weights[k] for k in weights))
    rule_score = max(0, 100 - len(errors) * 10 - len(warnings) * 3)
    return dimensions, min(rule_score, dimensional_score)


# ────────────────────────────────────────────────────────────────────────────
# Node
# ────────────────────────────────────────────────────────────────────────────

def make_reviewer_node(llm=None, mode: Optional[str] = None):
    mode = mode or os.environ.get("REVIEWER_MODE", "minimal")
    cfg = MODE_CONFIG.get(mode, MODE_CONFIG["minimal"])

    def reviewer_node(state: AgentState) -> AgentState:
        schema = state.get("schema_draft") or {}
        evidence = state.get("raw_evidence") or []
        collection_meta = state.get("collection_meta") or {}
        analysis_meta = state.get("analysis_meta") or {}

        all_issues: list[dict] = []
        executed_rules: set[str] = {"R0"}
        skipped_rules: set[str] = set()
        for issue in check_collection_coverage(collection_meta, analysis_meta):
            issue["severity"] = "error" if "R0" in cfg["hard_gate"] else "warning"
            all_issues.append(issue)
        for rule_id, runner in RULE_RUNNERS.items():
            executed_rules.add(rule_id)
            for issue in runner(schema, evidence):
                if rule_id in cfg["hard_gate"]:
                    issue["severity"] = "error"
                    all_issues.append(issue)
                elif rule_id in cfg["soft"]:
                    issue["severity"] = "warning"
                    all_issues.append(issue)

        errors_pre = [i for i in all_issues if i["severity"] == "error"]

        # R6 仅在 full 模式 + 结构通过后跑一次。
        if cfg["llm"]:
            if errors_pre:
                skipped_rules.add("R6")
            else:
                executed_rules.add("R6")
                all_issues += check_semantic_grounding(schema, evidence, llm)
        else:
            skipped_rules.add("R6")

        errors = [i for i in all_issues if i["severity"] == "error"]
        warnings = [i for i in all_issues if i["severity"] == "warning"]

        failed_rules = {i["rule"] for i in errors}
        warning_rules = {i["rule"] for i in warnings}
        passed_rules = sorted(executed_rules - failed_rules - warning_rules)

        module_status = {}
        for module in ("raw_evidence", "feature_tree", "pricing_model",
                       "user_persona", "recommendations", "swot"):
            errs = [i for i in errors if i["location"].startswith(module)]
            wrns = [i for i in warnings if i["location"].startswith(module)]
            module_status[module] = "failed" if errs else ("warning" if wrns else "passed")

        quality_dimensions, quality_score = _quality_dimensions(
            schema, evidence, collection_meta, analysis_meta, errors, warnings
        )

        quality_report = {
            "mode": mode,
            "quality_score": quality_score,
            "quality_dimensions": quality_dimensions,
            "passed_rules": passed_rules,
            "failed_rules": sorted(failed_rules),
            "warning_rules": sorted(warning_rules),
            "skipped_rules": sorted(skipped_rules),
            "module_status": module_status,
            "errors": errors,
            "warnings": warnings,
        }

        if not errors:
            return {**state, "quality_report": quality_report,
                    "status": "passed", "reject_target": None}

        # 选打回 target
        target_counts = Counter(i["reject_target"] for i in errors)
        priority = {"collector": 2, "analyzer": 1, "writer": 0}
        target = max(target_counts, key=lambda t: (target_counts[t], priority.get(t, 0)))

        retry_count = state["retry_count"]
        max_per = state["max_retries_per_target"]
        if retry_count.get(target, 0) >= max_per.get(target, 0):
            return {**state, "quality_report": quality_report,
                    "status": "degraded", "reject_target": None}

        new_retry = {**retry_count, target: retry_count.get(target, 0) + 1}
        reject_requirements = [
            {
                "rule": i["rule"], "issue_type": i["issue_type"],
                "location": i["location"],
                "required_claim_types": i.get("required_claim_types", []),
                "reject_target": i["reject_target"],
            }
            for i in errors if i["reject_target"] == "collector"
        ]

        return {
            **state,
            "quality_report": quality_report,
            "retry_count": new_retry,
            "reject_target": target,
            "reject_requirements": reject_requirements or None,
            "status": "running",
        }

    return reviewer_node


# ────────────────────────────────────────────────────────────────────────────
# 降级 Writer
# ────────────────────────────────────────────────────────────────────────────

def degraded_writer_node(state: AgentState) -> AgentState:
    qr = state.get("quality_report") or {}
    ms = qr.get("module_status", {})
    passed = [m for m, s in ms.items() if s == "passed"]
    warning = [m for m, s in ms.items() if s == "warning"]
    failed = [m for m, s in ms.items() if s == "failed"]

    actions = {
        "collector": "补充用户侧证据来源(Reddit / ProductHunt)",
        "analyzer": "修正证据引用关系与结论推理链",
        "writer": "修正报告格式与引用标注",
    }
    needed = list({i["reject_target"] for i in qr.get("errors", [])})
    action_lines = "\n".join(f"- {actions[t]}" for t in needed if t in actions)
    error_lines = "\n".join(
        f"- [{i['location']}] {i['issue_type']}: {i.get('detail', '')}"
        for i in qr.get("errors", [])
    )

    report = (
        f"# 竞品分析报告(部分置信)\n\n"
        f"> 质检评分: {qr.get('quality_score', '?')}/100 · "
        f"按 target 分桶配额耗尽后降级输出\n"
        f"> 重试明细: {state.get('retry_count')}\n\n"
        f"## 质检状态\n"
        f"- ✅ 通过: {', '.join(passed) or '无'}\n"
        f"- ⚠️ 存疑: {', '.join(warning) or '无'}\n"
        f"- ❌ 失败: {', '.join(failed) or '无'}\n\n"
        f"## 建议补充动作\n{action_lines or '- 无'}\n\n---\n\n"
        f"## 可参考结论(通过质检模块)\n\n{state.get('report_draft') or ''}\n\n---\n\n"
        f"## 不建议直接采纳的结论\n{error_lines or '- 无'}\n"
    )
    return {**state, "report_draft": report, "status": "degraded"}
