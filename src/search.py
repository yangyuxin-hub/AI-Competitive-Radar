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
import re
import threading
import time
from datetime import date, datetime
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
# bias → scoring.yaml[source_reliability] key,口径与 collector/hn/v2ex 集中一处
_BIAS_TO_CFG_KEY = {
    "vendor_claim": "web_vendor",
    "third_party": "web_third_party",
    "user_generated": "web_user",
}


def _reliability_for_bias(bias: str) -> float:
    """源可信度:优先 scoring.yaml,缺失回退 _RELIABILITY_BY_BIAS(零行为变化)。"""
    from . import scoring_config
    default = _RELIABILITY_BY_BIAS.get(bias, 0.6)
    key = _BIAS_TO_CFG_KEY.get(bias)
    return scoring_config.reliability(key, default) if key else default


# ────────────────────────────────────────────────────────────────────────────
# P0 相关性门 —— 检索结果必须"真的在讲这个产品"
# ----------------------------------------------------------------------------
# 检索最大的脏源不是内容农场(实测仅占 1-7%),而是"语义漂移 / 同名碰撞":
# 产品名是常见词时(Linear→线性回归、Notion→notion 概念),搜索引擎会返回大量
# "含关键词但与产品无关"的页面——学术 PDF、YouTube 教程、通用定价博客。
# 实测某次 Linear 的 search 证据 51% 连产品名都没真正在讲。
# 这里在证据入库前做确定性相关性判定:离题直接丢弃,不污染 analyzer。
# RELEVANCE_GATE=0 可关闭(排障用)。
# ────────────────────────────────────────────────────────────────────────────

# 软件语境锚点:产品名是常见词时,需要这些信号佐证"在讲一款软件产品"而非同名概念。
_CONTEXT_ANCHORS = {
    "app", "apps", "software", "tool", "tools", "platform", "saas", "product",
    "pricing", "price", "plan", "plans", "subscription", "free", "paid", "tier",
    "feature", "features", "integration", "api", "workspace", "dashboard", "editor",
    "team", "teams", "project", "task", "workflow", "collaboration", "review", "reviews",
    "vs", "alternative", "alternatives", "user", "users", "ide", "copilot",
    "定价", "价格", "订阅", "功能", "团队", "协作", "项目", "任务", "评价", "体验",
    "替代", "对比", "方案", "插件", "编辑器", "工具",
}

# 通用高信誉站(官网域名由 products.yaml 动态注入)。可信源对"提到产品"更宽容。
_TRUSTED_DOMAINS = {
    "reddit.com", "news.ycombinator.com", "g2.com", "capterra.com", "trustradius.com",
    "stackoverflow.com", "github.com", "producthunt.com", "getapp.com", "softwareadvice.com",
}

# 内容农场 / SEO 聚合站 / AI 导航站:即使"提到了产品"也几乎无信息量(拼凑、抄袭、套模板)。
# 相关性门挡不住它们(它们确实在讲产品),这里按域名黑名单直接丢。FARM_FILTER=0 可关。
_FARM_DOMAINS = {
    # 中文技术博客农场 / 采集站
    "csdn.net", "jishuzhan.net", "lashiblog.com", "cnblogs.com", "blog.51cto.com",
    # AI 导航 / 工具聚合 / SEO 站
    "toolradar.com", "utilio.io", "aitoolbriefing.com", "altoolbriefing.com", "allaboutai.com",
    "top3.software", "workflowautomation.net", "stacktidy.com", "digitalyoming.com",
    "blog.herdr.io", "hackceleration.com", "notionfaster.org",
}

_OFFICIAL_DOMAINS_CACHE: Optional[set] = None
_ALIASES_CACHE: Optional[dict] = None


def _relevance_gate_on() -> bool:
    return os.environ.get("RELEVANCE_GATE", "1").strip() not in ("0", "false", "False")


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _official_domains() -> set:
    """products.yaml 里所有产品官网/定价页的域名 → 视为权威源。"""
    global _OFFICIAL_DOMAINS_CACHE
    if _OFFICIAL_DOMAINS_CACHE is None:
        _OFFICIAL_DOMAINS_CACHE = set()
        try:
            import yaml
            from urllib.parse import urlparse
            path = Path(__file__).resolve().parent.parent / "config" / "products.yaml"
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for p in (cfg.get("products") or {}).values():
                for u in (p.get("official_pages") or []) + (p.get("pricing_pages") or []):
                    h = (urlparse(u).hostname or "").lower()
                    h = h[4:] if h.startswith("www.") else h
                    if h:
                        _OFFICIAL_DOMAINS_CACHE.add(h)
        except Exception:  # noqa: BLE001
            pass
    return _OFFICIAL_DOMAINS_CACHE


