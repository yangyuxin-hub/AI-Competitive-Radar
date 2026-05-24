"""Writer 节点 — 见 docs/design-v2.2.md §七

输出 Markdown 报告,每条 claim 句末追加 [SXXXXXXX] chip。
**禁止**在正文中包含 quality_score / 质检评分(Writer 在 Reviewer 之前运行)。
"""
from __future__ import annotations

from .state import AgentState


_SUPPORT_ICONS = {
    "supported": "✅",
    "partially_supported": "⚠️",
    "not_supported": "❌",
    "unknown": "❓",
}

_GAP_LABELS = {
    "accuracy": "准确性",
    "maturity": "成熟度",
    "feature_completeness": "功能完整度",
    "usability": "易用性",
    "performance": "性能",
}


def cite(evidence_ids: list[str]) -> str:
    """渲染 evidence chip。前端识别 \\[SXXXXXXX\\] 模式触发跳转。"""
    if not evidence_ids:
        return ""
    return "".join(f"[{eid}]" for eid in evidence_ids)


def _render_header(meta: dict, target: str, competitors: list[str], focus: list[str]) -> str:
    focus_text = " / ".join(focus) if focus else "全维度"
    comp_text = " vs ".join([target, *competitors])
    return (
        f"# {comp_text} — {focus_text} 竞品报告\n\n"
        f"> 报告 ID: {meta.get('report_id', '?')} · "
        f"数据截止: {meta.get('data_cutoff', '?')} · "
        f"目的: {meta.get('analysis_purpose', '?')}\n"
        f"> (质检评分由前端从 quality_report 单独渲染,不在正文)\n"
    )


def _render_feature_gaps(feature_tree: dict) -> str:
    if not feature_tree:
        return ""
    lines = [f"## 一、功能差距 — {feature_tree.get('category', '功能对比')}\n"]
    for feat in feature_tree.get("features", []):
        fid = feat.get("feature_id", "?")
        name = feat.get("name", "?")
        gap = feat.get("gap") or {}
        lines.append(f"### {fid} {name}\n")

        # gap 一句话总结
        if gap:
            winner = gap.get("winner", "?")
            gap_label = _GAP_LABELS.get(gap.get("gap_type", ""), gap.get("gap_type", ""))
            reason = gap.get("reason", "")
            ev = cite(gap.get("evidence_ids") or [])
            conf = gap.get("confidence")
            conf_text = f" · 置信 {conf:.2f}" if isinstance(conf, (int, float)) else ""
            lines.append(
                f"> **优胜:{winner}** · 差距类型:{gap_label}{conf_text}\n>\n"
                f"> {reason} {ev}\n"
            )

        # 产品对比表
        products = feat.get("products") or {}
        if products:
            lines.append("| 产品 | 状态 | 质量 | 关键证据 |")
            lines.append("|------|------|------|----------|")
            for pname, pdata in products.items():
                status = pdata.get("support_status", "unknown")
                icon = _SUPPORT_ICONS.get(status, "❓")
                qs = pdata.get("quality_score") or {}
                score = qs.get("score")
                scale = qs.get("scale", 5)
                quality = f"{score}/{scale}" if score is not None else "—"
                ev_list = (pdata.get("support_evidence_ids") or []) + (qs.get("evidence_ids") or [])
                ev = cite(sorted(set(ev_list))[:3])
                lines.append(f"| {pname} | {icon} {status} | {quality} | {ev} |")
            lines.append("")

            # quality basis(各产品一句话)
            for pname, pdata in products.items():
                basis = (pdata.get("quality_score") or {}).get("basis")
                if basis:
                    qs_ev = cite((pdata.get("quality_score") or {}).get("evidence_ids") or [])
                    lines.append(f"- **{pname}**:{basis} {qs_ev}")
            lines.append("")
    return "\n".join(lines)


def _render_pricing(pricing_model: dict) -> str:
    if not pricing_model:
        return ""
    lines = ["## 二、定价对比\n"]
    lines.append("| 产品 | 档位 | 价格(USD/月) | 限制 | 证据 |")
    lines.append("|------|------|---------------|------|------|")
    for p in pricing_model.get("products", []):
        name = p.get("name", "?")
        for tier in p.get("tiers") or []:
            tname = tier.get("tier_name", "?")
            price = tier.get("price") or {}
            amount = price.get("normalized_usd_month")
            amount_text = f"${amount}" if amount is not None else "—"
            limits = tier.get("display_limits", "")
            ev = cite(tier.get("evidence_ids") or [])
            lines.append(f"| {name} | {tname} | {amount_text} | {limits} | {ev} |")
    lines.append("")

    gap = pricing_model.get("pricing_gap") or {}
    if gap:
        pos = gap.get("target_position", "unknown")
        summary = gap.get("summary", "")
        ev = cite(gap.get("evidence_ids") or [])
        conf = gap.get("confidence")
        conf_text = f" · 置信 {conf:.2f}" if isinstance(conf, (int, float)) else ""
        lines.append(f"> **target 位置:{pos}**{conf_text} — {summary} {ev}\n")
    return "\n".join(lines)


