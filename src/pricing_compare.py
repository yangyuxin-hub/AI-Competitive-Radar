"""定价模型横向比较 —— 基于 pricing_engine 的确定性分析层。

输入来自 src.pricing_model.compute_product 的结果。这里不抓取、不调用 LLM,
只做同口径比较:能比则给 winner,不能比则输出 gap,避免把“元/秒”和
“元/条标准视频”硬塞成同一单位。
"""
from __future__ import annotations

from typing import Optional


def _entry_paid_monthly(engine: dict) -> Optional[dict]:
    best = None
    for tier in engine.get("tiers") or []:
        for opt in tier.get("billing_options") or []:
            price = opt.get("price") or {}
            amount = price.get("amount")
            if amount is None:
                continue
            try:
                value = float(amount)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            months = {"monthly": 1, "single_month": 1, "quarterly": 3, "yearly": 12}.get(
                opt.get("cycle"), 1
            )
            monthly = round(value / months, 2)
            if best is None or monthly < best["amount"]:
                best = {"amount": monthly, "currency": price.get("currency") or "unknown"}
    return best


def _best_price_per_credit(engine: dict) -> Optional[dict]:
    best = None
    for tier in engine.get("tiers") or []:
        value = tier.get("price_per_credit")
        if not isinstance(value, (int, float)):
            continue
        row = {"value": round(float(value), 4), "tier_name": tier.get("tier_name")}
        if best is None or row["value"] < best["value"]:
            best = row
    return best


def _best_unit_costs(engine: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for tier in engine.get("tiers") or []:
        for cost in tier.get("unit_costs") or []:
            cap = cost.get("capability")
            value = cost.get("value")
            if not cap or not isinstance(value, (int, float)):
                continue
            row = {
                "value": round(float(value), 4),
                "tier_name": tier.get("tier_name"),
                "unit": cost.get("unit"),
            }
            if cap not in out or row["value"] < out[cap]["value"]:
                out[cap] = row
    return out


def _summaries(products: list[dict]) -> list[dict]:
    rows = []
    for product in products:
        engine = product.get("pricing_engine") or {}
        if not engine:
            continue
        rows.append({
            "product": product.get("name") or engine.get("product"),
            "archetype": engine.get("archetype"),
            "comparison_axis": engine.get("comparison_axis"),
            "entry_paid_monthly": _entry_paid_monthly(engine),
            "best_price_per_credit": _best_price_per_credit(engine),
            "best_unit_costs": _best_unit_costs(engine),
        })
    return rows


def compare_pricing_products(products: list[dict]) -> dict:
    """比较多个产品的 pricing_engine 输出。

    返回结构既可给 Writer 渲染,也可给后续推荐模块引用。
    """
    rows = _summaries(products)
    gaps = []
    insights = []

    credit_rows = [
        {"product": r["product"], **r["best_price_per_credit"]}
        for r in rows if r.get("best_price_per_credit")
    ]
    credit_winner = None
    if len(credit_rows) >= 2:
        credit_winner = min(credit_rows, key=lambda r: r["value"])
        insights.append(
            f"同为积分制产品时,{credit_winner['product']}的最低额度单价较低,"
            f"约 {credit_winner['value']} / 积分;但这不等同于同规格产出成本优势。"
        )

    all_caps = sorted({
        cap
        for row in rows
        for cap in (row.get("best_unit_costs") or {}).keys()
    })
    unit_winners = []
    for cap in all_caps:
        candidates = [
            {"product": r["product"], **(r.get("best_unit_costs") or {})[cap]}
            for r in rows if cap in (r.get("best_unit_costs") or {})
        ]
        if len(candidates) >= 2:
            unit_winners.append({"capability": cap, "winner": min(candidates, key=lambda r: r["value"]),
                                 "values": candidates})

    if len(all_caps) > 1 and not unit_winners:
        gaps.append({
            "kind": "unit_cost_axis_mismatch",
            "note": "各产品的单位成本口径不同,不能直接比较;需补齐同一 capability/spec 的消耗率。",
            "capabilities": all_caps,
        })

    if not unit_winners and len(rows) >= 2:
        insights.append("单位成本暂不可硬比:当前证据里的产出单位或规格不一致。")

    return {
        "products": rows,
        "credit_price_winner": credit_winner,
        "unit_cost_winners": unit_winners,
        "gaps": gaps,
        "insights": insights,
    }
