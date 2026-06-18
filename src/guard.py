"""Guard — 结论强度控制的唯一 owner(design-v3-draft M2)。

职责一句话:保证每条结论强度 ≤ 证据强度。确定性、幂等、零 LLM;
只能降级/删除/收敛措辞,**不新增内容**。失败策略:纯函数,不会失败。

收编(v3 根因 1:"结论强度控制"散在三处互不知情):
- `analyzer_sanitize.*` 确定性后处理簇(re-export,本模块为对外入口)
- `soften_overgeneralization` 过度泛化收敛
- 新增两条对账规则(治 quality_score 73 的主要失分点——gap/basis 从单条证据过度泛化):
  G1 winner/gap 对比:winner 与次优产品各 ≥1 条 quality/pain 证据,否则降级为
     "证据不足,暂不强对比"安全措辞(与 analyzer._compute_gap 同口径)
  G2 quality_score.basis:声称"仅确认具备"必须有 ≥1 条 feature_existence 证据支撑,
     否则 support_status 改 unknown

入口 `apply(schema, evidence, findings=None) → (schema, report)`。
findings 预留给 Auditor 的定位清单(stage_report.Check,M4 接线做一次性修订)。
"""
from __future__ import annotations

import re
from typing import Optional

from .analyzer_sanitize import (  # noqa: F401 — Guard 是结论强度簇的对外入口,re-export 保单一 owner
    repair_rec_anchors,
    sanitize_derivations,
    sanitize_facts_evidence_refs,
    sanitize_schema_evidence_refs,
    soften_overgeneralization,
)

_QUALITY_CTS = ("performance_quality", "user_pain")
_BASIS_ONLY_CONFIRMED = re.compile(r"仅确认具备|官网[^,。;，]{0,8}确认具备?")
# 与 analyzer._compute_gap 的弱结论 gap_type 同口径:这些本身就是"不强对比",G1 跳过
_WEAK_GAP_TYPES = ("insufficient_evidence", "parity_unrated", "unknown")


def _ct_by_id(evidence: list[dict]) -> dict[str, str]:
    return {e["evidence_id"]: e.get("claim_type") or ""
            for e in evidence if e.get("evidence_id")}


def _has_ct(ids: Optional[list], ct_map: dict, allowed: tuple) -> bool:
    return any(ct_map.get(i) in allowed for i in ids or [])


def enforce_comparison_evidence(schema: dict, evidence: list[dict]) -> int:
    """G1:强对比结论(gap.winner 明确且 gap_type 非弱)要求 winner 与至少一个对手
    都有 ≥1 条 quality/pain 证据;否则就地降级为证据不足安全措辞。返回降级条数。"""
    ct_map = _ct_by_id(evidence)
    changed = 0
    for feat in (schema.get("feature_tree") or {}).get("features", []) or []:
        gap = feat.get("gap") or {}
        winner = gap.get("winner")
        if not winner or winner == "unknown" or gap.get("gap_type") in _WEAK_GAP_TYPES:
            continue
        products = feat.get("products") or {}

        def _q_ok(pdata: dict) -> bool:
            qs = pdata.get("quality_score") or {}
            return _has_ct(qs.get("evidence_ids"), ct_map, _QUALITY_CTS)

        comparable = {
            p: d for p, d in products.items()
            if d.get("support_status") in ("supported", "partial")
        }
        missing_quality = [p for p, d in comparable.items() if not _q_ok(d)]
        winner_ok = winner in products and _q_ok(products[winner])
        second_ok = any(_q_ok(d) for p, d in products.items() if p != winner)
        if winner_ok and second_ok and not missing_quality:
            continue
        name = feat.get("name") or "该能力"
        if winner_ok and missing_quality:
            reason = (f"「{name}」仅部分产品有用户体验质量证据，"
                      "部分产品缺少质量证据，暂不作强对比")
            conf = 0.25
        elif winner_ok:
            reason = (f"仅 {winner} 在「{name}」上有较充分的用户体验反馈，"
                      "其余产品证据不足，暂不作强对比")
            conf = 0.3
        else:
            reason = f"各产品在「{name}」上质量证据不足，暂不判断胜负"
            conf = 0.1
        feat["gap"] = {
            "winner": "unknown",
            "gap_type": "insufficient_evidence",
            "reason": reason,
            "evidence_ids": list(gap.get("evidence_ids") or []),
            "confidence": conf,
        }
        changed += 1
    return changed


def enforce_basis_support(schema: dict, evidence: list[dict]) -> int:
    """G2:basis 声称"仅确认具备/官网确认"必须有 ≥1 条 feature_existence 证据;
    否则该格降级 unknown(score 归 0,basis 换不可得措辞)。返回降级格数。"""
    ct_map = _ct_by_id(evidence)
    changed = 0
    for feat in (schema.get("feature_tree") or {}).get("features", []) or []:
        for pdata in (feat.get("products") or {}).values():
            qs = pdata.get("quality_score") or {}
            basis = qs.get("basis") or ""
            if not _BASIS_ONLY_CONFIRMED.search(basis):
                continue
            ids = list(pdata.get("support_evidence_ids") or []) + list(qs.get("evidence_ids") or [])
            if _has_ct(ids, ct_map, ("feature_existence",)):
                continue
            pdata["support_status"] = "unknown"
            qs["basis"] = "未检索到该能力的功能证据，支持情况不明"
            qs["score"] = 0
            changed += 1
    return changed


