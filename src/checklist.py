"""交付验收清单 — 见 docs/design-v3.md(M1 决策视角修订)

把一次分析的**该交付项**变成可勾选清单(不是单次遥测):采集/分析/质检三层,每项
✓ 达标 / ✗ 打回补充(带 owner_node + 修复提示 + 是否可补)。给 owner「一眼看齐不齐、
哪条卡住、谁去补」的把控视角。

数据全部复用既有产物:
- 采集层:quality.annotate_and_audit(raw_evidence) 的 gaps + 覆盖计数 → 每个 product×claim_type 一项
- 分析层:schema_draft 各 section 是否产出
- 质检层:quality_report 的 passed/failed/warning rules
打回归属走 stage_report.gap_owner(确定性查表),与时间线/控制环同一口径。
纯函数,零副作用(evidence 传副本),失败由调用方兜底。
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from . import quality
from .stage_report import gap_owner

_CT_LABEL = {
    "feature_existence": "功能证据",
    "performance_quality": "体验/性能证据",
    "pricing": "定价证据",
    "user_pain": "用户痛点证据",
}

# 质检规则 → 人话标签(避免 import reviewer 带入重依赖)
_RULE_LABEL = {
    "R0": "证据覆盖", "R1": "引用完整", "R2": "类型匹配", "R3": "聚合自洽",
    "R4": "推理链完整", "R5": "结构无矛盾", "R6": "语义落地", "R7": "时效",
    "R8": "内容覆盖", "R9": "chip 可溯源", "R10": "禁泄质检分",
}

# 采集 gap_type → 一句话补采提示
_FIX_HINT = {
    "coverage_short": "补足该类证据",
    "no_official": "补官网/定价页权威源",
    "pricing_no_number": "补含真实价格数值的定价页",
    "bias_all_vendor": "补真实用户/第三方视角",
    "community_missing": "补 Reddit/HN/G2 等社区证据",
    "community_low_quality": "补更高质量的社区证据",
    "total_too_few": "样本过薄,扩大检索",
}


def _products(meta: dict) -> list[str]:
    return [p for p in [meta.get("target_product"), *(meta.get("competitors") or [])] if p]


def _required_claim_types(meta: dict) -> tuple:
    # 与评分/采集门同口径:痛点归因只看痛点+体验,不强求功能/定价齐全
    if meta.get("analysis_intent") == "pain_attribution":
        return ("user_pain", "performance_quality")
    return quality.REQUIRED_TYPES


def _collection_group(report: dict) -> dict:
    meta = report.get("meta") or {}
    evidence = report.get("raw_evidence") or []
    products = _products(meta)
    required = _required_claim_types(meta)

    audit = quality.annotate_and_audit([dict(e) for e in evidence], meta)
    gap_by: dict = {}
    for g in audit.get("gaps") or []:
        gap_by.setdefault((g.get("product"), g.get("claim_type")), g)

    cov = Counter((e.get("product"), e.get("claim_type")) for e in evidence)

    items: list[dict] = []
    done = 0
    for p in products:
        for ct in required:
            g = gap_by.get((p, ct))
            if g:
                fixable = bool(g.get("fixable", True))
                items.append({
                    "label": f"{p} · {_CT_LABEL.get(ct, ct)}",
                    "status": "unfixable" if not fixable else "missing",
                    "detail": g.get("reason"),
                    "owner": gap_owner(g),
                    "fix": _FIX_HINT.get(g.get("gap_type"), "定向补采"),
                    "fixable": fixable,
                    "product": p, "claim_type": ct,
                })
            else:
                done += 1
                items.append({
                    "label": f"{p} · {_CT_LABEL.get(ct, ct)}",
                    "status": "ok",
                    "detail": f"{cov.get((p, ct), 0)} 条",
                    "owner": None, "fix": None, "fixable": True,
                    "product": p, "claim_type": ct,
                })
    return {"layer": "采集", "done": done, "total": len(items), "items": items}


def _analysis_group(report: dict) -> dict:
    schema = report.get("schema_draft") or {}
    swot = schema.get("swot") or {}
    swot_n = sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats"))
    checks = [
        ("功能对比矩阵", bool((schema.get("feature_tree") or {}).get("features"))),
        ("定价对比", bool((schema.get("pricing_model") or {}).get("products"))),
        ("用户画像/痛点", bool((schema.get("user_persona") or {}).get("pain_points"))),
        ("SWOT 推导", swot_n > 0),
        ("优先级建议", len(schema.get("recommendations") or []) >= 2),
    ]
    items = []
    done = 0
    for label, ok in checks:
        if ok:
            done += 1
        items.append({
            "label": label,
            "status": "ok" if ok else "missing",
            "detail": None,
            "owner": None if ok else "analyzer",
            "fix": None if ok else "重跑该 section 的分析",
            "fixable": True,
        })
    return {"layer": "分析", "done": done, "total": len(items), "items": items}


def _qc_group(report: dict) -> dict:
    qr = report.get("quality_report") or {}
    # 失败规则的归属:从 errors 里按规则取 reject_target → gap_owner
    owner_by_rule: dict = {}
    for e in qr.get("errors") or []:
        r = e.get("rule")
        if r and r not in owner_by_rule:
            owner_by_rule[r] = gap_owner(e)

    items: list[dict] = []
    done = 0
    for r in qr.get("passed_rules") or []:
        done += 1
        items.append({"label": f"{r} {_RULE_LABEL.get(r, '')}".strip(), "status": "ok",
                      "detail": None, "owner": None, "fix": None, "fixable": True})
    for r in qr.get("failed_rules") or []:
        items.append({"label": f"{r} {_RULE_LABEL.get(r, '')}".strip(), "status": "missing",
                      "detail": None, "owner": owner_by_rule.get(r, "analyzer"),
                      "fix": "按规则修复后重跑", "fixable": True})
    for r in qr.get("warning_rules") or []:
        items.append({"label": f"{r} {_RULE_LABEL.get(r, '')}".strip(), "status": "warn",
                      "detail": None, "owner": None, "fix": None, "fixable": True})
    total = len(items)
    return {"layer": "质检", "done": done, "total": total, "items": items}


def build_checklist(report: dict) -> dict:
    """从一份持久化 report 生成三层交付清单 + 汇总。"""
    meta = report.get("meta") or {}
    groups = [_collection_group(report), _analysis_group(report), _qc_group(report)]

    all_items = [it for g in groups for it in g["items"]]
    done = sum(1 for it in all_items if it["status"] == "ok")
    blocked = [it for it in all_items if it["status"] == "unfixable"]
    open_repairs = [it for it in all_items if it["status"] == "missing"]

    return {
        "report_id": report.get("report_id"),
        "target": meta.get("target_product"),
        "competitors": meta.get("competitors") or [],
        "status": report.get("status"),
        "groups": groups,
        "summary": {
            "done": done,
            "total": len(all_items),
            "open_repairs": len(open_repairs),
            "blocked": len(blocked),
            # 按 owner_node 聚合"还欠谁去补几项",给 owner 直接的行动清单
            "repairs_by_owner": dict(Counter(
                it["owner"] for it in open_repairs if it.get("owner"))),
        },
    }
