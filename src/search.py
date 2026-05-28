"""Web 搜索抓取 — 通过 Tavily 把「搜索计划」变成真实证据。

像 GPT 检索那样:对每条查询调搜索 API，取回一批真实来源(url+摘要)，
直接映射成 raw_evidence。无 TAVILY_API_KEY 时整体禁用(优雅降级，不影响 mock 流程)。

环境变量:
    TAVILY_API_KEY  — 搜索 API key(https://tavily.com 免费注册)
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

_TAVILY_URL = "https://api.tavily.com/search"

# 不同立场的来源可信度先验(可后续下沉 config)
_RELIABILITY_BY_BIAS = {
    "vendor_claim": 0.9,
    "third_party": 0.7,
    "user_generated": 0.6,
}


def tavily_available() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def _domain_of(site: str) -> Optional[str]:
    """'reddit.com/r/cursor' → 'reddit.com';空 → None。"""
    site = (site or "").strip()
    if not site:
        return None
    return site.split("/")[0]


def tavily_search(query: str, site: str = "", max_results: int = 5) -> list[dict]:
    """调 Tavily,返回 [{title, url, content, score}]。失败抛异常由上层兜底。"""
    import httpx

    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }
    domain = _domain_of(site)
    if domain:
        payload["include_domains"] = [domain]
    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)) as client:
        resp = client.post(_TAVILY_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data.get("results") or []


def _result_to_evidence(product: str, result: dict, q: dict) -> Optional[dict]:
    from .collector import generate_evidence_id  # 延迟导入避免循环

    url = result.get("url") or ""
    content = (result.get("content") or "").strip()
    title = (result.get("title") or "").strip()
    if not content or not url:
        return None
    # 片段过长会撑大 analyzer prompt、抬高 evidence_id 误引率 → 截断控质量与速度
    snippet = content[:260]
    claim = title or content[:120]
    bias = q.get("bias") or "third_party"
    score = result.get("score")
    return {
        "evidence_id": generate_evidence_id(product, url, snippet),
        "product": product,
        "claim_type": q.get("claim_type"),
        "source_type": q.get("source_type") or "web_search",
        "source_bias": bias,
        "source_url": url,
        "observed_at": date.today().isoformat(),
        "source_freshness": "current",
        "claim": claim,
        "extracted_snippet": snippet,
        "source_reliability": _RELIABILITY_BY_BIAS.get(bias, 0.6),
        "claim_relevance": round(float(score), 2) if isinstance(score, (int, float)) else None,
        "evidence_confidence": _RELIABILITY_BY_BIAS.get(bias, 0.6),
        "collection_source": "search",
    }


def _run_one_query(product: str, q: dict, results_per_query: int) -> tuple[list[dict], dict]:
    query = q.get("query")
    if not query:
        return [], {}
    base = {"query": query, "site": q.get("site", ""), "claim_type": q.get("claim_type")}
    try:
        results = tavily_search(query, q.get("site", ""), results_per_query)
        hits = [ev for r in results if (ev := _result_to_evidence(product, r, q))]
        return hits, {**base, "status": "ok", "count": len(hits),
                      "urls": [h["source_url"] for h in hits]}
    except Exception as e:  # noqa: BLE001
        return [], {**base, "status": "error", "error": str(e)}


def search_plan_to_evidence(
    product: str,
    plan: list[dict],
    results_per_query: int = 5,
) -> tuple[list[dict], list[dict]]:
    """并发执行整份搜索计划，返回 (evidence_list, query_events)。

    query_events 记录每条查询的命中数/状态，供前端展示「爬了哪些来源」。
    """
    from concurrent.futures import ThreadPoolExecutor

    evidences: list[dict] = []
    events: list[dict] = []
    if not tavily_available() or not plan:
        return evidences, events
    # 多条查询并发(每条是独立的 Tavily HTTP 调用)
    with ThreadPoolExecutor(max_workers=min(8, len(plan))) as pool:
        for hits, event in pool.map(
            lambda q: _run_one_query(product, q, results_per_query), plan
        ):
            if event:
                events.append(event)
            evidences.extend(hits)
    return evidences, events
