"""Analyzer 节点(两步式) — 见 docs/design-v2.2.md §六

Step 1: facts (feature_tree + pricing_model + user_persona)
Step 2: derivations (swot + recommendations)

每步带一次 quick_validate 本地自修复。
"""
from __future__ import annotations

import contextvars
import copy
import json
import os
import re
import threading
from concurrent.futures import as_completed

from .progress import CtxThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .analyzer_sanitize import (  # noqa: F401 — 确定性后处理簇,re-export 保 callsite/测试兼容
    _filter_evidence_ids,
    _freq_is_truly_frequent,
    _soften_text,
    repair_rec_anchors,
    sanitize_derivations,
    sanitize_facts_evidence_refs,
    sanitize_schema_evidence_refs,
    soften_overgeneralization,
)
from .analyzer_common import (  # noqa: F401 — 基座 helper/预览/dedup/进度,re-export 保 back-compat
    _FACTS_SECTIONS,
    _REQUIRED_CT,
    _compact_evidence,
    _der_summary,
    compact_evidence_for_deriv,
    llm_meta,
    prompt_slim_enabled,
    _derivations_preview,
    _emit_progress,
    _evidence_ids,
    _evidence_preview,
    _facts_preview,
    _facts_summary,
    _is_demo_loop,
    _near_dup,
    _norm_tokens,
    _safe_price_tier,
    _short,
    _target_products,
    load_prompt,
    set_progress_callback,
)
from .analyzer_augment import (  # noqa: F401 — 证据增强侧流(补采/合成访谈),re-export 保 back-compat
    _coverage_gaps,
    _gap_affected_sections,
    _gap_refill_enabled,
    _gap_targeted_recollect,
    _real_ugc_count,
    _recollect_pricing_official,
    _run_survey,
    _survey_enabled,
    _survey_should_run,
)
from .analyzer_fallback import (  # noqa: F401 — 骨架兜底构建器,re-export 保 back-compat
    _corrupt_derivations_for_demo,
    _corrupt_facts_for_demo,
    _fallback_derivations,
    _fallback_facts,
)
from .evidence_gaps import pool_recall_enabled, recall_from_pool  # M1 统一缺口口径+池内回捞
from .llm import deep_thinking_mode, get_llm, is_mock_mode, load_sample_report
from .state import AgentState

# B2: per-product feature_fill 调用计数（跨 gap_refill 轮次累加），超阈值走骨架兜底。
# ContextVar 按 run 隔离(此前是模块级全局,并发 run 共享计数:一方 clear 抹掉另一方,熔断失效/误熔断)。
# 跨线程池传播由 CtxThreadPoolExecutor submit 时快照保证,子线程拿到同一 dict 引用,自增用锁保护
# (与 llm._TOKEN_ACC 同模式)。
_FILL_ATTEMPTS_VAR: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "analyzer_fill_attempts", default=None)
_FILL_ATTEMPTS_LOCK = threading.Lock()
_FILL_ATTEMPTS_MAX = int(os.environ.get("FEATURE_FILL_MAX_ATTEMPTS", "3"))