def _is_trusted_domain(url: str) -> bool:
    h = _host_of(url)
    if not h:
        return False
    pool = _TRUSTED_DOMAINS | _official_domains()
    return any(h == d or h.endswith("." + d) for d in pool)


def _is_farm_domain(url: str) -> bool:
    if os.environ.get("FARM_FILTER", "1").strip() in ("0", "false", "False"):
        return False
    h = _host_of(url)
    return bool(h) and any(h == d or h.endswith("." + d) for d in _FARM_DOMAINS)


def _product_aliases(product: str) -> list[str]:
    """products.yaml 里该产品的规范名 + 别名(小写),含去空格/去点变体。
    用于判断检索结果是否真的在讲这个产品。"""
    global _ALIASES_CACHE
    if _ALIASES_CACHE is None:
        _ALIASES_CACHE = {}
        try:
            import yaml
            path = Path(__file__).resolve().parent.parent / "config" / "products.yaml"
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for name, p in (cfg.get("products") or {}).items():
                _ALIASES_CACHE[name] = {name.lower()} | {a.lower() for a in (p.get("aliases") or [])}
        except Exception:  # noqa: BLE001
            _ALIASES_CACHE = {}
    al = set(_ALIASES_CACHE.get(product, set()))
    al.add(product.lower())
    extra = set()
    for a in al:
        extra.add(a.replace(".", " ").strip())  # linear.app → linear app
        extra.add(a.replace(" ", ""))            # github copilot → githubcopilot
    return sorted(a for a in (al | extra) if a)


def _mention_match(text: str, aliases: list[str]) -> bool:
    """产品名/别名是否出现。多词别名用子串(已足够特异);
    单词用词边界匹配,避免 'linear' 撞 'linearly' / 'co' 撞 'code'。"""
    for a in aliases:
        if not a:
            continue
        if " " in a or "." in a:
            if a in text:
                return True
        elif re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", text):
            return True
    return False


def _is_on_topic(text: str, aliases: list[str], trusted: bool) -> bool:
    """检索结果是否真的在讲这个产品。
    可信源:提到产品名即可。中性源:还需软件语境锚点佐证(挡同名碰撞)。"""
    text = text.lower()
    if not _mention_match(text, aliases):
        return False
    if trusted:
        return True
    return any(anchor in text for anchor in _CONTEXT_ANCHORS)


def _topic_relevance_score(text: str, aliases: list[str], trusted: bool) -> float:
    """A2: 基于关键词命中数的 relevance 梯度分(0.6-0.95)，替代常量 0.7/0.9。

    计分规则:
    - 基础分:trusted 域 0.75,非 trusted 0.60
    - 每命中一个 alias +0.05(上限 +0.15)
    - 命中语境锚点 +0.05
    - 最终 clamp 到 [0.6, 0.95]
    """
    text_lower = text.lower()
    base = 0.75 if trusted else 0.60
    # alias 命中数
    alias_hits = sum(1 for a in aliases if a and a in text_lower)
    bonus = min(alias_hits * 0.05, 0.15)
    # 语境锚点
    if any(anchor in text_lower for anchor in _CONTEXT_ANCHORS):
        bonus += 0.05
    return round(min(base + bonus, 0.95), 2)

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
    if os.environ.get("DISABLE_DDG_SEARCH", "").strip() in ("1", "true", "True"):
        return False
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


# DDG 免费、无 key,但对**突发并发**很敏感(collector 多产品 × 多查询并行会瞬间打几十个请求 → 限流)。
# 用全局锁把 DDG 调用**串行化 + 最小间隔**,再加退避重试,让它细水长流而非突发。
_DDG_LOCK = threading.Lock()
_DDG_LAST = [0.0]