def _render_personas(user_persona: dict) -> str:
    if not user_persona:
        return ""
    lines = ["## 三、用户画像与痛点\n"]

    segs = user_persona.get("user_segments") or []
    if segs:
        lines.append("### 用户分群\n")
        for u in segs:
            uid = u.get("segment_id", "?")
            name = u.get("name", "?")
            desc = u.get("description", "")
            ev = cite(u.get("evidence_ids") or [])
            lines.append(f"- **{uid} {name}** — {desc} {ev}")
        lines.append("")

    pains = user_persona.get("pain_points") or []
    if pains:
        lines.append("### 核心痛点\n")
        for p in pains:
            pid = p.get("pain_id", "?")
            desc = p.get("description", "")
            freq = p.get("frequency") or {}
            level = freq.get("level", "?")
            count = freq.get("count", "")
            ev = cite(freq.get("evidence_ids") or [])
            affected = ", ".join(p.get("affected_products") or [])
            exp = p.get("user_expectation", "")
            lines.append(f"#### {pid} · 频度 {level}({count})\n")
            lines.append(f"**问题**:{desc} {ev}\n")
            if affected:
                lines.append(f"- 影响产品:{affected}")
            if exp:
                lines.append(f"- 用户期望:{exp}")
            lines.append("")
    return "\n".join(lines)


def _render_recommendations(recs: list[dict]) -> str:
    if not recs:
        return ""
    lines = ["## 四、改进建议(按优先级)\n"]
    for r in recs:
        rid = r.get("rec_id", "?")
        action = r.get("action", "")
        rationale = r.get("rationale", "")
        ps = r.get("priority_score") or {}
        priority = ps.get("priority", "?")
        final = ps.get("final_score")
        final_text = f"{final:.2f}" if isinstance(final, (int, float)) else "—"
        ev = cite(r.get("evidence_ids") or [])
        fids = ", ".join(r.get("source_feature_ids") or []) or "—"
        pids = ", ".join(r.get("source_pain_ids") or []) or "—"

        lines.append(f"### {rid} · **{priority}**(评分 {final_text})\n")
        lines.append(f"**建议**:{action}\n")
        lines.append(f"**依据**:{rationale} {ev}\n")
        lines.append(f"- 源功能差距:{fids}")
        lines.append(f"- 源用户痛点:{pids}")
        if ps:
            lines.append(
                f"- 评分明细:痛点频率 {ps.get('pain_frequency', '?')} / "
                f"商业影响 {ps.get('business_impact', '?')} / "
                f"实施可行性 {ps.get('implementation_feasibility', '?')} / "
                f"证据置信 {ps.get('evidence_confidence', '?')}"
            )
        lines.append("")
    return "\n".join(lines)


def _render_swot(swot: dict) -> str:
    if not swot:
        return ""
    lines = [f"## 五、SWOT(target = {swot.get('target', '?')})\n"]
    note = swot.get("note")
    if note:
        lines.append(f"> {note}\n")

    for label, key in (("优势", "strengths"), ("劣势", "weaknesses"),
                       ("机会", "opportunities"), ("威胁", "threats")):
        items = swot.get(key) or []
        if not items:
            continue
        lines.append(f"### {label}")
        for item in items:
            point = item.get("point", "")
            ev = cite(item.get("evidence_ids") or [])
            conf = item.get("confidence")
            conf_text = f" · 置信 {conf:.2f}" if isinstance(conf, (int, float)) else ""
            lines.append(f"- {point} {ev}{conf_text}")
        lines.append("")
    return "\n".join(lines)


def writer_node(state: AgentState) -> AgentState:
    schema = state.get("schema_draft") or {}
    meta = state["analysis_meta"]

    sections = [
        _render_header(
            meta,
            target=meta.get("target_product", ""),
            competitors=list(meta.get("competitors") or []),
            focus=list(meta.get("analysis_focus") or []),
        ),
        _render_feature_gaps(schema.get("feature_tree") or {}),
        _render_pricing(schema.get("pricing_model") or {}),
        _render_personas(schema.get("user_persona") or {}),
        _render_recommendations(schema.get("recommendations") or []),
        _render_swot(schema.get("swot") or {}),
    ]
    report = "\n\n".join(s for s in sections if s.strip())
    return {**state, "report_draft": report}