def enforce_quality_basis_evidence(schema: dict, evidence: list[dict]) -> int:
    """G3:quality_score.basis 只能表达有质量证据支撑的质量判断。

    support_status 已承载"是否具备"；当 quality_score 没有 performance_quality/user_pain
    证据时,质量格只保留"暂不评分"安全措辞,避免把功能存在性混进质量 claim。
    """
    ct_map = _ct_by_id(evidence)
    changed = 0
    neutral_basis = "无用户/第三方质量评价证据，暂不评分"
    for feat in (schema.get("feature_tree") or {}).get("features", []) or []:
        for pdata in (feat.get("products") or {}).values():
            qs = pdata.get("quality_score") or {}
            if _has_ct(qs.get("evidence_ids"), ct_map, _QUALITY_CTS):
                continue
            if (
                qs.get("score") == 0
                and qs.get("basis") == neutral_basis
                and not qs.get("evidence_ids")
            ):
                continue
            qs["score"] = 0
            qs["basis"] = neutral_basis
            qs["evidence_ids"] = []
            agg = qs.get("aggregation")
            if isinstance(agg, dict):
                agg["representative_evidence_ids"] = []
            changed += 1
    return changed


def _degraded_annex(state: dict) -> str:
    """degraded 出货的分层说明(原 degraded_writer_node 的渲染,M4 收编为 Guard 降级措辞):
    质检状态 + 建议动作 + 不建议直接采纳清单,包在正常报告外层。诚实标注,不藏分。"""
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
    needed = list({i.get("reject_target") for i in qr.get("errors", [])})
    action_lines = "\n".join(f"- {actions[t]}" for t in needed if t in actions)
    error_lines = "\n".join(
        f"- [{i.get('location')}] {i.get('issue_type')}: {i.get('detail', '')}"
        for i in qr.get("errors", [])
    )
    return (
        f"# 竞品分析报告(部分置信)\n\n"
        f"> 质检评分: {qr.get('quality_score', '?')}/100 · "
        f"Auditor 发现未能全部由确定性修订消化,分层降级输出\n\n"
        f"## 质检状态\n"
        f"- ✅ 通过: {', '.join(passed) or '无'}\n"
        f"- ⚠️ 存疑: {', '.join(warning) or '无'}\n"
        f"- ❌ 失败: {', '.join(failed) or '无'}\n\n"
        f"## 建议补充动作\n{action_lines or '- 无'}\n\n---\n\n"
        f"## 可参考结论(通过质检模块)\n\n{state.get('report_draft') or ''}\n\n---\n\n"
        f"## 不建议直接采纳的结论\n{error_lines or '- 无'}\n"
    )


def guard_revise_node(state: dict) -> dict:
    """v3 直线控制流的"一次修订"节点(M4):消费 Auditor(reviewer)定位清单 →
    确定性修订 schema → 有变化则 Reporter 重渲染 → 出货。
    打回循环与 degraded_writer 已删(54 run 仅 1 次触发),本节点是唯一审后修复路径:
    无环、无 retry 配额、无路由函数。

    终态规则(诚实出货):
    - 原 status=running(旧打回信号)且修订有变化 → passed(问题已被确定性修订消化,
      未消化的发现仍留在 quality_report 供前端徽章/checklist 展示)
    - 原 status=running 且零修订 → degraded(审出问题但修不了,诚实降级)
    - passed/degraded 原样保留(degraded 的"分层输出"由 Guard 降级措辞承担)"""
    qr = state.get("quality_report") or {}
    findings = list(qr.get("errors") or []) + list(qr.get("warnings") or [])
    schema = state.get("schema_draft")
    if not schema:
        status = "degraded" if state.get("status") == "running" else state.get("status")
        out = {**state, "status": status}
        if out.get("status") == "degraded":
            out["report_draft"] = _degraded_annex(out)
        return out
    schema, rep = apply(schema, state.get("raw_evidence") or [], findings=findings)
    out = {**state, "schema_draft": schema, "guard_revision": rep}
    if rep["changes_total"]:
        from .writer import writer_node  # Reporter 重渲染(纯模板,不会失败);懒导入避环
        out = writer_node(out)
        out["guard_revision"] = rep
        print(f"[guard_revise] 修订 {rep['changes_total']} 处并重渲染报告"
              f"(消费 Auditor 发现 {rep['findings_consumed']} 条)")
    if out.get("status") == "running":
        out["status"] = "passed" if rep["changes_total"] else "degraded"
    if out.get("status") == "degraded":
        # 降级是正常路径的一种程度:正常报告外层包质检状态/不建议采纳清单(原 degraded_writer 渲染)
        out["report_draft"] = _degraded_annex(out)
    return out


def apply(schema: dict, evidence: list[dict],
          findings: Optional[list] = None) -> tuple[dict, dict]:
    """Guard 终门:幻觉引用清理 → G1/G2 对账降级 → 过度泛化收敛。幂等(二次套用零变化)。
    findings(Auditor Check 定位清单)当前仅计数,M4 接线后驱动定向修订。"""
    schema, dropped = sanitize_schema_evidence_refs(schema, evidence)
    g1 = enforce_comparison_evidence(schema, evidence)
    g2 = enforce_basis_support(schema, evidence)
    g3 = enforce_quality_basis_evidence(schema, evidence)
    softened = soften_overgeneralization(schema)
    report = {
        "dropped_refs": dropped,
        "comparison_downgraded": g1,
        "basis_unknowned": g2,
        "quality_basis_neutralized": g3,
        "softened": softened,
        "findings_consumed": len(findings or []),
        "changes_total": dropped + g1 + g2 + g3 + softened,
    }
    return schema, report