def _ddg_search(query: str, site: str, max_results: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # 旧包名
        except ImportError:
            return []

    min_interval = float(os.environ.get("DDG_MIN_INTERVAL", "1.5"))
    retries = int(os.environ.get("DDG_RETRIES", "3"))
    for attempt in range(retries):
        with _DDG_LOCK:  # 串行 + 限速:同一时刻只允许一个 DDG 请求,且与上次至少间隔 min_interval
            gap = min_interval - (time.time() - _DDG_LAST[0])
            if gap > 0:
                time.sleep(gap)
            try:
                out = []
                with DDGS() as ddgs:
                    for r in ddgs.text(_site_query(query, site), max_results=max_results):
                        out.append({
                            "title": r.get("title") or "",
                            "url": r.get("href") or r.get("url") or "",
                            "content": r.get("body") or "",
                            "score": None,
                        })
                _DDG_LAST[0] = time.time()
                return out
            except Exception as e:  # noqa: BLE001
                _DDG_LAST[0] = time.time()
                msg = str(e).lower()
                if attempt < retries - 1 and ("ratelimit" in msg or "rate" in msg or "202" in msg or "429" in msg):
                    time.sleep((2 ** attempt) * 2)  # 指数退避:2s,4s
                    continue
                raise
    return []


_PROVIDERS = {"brave": _brave_search, "tavily": _tavily_search, "ddg": _ddg_search}


def _provider_chain() -> list[str]:
    explicit = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    if explicit and explicit != "auto":
        chain = [x.strip() for x in explicit.split(",") if x.strip() in _PROVIDERS]
        if os.environ.get("DISABLE_DDG_SEARCH", "").strip() in ("1", "true", "True"):
            chain = [x for x in chain if x != "ddg"]
        return chain
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
    from .collector_common import compute_freshness, smart_truncate

    url = result.get("url") or ""
    content = (result.get("content") or "").strip()
    title = (result.get("title") or "").strip()
    if not content or not url:
        return None
    # P2 农场黑名单:已知 SEO 聚合/导航站即使"在讲产品"也无信息量,直接丢
    if _is_farm_domain(url):
        return None
    # P0 相关性门:检索结果必须真的在讲这个产品,否则丢弃(挡同名碰撞/语义漂移)
    trusted = _is_trusted_domain(url)
    if _relevance_gate_on() and not _is_on_topic(f"{title} {content}", _product_aliases(product), trusted):
        return None
    # A3: 智能截断 — 优先保留含数字/价格的句子，避免截断点丢关键数据
    snippet = smart_truncate(content, 260)
    claim = title or content[:120]
    bias = q.get("bias") or "third_party"
    score = result.get("score")
    # A2: 供应商无 score 时(Brave/DDG)用关键词命中梯度分，替代常量 0.7/0.9
    aliases = _product_aliases(product)
    relevance = round(float(score), 2) if isinstance(score, (int, float)) else _topic_relevance_score(f"{title} {content}", aliases, trusted)
    # A1: freshness 按发布时间对 TTL 判定，不再硬编码 current
    published_at = result.get("published_date") or result.get("date") or ""
    claim_type = q.get("claim_type")
    freshness = compute_freshness(published_at or None, claim_type)
    return {
        "evidence_id": generate_evidence_id(product, url, snippet),
        "product": product,
        "claim_type": claim_type,
        "source_type": q.get("source_type") or "web_search",
        "source_bias": bias,
        "source_url": url,
        "observed_at": date.today().isoformat(),
        "source_freshness": freshness,
        "published_at": published_at or None,
        "claim": claim,
        "extracted_snippet": snippet,
        "source_reliability": _reliability_for_bias(bias),
        "claim_relevance": relevance,
        "evidence_confidence": _reliability_for_bias(bias),
        "collection_source": "search",
    }


def _run_one_query(product: str, q: dict, results_per_query: int) -> tuple[list[dict], dict]:
    query = q.get("query")
    if not query:
        return [], {}
    site = q.get("site", "")
    base = {"query": query, "site": site, "claim_type": q.get("claim_type")}
    try:
        results = tavily_search(query, site, results_per_query)
        # 权威源定向无召回 → 回退全网(农场/离题由 _result_to_evidence 相关性门过滤)
        if not results and site:
            results = tavily_search(query, "", results_per_query)
            base["fallback"] = "full_web"
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
    from .progress import CtxThreadPoolExecutor

    evidences: list[dict] = []
    events: list[dict] = []
    if not tavily_available() or not plan:
        return evidences, events
    # 多条查询并发(每条是独立的 Tavily HTTP 调用)
    with CtxThreadPoolExecutor(max_workers=min(8, len(plan))) as pool:
        for hits, event in pool.map(
            lambda q: _run_one_query(product, q, results_per_query), plan
        ):
            if event:
                events.append(event)
            evidences.extend(hits)
    return evidences, events
