"""确定性定价引擎 —— 把抽取出的结构化定价事实算成可比指标。

设计见 docs/pricing-model-jimeng.example.yaml。本模块是纯函数集合:
LLM 只负责把各产品定价「翻译」进统一 schema(填空,不算术),本模块对该
schema 做确定性计算(归一月价 / 元每积分 / 单位成本 / 商业模式 archetype /
比较轴),每个数字可溯源、同输入恒同输出。

铁律:
  - 单位成本只用「续费常规价」做 basis,促销/体验价永不入基准。
  - 缺数据 → 返回 None(诚实 unknown),绝不估算填数。
"""
from __future__ import annotations

from typing import Literal, Optional, TypedDict


class Price(TypedDict, total=False):
    amount: float
    currency: str


class BillingOption(TypedDict, total=False):
    cycle: Literal["monthly", "quarterly", "yearly", "single_month", "one_time"]
    kind: str
    price: Price
    amount_status: Literal["known", "unknown"]
    is_promo: bool
    promo_scope: str
    note: str


class CreditGrant(TypedDict, total=False):
    amount: float
    adjustable: bool
    default: float
    options: list[float]
    unit: str
    cadence: str
    expires: bool


class ConsumptionItem(TypedDict, total=False):
    capability: str
    spec: str
    credits_per_output: float
    credits_per_unit: float
    unit: str


class PricingLayer(TypedDict, total=False):
    mechanism: str
    present: bool
    unit: str
    role: str
    detail: str


class PricingStructure(TypedDict, total=False):
    archetype: str
    layers: list[PricingLayer]


class PricingTier(TypedDict, total=False):
    tier_name: str
    billing_options: list[BillingOption]
    credit_grant: CreditGrant
    pricing_mode: str
    is_free: bool
    segment: str


class ProductPricingModel(TypedDict, total=False):
    product: str
    pricing_structure: PricingStructure
    tiers: list[PricingTier]
    consumption: list[ConsumptionItem]
    raw_billing_items: list[dict]
    gaps: list[dict]


class DerivedUnitCost(TypedDict, total=False):
    capability: str
    spec: str
    value: float
    unit: str
    basis: str


class DerivedTier(PricingTier, total=False):
    price_per_credit: Optional[float]
    unit_costs: list[DerivedUnitCost]


class DerivedProductPricingModel(TypedDict, total=False):
    product: str
    pricing_structure: PricingStructure
    consumption: list[ConsumptionItem]
    raw_billing_items: list[dict]
    gaps: list[dict]
    archetype: str
    comparison_axis: str
    tiers: list[DerivedTier]
    unit_costs: dict

# 计费周期 → 每月折算系数(周期价 ÷ 月数)
_CYCLE_MONTHS = {
    "monthly": 1,
    "single_month": 1,
    "one_time": 1,
    "quarterly": 3,
    "yearly": 12,
}


def normalized_monthly(billing_option: BillingOption) -> Optional[float]:
    """把一个计费变体归一成「每月价」(原币)。金额未知 → None。"""
    if billing_option.get("amount_status") == "unknown":
        return None
    price = billing_option.get("price") or {}
    amount = price.get("amount")
    if amount is None:
        return None
    months = _CYCLE_MONTHS.get(billing_option.get("cycle"), 1)
    return round(float(amount) / months, 2)


def regular_monthly_price(tier: PricingTier) -> Optional[float]:
    """取该档的「续费常规月价」(非促销的月计费变体)。无 → None。
    这是单位成本的唯一 basis,促销/体验价一律不入。"""
    for opt in tier.get("billing_options") or []:
        if opt.get("is_promo"):
            continue
        if opt.get("cycle") not in ("monthly", "single_month"):
            continue
        m = normalized_monthly(opt)
        if m is not None:
            return m
    return None


def _grant_amount(tier: PricingTier) -> Optional[float]:
    """该档每周期发放的积分量。滑块档取 default。无积分概念 → None。"""
    grant = tier.get("credit_grant")
    if not grant:
        return None
    amount = grant.get("default") if grant.get("adjustable") else grant.get("amount")
    if amount is None:
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def price_per_credit(tier: PricingTier) -> Optional[float]:
    """元每积分 = 续费常规月价 ÷ 月积分。缺价或缺积分 → None。"""
    price = regular_monthly_price(tier)
    grant = _grant_amount(tier)
    if price is None or not grant:
        return None
    return round(price / grant, 4)


