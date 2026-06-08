"""Evidence planning between intake and collection.

This module turns a user intent into a declarative collection contract:
what evidence types are required, which extra signals are useful, and how
collector/reviewer gates should judge coverage.
"""
from __future__ import annotations

from typing import Iterable, Optional


BASE_CLAIM_TYPES = ("feature_existence", "performance_quality", "pricing", "user_pain")
PAIN_CLAIM_TYPES = ("user_pain", "performance_quality")
PRICING_CLAIM_TYPES = ("pricing", "feature_existence")
MARKET_ENTRY_CLAIM_TYPES = (
    "feature_existence",
    "performance_quality",
    "pricing",
    "user_pain",
    "market_signal",
)

_MARKET_KEYWORDS = (
    "用户量", "使用量", "用户数", "付费用户", "订阅用户", "订阅数", "收入", "营收",
    "arr", "mrr", "融资", "估值", "投资", "公司状况", "公司状态", "员工",
    "市场", "增长", "流量", "下载量", "排名", "版权", "诉讼", "合规",
    "users", "subscribers", "revenue", "funding", "valuation", "employees",
    "traffic", "downloads", "ranking", "lawsuit", "legal", "copyright",
)

_TASKS_BY_CLAIM = {
    "feature_existence": [
        ("capabilities", "官方功能/文档证据", ["official_page", "official_doc"]),
    ],
    "performance_quality": [
        ("experience_quality", "第三方评测或用户体验反馈", ["review", "reddit", "hn", "web_search"]),
    ],
    "pricing": [
        ("pricing_tiers", "官方定价、免费层、计费限制", ["pricing_page", "official_page"]),
    ],
    "user_pain": [
        ("pain_points", "用户吐槽、迁移原因、差评与限制", ["reddit", "hn", "g2", "web_search"]),
    ],
    "market_signal": [
        ("users_or_subscribers", "用户量、付费用户、使用量", ["official_blog", "news", "web_search"]),
        ("revenue_or_funding", "收入、ARR、融资、估值", ["news", "official_blog", "web_search"]),
        ("company_status", "公司状态、员工、法律风险、合作关系", ["news", "sec", "web_search"]),
    ],
}

_ACCEPTANCE = {
    "feature_existence": "每个产品至少有官方或可信功能证据；缺失则回采官网/文档。",
    "performance_quality": "每个产品至少有体验/质量证据，优先用户侧或第三方来源。",
    "pricing": "每个产品至少有定价/免费层证据，优先官方定价页。",
    "user_pain": "每个产品至少有用户痛点或社区反馈；没有则标缺口。",
    "market_signal": "至少命中官方或可信媒体的市场/公司信号；找不到时保留缺口，不编估算。",
}


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _wants_market_signal(user_input: str, focus: list[str], intent: str) -> bool:
    if intent == "market_entry":
        return True
    blob = " ".join([user_input or "", *(focus or [])]).lower()
    return any(k.lower() in blob for k in _MARKET_KEYWORDS)


def required_for_intent(intent: Optional[str], *, wants_market_signal: bool = False) -> list[str]:
    """Return the required claim types for an analysis intent."""
    if intent == "pain_attribution":
        required = list(PAIN_CLAIM_TYPES)
    elif intent == "pricing":
        required = list(PRICING_CLAIM_TYPES)
    elif intent == "market_entry":
        required = list(MARKET_ENTRY_CLAIM_TYPES)
    else:
        required = list(BASE_CLAIM_TYPES)
    if wants_market_signal and "market_signal" not in required:
        required.append("market_signal")
    return required


def build_evidence_plan(analysis_meta: dict, user_input: Optional[str] = None) -> dict:
    """Build a declarative evidence plan from analysis metadata."""
    intent = analysis_meta.get("analysis_intent") or "feature_compare"
    focus = list(analysis_meta.get("analysis_focus") or [])
    text = user_input if user_input is not None else analysis_meta.get("user_input", "")
    wants_market = _wants_market_signal(text or "", focus, intent)
    required = required_for_intent(intent, wants_market_signal=wants_market)

    optional: list[str] = []
    if intent in ("selection", "feature_compare") and not wants_market:
        optional.append("market_signal")
    optional = [ct for ct in optional if ct not in required]

    products = [analysis_meta.get("target_product"), *list(analysis_meta.get("competitors") or [])]
    products = [p for p in products if p]
    tasks = []
    for ct in _dedupe([*required, *optional]):
        for metric, description, sources in _TASKS_BY_CLAIM.get(ct, []):
            tasks.append({
                "claim_type": ct,
                "metric": metric,
                "products": products,
                "sources": sources,
                "required": ct in required,
                "description": description,
            })

    return {
        "schema_version": "1.0",
        "analysis_intent": intent,
        "required_claim_types": required,
        "optional_claim_types": optional,
        "evidence_tasks": tasks,
        "acceptance_criteria": {ct: _ACCEPTANCE[ct] for ct in required if ct in _ACCEPTANCE},
        "planner_notes": [
            "Collector 必须优先闭合 required_claim_types。",
            "optional_claim_types 只作为增强信号，不因缺失阻断分析。",
            "公开市场/公司数据不可得时必须标注缺口，不允许估算冒充事实。",
        ],
    }


def required_claim_types_from_plan(plan: Optional[dict], fallback: Iterable[str] = BASE_CLAIM_TYPES) -> list[str]:
    if isinstance(plan, dict) and plan.get("required_claim_types"):
        return _dedupe(str(ct) for ct in plan.get("required_claim_types") or [])
    return _dedupe(str(ct) for ct in fallback)


def planned_claim_types_from_plan(plan: Optional[dict], fallback: Iterable[str] = BASE_CLAIM_TYPES) -> list[str]:
    if not isinstance(plan, dict):
        return _dedupe(str(ct) for ct in fallback)
    return _dedupe([
        *(str(ct) for ct in plan.get("required_claim_types") or []),
        *(str(ct) for ct in plan.get("optional_claim_types") or []),
    ]) or _dedupe(str(ct) for ct in fallback)


def evidence_plan_from_meta(analysis_meta: dict) -> Optional[dict]:
    plan = (analysis_meta or {}).get("evidence_plan")
    return plan if isinstance(plan, dict) else None


def required_claim_types_for_meta(analysis_meta: dict) -> list[str]:
    plan = evidence_plan_from_meta(analysis_meta)
    if plan:
        return required_claim_types_from_plan(plan)
    intent = (analysis_meta or {}).get("analysis_intent")
    return required_for_intent(intent)


def planned_claim_types_for_meta(analysis_meta: dict) -> list[str]:
    plan = evidence_plan_from_meta(analysis_meta)
    if plan:
        return planned_claim_types_from_plan(plan)
    return required_claim_types_for_meta(analysis_meta)


def evidence_planner_node(state: dict) -> dict:
    """LangGraph node: create and attach an EvidencePlan."""
    meta = dict(state.get("analysis_meta") or {})
    plan = build_evidence_plan(meta, state.get("user_input") or meta.get("user_input"))
    meta["evidence_plan"] = plan
    print(
        "[evidence_planner] intent="
        f"{plan['analysis_intent']}, required={plan['required_claim_types']}, "
        f"optional={plan['optional_claim_types']}, tasks={len(plan['evidence_tasks'])}"
    )
    return {**state, "analysis_meta": meta, "evidence_plan": plan}
