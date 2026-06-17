"""Writer 节点 — 见 docs/design-v2.2.md §七

输出 Markdown 报告,每条 claim 句末追加 [SXXXXXXX] chip。
**禁止**在正文中包含 quality_score / 质检评分(Writer 在 Reviewer 之前运行)。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from .pricing_model import regular_monthly_price
from .state import AgentState


_ORDINALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九",
             "十", "十一", "十二", "十三", "十四", "十五"]


def _renumber_sections(sections: list[str]) -> list[str]:
    """中央重编号:给每个非空 section 的首个顶级 `## ` 标题顺序编上中文序号。
    各 _render_* 函数标题里若已带旧序号(## 一、…)会被剥掉重排,这样调整章节顺序/增删
    模块时不必逐个改函数。h1(`# `)标题与 `### ` 子标题不动。"""
    out: list[str] = []
    idx = 0
    for s in sections:
        if not s or not s.strip():
            continue
        lines = s.split("\n")
        for i, ln in enumerate(lines):
            if ln.startswith("## "):
                title = re.sub(r"^##\s+(?:[一二三四五六七八九十]+、\s*)?", "", ln)
                num = _ORDINALS[idx] if idx < len(_ORDINALS) else str(idx + 1)
                lines[i] = f"## {num}、{title}"
                idx += 1
                break
        out.append("\n".join(lines))
    return out


_RELATION_LABELS = {"direct": "直接竞品", "indirect": "间接竞品", "alternative": "替代方案"}


def _render_competitor_landscape(landscape: dict) -> str:
    if not landscape:
        return ""
    rows: list[str] = []
    for key in ("direct", "indirect", "alternative"):
        for item in landscape.get(key) or []:
            name = item.get("name", "?")
            reason = item.get("reason", "")
            ev = cite(item.get("evidence_ids") or [])
            rows.append(f"| {_RELATION_LABELS.get(key, key)} | {name} | {reason} | {ev} |")
    if not rows:
        return ""
    lines = ["## 竞品格局(直接 / 间接 / 替代)\n"]
    lines.append("| 关系 | 竞品 | 为何纳入 | 证据 |")
    lines.append("|------|------|----------|------|")
    lines.extend(rows)
    rationale = landscape.get("selection_rationale")
    if rationale:
        lines.append("")
        lines.append(f"> **竞品筛选理由**:{rationale}")
    return "\n".join(lines)


def _render_positioning_map(positioning: dict) -> str:
    products = (positioning or {}).get("products") or []
    if not products:
        return ""
    lines = ["## 产品定位地图\n"]
    lines.append("| 产品 | 目标用户 | 核心场景 | 价值主张 | 定位标签 | 证据 |")
    lines.append("|------|----------|----------|----------|----------|------|")
    for p in products:
        ev = cite(p.get("evidence_ids") or [])
        lines.append(
            f"| {p.get('name', '?')} | {p.get('target_user', '')} | {p.get('core_scenario', '')} | "
            f"{p.get('value_proposition', '')} | {p.get('positioning_label', '')} | {ev} |"
        )
    return "\n".join(lines)


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
    "parity_unrated": "能力对等(未评分)",
    "unknown": "待确认",
}

_STATUS_LABELS = {
    "supported": "supported",
    "partially_supported": "partial",
    "not_supported": "unsupported",
    "unknown": "unknown",
}


def cite(evidence_ids: list[str]) -> str:
    """渲染 evidence chip。前端识别 \\[SXXXXXXX\\] 模式触发跳转。"""
    if not evidence_ids:
        return ""
    return "".join(f"[{eid}]" for eid in evidence_ids)


def _evidence_map(evidence: Optional[list[dict]]) -> dict[str, dict]:
    return {str(e.get("evidence_id")): e for e in (evidence or []) if e.get("evidence_id")}


def _source_label(e: dict) -> str:
    st = (e.get("source_type") or "").lower()
    bias = (e.get("source_bias") or "").lower()
    if st == "reddit":
        return "Reddit"
    if st == "hn":
        return "Hacker News"
    if st in ("official_page", "pricing_page") or bias == "vendor_claim":
        return "官方"
    if bias == "third_party":
        return "第三方"
    if bias == "user_generated":
        return "用户反馈"
    if st == "web_search":
        return "搜索摘要"
    return st or bias or "来源未知"


def _claim_label(e: dict) -> str:
    return {
        "feature_existence": "功能确认",
        "performance_quality": "体验质量",
        "pricing": "定价",
        "user_pain": "用户痛点",
        "market_signal": "市场信号",
    }.get(e.get("claim_type"), e.get("claim_type") or "证据")


def cite_readable(evidence_ids: list[str], evidence: Optional[list[dict]], limit: int = 3) -> str:
    """保留裸 [SXXXXXXX] chip,但在旁边补来源/类型,让 Markdown 导出也可审计。"""
    if not evidence_ids:
        return ""
    by_id = _evidence_map(evidence)
    parts = []
    for eid in evidence_ids[:limit]:
        e = by_id.get(str(eid)) or {}
        if e:
            parts.append(f"{cite([eid])} · {_source_label(e)} · {_claim_label(e)}")
        else:
            parts.append(cite([eid]))
    return "<br>".join(parts)


def _evidence_grade(evidence_ids: list[str], evidence: Optional[list[dict]]) -> str:
    if not evidence_ids:
        return "D"
    by_id = _evidence_map(evidence)
    evs = [by_id[eid] for eid in evidence_ids if eid in by_id]
    if not evs:
        return "D"
    biases = {e.get("source_bias") for e in evs}
    claims = {e.get("claim_type") for e in evs}
    has_vendor = "vendor_claim" in biases
    has_user_or_third = bool({"user_generated", "third_party"} & biases)
    has_quality = bool({"performance_quality", "user_pain"} & claims)
    if has_vendor and has_user_or_third and has_quality and len(evs) >= 3:
        return "A"
    if has_user_or_third and has_quality:
        return "B"
    if has_vendor:
        return "C"
    return "D"


def _feature_name_map(feature_tree: dict) -> dict[str, str]:
    return {
        f.get("feature_id"): f.get("name", f.get("feature_id"))
        for f in feature_tree.get("features") or []
        if f.get("feature_id")
    }


def _pain_name_map(schema: dict) -> dict[str, str]:
    return {
        p.get("pain_id"): p.get("description", p.get("pain_id"))
        for p in ((schema.get("user_persona") or {}).get("pain_points") or [])
        if p.get("pain_id")
    }


def _render_header(meta: dict, target: str, competitors: list[str], focus: list[str]) -> str:
    focus_text = " / ".join(focus) if focus else "全维度"
    comp_text = " vs ".join([target, *competitors])
    return (
        f"# {comp_text} — {focus_text} 竞品报告\n\n"
        f"> 报告 ID: {meta.get('report_id', '?')} · "
        f"数据截止: {meta.get('data_cutoff', '?')} · "
        f"目的: {meta.get('analysis_purpose', '?')}\n"
    )


_DECISION_QUESTIONS = [
    ("why_success", "为什么成功"),
    ("how_monetize", "靠什么赚钱"),
    ("moat", "护城河是什么"),
    ("what_to_learn", "我们该学什么"),
    ("what_to_avoid", "我们该避开什么"),
]
_CONF_CN = {"high": "高", "medium": "中", "low": "低"}


def _render_decision_summary(schema: dict, meta: dict) -> str:
    ds = schema.get("decision_summary") or {}
    lines = ["## 决策摘要", "", "> 只读一页:5 个终极问题,每条结论标注置信度。", ""]
    for key, label in _DECISION_QUESTIONS:
        item = ds.get(key) or {}
        answer = item.get("answer")
        if not answer:
            lines.append(f"- **{label}？** 证据不足（置信度低）")
            continue
        conf = _CONF_CN.get(item.get("confidence", "low"), "低")
        chips = cite(item.get("refs") or [])
        lines.append(f"- **{label}？** {answer}（置信度{conf}）{chips}")
    return "\n".join(lines)


_TECH_FIELDS_DEFAULT = [
    {"key": "max_resolution", "label": "最大分辨率"},
    {"key": "max_duration", "label": "最大时长"},
    {"key": "gen_speed", "label": "生成速度"},
    {"key": "model_version", "label": "模型版本/benchmark"},
]


def _render_tech_capability(schema: dict, products: list[str], meta: dict) -> str:
    tc = (schema.get("tech_capability") or {}).get("products") or {}
    indicators = meta.get("tech_indicators") or _TECH_FIELDS_DEFAULT
    lines = [
        "## 技术能力",
        "",
        "> 功能树答『能不能做』,本节答『背后的性能/质量/限制到什么水平』。证据不足标 unknown。",
        "",
        "| 指标 | " + " | ".join(products) + " |",
        "|---|" + "|".join(["---"] * len(products)) + "|",
    ]
    for ind in indicators:
        key = ind.get("key")
        label = ind.get("label", key)
        cells = []
        for product in products:
            value = (tc.get(product) or {}).get(key)
            cells.append(str(value) if value else "unknown")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_business_model(pricing_model: dict) -> str:
    lines = ["## 商业模式逻辑", "", "> 承接定价事实,落到『靠什么赚钱、为什么这么设计』的判断。", ""]
    for prod in pricing_model.get("products") or []:
        engine = prod.get("pricing_engine") or {}
        archetype = engine.get("archetype")
        if archetype:
            lines.append(f"- **{prod.get('name')}**：定价范式 `{archetype}`")
    model_analysis = (
        (pricing_model.get("pricing_strategy_analysis") or {})
        .get("pricing_model_analysis") or {}
    )
    if model_analysis.get("summary"):
        lines += ["", model_analysis["summary"]]
    for product in model_analysis.get("products") or []:
        if product.get("business_logic"):
            lines.append(f"- **{product.get('product')}**：{product['business_logic']}")
    return "\n".join(lines)


def _render_feature_coverage(feature_tree: dict, products: list[str]) -> str:
    analysis = feature_tree.get("analysis") or {}
    coverage = analysis.get("coverage") or {}
    lines = ["## 功能覆盖与差距", ""]
    for product in products:
        item = coverage.get(product) or {}
        known = item.get("coverage_known_only")
        evidence = item.get("evidence_coverage_rate")
        known_text = f"{known * 100:.0f}%" if known is not None else "?"
        evidence_text = f"{evidence * 100:.0f}%" if evidence is not None else "?"
        lines.append(f"- **{product}**：功能覆盖率 {known_text}，证据覆盖率 {evidence_text}")
    lines += ["", "### 单项胜负（缺深度证据时如实判 tie/unclear）", ""]
    for winner in analysis.get("winners") or []:
        conf = _CONF_CN.get(winner.get("confidence", "low"), "低")
        lines.append(
            f"- {winner.get('name')}：**{winner.get('winner')}**"
            f"（置信度{conf}）— {winner.get('reason')}"
        )
    diff_rows = analysis.get("differentiation_matrix") or []
    if diff_rows:
        lines += ["", "### 样本内差异点", ""]
        for row in diff_rows:
            note = row.get("note")
            if note:
                lines.append(f"- {note}")
    return "\n".join(lines)


def _render_feature_insights(feature_tree: dict, target: str) -> str:
    analysis = feature_tree.get("analysis") or {}
    lines = ["## 功能定位 + 护城河 + 蓝海", "", "### 产品形态", ""]
    archetypes = analysis.get("archetypes") or {}
    if archetypes:
        for product, archetype in archetypes.items():
            lines.append(f"- {product}：{archetype}")
    else:
        lines.append("- 证据不足")
    lines += ["", "### 护城河候选（公式：高权重 × 高深度 × 样本内差异点 × 难复制因素）", ""]
    moats = analysis.get("moat_candidates") or []
    if moats:
        for moat in moats:
            conf = _CONF_CN.get(moat.get("confidence", "low"), "低")
            factors = "、".join(moat.get("factors") or []) or "（缺难复制因素，需人工确认）"
            lines.append(
                f"- {moat.get('name')}（{moat.get('domain')}，深度{moat.get('depth_score')}，"
                f"置信度{conf}）：{factors}"
            )
    else:
        lines.append(f"- {target or '目标产品'} 暂无满足公式的护城河候选")
    lines += ["", "### 蓝海机会（公式：高权重需求 × 覆盖不足 × 门槛高）", ""]
    whitespace = analysis.get("whitespace") or []
    if whitespace:
        for item in whitespace:
            lines.append(
                f"- {item.get('name')}（{item.get('domain')}）："
                f"{item.get('reason')}；门槛——{item.get('barrier')}"
            )
    else:
        lines.append("- 暂无满足公式的蓝海机会")
    return "\n".join(lines)


def _render_caliber_lock(schema: dict, meta: dict) -> str:
    feature_analysis = ((schema.get("feature_tree") or {}).get("analysis") or {})
    feature_weight_version = feature_analysis.get("feature_weight_version", "unversioned")
    rows = [
        ("analysis_focus", " / ".join(meta.get("analysis_focus") or []) or "—"),
        ("selected_competitors", " / ".join(meta.get("competitors") or []) or "—"),
        ("comparison_scope", f"target={meta.get('target_product', '—')}"),
        ("pricing_currency", "CNY（混币时如实标注，不跨币比较）"),
        ("pricing_period", "归一为月（年付÷12，季付÷3）"),
        ("unit_cost_formula", "元/积分 = 续费常规月价 ÷ 月积分；单位成本 = 元/积分 × 单位耗分"),
        ("feature_weight_version", feature_weight_version),
        ("unknown_handling_rule", "unknown 不进覆盖率分子/分母，单列证据覆盖率"),
        ("generated_at", meta.get("generated_at", "—")),
    ]
    lines = ["## 口径锁定表", "", "| 口径项 | 取值 |", "|---|---|"]
    lines.extend(f"| {key} | {value} |" for key, value in rows)
    return "\n".join(lines)


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

    if len(avgs) < 2:
        # 不足 2 个产品有质量分 → 评分对比不成立,不宣布领先(避免单格误导)
        if avgs:
            only = next(iter(avgs))
            position = (f"仅 {only} 有足够质量证据({avgs[only]:.1f}/5),"
                        "其余产品质量证据不足,暂不判定已验证体验领先,功能对比以「是否具备」为主。")
        else:
            position = "当前缺少质量证据,无法形成已验证体验评分,功能对比以「是否具备」为主。"
    elif leader and leader == target:
        position = f"{target} 在已验证体验均分上领先({avgs[leader]:.1f}/5)。"
    elif leader and target_score is not None:
        position = (
            f"{leader} 当前已验证体验均分最高({avgs[leader]:.1f}/5)，"
            f"{target} 为 {target_score:.1f}/5。"
        )
    elif leader:
        position = f"{leader} 当前已验证体验均分最高({avgs[leader]:.1f}/5)。"
    else:
        position = "当前证据不足以形成稳定综合评分。"

    risk_gap = next((g for g in gaps if not g["target_wins"]), None)
    strength_gap = next((g for g in gaps if g["target_wins"]), None)
    target_pain = next(
        (p for p in pains if target in (p.get("affected_products") or [])),
        pains[0] if pains else None,
    )
    coverage_note = ""
    products_with_no_score = [p for p in products if p not in avgs]
    if products_with_no_score:
        coverage_note = (
            "但 " + "、".join(products_with_no_score[:3]) +
            " 多数维度仍缺用户侧/第三方质量证据,排名只能视为已验证体验的阶段性判断。"
        )
    short_action = recs[0].get("action", "") if recs else "补齐高置信证据后再排优先级"
    if target_pain:
        risk_text = target_pain.get("description", "")
    elif risk_gap:
        risk_text = f"{risk_gap.get('winner')} 在{risk_gap['name']}上形成压力"
    else:
        risk_text = "核心风险仍需进一步验证"

    if strength_gap:
        strength_text = f"{strength_gap['name']}是当前最可讲清的优势"
    else:
        strength_text = "暂未形成足够稳定的单点优势"

    lines = ["## 一、Executive Summary\n"]
    lines.append(f"**一句话主线**:{position}{coverage_note}")
    lines.append(
        f"{target} 的机会不是把所有指标都包装成领先,而是把「{strength_text}」"
        f"和「{risk_text}」放在同一条决策链里:短期先处理会削弱优势转化的基础体验/可信度问题,"
        "中期再围绕专业工作流、价格带或合规服务扩大差异化。"
    )
    if strength_gap:
        lines.append(f"- **已验证优势**:{strength_gap['reason']} {cite(strength_gap['evidence_ids'][:3])}")
    if risk_gap:
        lines.append(f"- **竞争压力**:{risk_gap['reason']} {cite(risk_gap['evidence_ids'][:3])}")
    if target_pain:
        ev_ids = (target_pain.get("frequency") or {}).get("evidence_ids") or target_pain.get("evidence_ids") or []
        lines.append(f"- **会削弱转化的短板**:{target_pain.get('description', '')} {cite(ev_ids[:3])}")
    if recs:
        top = recs[0]
        lines.append(f"- **优先行动**:{top.get('action', '')} {cite((top.get('evidence_ids') or [])[:3])}")
    lines.append("")
    lines.append("| 关键判断 | 内容 |")
    lines.append("|----------|------|")
    lines.append(f"| 竞争位置 | {position} |")
    lines.append(f"| 核心风险 | {risk_text} |")
    lines.append(f"| 产品机会 | {short_action} |")
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
        stale_pricing = sum(1 for e in evidence
                           if e.get("source_freshness") == "stale" and e.get("claim_type") == "pricing")
        stale_other = stale_count - stale_pricing
        parts: list[str] = []
        if stale_pricing:
            parts.append(f"{stale_pricing} 条定价证据(TTL=7天)已过期，定价可能已调整，建议立即复核官网")
        if stale_other:
            parts.append(f"{stale_other} 条其他证据超过 TTL")
        notes.append("存在 " + "；".join(parts) + "。")
    if not schema.get("recommendations"):
        notes.append("本次分析缺少可执行建议，不能直接作为产品排期依据。")
    swot = schema.get("swot") or {}
    if sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats")) == 0:
        notes.append("SWOT 为空，说明事实层到战略判断层的推导不足。")
    if not notes:
        notes.append("未发现明显信息缺口；仍建议在关键立项前复核最新定价页和用户侧反馈。")
    return "## 三、本报告的不确定性\n\n" + "\n".join(f"{i + 1}. {n}" for i, n in enumerate(notes))


def _render_score_overview(feature_tree: dict, products: list[str], evidence: Optional[list[dict]] = None) -> str:
    features = feature_tree.get("features") or []
    if not features or not products:
        return ""

    product_scores: dict[str, list[float]] = {p: [] for p in products}
    lines = ["## 四、多维度评分总览\n"]
    lines.append(
        "> 口径: **能力状态**只说明是否具备;**已验证体验评分**仅在有用户侧或第三方质量证据时给 1-5 分;"
        "**证据等级** A=官方+用户/第三方交叉验证,B=用户/第三方质量证据,C=仅官方功能证据,D=证据很薄。"
        "未评分不计入均分,`supported` 不等于体验领先。\n"
    )
    lines.append("| 维度 | 能力状态 | 已验证体验评分 | 证据等级 | 关键判断 | 决策含义 |")
    lines.append("|------|----------|----------------|----------|----------|----------|")

    for feat in features:
        name = feat.get("name", "?")
        by_product = feat.get("products") or {}
        status_cells = []
        score_cells = []
        grade_cells = []
        any_score = False
        for product in products:
            pdata = by_product.get(product) or {}
            score = _score_cell(pdata)
            if score is not None:
                product_scores.setdefault(product, []).append(score)
                score_cells.append(f"{product}: {score:.1f}/5")
                any_score = True
            else:
                score_cells.append(f"{product}: 未评分")
            status = (pdata.get("support_status") or "unknown").lower()
            status_cells.append(f"{product}: {_STATUS_LABELS.get(status, status)}")
            ev_ids = (pdata.get("support_evidence_ids") or []) + ((pdata.get("quality_score") or {}).get("evidence_ids") or [])
            grade_cells.append(f"{product}: {_evidence_grade(ev_ids, evidence)}")
        gap = feat.get("gap") or {}
        winner = gap.get("winner")
        reason = gap.get("reason", "")
        insufficient = gap.get("gap_type") == "insufficient_evidence"
        if not any_score:
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
            f"| {name} | "
            + "；".join(status_cells)
            + " | "
            + "；".join(score_cells)
            + " | "
            + "；".join(grade_cells)
            + f" | {reason} {cite((gap.get('evidence_ids') or [])[:2])} | {implication} |"
        )

    avg_cells = []
    for product in products:
        scores = product_scores.get(product) or []
        avg_cells.append(f"**{sum(scores) / len(scores):.1f}/5**" if scores else "—")
    ranked = [
        (sum(scores) / len(scores), product)
        for product, scores in product_scores.items()
        if scores
    ]
    # 只有 ≥2 个产品有质量分时才宣布"已验证体验领先";否则不下结论(避免单格=领先的误导)
    if len(ranked) >= 2:
        leader_text = f"**{max(ranked)[1]} 已验证体验均分领先**"
        implication = "只代表有质量证据覆盖的维度,不是全能力绝对排名"
    else:
        leader_text = "评分覆盖过低,不判定综合领先"
        implication = "需补采用户/第三方质量证据后再比"
    avg_text = "；".join(f"{p}: {avg_cells[i]}" for i, p in enumerate(products))
    lines.append(f"| **已验证体验均分** | — | {avg_text} | — | {leader_text} | {implication} |")
    return "\n".join(lines)


def _render_feature_gaps(feature_tree: dict, evidence: Optional[list[dict]] = None) -> str:
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
                ev = cite_readable(sorted(set(ev_list))[:3], evidence)
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


def _native_price_label(product: dict) -> Optional[str]:
    prices = []
    for tier in product.get("tiers") or []:
        price = tier.get("price") or {}
        amount = price.get("amount")
        currency = (price.get("currency") or "").upper()
        if not isinstance(amount, (int, float)) or amount <= 0:
            for opt in tier.get("billing_options") or []:
                if opt.get("is_promo") or opt.get("cycle") not in ("monthly", "single_month"):
                    continue
                opt_price = opt.get("price") or {}
                opt_amount = opt_price.get("amount")
                if isinstance(opt_amount, (int, float)) and opt_amount > 0:
                    amount = opt_amount
                    currency = (opt_price.get("currency") or "").upper()
                    break
            else:
                continue
        symbol = {"CNY": "¥", "RMB": "¥", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency)
        prices.append((float(amount), f"{symbol or currency or ''}{amount}"))
    if not prices:
        return None
    return min(prices, key=lambda x: x[0])[1]


def _regular_monthly_label(tier: dict) -> Optional[str]:
    monthly = regular_monthly_price(tier)
    if monthly is None:
        return None
    currency = ""
    for opt in tier.get("billing_options") or []:
        if opt.get("is_promo") or opt.get("cycle") not in ("monthly", "single_month"):
            continue
        currency = ((opt.get("price") or {}).get("currency") or "").upper()
        break
    symbol = {"CNY": "¥", "RMB": "¥", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency)
    amount = f"{monthly:g}"
    return f"{symbol or currency or ''}{amount}"


def _render_pricing(pricing_model: dict, feature_tree: dict, products: list[str]) -> str:
    if not pricing_model:
        return ""
    lines = ["## 六、定价对比\n"]
    lines.append("| 产品 | 档位 | 面向用户 | 价格(月费/原币) | 限制 | 证据 |")
    lines.append("|------|------|----------|---------------|------|------|")
    for p in pricing_model.get("products", []):
        name = p.get("name", "?")
        for tier in p.get("tiers") or []:
            tname = tier.get("tier_name", "?")
            seg = tier.get("segment") or "—"
            price = tier.get("price") or {}
            amount = price.get("normalized_usd_month")
            native_amount = price.get("amount")
            currency = (price.get("currency") or "").upper()
            # 0/None 都按「未获取价格」处理;若无 USD 归一价,保留原币金额,避免中文产品整表显示空价。
            if isinstance(amount, (int, float)) and amount > 0:
                amount_text = f"${amount}"
            elif isinstance(native_amount, (int, float)) and native_amount > 0:
                symbol = {"CNY": "¥", "RMB": "¥", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency)
                amount_text = f"{symbol or currency or ''}{native_amount}"
            else:
                amount_text = _regular_monthly_label(tier) or "—"
            limits = tier.get("display_limits", "")
            ev = cite(tier.get("evidence_ids") or [])
            lines.append(f"| {name} | {tname} | {seg} | {amount_text} | {limits} | {ev} |")
    lines.append("")

    unit_rows = []
    for p in pricing_model.get("products", []):
        engine = p.get("pricing_engine") or {}
        if not engine:
            continue
        for tier in engine.get("tiers") or []:
            costs = tier.get("unit_costs") or []
            if not costs and tier.get("price_per_credit") is None:
                continue
            cost_text = "；".join(
                f"{c.get('capability', 'unit')}={c.get('value')} ({c.get('unit') or '单位成本'})"
                for c in costs
            ) or "—"
            ppc = tier.get("price_per_credit")
            ppc_text = f"{ppc}" if isinstance(ppc, (int, float)) else "—"
            unit_rows.append(
                f"| {p.get('name', '?')} | {tier.get('tier_name', '?')} | {ppc_text} | {cost_text} |"
            )
    if unit_rows:
        lines.append("### 单位成本归一化\n")
        lines.append("| 产品 | 档位 | 单位积分成本 | 派生单位成本 |")
        lines.append("|------|------|--------------:|--------------|")
        lines.extend(unit_rows)
        lines.append("")
    comparison = pricing_model.get("engine_comparison") or {}
    if comparison:
        for insight in comparison.get("insights") or []:
            lines.append(f"- {insight}")
        for gap_item in comparison.get("gaps") or []:
            note = gap_item.get("note")
            if note:
                lines.append(f"- 口径限制:{note}")
        if comparison.get("insights") or comparison.get("gaps"):
            lines.append("")
    strategy = pricing_model.get("pricing_strategy_analysis") or {}
    value_analysis = strategy.get("value_for_money_analysis") or {}
    if value_analysis:
        lines.append("### 价格与性价比:场景判断\n")
        lines.append("| 场景 | 预算 | 需求 | 所需能力 | 结论 |")
        lines.append("|------|------|------|----------|------|")
        for scenario in value_analysis.get("scenario_baskets") or []:
            capabilities = "、".join(scenario.get("required_capabilities") or [])
            lines.append(
                f"| {scenario.get('scenario', '?')} | {scenario.get('monthly_budget', '—')} | "
                f"{scenario.get('expected_outputs', scenario.get('decision_basis', '—'))} | "
                f"{capabilities or scenario.get('decision_basis', '—')} | "
                f"{scenario.get('best_for', '—')} |"
            )
        caveats = [c for c in (value_analysis.get("caveats") or []) if c]
        if caveats:
            lines.append("")
            caveat_text = "；".join(c.rstrip("。.;；") for c in caveats)
            lines.append(f"> 注意:{caveat_text}。")
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
        lines.append("| 产品 | 入门付费价 | 能力均分 | 性价比判断 | 对目标产品的威胁 |")
        lines.append("|------|---------:|---------:|------------|------------------|")
        no_price: list[str] = []
        no_score: list[str] = []
        for product in products:
            info = by_name.get(product) or {}
            price = _lowest_price(info)
            score = avgs.get(product)
            if price is None:
                if not _native_price_label(info):
                    no_price.append(product)
            if score is None:
                no_score.append(product)
            price_text = f"${price:.0f}" if price is not None else (_native_price_label(info) or "—")
            score_text = f"{score:.1f}/5" if score is not None else "—"
            if product == target:
                # 目标产品本身就是基准(即便自身价格信息不足,也不应被竞品比下去标成别的)
                value = "基准产品"
                threat = "—"
            elif price is None and _native_price_label(info):
                value = "缺少统一汇率口径"
                threat = "需先归一币种"
            else:
                value, threat = _judge_value_threat(price, score, target_price, target_score)
            lines.append(f"| {product} | {price_text} | {score_text} | {value} | {threat} |")
        notes: list[str] = []
        if no_score:
            notes.append(f"{'、'.join(no_score)} 缺少体验类证据,能力分未评出")
        if no_price:
            notes.append(f"{'、'.join(no_price)} 未抓到付费档价格数值")
        if notes:
            notes.append("缺失维度的判断按可得信息单维给出,空格以 — 展示")
            lines.append("")
            lines.append(f"> {';'.join(notes)}。")
    return "\n".join(lines)


def _judge_value_threat(
    price: Optional[float], score: Optional[float],
    target_price: Optional[float], target_score: Optional[float],
) -> tuple[str, str]:
    """价格-能力映射的判断:price/score 缺谁就按可得单维判,全缺才落 "—"。
    与前端 ReportView.judgeValueThreat 同一套规则,两端呈现一致。"""
    has_p = price is not None and target_price is not None
    has_s = score is not None and target_score is not None
    if has_p and has_s:
        if price < target_price and score >= target_score - 0.5:
            return "性价比压力强", "高"
        if score > target_score:
            return "能力领先", "中高"
        if price < target_price:
            return "价格防守强", "中"
        return "差异化压力有限", "低"
    if has_s:
        if score > target_score + 0.4:
            return "能力领先", "中高"
        if score >= target_score - 0.5:
            return "能力接近", "中"
        return "能力落后", "低"
    if has_p:
        if price < target_price:
            return "价格更低", "待确认"
        return "价格更高", "低"
    return "—", "—"


def _render_personas(user_persona: dict, evidence: Optional[list[dict]] = None) -> str:
    if not user_persona:
        return ""
    # 合成(模拟访谈)证据 id 集合 —— 给纯合成支撑的痛点打「模拟」标
    synthetic_ids = {
        e.get("evidence_id") for e in (evidence or [])
        if str(e.get("source_url") or "").startswith("synthetic")
    }
    lines = ["## 用户之声 — 画像 / 正向反馈 / 痛点\n"]

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

    praises = user_persona.get("praise_points") or []
    if praises:
        lines.append("### 正向反馈(高频表扬)\n")
        lines.append("| 亮点 | 热度 | 出现频次 | 涉及产品 | 证据 |")
        lines.append("|------|------|----------|----------|------|")
        for p in praises:
            prid = p.get("praise_id", "?")
            desc = p.get("description", "")
            freq = p.get("frequency") or {}
            level = freq.get("level", "?")
            count = freq.get("count", "")
            ev_ids = freq.get("evidence_ids") or p.get("evidence_ids") or []
            ev = cite(ev_ids)
            synth_only = bool(ev_ids) and synthetic_ids and all(i in synthetic_ids for i in ev_ids)
            desc_md = f"【模拟】{desc}" if synth_only else desc
            affected = ", ".join(p.get("affected_products") or [])
            lines.append(f"| {prid} {desc_md} | {level} | {count} | {affected or '—'} | {ev} |")
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


def _competitive_link_for_rec(r: dict, schema: dict) -> str:
    feature_names = _feature_name_map(schema.get("feature_tree") or {})
    pain_names = _pain_name_map(schema)
    fids = [fid for fid in (r.get("source_feature_ids") or []) if fid in feature_names]
    pids = [pid for pid in (r.get("source_pain_ids") or []) if pid in pain_names]
    action = str(r.get("action") or "")
    rationale = str(r.get("rationale") or "")
    if r.get("source_pricing") or any(w in action + rationale for w in ("价格", "定价", "订阅", "档位", "$", "美元")):
        return (
            "这是价格带/价值感动作,需要明确回应竞品价格锚点;若新档位没有低于被防守竞品,"
            "应改为权益增强或说明不是价格战。"
        )
    if fids and pids:
        return (
            "这条动作同时连接功能差距和用户痛点:围绕 "
            + "、".join(feature_names[fid] for fid in fids[:2])
            + " 修复 "
            + "、".join(pain_names[pid] for pid in pids[:2])
            + ",目的是把已验证优势转化为留存/迁移理由。"
        )
    if fids:
        return (
            "这条动作服务于竞争定位:围绕 "
            + "、".join(feature_names[fid] for fid in fids[:2])
            + " 拉开差异,而不是孤立补功能。"
        )
    if pids:
        return (
            "这条动作是基础体验防守:先修复 "
            + "、".join(pain_names[pid] for pid in pids[:2])
            + ",避免核心优势在转化和留存环节被抵消。"
        )
    return "这条动作的竞争锚点偏弱,建议补充对应功能差距、用户痛点或定价证据后再进入排期。"


def _pricing_logic_warning(r: dict) -> str:
    text = f"{r.get('action', '')} {r.get('rationale', '')}"
    if not any(w in text for w in ("低于", "低价", "防守", "价格", "定价", "档位", "美元", "$")):
        return ""
    nums = [
        float(a or b)
        for a, b in re.findall(
            r"(?:\$\s*(\d+(?:\.\d+)?)|(?<!\d)(\d+(?:\.\d+)?)\s*(?:美元|美金|USD))",
            text,
            flags=re.I,
        )
    ]
    if len(nums) >= 2 and "低于" in text and nums[0] >= min(nums[1:]):
        return (
            "⚠️ 定价逻辑需复核:建议动作中的新价格并未低于文中对比价。"
            "应改为真正低价档,或保持价格并增加权益。"
        )
    return ""


_ACTION_BLOCKS = [
    ("learn", "12.1 Learn — 学竞品已验证强项"),
    ("avoid", "12.2 Avoid — 避开竞品高权重领先区"),
    ("attack", "12.3 Attack — 切入高价值空白区"),
]


def _priority_value(item: dict) -> object:
    value = item.get("priority_score_100")
    if isinstance(value, (int, float)):
        return value
    ps = item.get("priority_score")
    if isinstance(ps, (int, float)):
        return ps
    if isinstance(ps, dict) and isinstance(ps.get("final_score"), (int, float)):
        return round(float(ps["final_score"]) * 20)
    return "?"


def _priority_sort_key(item: dict) -> float:
    value = _priority_value(item)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _render_legacy_recommendation(r: dict, schema: dict) -> list[str]:
    rid = r.get("rec_id", "?")
    action = r.get("action", "")
    rationale = r.get("rationale", "")
    ps = r.get("priority_score") or {}
    priority = ps.get("priority", "?") if isinstance(ps, dict) else "?"
    final = ps.get("final_score") if isinstance(ps, dict) else None
    final_text = f"{final:.2f}" if isinstance(final, (int, float)) else "—"
    ev = cite(r.get("evidence_ids") or [])
    fids = ", ".join(r.get("source_feature_ids") or []) or "—"
    pids = ", ".join(r.get("source_pain_ids") or []) or "—"
    lines = [f"#### {rid} · **{priority}**(评分 {final_text})", ""]
    lines.append(f"**建议**:{action}")
    lines.append(f"**竞品机会**:{_competitive_link_for_rec(r, schema)}")
    lines.append(f"**依据**:{rationale} {ev}")
    warning = _pricing_logic_warning(r)
    if warning:
        lines.append(f"- 逻辑校验:{warning}")
    lines.append(f"- 目标收益:{r.get('expected_impact') or '待验证'}")
    lines.append(f"- 验收指标:{r.get('success_metric') or '待定义'}")
    lines.append(f"- 风险:{r.get('risk') or '待评估'}")
    lines.append(f"- 周期:{r.get('time_horizon') or '待估算'}")
    lines.append(f"- 验证方式:{r.get('validation_method') or '用户访谈 / A/B 测试 / 灰度验证'}")
    lines.append(f"- 源功能差距:{fids}")
    lines.append(f"- 源用户痛点:{pids}")
    if isinstance(ps, dict) and ps:
        lines.append(
            f"- 评分明细:痛点频率 {ps.get('pain_frequency', '?')} / "
            f"商业影响 {ps.get('business_impact', '?')} / "
            f"实施可行性 {ps.get('implementation_feasibility', '?')} / "
            f"证据置信 {ps.get('evidence_confidence', '?')}"
        )
    lines.append("")
    return lines


def _render_recommendations(recs: list[dict], schema: Optional[dict] = None) -> str:
    if not recs:
        return ""
    schema = schema or {}
    lines = ["## 优先级建议", ""]
    by_type = {key: [] for key, _ in _ACTION_BLOCKS}
    other = []
    for r in recs:
        action_type = r.get("action_type")
        if action_type in by_type:
            by_type[action_type].append(r)
        else:
            other.append(r)
    for key, title in _ACTION_BLOCKS:
        items = sorted(
            by_type[key],
            key=_priority_sort_key,
            reverse=True,
        )
        lines += [f"### {title}", ""]
        if not items:
            lines.append("- 暂无")
            lines.append("")
            continue
        for item in items:
            chips = cite(item.get("evidence_refs") or item.get("evidence_ids") or [])
            lines.append(
                f"- **{item.get('action', '')}**（优先级 {_priority_value(item)}，"
                f"对标 {item.get('target_competitor', '—')}）{chips}"
            )
            if item.get("rationale"):
                lines.append(f"  - 依据：{item['rationale']}")
            if item.get("risk"):
                lines.append(f"  - 风险：{item['risk']}")
        lines.append("")
    if other:
        lines += ["### 其它建议", ""]
        for item in other:
            lines.extend(_render_legacy_recommendation(item, schema))
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


_GAP_LABEL = {
    "pricing_no_number": "定价数据不可得（疑似积分制 / SPA 动态渲染，未抓到明码月费）",
    "no_official": "缺官网权威源（功能/定价以第三方为准，审慎）",
    "bias_all_vendor": "仅厂商口径，缺真实用户/第三方视角，结论需审慎",
    "coverage_short": "该维度证据偏薄，结论置信有限",
    "total_too_few": "整体样本过薄，结论置信有限",
}


def _render_data_availability(quality_audit: dict) -> str:
    """诚实降级:把采集自愈后仍未闭合的 Gap 显式标注「不可得」,不以推测填充(核心原则#4)。"""
    gaps = (quality_audit or {}).get("gaps") or []
    if not gaps:
        return ""
    lines = [
        "## 数据可得性说明\n",
        "> 以下维度经采集自愈后仍未达标，已**诚实标注「不可得」**，未以推测/营销话术填充。\n",
        "| 产品 | 维度 | 说明 |",
        "|------|------|------|",
    ]
    for g in gaps:
        label = _GAP_LABEL.get(g.get("gap_type")) or g.get("reason", "")
        lines.append(f"| {g.get('product', '—')} | {g.get('claim_type') or '—'} | {label} |")
    lines.append("")
    return "\n".join(lines)


def writer_node(state: AgentState) -> AgentState:
    schema = state.get("schema_draft") or {}
    meta = state["analysis_meta"]
    products = _products(meta)
    evidence = state.get("raw_evidence") or []
    quality_audit = (state.get("collection_meta") or {}).get("quality_audit") or {}

    feature_tree = schema.get("feature_tree") or {}
    pricing_model = schema.get("pricing_model") or {}
    target = meta.get("target_product", "")
    sections = [
        _render_header(
            meta,
            target=target,
            competitors=list(meta.get("competitors") or []),
            focus=list(meta.get("analysis_focus") or []),
        ),
        _render_decision_summary(schema, meta),
        # 事实层 FACTS
        _render_competitor_landscape(schema.get("competitor_landscape") or {}),
        _render_score_overview(feature_tree, products, evidence),
        _render_pricing(pricing_model, feature_tree, products),
        _render_personas(schema.get("user_persona") or {}, evidence),
        _render_tech_capability(schema, products, meta),
        # 比较层 COMPARISON
        _render_positioning_map(schema.get("positioning_map") or {}),
        _render_feature_coverage(feature_tree, products),
        # 洞察层 INSIGHTS
        _render_feature_insights(feature_tree, target),
        _render_business_model(pricing_model),
        _render_swot(schema.get("swot") or {}),
        # 决策层 DECISIONS
        _render_recommendations(schema.get("recommendations") or [], schema),
        # 支撑 APPENDIX
        _render_data_availability(quality_audit),
        _render_evidence_coverage(evidence),
        _render_uncertainty(evidence, schema),
        _render_caliber_lock(schema, meta),
    ]
    sections = _renumber_sections(sections)
    report = "\n\n".join(s for s in sections if s.strip())
    return {**state, "report_draft": report}