def _fill_attempts() -> dict:
    """本 run 的 fill 计数 dict;直接调用(测试/不经 analyzer_node)时懒初始化。"""
    d = _FILL_ATTEMPTS_VAR.get()
    if d is None:
        d = {}
        _FILL_ATTEMPTS_VAR.set(d)
    return d


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

        # (c) 至少 1 个有效引用(feature_id / pain_id / 定价锚 source_pricing 三选一)
        if not (fids & valid_fids) and not (pids & valid_pids) and not rec.get("source_pricing"):
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
        # 措辞纪律(R6 收敛):不在 prose 里复述精确 X/5 评分(那是合成判断,非测量值,
        # 易被语义审计判为"无依据的精确结论");精确分仍保留在结构化字段与§四评分表中。
        winner, score, win_data = rated[0]
        return {"winner": winner, "gap_type": "insufficient_evidence",
                "reason": f"仅 {winner} 在「{name}」上有较充分的用户体验反馈，"
                          "其余产品证据不足，暂不作强对比",
                "evidence_ids": _eids(win_data), "confidence": 0.3}

    # ≥2 个产品有真实分:在它们之间判胜负
    rated.sort(key=lambda x: (x[1], x[0] == target), reverse=True)
    winner, top, win_data = rated[0]
    second = rated[1][1]
    spread = top - second
    any_missing = any((d.get("support_status") == "not_supported") for _, _, d in rated)
    gap_type = "feature_completeness" if any_missing else ("performance" if spread > 0 else "usability")
    # 按差距量级分级措辞:1 分(粗判)的差距用"略优/有限",不夸成"领先";
    # 且不复述精确 X/5 vs Y/5(避免被读作基准测量)——精确分见§四评分表。
    if spread >= 2:
        reason = f"{winner} 在「{name}」上用户体验评价明显更优（综合多条反馈，优于次优产品）"
    elif spread == 1:
        reason = f"{winner} 在「{name}」上用户体验评价略优，但与次优产品差距有限"
    else:
        reason = f"已评分产品在「{name}」上体验评价相近，差距主要在支持范围"
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
        '只输出 JSON: {"features":[{"feature_id":"F001","name":"功能名","name_en":"english capability term"}]}。\n'
        "feature_id 用 F001/F002…;name 用产品能力级短语(≤12字);"
        "**name_en 给该能力的通用英文检索词**(如 实时多人协同→real-time collaboration、原型交互→prototyping、"
        "第三方集成→third-party integrations、跨信息关联→database relations linking)——定向补采会拿它去搜英文官网/文档,"
        "务必准确、用业界通用说法;不要输出 products / gap / quality 等其它字段。"
    )
    spine = get_llm().call_json(
        f"{system_base}\n\n## 本次任务范围(重要)\n{spine_instruct}",
        {"analysis_meta": llm_meta(meta), "feature_evidence_count_by_product": cov_by_prod,
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
                   summary=f"[分析阶段] 按 {len(feat_names)} 个功能为 {len(products)} 个产品定向补采证据")
    existing_ids = {e.get("evidence_id") for e in evidence}
    added: list[dict] = []
    with CtxThreadPoolExecutor(max_workers=max(1, len(products))) as ex:
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
                   summary=f"[分析阶段] 定向补采完成，新增 {len(added)} 条证据")
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
        # B2: 超阈值直接走骨架兜底，不再反复调 LLM
        with _FILL_ATTEMPTS_LOCK:
            attempts = _fill_attempts()
            attempts[product] = attempts.get(product, 0) + 1
            n_attempts = attempts[product]
        if n_attempts > _FILL_ATTEMPTS_MAX:
            print(f"[analyzer] feature_fill '{product}' 已达 {_FILL_ATTEMPTS_MAX} 次上限，用骨架兜底")
            return product, {}
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
            {"analysis_meta": llm_meta(meta), "feature_list": feats, "raw_evidence": prod_ev},
            label=f"facts:feature_fill:{product}", timeout=timeout,
        )
        block = out.get("products") if isinstance(out, dict) else None
        return product, (block or {})

    per_product: dict[str, dict] = {}
    with CtxThreadPoolExecutor(max_workers=max(1, len(fill_products))) as ex:
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
            "feature_id": fid, "name": f["name"], "name_en": f.get("name_en", ""), "products": block,
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
    payload = {"analysis_meta": llm_meta(meta), "raw_evidence": sub_ev}
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
    # swot/recommendations 是真推理任务,走 LLM_THINKING_DEEP 档位保质量;
    # positioning_map/competitor_landscape 偏结构化抽取,沿用全局档位。
    deep = deep_thinking_mode() if section in ("swot", "recommendations") else None
    out = get_llm().call_json(system, payload, label=f"derivations:{section}",
                              timeout=timeout, thinking=deep)
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
    with CtxThreadPoolExecutor(max_workers=len(sections)) as ex:
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
    softened = soften_overgeneralization(facts)
    if softened:
        print(f"[analyzer] facts 过度泛化措辞收敛 {softened} 处(大量/普遍→部分用户)")
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
    # derivations 主要基于 facts;证据用精简版即可(不再重复塞全量,防 prompt 爆炸)。
    # ANALYZER_PROMPT_SLIM=1 时进一步按 section 过滤证据类型(四 section 不再共享同一份
    # 45k 字符快照)+ meta 白名单;关闭时保持共享 payload 旧口径。
    if prompt_slim_enabled():
        payloads = {
            s: {"analysis_meta": llm_meta(meta),
                "raw_evidence": compact_evidence_for_deriv(evidence, s),
                "facts": facts}
            for s in _DERIV_SECTIONS
        }
    else:
        shared = {"analysis_meta": meta, "raw_evidence": _compact_evidence(evidence), "facts": facts}
        payloads = {s: shared for s in _DERIV_SECTIONS}
    _emit_progress(step="derivations", phase="start", attempt=1, preview=_facts_preview(facts))

    # swot ‖ recommendations 并行子调用;任一失败用兜底对应字段填充
    der: dict = {}
    fb = None
    with CtxThreadPoolExecutor(max_workers=len(_DERIV_SECTIONS)) as ex:
        futs = {ex.submit(_deriv_section_call, s, system, payloads[s]): s for s in _DERIV_SECTIONS}
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
    # R4 锚点确定性兜底:sanitize 删完无效 ID 后,断链 rec 先重锚现有 feature、定价类打 source_pricing。
    # 仍断链的(still_broken)由 analyzer_node 决定是否定向补采那一条 / 丢弃保底。
    der, anchor_stats = repair_rec_anchors(der, facts)
    if anchor_stats["reanchored"] or anchor_stats["pricing_anchored"]:
        print(f"[analyzer] rec 锚点修复: 重锚 {anchor_stats['reanchored']} 条, "
              f"定价锚 {anchor_stats['pricing_anchored']} 条")
    softened = soften_overgeneralization(der)
    if softened:
        print(f"[analyzer] swot 过度泛化措辞收敛 {softened} 处(大量/普遍→部分用户)")
    return der


