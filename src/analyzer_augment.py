"""Analyzer 证据增强侧流 — 覆盖缺口定向补采 + 合成用户访谈。

analyzer_node 在主 pipeline 之外调用:_coverage_gaps 扫缺口→_gap_targeted_recollect 补采;
真实 UGC 不足时 _run_survey 合成访谈兜底。依赖 analyzer_common(单向),采集/搜索为懒导入避环。
analyzer.py re-export 保 back-compat(测试引 _coverage_gaps/_run_survey/_real_ugc_count 等)。
"""
from __future__ import annotations

import os
import re
from concurrent.futures import as_completed
from typing import Optional

from .analyzer_common import _FACTS_SECTIONS, _REQUIRED_CT, _target_products
from .progress import CtxThreadPoolExecutor


def _gap_refill_enabled() -> bool:
    return os.environ.get("ANALYZER_GAP_REFILL", "1").strip() not in ("0", "false", "False")


def _coverage_gaps(facts: dict, meta: dict, evidence: list[dict]) -> dict:
    """扫描 facts 暴露的缺口:未评分的 (产品×功能) 格子 + 缺失的必需 claim_type。"""
    products = _target_products(meta)
    unknown_cells: list[tuple[str, str]] = []
    dim_en: dict[str, str] = {}  # 维度中文名 → 英文检索别名(定向补采用英文搜官网/文档,命中更准)
    for feat in (facts.get("feature_tree") or {}).get("features", []):
        name = feat.get("name")
        if not name:
            continue
        if feat.get("name_en"):
            dim_en[name] = feat["name_en"]
        for p in products:
            d = (feat.get("products") or {}).get(p) or {}
            if (d.get("support_status") or "").lower() == "unknown":
                unknown_cells.append((p, name))
    present_ct = {e.get("claim_type") for e in evidence}
    try:
        from .evidence_plan import required_claim_types_for_meta
        required_ct = required_claim_types_for_meta(meta)
    except Exception:  # noqa: BLE001
        required_ct = list(_REQUIRED_CT)
    missing_ct = [ct for ct in required_ct if ct not in present_ct]

    # 时效性缺口:某必需 claim_type 的证据全部 stale → 视为缺失,触发补采。
    # pricing(TTL=7d)对时效最敏感,全 stale 定价 ≈ 无定价。
    stale_gap_ct: list[str] = []
    for ct in required_ct:
        if ct in missing_ct:
            continue  # 已经算缺失,不需要重复
        ct_ev = [e for e in evidence if e.get("claim_type") == ct]
        if ct_ev and all(e.get("source_freshness") == "stale" for e in ct_ev):
            stale_gap_ct.append(ct)
    if stale_gap_ct:
        missing_ct.extend(stale_gap_ct)
        print(f"[analyzer] 时效性缺口: {stale_gap_ct} 全部证据已过期 → 视为缺失触发补采")

    # 定价抽到了但整张表没有任何可用价格数值(全 $0/None)→ 抽取失败,当作缺口触发定向重搜定价。
    # 只在「全表无正价」时触发(强信号),避免把某个真免费档误判成缺口。
    # 字段名按 schema 规范取 `name`(prompts/analyzer_facts.md / writer 同口径),
    # 兼容旧输出的 `product`;匹配做大小写/空格归一化——此处曾因只读 `product`(恒为 None)
    # 把三个产品全判成定价缺口,引发整轮无效补采+pricing_model 4 次重算(2026-06-11 实测)。
    def _canon(s):
        return re.sub(r"[\s\-_]+", "", str(s or "")).lower()

    pm_products = (facts.get("pricing_model") or {}).get("products") or []
    tiers_by_prod: dict[str, list] = {
        _canon(p.get("name") or p.get("product")): (p.get("tiers") or []) for p in pm_products
    }
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
            if not tiers_by_prod.get(_canon(p)):  # 该产品缺席 pricing_model 或 0 档
                pricing_gap_products.append(p)
        if pricing_gap_products:
            print(f"[analyzer] per-product 定价缺口: {pricing_gap_products} 无档位而别家有 → 定向补采")

    return {
        "unknown_cells": unknown_cells,
        "missing_claim_types": missing_ct,
        "stale_gap_claim_types": stale_gap_ct,
        "pricing_gap_products": pricing_gap_products,
        "dim_en": dim_en,
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
        elif ct == "market_signal":
            secs.update(("pricing_model", "user_persona"))
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
    # **用英文别名检索**:维度名是中文,官网/文档是英文,中文名搜命中低;spine 给的 name_en 命中更准。
    dim_en = gaps.get("dim_en") or {}
    for product, fname in gaps["unknown_cells"][:max_cells]:
        kw = dim_en.get(fname) or fname or focus
        _emit(product, "feature_existence", kw)
        _emit(product, "performance_quality", kw)
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
    with CtxThreadPoolExecutor(max_workers=max(1, len(products))) as ex:
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