def _credits_per(consumption: ConsumptionItem) -> Optional[float]:
    """单位耗分:图按张(credits_per_output)、视频/用量按单位(credits_per_unit)。"""
    rate = consumption.get("credits_per_output")
    if rate is None:
        rate = consumption.get("credits_per_unit")
    return float(rate) if rate is not None else None


def unit_cost(tier: PricingTier, consumption: ConsumptionItem) -> Optional[float]:
    """单位成本(元/张、元/秒…)= 元每积分 × 单位耗分。缺任一 → None(诚实 unknown)。"""
    ppc = price_per_credit(tier)
    rate = _credits_per(consumption)
    if ppc is None or rate is None:
        return None
    return round(ppc * rate, 4)


# ── 结构层:商业模式 archetype + 比较轴(异构产品靠数据分流,不写 if 产品名)──────────
_MECHANISM_LABEL = {
    "freemium": "Freemium",
    "subscription": "Subscription",
    "credits": "Credits",
    "one_time_packs": "Credit Packs",
    "usage": "Usage-based",
}
_LAYER_ORDER = ("freemium", "subscription", "credits", "one_time_packs", "usage")


def _present_mechanisms(structure: PricingStructure) -> set:
    return {l.get("mechanism") for l in (structure.get("layers") or [])
            if l.get("present")}


def _has_unmodeled_shape(structure: PricingStructure) -> bool:
    return any(m and m not in _MECHANISM_LABEL
               for m in _present_mechanisms(structure))


def derive_archetype(structure: PricingStructure) -> str:
    """由 present 的结构层确定性拼出 archetype 串。无层 → Unknown。"""
    present = _present_mechanisms(structure)
    parts = [_MECHANISM_LABEL[m] for m in _LAYER_ORDER
             if m in present and m in _MECHANISM_LABEL]
    if not parts and _has_unmodeled_shape(structure):
        return "custom/unknown"
    return " + ".join(parts) if parts else "Unknown"


def comparison_axis(structure: PricingStructure) -> str:
    """按结构形态选横向比较轴:计量制→单位成本;席位→每席位;否则按档月价。"""
    present = _present_mechanisms(structure)
    if "credits" in present or "one_time_packs" in present or "usage" in present:
        return "unit_cost"
    if "subscription" in present:
        sub = next((l for l in (structure.get("layers") or [])
                    if l.get("mechanism") == "subscription" and l.get("present")), {})
        return "per_seat" if sub.get("unit") == "seat" else "per_tier_monthly"
    return "unknown"


def _tier_unit_costs(
    tier: PricingTier,
    consumption_items: list[ConsumptionItem],
) -> list[DerivedUnitCost]:
    costs = []
    for item in consumption_items:
        value = unit_cost(tier, item)
        if value is None:
            continue
        costs.append({
            "capability": item.get("capability"),
            "spec": item.get("spec"),
            "value": value,
            "unit": item.get("unit"),
            "basis": "regular_monthly",
        })
    return costs


def compute_product(product_model: ProductPricingModel) -> DerivedProductPricingModel:
    """计算整份产品定价模型的派生字段。

    输入是统一 schema 的一个稀疏实例。只要某块数据缺席,对应派生值就返回
    None/空列表,不会按产品名分支,也不会估算。
    """
    structure = product_model.get("pricing_structure") or {}
    consumption_items = product_model.get("consumption") or []

    tiers = []
    for tier in product_model.get("tiers") or []:
        derived = dict(tier)
        derived["price_per_credit"] = price_per_credit(tier)
        derived["unit_costs"] = _tier_unit_costs(tier, consumption_items)
        tiers.append(derived)

    out = dict(product_model)
    out["archetype"] = derive_archetype(structure)
    out["comparison_axis"] = comparison_axis(structure)
    out["tiers"] = tiers
    if _has_unmodeled_shape(structure):
        out["raw_billing_items"] = product_model.get("raw_billing_items") or []
        out["unit_costs"] = {
            "status": "not_applicable",
            "reason": "pricing shape is not covered by normalized subscription/credits/usage axes",
        }
        out["gaps"] = list(product_model.get("gaps") or [])
        out["gaps"].append({
            "kind": "pricing_shape_unmodeled",
            "field": "pricing_structure.layers",
            "fix": "人工确认该计费形态是否需要新增形态轴,或保留 raw_billing_items 原样展示",
        })
    return out
