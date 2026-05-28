"""信息源规划 — 自主决定「该去哪些站搜哪类证据」。

输入:产品 / 竞品 / 焦点 / 还缺的 claim_types。
参考:config/sources.yaml 的推荐源（用户长期复用 + LLM 模仿其形式补充）。
输出:搜索计划 list[dict]，每条可直接投给 Tavily 检索。

Demo 默认走 config 启发式；设置 SOURCE_PLANNER_LLM=1 时启用
prompts/source_discovery.md 的 LLM 规划，失败回退 config 启发式。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from . import llm

_ROOT = Path(__file__).resolve().parent.parent
_SOURCES_YAML = _ROOT / "config" / "sources.yaml"
_PROMPT = _ROOT / "prompts" / "source_discovery.md"

REQUIRED_CLAIM_TYPES = ["feature_existence", "performance_quality", "pricing", "user_pain"]


def load_sources_config() -> dict:
    if not _SOURCES_YAML.exists():
        return {}
    with _SOURCES_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def recommended_for(domain: Optional[str], claim_types: list[str]) -> list[dict]:
    """汇总 config 里 by_claim_type + by_domain + custom 的推荐源（去重）。"""
    cfg = load_sources_config()
    out: list[dict] = []
    seen: set[tuple] = set()

    def add(item: dict) -> None:
        key = (item.get("source_type"), item.get("site"))
        if key not in seen:
            seen.add(key)
            out.append(item)

    for ct in claim_types:
        for item in (cfg.get("by_claim_type") or {}).get(ct, []):
            add(item)
    if domain:
        for item in (cfg.get("by_domain") or {}).get(domain, []):
            add(item)
    for item in cfg.get("custom") or []:
        add(item)
    return out


def _system_prompt() -> str:
    text = _PROMPT.read_text(encoding="utf-8")
    # 取 SYSTEM 段之后的内容
    marker = "## SYSTEM"
    return text[text.index(marker) + len(marker):].strip() if marker in text else text


_CLAIM_KEYWORDS = {
    "feature_existence": "features capabilities official",
    "pricing": "pricing plans cost",
    "performance_quality": "review performance accuracy",
    "user_pain": "user complaints problems",
}


def _heuristic_plan(product: str, focus: list[str], missing: list[str], recs: list[dict],
                    max_per_claim: int) -> list[dict]:
    """无 LLM 时:直接用推荐源 + 模板查询(覆盖全部 claim_type)。"""
    focus_kw = focus[0] if focus else ""
    queries: list[dict] = []
    for ct in missing:
        rel = [r for r in recs if (r.get("note") or "").strip()] if False else recs
        chosen = rel[:max_per_claim] or [{}]
        kw = _CLAIM_KEYWORDS.get(ct, "")
        for r in chosen:
            queries.append({
                "claim_type": ct,
                "query": f"{product} {focus_kw} {kw}".strip(),
                "site": r.get("site", ""),
                "source_type": r.get("source_type", "web_search"),
                "bias": r.get("bias", "third_party"),
                "why": "config 推荐源 + 模板查询(无 LLM 回退)",
            })
    return queries


def plan_sources(
    product: str,
    competitors: list[str],
    analysis_focus: list[str],
    missing_claim_types: Optional[list[str]] = None,
    domain: Optional[str] = None,
) -> list[dict]:
    """产出搜索计划。默认覆盖全部 4 类证据(Tavily 主力，不依赖官网抓取)。"""
    missing = missing_claim_types or REQUIRED_CLAIM_TYPES
    cfg = load_sources_config()
    max_per_claim = int((cfg.get("defaults") or {}).get("max_queries_per_claim", 2))
    recs = recommended_for(domain, missing)

    if _llm_planning_enabled() and not llm.is_mock_mode() and _has_llm():
        plan = _plan_via_llm(product, competitors, analysis_focus, missing, recs, max_per_claim)
        if plan:
            return plan
    return _heuristic_plan(product, analysis_focus, missing, recs, max_per_claim)


def _llm_planning_enabled() -> bool:
    import os
    return os.environ.get("SOURCE_PLANNER_LLM", "").strip() in ("1", "true", "True")


def _has_llm() -> bool:
    import os
    return bool(os.environ.get("ARK_API_KEY"))


def _plan_via_llm(product, competitors, focus, missing, recs, max_per_claim) -> list[dict]:
    try:
        raw = llm.get_llm().call_json(
            system_prompt=_system_prompt(),
            user_payload={
                "product": product,
                "competitors": competitors,
                "analysis_focus": focus,
                "missing_claim_types": missing,
                "recommended_sources": recs,
                "max_queries_per_claim": max_per_claim,
            },
            max_tokens=1024,
            label="source_discovery",
        )
        queries = raw.get("queries") if isinstance(raw, dict) else None
        return queries or []
    except Exception as e:  # noqa: BLE001
        print(f"[source_planner] LLM 规划失败，回退启发式: {e}")
        return []
