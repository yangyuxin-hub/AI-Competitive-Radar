"""Analyzer 缺口判定(纯函数,零网络) — v3 M3 后只留"声明缺口",不再执行采集。

执行逻辑(_gap_targeted_recollect/_recollect_pricing_official/_run_survey 系)已迁入
`evidence_service.py`(系统里唯一碰外部世界的缺口补证据模块),本文件 re-export
保 back-compat(测试/旧 callsite 引 analyzer._run_survey 等零改动)。
本文件仅剩:_coverage_gaps(facts 感知缺口扫描)+ _gap_affected_sections(缺口→section 映射)。
"""
from __future__ import annotations

import os
import re

from .analyzer_common import _FACTS_SECTIONS, _REQUIRED_CT, _target_products
from .evidence_service import (  # noqa: F401 — M3 执行逻辑迁入 evidence_service,re-export 保 back-compat
    _gap_targeted_recollect,
    _real_ugc_count,
    _recollect_pricing_official,
    _run_survey,
    _survey_enabled,
    _survey_should_run,
)


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