def _rec_refill_enabled() -> bool:
    """rec 锚不到时是否定向补采那一条的证据(默认开;ANALYZER_REC_REFILL=0 关)。"""
    return os.environ.get("ANALYZER_REC_REFILL", "1").strip() not in ("0", "false", "False")


def _unanchored_recs(derivations: dict, facts: dict) -> list[dict]:
    """返回推理链断链的 recommendation(无任何有效 feature/pain 锚且非定价锚)。"""
    valid_fids = {f.get("feature_id")
                  for f in (facts.get("feature_tree") or {}).get("features") or []}
    valid_pids = {p.get("pain_id")
                  for p in (facts.get("user_persona") or {}).get("pain_points") or []}
    out: list[dict] = []
    for rec in derivations.get("recommendations") or []:
        fids = set(rec.get("source_feature_ids") or []) & valid_fids
        pids = set(rec.get("source_pain_ids") or []) & valid_pids
        if not fids and not pids and not rec.get("source_pricing"):
            out.append(rec)
    return out


def analyzer_node(state: AgentState) -> AgentState:
    evidence = state["raw_evidence"] or []
    _FILL_ATTEMPTS_VAR.set({})  # B2: 每 run 换新 dict(不 clear 共享对象,僵尸 run 持旧引用也污染不到本轮)
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
            _emit_progress(step="facts", phase="survey_start", summary="[分析阶段] 设计问卷并模拟用户访谈采集")
            evidence, research_method = _run_survey(evidence, meta)
            if research_method:
                print(f"[analyzer] survey added {research_method['n_findings']} synthetic interview evidence")
                _emit_progress(step="facts", phase="survey_done",
                               summary=f"[分析阶段] 问卷调研完成：{len(research_method['questions'])} 题 / "
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
                           summary=f"[分析阶段] {_rtag}发现 {len(gaps['unknown_cells'])} 个空白项{_tail}{_ptail}，定向补采")
            # M1 池内回捞优先(design-v3-draft §三):缺口对应的证据可能本来就在池里,
            # 只是被 top-K/截断挡在 prompt 视野外 → 先零成本捞回(标 _recalled,
            # _compact_evidence 顶进视野+放宽截断),捞不到才外搜。
            pool_recalled = recall_from_pool(evidence, gaps) if pool_recall_enabled() else []
            if pool_recalled:
                new_ev = pool_recalled  # 已在池内:不追加 evidence,只触发受影响 section 重出
                print(f"[analyzer] gap refill round {round_idx + 1}: 池内回捞 {len(new_ev)} 条"
                      f"(零搜索成本),重出受影响 section")
            else:
                try:
                    new_ev = _gap_targeted_recollect(meta, gaps, focus, round_idx=round_idx)
                except Exception as e:  # noqa: BLE001
                    print(f"[analyzer] gap refill 失败(忽略): {e}")
                    new_ev = []
                # 双重去重:evidence_id 之外再按归一化文本去一次——SPA 页重抓时 chunk 顺序
                # 漂移会让同内容拿到新 ID(id 含页内 idx),纯 id 去重拦不住,重复内容会
                # 虚增"新证据"并触发无意义的 section 重算(实测一轮虚增 32 条)。
                existing_ids = {e.get("evidence_id") for e in evidence}
                def _ev_text(e):
                    return " ".join(sorted(_norm_tokens(
                        (e.get("extracted_snippet") or e.get("claim") or ""))))
                existing_txt = {_ev_text(e) for e in evidence}
                new_ev = [e for e in new_ev
                          if e.get("evidence_id") not in existing_ids
                          and _ev_text(e) not in existing_txt]
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
                           summary=f"[分析阶段] {_rtag}定向补采 {len(new_ev)} 条，重新梳理{_names}{_ponly}")
            partial = _step1_facts(evidence, meta, analyzer_retry=analyzer_retry,
                                   spine=spine, only_sections=affected,
                                   only_products=gap_products or None, prev_facts=facts)
            facts.update(partial)

    derivations = _step2_derivations(facts, evidence, meta, analyzer_retry=analyzer_retry)

    # R4 锚点收口(治本):_step2 已做确定性重锚 + 定价锚;仍断链的非定价 rec 在这里
    # **定向补采那一条的证据**(只搜该建议的主题,不泛补整张 feature_tree)→ 重出 feature_tree
    # → 再重锚。补不到则丢弃(保 ≥2 条),避免一条无依据建议把整张报告拖进 degraded。
    if _rec_refill_enabled() and not is_mock_mode():
        broken = _unanchored_recs(derivations, facts)
        if broken:
            target = (_target_products(meta) or [""])[0]
            topics = [(target, t) for t in (
                (rec.get("title") or rec.get("action") or "").strip()[:24] for rec in broken[:3]
            ) if t]
            if topics:
                print(f"[analyzer] {len(broken)} 条建议锚不到 feature/pain → 定向补采: "
                      f"{[t for _, t in topics]}")
                _emit_progress(step="derivations", phase="rec_refill",
                               summary=f"{len(broken)} 条建议缺依据，定向补采证据")
                gaps = {"unknown_cells": topics, "missing_claim_types": [],
                        "pricing_gap_products": [], "dim_en": {}}
                try:
                    new_ev = _gap_targeted_recollect(meta, gaps, focus, round_idx=1)
                except Exception as e:  # noqa: BLE001
                    print(f"[analyzer] rec 定向补采失败(忽略): {type(e).__name__}: {e}")
                    new_ev = []
                existing_ids = {e.get("evidence_id") for e in evidence}
                new_ev = [e for e in new_ev if e.get("evidence_id") not in existing_ids]
                if new_ev:
                    evidence = evidence + new_ev
                    partial = _step1_facts(evidence, meta, analyzer_retry=analyzer_retry,
                                           spine=spine, only_sections=["feature_tree"],
                                           only_products=[target], prev_facts=facts)
                    facts.update(partial)
                    derivations, st = repair_rec_anchors(derivations, facts)
                    if st["reanchored"] or st["pricing_anchored"]:
                        print(f"[analyzer] 补采后重锚: 重锚 {st['reanchored']} 条, "
                              f"定价锚 {st['pricing_anchored']} 条")
        # 最终仍断链 → 丢弃(只在丢完仍 ≥2 条时执行,守住 R4 的"建议数量"底线)
        broken = _unanchored_recs(derivations, facts)
        recs = derivations.get("recommendations") or []
        if broken and (len(recs) - len(broken)) >= 2:
            keep = [r for r in recs if r not in broken]
            print(f"[analyzer] 丢弃 {len(broken)} 条无法锚定的建议(剩 {len(keep)} 条)")
            derivations["recommendations"] = keep

    schema_draft = {
        "analysis_meta": meta,
        **facts,
        **derivations,
    }
    if research_method:
        schema_draft["research_method"] = research_method  # 供报告「调研方法」卡展示
    # 收尾确定性安全网(幂等):Guard 终门(M2)——幻觉引用清理 + G1 强对比对账 +
    # G2 basis 声称对账 + 过度泛化收敛。覆盖新模块 competitor_landscape /
    # positioning_map / praise_points,杜绝幻觉 ID / 超证据强度的结论漏进 writer chip。
    from .guard import apply as _guard_apply
    schema_draft, guard_rep = _guard_apply(schema_draft, evidence)
    if guard_rep["changes_total"]:
        print(f"[analyzer] guard: -{guard_rep['dropped_refs']} 幻觉引用, "
              f"降级强对比 {guard_rep['comparison_downgraded']} 条, "
              f"basis 改 unknown {guard_rep['basis_unknowned']} 格, "
              f"软化措辞 {guard_rep['softened']} 处")
        schema_draft["_guard_report"] = guard_rep  # 供 stage_report 观测
    # B2: 记录 per-product fill 尝计数到 schema_draft metadata（供 stage_report 观测）
    fill_attempts = _FILL_ATTEMPTS_VAR.get() or {}
    if fill_attempts:
        schema_draft["_fill_attempts"] = dict(fill_attempts)
        _exceeded = {p: n for p, n in fill_attempts.items() if n > _FILL_ATTEMPTS_MAX}
        if _exceeded:
            print(f"[analyzer] feature_fill 超限产品(用骨架兜底): {_exceeded}")
    # 补采可能扩充了 evidence → 写回 state,保证 writer/reviewer 看到的引用都真实存在
    return {**state, "raw_evidence": evidence, "schema_draft": schema_draft}
