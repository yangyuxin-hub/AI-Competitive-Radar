"""Web 搜索抓取 — 通过 Tavily 把「搜索计划」变成真实证据。

像 GPT 检索那样:对每条查询调搜索 API，取回一批真实来源(url+摘要)，
直接映射成 raw_evidence。无 TAVILY_API_KEY 时整体禁用(优雅降级，不影响 mock 流程)。

环境变量:
    TAVILY_API_KEY  — 搜索 API key(https://tavily.com 免费注册)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional

_TAVILY_URL = "https://api.tavily.com/search"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "tavily"

# 不同立场的来源可信度先验(可后续下沉 config)
_RELIABILITY_BY_BIAS = {
    "vendor_claim": 0.9,
    "third_party": 0.7,
    "user_generated": 0.6,
}

_HTTP_TIMEOUT = None  # 懒建,见 _timeout()


def _timeout():
    import httpx
    return httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


def _domain_of(site: str) -> Optional[str]:
    """'reddit.com/r/cursor' → 'reddit.com';空 → None。"""
    site = (site or "").strip()
    if not site:
        return None
    return site.split("/")[0]


def _site_query(query: str, site: str) -> str:
    """Brave/DDG 无 include_domains 参数 → 用 'site:domain' 操作符限定。"""
    domain = _domain_of(site)
    return f"{query} site:{domain}" if domain else query


# ────────────────────────────────────────────────────────────────────────────
# 多供应商:Brave(主力,免费额度大) → Tavily(若配 key) → DuckDuckGo(免费兜底,无 key)
# 统一返回 [{title, url, content, score}]。新增供应商只加一个 _xxx_search + 注册即可。
# SEARCH_PROVIDER=brave,ddg 可显式指定顺序;留空=auto(按可用性自动编排)。
# ────────────────────────────────────────────────────────────────────────────

def _brave_key() -> str:
    return (os.environ.get("BRAVE_API_KEY") or os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()


def _tavily_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def _ddg_installed() -> bool:
    import importlib.util
    return (importlib.util.find_spec("ddgs") is not None
            or importlib.util.find_spec("duckduckgo_search") is not None)


def _tavily_search(query: str, site: str, max_results: int) -> list[dict]:
    import httpx
    key = _tavily_key()
    if not key:
        return []
    payload = {"api_key": key, "query": query, "max_results": max_results, "search_depth": "basic"}
    domain = _domain_of(site)
    if domain:
        payload["include_domains"] = [domain]
    with httpx.Client(timeout=_timeout()) as client:
        resp = client.post(_TAVILY_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data.get("results") or []


def _brave_search(query: str, site: str, max_results: int) -> list[dict]:
    import httpx
    key = _brave_key()
    if not key:
        return []
    headers = {"X-Subscription-Token": key, "Accept": "application/json"}
    params = {"q": _site_query(query, site), "count": max_results}
    with httpx.Client(timeout=_timeout()) as client:
        resp = client.get(_BRAVE_URL, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
    out = []
    for r in ((data.get("web") or {}).get("results") or []):
        out.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "content": r.get("description") or r.get("snippet") or "",
            "score": None,
        })
    return out


def _ddg_search(query: str, site: str, max_results: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # 旧包名
        except ImportError:
            return []
    out = []
    with DDGS() as ddgs:
        for r in ddgs.text(_site_query(query, site), max_results=max_results):
            out.append({
                "title": r.get("title") or "",
                "url": r.get("href") or r.get("url") or "",
                "content": r.get("body") or "",
                "score": None,
            })
    return out


_PROVIDERS = {"brave": _brave_search, "tavily": _tavily_search, "ddg": _ddg_search}


def _provider_chain() -> list[str]:
    explicit = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    if explicit and explicit != "auto":
        return [x.strip() for x in explicit.split(",") if x.strip() in _PROVIDERS]
    chain: list[str] = []
    if _brave_key():
        chain.append("brave")
    if _tavily_key():
        chain.append("tavily")
    if _ddg_installed():
        chain.append("ddg")  # 免费无 key 兜底,永远兜底在最后
    return chain


def search_available() -> bool:
    """只要链上有任一可用供应商(含免费 DDG)即为 True。"""
    return bool(_provider_chain())


def tavily_available() -> bool:
    """向后兼容别名:现在表示"是否有任一可用搜索供应商"。"""
    return search_available()


def _cache_enabled() -> bool:
    return os.environ.get("TAVILY_CACHE", "1").strip() not in ("0", "false", "False")


def _cache_path(query: str, site: str, max_results: int) -> Path:
    key = hashlib.sha1(f"{query}|{site}|{max_results}".encode("utf-8")).hexdigest()[:16]
    return _CACHE_DIR / f"{key}.json"


def _cache_get(query: str, site: str, max_results: int) -> Optional[list[dict]]:
    if not _cache_enabled():
        return None
    path = _cache_path(query, site, max_results)
    if not path.exists():
        return None
    ttl_h = float(os.environ.get("TAVILY_CACHE_TTL_HOURS", "72"))
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - rec.get("ts", 0) > ttl_h * 3600:
            return None  # 过期
        return rec.get("results")
    except (json.JSONDecodeError, OSError):
        return None


def _cache_set(query: str, site: str, max_results: int, results: list[dict]) -> None:
    if not _cache_enabled():
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(query, site, max_results).write_text(
            json.dumps({"ts": time.time(), "query": query, "results": results}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def web_search(query: str, site: str = "", max_results: int = 5) -> list[dict]:
    """统一搜索入口:带磁盘缓存,按 _provider_chain 依次尝试,任一非空即返回。
    返回 [{title, url, content, score}]。全部失败/空 → 返回 [](不缓存空,便于下次重试)。
    缓存与供应商无关:Brave 抓到的结果下次直接命中,换供应商也复用。
    TAVILY_CACHE=0 关缓存,TAVILY_CACHE_TTL_HOURS 调 TTL(变量名沿用历史)。"""
    cached = _cache_get(query, site, max_results)
    if cached is not None:
        return cached
    for name in _provider_chain():
        try:
            results = _PROVIDERS[name](query, site, max_results)
        except Exception as e:  # noqa: BLE001
            print(f"[search] provider '{name}' 失败,降级下一个: {type(e).__name__}: {e}")
            continue
        if results:
            _cache_set(query, site, max_results, results)
            return results
    return []


# 向后兼容别名:历史调用点(_run_one_query / 测试)仍用 tavily_search 这个名字。
tavily_search = web_search


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


def feature_targeted_evidence(
    product: str,
    feature_names: list[str],
    focus: str = "",
    max_results: int = 3,
) -> list[dict]:
    """按「功能名」给单个产品做针对性补采:每个 feature 两条角度(体验/痛点)。
    用于让 (product × feature) 对比矩阵更密;无 TAVILY_API_KEY 时返回 []。
    复用 search_plan_to_evidence(自带并发 + 磁盘缓存)。"""
    if not tavily_available() or not feature_names:
        return []
    plan: list[dict] = []
    for fname in feature_names:
        base = f"{product} {fname} {focus}".strip()
        plan.append({"query": base, "claim_type": "performance_quality",
                     "bias": "third_party", "source_type": "web_search", "site": ""})
        plan.append({"query": f"{product} {fname} 体验 问题 评价".strip(), "claim_type": "user_pain",
                     "bias": "user_generated", "source_type": "web_search", "site": ""})
    evidences, _events = search_plan_to_evidence(product, plan, results_per_query=max_results)
    return evidences


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
