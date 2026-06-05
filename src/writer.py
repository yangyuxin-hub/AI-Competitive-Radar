"""Writer 节点 — 见 docs/design-v2.2.md §七

输出 Markdown 报告,每条 claim 句末追加 [SXXXXXXX] chip。
**禁止**在正文中包含 quality_score / 质检评分(Writer 在 Reviewer 之前运行)。
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

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
    "insufficient_evidence": "证据不足",
    "unknown": "待确认",
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
    )


def _products(meta: dict) -> list[str]:
    return [p for p in [meta.get("target_product"), *list(meta.get("competitors") or [])] if p]


def _score_cell(pdata: dict) -> Optional[float]:
    """返回该产品在某功能上的"真实"质量分;证据不足→ None(渲染为「未评分」)。
    约定:score 0 一律视为「未评分」——0-5 质量分上的 0 从不是「真打了 0 分」,
    而是 Analyzer 对「证据不足」的占位(见 prompts/analyzer_facts 约定)。
    None 不计入对比/均分,避免把"没数据"误当成"真实 0 分/打平"。"""
    qs = pdata.get("quality_score") or {}
    if (pdata.get("support_status") or "").lower() == "unknown":
        return None
    try:
        f = float(qs.get("score"))
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return f


def _average_scores(feature_tree: dict, products: list[str]) -> dict[str, float]:
    scores: dict[str, list[float]] = {p: [] for p in products}
    for feat in feature_tree.get("features") or []:
        for product in products:
            score = _score_cell((feat.get("products") or {}).get(product) or {})
            if score is not None:
                scores.setdefault(product, []).append(score)
    return {p: round(sum(v) / len(v), 2) for p, v in scores.items() if v}


def _top_gaps(feature_tree: dict, target: str, limit: int = 3) -> list[dict]:
    gaps = []
    for feat in feature_tree.get("features") or []:
        gap = feat.get("gap") or {}
        if not gap:
            continue
        gaps.append({
            "name": feat.get("name", ""),
            "winner": gap.get("winner"),
            "reason": gap.get("reason", ""),
            "confidence": gap.get("confidence") or 0,
            "evidence_ids": gap.get("evidence_ids") or [],
            "target_wins": gap.get("winner") == target,
        })
    return sorted(gaps, key=lambda g: float(g["confidence"] or 0), reverse=True)[:limit]


def _render_executive_summary(schema: dict, meta: dict) -> str:
    feature_tree = schema.get("feature_tree") or {}
    products = _products(meta)
    target = meta.get("target_product", "")
    avgs = _average_scores(feature_tree, products)
    leader = max(avgs, key=avgs.get) if avgs else None
    target_score = avgs.get(target)
    gaps = _top_gaps(feature_tree, target, limit=3)
    recs = schema.get("recommendations") or []
    pains = (schema.get("user_persona") or {}).get("pain_points") or []

    if leader and leader == target:
        position = f"{target} 当前综合评分领先({avgs[leader]:.1f}/5)。"
    elif leader and target_score is not None:
        position = (
            f"{leader} 当前综合评分最高({avgs[leader]:.1f}/5)，"
            f"{target} 为 {target_score:.1f}/5。"
        )
    elif leader:
        position = f"{leader} 当前综合评分最高({avgs[leader]:.1f}/5)。"
    else:
        position = "当前证据不足以形成稳定综合评分。"

    risk_gap = next((g for g in gaps if not g["target_wins"]), None)
    strength_gap = next((g for g in gaps if g["target_wins"]), None)
    lines = ["## 一、Executive Summary\n"]
    lines.append(f"**结论先行**:{position}")
    if strength_gap:
        lines.append(f"目标产品优势集中在 **{strength_gap['name']}**:{strength_gap['reason']} {cite(strength_gap['evidence_ids'][:3])}")
    if risk_gap:
        lines.append(f"最大竞争压力来自 **{risk_gap.get('winner')}** 在 **{risk_gap['name']}** 上的领先:{risk_gap['reason']} {cite(risk_gap['evidence_ids'][:3])}")
    if recs:
        top = recs[0]
        lines.append(f"优先行动建议: **{top.get('action', '')}** {cite((top.get('evidence_ids') or [])[:3])}")
    elif pains:
        lines.append(f"当前最需要补齐的是可执行建议层；已识别的首要痛点是 **{pains[0].get('description', '')}**。")
    lines.append("")
    lines.append("| 关键判断 | 内容 |")
    lines.append("|----------|------|")
    lines.append(f"| 竞争位置 | {position} |")
    lines.append(f"| 核心风险 | {risk_gap['name'] if risk_gap else '待进一步验证'} |")
    lines.append(f"| 产品机会 | {recs[0].get('action') if recs else '补齐高置信建议与验证指标'} |")
    return "\n".join(lines)


def _render_evidence_coverage(evidence: list[dict]) -> str:
    if not evidence:
        return ""
    claim_labels = {
        "feature_existence": "功能确认",
        "performance_quality": "性能/体验",
        "pricing": "定价",
        "user_pain": "用户痛点",
        "market_signal": "市场信号",
    }
    source_counts = Counter(e.get("source_type") or "unknown" for e in evidence)
    claim_counts = Counter(e.get("claim_type") or "unknown" for e in evidence)
    bias_counts = Counter(e.get("source_bias") or "unknown" for e in evidence)
    lines = ["## 二、证据覆盖地图\n"]
    lines.append(f"> 当前报告共使用 {len(evidence)} 条证据。功能和定价可主要依赖官方来源；体验质量和用户痛点需要用户侧或第三方来源交叉验证。\n")
    lines.append("| 证据类型 | 数量 |")
    lines.append("|----------|-----:|")
    for key, count in claim_counts.most_common():
        lines.append(f"| {claim_labels.get(key, key)} | {count} |")
    lines.append("")
    lines.append("| 来源立场 | 数量 | 用途 |")
    lines.append("|----------|-----:|------|")
    purpose = {
        "vendor_claim": "确认功能、定价、官方定位",
        "user_generated": "校验痛点、体验波动、真实抱怨",
        "third_party": "横向评测、市场视角、交叉验证",
        "synthetic": "模拟问卷/访谈(LLM 合成,非真实用户,仅作补充)",
    }
    for key, count in bias_counts.most_common():
        lines.append(f"| {key} | {count} | {purpose.get(key, '补充参考')} |")
    if source_counts:
        lines.append("")
        lines.append("来源类型: " + " / ".join(f"{k}×{v}" for k, v in source_counts.most_common(6)))
    return "\n".join(lines)


def _render_uncertainty(evidence: list[dict], schema: dict) -> str:
    notes: list[str] = []
    meta = schema.get("analysis_meta") or {}
    products = _products(meta) or list(
        ((schema.get("feature_tree") or {}).get("features") or [{}])[0].get("products") or {}
    )

    # (1) 对比矩阵稀疏度:统计每个产品 unknown/未评分的格子,点名覆盖不足的产品
    features = (schema.get("feature_tree") or {}).get("features") or []
    if features and products:
        unknown_by_product = {p: 0 for p in products}
        insufficient = 0
        for feat in features:
            for p in products:
                if _score_cell((feat.get("products") or {}).get(p) or {}) is None:
                    unknown_by_product[p] += 1
            if (feat.get("gap") or {}).get("gap_type") == "insufficient_evidence":
                insufficient += 1
        weak = [f"{p}（{n}/{len(features)} 项无质量证据）"
                for p, n in unknown_by_product.items() if n >= max(2, len(features) // 2)]
        if weak:
            notes.append("以下产品在多数功能维度证据不足，对比结论偏单薄，建议补采："
                         + "、".join(weak) + "。")
        if insufficient:
            notes.append(f"{insufficient} 个功能仅单个产品有证据（差距类型 insufficient_evidence），"
                         "其胜负判断为弱结论，不宜直接用于决策。")

    # (2) 定价完整度:统计有归一化价格数值的档位占比
    tiers = [t for p in (schema.get("pricing_model") or {}).get("products") or []
             for t in (p.get("tiers") or [])]
    if tiers:
        priced = sum(1 for t in tiers if (t.get("price") or {}).get("normalized_usd_month") is not None)
        if priced < max(1, len(tiers) // 2):
            notes.append(f"定价档位中仅 {priced}/{len(tiers)} 个有明确价格数值，价格高低对比不完整，"
                         "需补采官方定价页的具体金额。")

    # (3) 来源结构 / 时效
    bias_counts = Counter(e.get("source_bias") or "unknown" for e in evidence)
    if evidence and set(bias_counts) <= {"vendor_claim"}:
        notes.append("当前证据主要来自厂商官方材料，用户体验与痛点结论需要第三方/用户侧证据补强。")
    stale_count = sum(1 for e in evidence if e.get("source_freshness") == "stale")
    if stale_count:
        notes.append(f"存在 {stale_count} 条超过 TTL 的证据，涉及定价或功能变化时应优先复核。")
    if not schema.get("recommendations"):
        notes.append("本次分析缺少可执行建议，不能直接作为产品排期依据。")
    swot = schema.get("swot") or {}
    if sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats")) == 0:
        notes.append("SWOT 为空，说明事实层到战略判断层的推导不足。")
    if not notes:
        notes.append("未发现明显信息缺口；仍建议在关键立项前复核最新定价页和用户侧反馈。")
    return "## 三、本报告的不确定性\n\n" + "\n".join(f"{i + 1}. {n}" for i, n in enumerate(notes))


def _render_score_overview(feature_tree: dict, products: list[str]) -> str:
    features = feature_tree.get("features") or []
    if not features or not products:
        return ""

    product_scores: dict[str, list[float]] = {p: [] for p in products}
    lines = ["## 四、多维度评分总览\n"]
    lines.append("> 评分口径: 基于各维度 `quality_score.score` 计算，满分 5 分；证据不足时不计入均分。\n")
    lines.append("| 维度 | " + " | ".join(products) + " | 关键差异 | 产品含义 |")
    lines.append("|------|" + "|".join(["------"] * len(products)) + "|----------|----------|")

    for feat in features:
        name = feat.get("name", "?")
        by_product = feat.get("products") or {}
        cells = []
        for product in products:
            score = _score_cell(by_product.get(product) or {})
            if score is None:
                cells.append("—")
            else:
                product_scores.setdefault(product, []).append(score)
                cells.append(f"{score:.1f}/5")
        scored_in_row = [c for c in cells if c != "—"]
        gap = feat.get("gap") or {}
        winner = gap.get("winner")
        reason = gap.get("reason", "")
        insufficient = gap.get("gap_type") == "insufficient_evidence"
        if not scored_in_row:
            reason = "各产品均缺少质量证据，未评分"
            implication = "证据不足，需补采"
        elif insufficient:
            # 只有单个产品有分,不能据此宣称"放大优势";如实提示补齐竞品
            implication = f"仅 {winner} 有数据，需补齐竞品对比再下结论"
        elif winner and winner == products[0]:
            implication = "可作为优势叙事继续放大"
        elif winner and winner != "unknown":
            implication = f"需要解释或补齐 {winner} 的领先点"
        else:
            implication = "需要补充证据确认"
        lines.append(
            f"| {name} | " + " | ".join(cells) +
            f" | {reason} {cite((gap.get('evidence_ids') or [])[:2])} | {implication} |"
        )

    avg_cells = []
    for product in products:
        scores = product_scores.get(product) or []
        avg_cells.append(f"**{sum(scores) / len(scores):.1f}/5**" if scores else "—")
    leader = "—"
    ranked = [
        (sum(scores) / len(scores), product)
        for product, scores in product_scores.items()
        if scores
    ]
    if ranked:
        leader = max(ranked)[1]
    lines.append("| **综合均分** | " + " | ".join(avg_cells) + f" | **{leader} 综合领先** | 作为定位判断的第一层依据 |")
    return "\n".join(lines)


def _render_feature_gaps(feature_tree: dict) -> str:
    if not feature_tree:
        return ""
    lines = [f"## 五、功能差距 — {feature_tree.get('category', '功能对比')}\n"]
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
                scale = qs.get("scale", 5)
                cell = _score_cell(pdata)
                quality = f"{cell:.0f}/{scale}" if cell is not None else "未评分"
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


def _lowest_price(product: dict) -> Optional[float]:
    prices = []
    for tier in product.get("tiers") or []:
        price = tier.get("price") or {}
        amount = price.get("normalized_usd_month")
        # 0 视为「未获取价格」占位(Analyzer 抽不到数值时填 0),不计入最低价
        if isinstance(amount, (int, float)) and amount > 0:
            prices.append(float(amount))
    return min(prices) if prices else None


def _render_pricing(pricing_model: dict, feature_tree: dict, products: list[str]) -> str:
    if not pricing_model:
        return ""
    lines = ["## 六、定价对比\n"]
    lines.append("| 产品 | 档位 | 价格(USD/月) | 限制 | 证据 |")
    lines.append("|------|------|---------------|------|------|")
    for p in pricing_model.get("products", []):
        name = p.get("name", "?")
        for tier in p.get("tiers") or []:
            tname = tier.get("tier_name", "?")
            price = tier.get("price") or {}
            amount = price.get("normalized_usd_month")
            # 0/None 都按「未获取价格」渲染为「—」(Analyzer 抽不到数值时填 0,显示 $0 会误导)
            amount_text = f"${amount}" if isinstance(amount, (int, float)) and amount > 0 else "—"
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

    avgs = _average_scores(feature_tree, products)
    by_name = {p.get("name"): p for p in pricing_model.get("products", [])}
    target = products[0] if products else ""
    target_price = _lowest_price(by_name.get(target) or {})
    target_score = avgs.get(target)
    if products and by_name:
        lines.append("### 价格-能力映射\n")
        lines.append("| 产品 | 入门月价 | 能力均分 | 性价比判断 | 对目标产品的威胁 |")
        lines.append("|------|---------:|---------:|------------|------------------|")
        for product in products:
            info = by_name.get(product) or {}
            price = _lowest_price(info)
            score = avgs.get(product)
            price_text = f"${price:.0f}" if price is not None else "待确认"
            score_text = f"{score:.1f}/5" if score is not None else "—"
            if product == target:
                # 目标产品本身就是基准(即便自身价格信息不足,也不应被竞品比下去标成别的)
                value = "基准产品"
                threat = "—"
            elif price is None or score is None:
                value = "信息不足"
                threat = "待确认"
            elif target_score is not None and target_price is not None:
                if price < target_price and score >= target_score - 0.5:
                    value = "性价比压力强"
                    threat = "高"
                elif score > target_score:
                    value = "能力领先"
                    threat = "中高"
                elif price < target_price:
                    value = "价格防守强"
                    threat = "中"
                else:
                    value = "差异化压力有限"
                    threat = "低"
            else:
                # 目标产品价格/能力信息不足,无法做横向对比
                value = "待对齐(目标信息不足)"
                threat = "待确认"
            lines.append(f"| {product} | {price_text} | {score_text} | {value} | {threat} |")
    return "\n".join(lines)


def _render_personas(user_persona: dict, evidence: Optional[list[dict]] = None) -> str:
    if not user_persona:
        return ""
    # 合成(模拟访谈)证据 id 集合 —— 给纯合成支撑的痛点打「模拟」标
    synthetic_ids = {
        e.get("evidence_id") for e in (evidence or [])
        if str(e.get("source_url") or "").startswith("synthetic")
    }
    lines = ["## 七、用户画像与痛点\n"]

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
        lines.append("| 痛点 | 严重程度 | 出现频次 | 影响产品 | 产品机会 | 证据 |")
        lines.append("|------|----------|----------|----------|----------|------|")
        for p in pains:
            pid = p.get("pain_id", "?")
            desc = p.get("description", "")
            freq = p.get("frequency") or {}
            level = freq.get("level", "?")
            count = freq.get("count", "")
            ev_ids = freq.get("evidence_ids") or p.get("evidence_ids") or []
            ev = cite(ev_ids)
            # 仅靠合成证据支撑 → 描述前加「【模拟】」,避免误读为真实用户反馈
            synth_only = bool(ev_ids) and synthetic_ids and all(i in synthetic_ids for i in ev_ids)
            desc_md = f"【模拟】{desc}" if synth_only else desc
            affected = ", ".join(p.get("affected_products") or [])
            exp = p.get("user_expectation", "")
            lines.append(f"| {pid} {desc_md} | {level} | {count} | {affected or '—'} | {exp or '待分析'} | {ev} |")
        lines.append("")
    return "\n".join(lines)


def _render_recommendations(recs: list[dict]) -> str:
    if not recs:
        return ""
    lines = ["## 八、改进建议(按优先级)\n"]
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
        lines.append(f"- 目标收益:{r.get('expected_impact') or '待验证'}")
        lines.append(f"- 验收指标:{r.get('success_metric') or '待定义'}")
        lines.append(f"- 风险:{r.get('risk') or '待评估'}")
        lines.append(f"- 周期:{r.get('time_horizon') or '待估算'}")
        lines.append(f"- 验证方式:{r.get('validation_method') or '用户访谈 / A/B 测试 / 灰度验证'}")
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
    lines = [f"## 九、SWOT(target = {swot.get('target', '?')})\n"]
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
    products = _products(meta)
    evidence = state.get("raw_evidence") or []

    sections = [
        _render_header(
            meta,
            target=meta.get("target_product", ""),
            competitors=list(meta.get("competitors") or []),
            focus=list(meta.get("analysis_focus") or []),
        ),
        _render_executive_summary(schema, meta),
        _render_evidence_coverage(evidence),
        _render_uncertainty(evidence, schema),
        _render_score_overview(
            schema.get("feature_tree") or {},
            products,
        ),
        _render_feature_gaps(schema.get("feature_tree") or {}),
        _render_pricing(schema.get("pricing_model") or {}, schema.get("feature_tree") or {}, products),
        _render_personas(schema.get("user_persona") or {}, evidence),
        _render_recommendations(schema.get("recommendations") or []),
        _render_swot(schema.get("swot") or {}),
    ]
    report = "\n\n".join(s for s in sections if s.strip())
    return {**state, "report_draft": report}
