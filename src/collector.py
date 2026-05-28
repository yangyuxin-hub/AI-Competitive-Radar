"""Collector 节点 — 见 docs/design-v2.2.md §五

三层兜底:live → cache → mock。
URL Discovery:LLM 自动发现产品官网和定价页(替代 products.yaml 硬编码)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from .state import AgentState
from .skill import create_skill_registry


# ────────────────────────────────────────────────────────────────────────────
# 进度回调(供 api SSE 实时展示采集思考/进度)
# ────────────────────────────────────────────────────────────────────────────

_PROGRESS_CALLBACK: Optional[Callable[[dict], None]] = None


def set_progress_callback(cb: Optional[Callable[[dict], None]]) -> None:
    global _PROGRESS_CALLBACK
    _PROGRESS_CALLBACK = cb


def _emit_progress(**event) -> None:
    if _PROGRESS_CALLBACK is None:
        return
    try:
        _PROGRESS_CALLBACK(dict(event))
    except Exception:
        pass


_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _ROOT / "data" / "cache"

# 按 claim_type 的 freshness TTL(天) — 见 design v2.2 §4.1
FRESHNESS_TTL_DAYS = {
    "pricing": 7,
    "feature_existence": 30,
    "performance_quality": 60,
    "user_pain": 90,
    "market_signal": 30,
}


def _resolve_sample_path() -> Path:
    """优先级: SAMPLE_SOURCES_PATH 环境变量 > config/domains.yaml[DOMAIN] > 默认 sample_sources.json"""
    env_path = os.environ.get("SAMPLE_SOURCES_PATH")
    if env_path:
        return Path(env_path)

    domain = os.environ.get("DOMAIN", "").strip()
    if domain:
        try:
            import yaml  # 延迟导入,避免 yaml 不装时也能跑默认
            with (_ROOT / "config" / "domains.yaml").open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            entry = (cfg.get("domains") or {}).get(domain)
            if entry and entry.get("sample_path"):
                return _ROOT / entry["sample_path"]
        except Exception as e:
            print(f"[collector] WARN: failed to resolve DOMAIN={domain}: {e}")
    return _ROOT / "data" / "sample_sources.json"

# ────────────────────────────────────────────────────────────────────────────
# URL Discovery — LLM 自动发现产品官网
# ────────────────────────────────────────────────────────────────────────────

def _load_products_config() -> dict:
    """读取 products.yaml，返回 {product_name: {official_pages, pricing_pages, aliases}}"""
    path = _ROOT / "config" / "products.yaml"
    try:
        import yaml
        with path.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        out = {}
        for name, p in (cfg.get("products") or {}).items():
            out[name] = {
                "official_pages": list(p.get("official_pages") or []),
                "pricing_pages": list(p.get("pricing_pages") or []),
                "aliases": list(p.get("aliases") or []),
            }
        return out
    except Exception:
        return {}


def discover_urls(product: str, products_config: Optional[dict] = None) -> dict:
    """为单个产品发现官网 URL。

    Mock 模式:从 products.yaml 读取已配置的 URL。
    真实模式:调用 LLM 让其搜索并返回官方 URL。

    Returns: {"official_pages": [...], "pricing_pages": [...], "source": "config"|"llm"}
    """
    from .llm import is_mock_mode

    # 先看 products.yaml 有没有配置
    cfg = products_config or _load_products_config()
    if product in cfg and (cfg[product]["official_pages"] or cfg[product]["pricing_pages"]):
        return {**cfg[product], "source": "config"}

    # Mock 模式:没有配置就返回空，让 MockAdapter 兜底
    if is_mock_mode():
        return {"official_pages": [], "pricing_pages": [], "source": "mock"}

    # 真实模式:调用 LLM 发现 URL
    from .llm import get_llm

    llm = get_llm()
    prompt_path = _ROOT / "prompts" / "url_discovery.md"
    if prompt_path.exists():
        system = prompt_path.read_text(encoding="utf-8")
    else:
        system = (
            "你是 URL 发现 Agent。根据产品名称找到官方功能页和定价页的 URL。"
            "返回 JSON: {\"official_pages\": [...], \"pricing_pages\": [...]}。"
            "只返回官方域名下的页面，不要第三方网站。"
        )

    payload = {"product": product, "language": "en"}
    try:
        result = llm.call_json(system, payload, max_tokens=1024, label=f"url_discovery_{product}")
        return {
            "official_pages": result.get("official_pages") or [],
            "pricing_pages": result.get("pricing_pages") or [],
            "source": "llm",
            "reasoning": result.get("reasoning", ""),
        }
    except Exception as e:
        print(f"[collector] URL discovery failed for {product}: {e}")
        return {"official_pages": [], "pricing_pages": [], "source": "error", "error": str(e)}


def discover_all_urls(products: list[str]) -> dict[str, dict]:
    """批量发现多个产品的 URL。返回 {product: {official_pages, pricing_pages, source}}

    使用 ThreadPoolExecutor 并发执行。
    """
    cfg = _load_products_config()
    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(discover_urls, p, cfg): p for p in products}
        done, _ = wait(futures.keys(), timeout=60)
        for fut in done:
            product = futures[fut]
            try:
                results[product] = fut.result()
            except Exception as e:
                results[product] = {
                    "official_pages": [],
                    "pricing_pages": [],
                    "source": "error",
                    "error": str(e),
                }

    # 补超时的
    for fut, product in futures.items():
        if product not in results:
            results[product] = {
                "official_pages": [],
                "pricing_pages": [],
                "source": "timeout",
            }

    return results


MAX_EVIDENCE_PER_PRODUCT = 40


def cap_evidence_per_product(evidences: list[dict], limit: int = MAX_EVIDENCE_PER_PRODUCT) -> list[dict]:
    """每个产品按 evidence_confidence 降序取 top limit 条"""
    by_product: dict[str, list[dict]] = {}
    for ev in evidences:
        p = ev.get("product", "unknown")
        by_product.setdefault(p, []).append(ev)

    result: list[dict] = []
    for product, items in by_product.items():
        items.sort(key=lambda e: e.get("evidence_confidence", 0), reverse=True)
        kept = items[:limit]
        dropped = len(items) - len(kept)
        if dropped:
            print(f"  [cap] {product}: {len(items)} → {limit} (dropped {dropped} low-confidence)")
        result.extend(kept)
    return result


_debug_file_path: Optional[Path] = None


def reset_debug_file() -> None:
    """重置 debug 文件路径（每次 graph 执行前调用）"""
    global _debug_file_path
    _debug_file_path = None


def dump_evidence_debug(evidences: list[dict], path: Optional[Path] = None, run_id: int = 0) -> Path:
    """将 evidence 输出到 data/ 下的 debug 文件，按产品分组，跨 run 追加"""
    import json
    from datetime import datetime

    global _debug_file_path

    print(f"  [debug] dump_evidence_debug called with {len(evidences)} items (run #{run_id})")

    # 首次调用时创建文件路径，后续复用（追加）
    if path is None:
        if _debug_file_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _debug_file_path = _ROOT / "data" / f"evidence_debug_{ts}.json"
        path = _debug_file_path

    # 加载已有数据（追加模式）
    existing: dict = {}
    if path.exists():
        try:
            with path.open(encoding="utf-8") as f:
                existing = json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    # 合并：按产品去重（evidence_id），新数据覆盖旧的
    for ev in evidences:
        product = ev.get("product", "unknown")
        if product not in existing:
            existing[product] = {"count": 0, "by_source": {}, "by_claim_type": {}, "evidence": [], "_seen_ids": set()}
        bucket = existing[product]
        # 检查是否已存在
        seen = bucket.get("_seen_ids", set())
        eid = ev.get("evidence_id", "")
        if eid not in seen:
            bucket["evidence"].append(ev)
            seen.add(eid)
            bucket["_seen_ids"] = seen

    # 更新统计
    for product, bucket in existing.items():
        items = bucket["evidence"]
        bucket["count"] = len(items)
        bucket["by_source"] = _count_by(items, "source_type")
        bucket["by_claim_type"] = _count_by(items, "claim_type")

    # 写入（_seen_ids 不序列化）
    output = {}
    for product, bucket in existing.items():
        output[product] = {
            "count": bucket["count"],
            "by_source": bucket["by_source"],
            "by_claim_type": bucket["by_claim_type"],
            "evidence": bucket["evidence"],
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(v["count"] for v in output.values())
    products = list(output.keys())
    print(f"  [debug] evidence dump → {path} (run#{run_id}, total={total}, products={products})")
    return path


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        k = item.get(key, "unknown")
        counts[k] = counts.get(k, 0) + 1
    return counts


REQUIRED_CLAIM_TYPES = {
    "feature_existence",
    "performance_quality",
    "pricing",
    "user_pain",
}


# ────────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────────

def generate_evidence_id(product: str, source_url: str, claim: str) -> str:
    """S + sha1[:7] = 8 字符,见 design §4.1"""
    raw = f"{product}|{source_url}|{claim}".encode("utf-8")
    return "S" + hashlib.sha1(raw).hexdigest()[:7].upper()


def dedupe_evidence(evidences: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for ev in evidences:
        merged[ev["evidence_id"]] = ev
    return list(merged.values())


def patch_by_requirements(
    existing: list[dict],
    new: list[dict],
    requirements: list[dict],
) -> list[dict]:
    """打回时:基于结构化 requirements 精准追加证据,不重复塞旧的

    当 requirements 中没有 collector 相关的 required_claim_types 时,
    不做过滤,保留全部新证据(由后续 dedupe_evidence 去重)。
    """
    existing_ids = {e["evidence_id"] for e in existing}
    needed_types: set[str] = set()
    for r in requirements:
        if r.get("reject_target") != "collector":
            continue
        for ct in r.get("required_claim_types", []) or []:
            needed_types.add(ct)

    if needed_types:
        # 有明确需求:只补充缺失的 claim_type
        patches = [
            e for e in new
            if e["evidence_id"] not in existing_ids
            and e["claim_type"] in needed_types
        ]
    else:
        # 无明确需求:保留全部新证据,由 dedupe 去重
        patches = [e for e in new if e["evidence_id"] not in existing_ids]
    return existing + patches


# ────────────────────────────────────────────────────────────────────────────
# Adapter 接口与实现
# ────────────────────────────────────────────────────────────────────────────

class SourceAdapter(ABC):
    @abstractmethod
    def fetch(self, product: str, focus: str) -> list[dict]:
        ...

    @abstractmethod
    def can_fetch(self, product: str) -> bool:
        ...


class OfficialPageAdapter(SourceAdapter):
    """httpx + BeautifulSoup 抓官方 features 页,提取段落作为 feature_existence 证据。

    工程态度:
    - 优先读 config/products.yaml 拿 URL 列表
    - 超时 15s,失败立即降级到 cache
    - 提取段落 + 标题作为 claim/snippet,不做复杂 NLP
    - 支持本地 file:// fixture 用作单测,见 data/fixtures/

    生产化路径:
    - 需要 robust 抓取的 SPA(React)可换 Playwright/Selenium
    - 高频抓取应加 rate limit + Etag/If-Modified-Since
    """

    def __init__(self, products_config_path: Optional[Path] = None,
                 dynamic_urls: Optional[dict[str, list[str]]] = None) -> None:
        """初始化。

        Args:
            products_config_path: products.yaml 路径(默认读 config/products.yaml)
            dynamic_urls: {product: [url, ...]} 动态 URL，优先级高于 yaml 配置
        """
        self._urls: dict[str, list[str]] = {}
        path = products_config_path or _ROOT / "config" / "products.yaml"
        try:
            import yaml
            with path.open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            for name, p in (cfg.get("products") or {}).items():
                self._urls[name] = list(p.get("official_pages") or [])
        except Exception:
            self._urls = {}
        # 动态 URL 覆盖 yaml 配置
        if dynamic_urls:
            for product, urls in dynamic_urls.items():
                if urls:
                    self._urls[product] = urls

    def can_fetch(self, product: str) -> bool:
        return bool(self._urls.get(product))

    def fetch(self, product: str, focus: str) -> list[dict]:
        urls = self._urls.get(product) or []
        print(f"  [OfficialPageAdapter] {product}: {len(urls)} URLs configured: {urls}")
        evidences: list[dict] = []
        for url in urls:
            try:
                evs = self._fetch_one(product, url)
                print(f"  [OfficialPageAdapter] {product} <- {url}: extracted {len(evs)} evidence")
                evidences.extend(evs)
            except Exception as e:
                print(f"  [OfficialPageAdapter] {product} <- {url}: FAIL {type(e).__name__}: {e}")
        return evidences

    def _fetch_one(self, product: str, url: str) -> list[dict]:
        print(f"  [OfficialPageAdapter] fetching {url} ...")
        html = self._read(url)
        print(f"  [OfficialPageAdapter] got {len(html)} chars from {url}")
        result = self._extract(product, url, html)
        print(f"  [OfficialPageAdapter] extracted {len(result)} evidence from {url}")

        # SPA 兜底: httpx 提取 0 条时用 Playwright 渲染 JS 重试
        if not result and url.startswith("http"):
            print(f"  [OfficialPageAdapter] 0 evidence from httpx, retrying with Playwright ...")
            try:
                html_pw = self._read_playwright(url)
                print(f"  [OfficialPageAdapter] Playwright got {len(html_pw)} chars from {url}")
                result = self._extract(product, url, html_pw)
                print(f"  [OfficialPageAdapter] Playwright extracted {len(result)} evidence from {url}")
            except Exception as e:
                print(f"  [OfficialPageAdapter] Playwright fallback FAILED: {type(e).__name__}: {e}")

        return result

    def _read(self, url: str) -> str:
        if url.startswith("file://") or url.startswith(("/", ".")) or len(url) >= 2 and url[1] == ":":
            # 本地 fixture
            p = Path(url.replace("file://", "")) if url.startswith("file://") else Path(url)
            return p.read_text(encoding="utf-8")
        # 网络抓取
        import httpx
        print(f"  [OfficialPageAdapter] HTTP GET {url} ...")
        headers = {
            "User-Agent": "AICompetitiveRadar/0.1 (+https://github.com/yangyuxin-hub/AI-Competitive-Radar; academic)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
                          follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            print(f"  [OfficialPageAdapter] HTTP {r.status_code}, {len(r.text)} chars from {url}")
            return r.text

    @staticmethod
    def _read_playwright(url: str) -> str:
        """Playwright 渲染 JS 后取 innerText，用于 SPA 页面"""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)  # 等 JS 渲染
                html = page.content()
                return html
            finally:
                browser.close()

    @staticmethod
    def _extract(product: str, url: str, html: str) -> list[dict]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise RuntimeError("beautifulsoup4 未安装。pip install -r requirements.txt")

        soup = BeautifulSoup(html, "html.parser")
        # 优先取 main,其次 body
        root = soup.find("main") or soup.body or soup

        # 判断 URL 类型，决定 claim_type
        url_lower = url.lower()
        if "pric" in url_lower or "plan" in url_lower:
            default_claim_type = "pricing"
        else:
            default_claim_type = "feature_existence"

        # 过滤掉导航、页脚等噪声的关键词
        _NOISE_KEYWORDS = {
            "cookie", "privacy", "terms of service", "copyright",
            "all rights reserved", "sign up", "log in", "sign in",
            "subscribe to", "newsletter", "follow us", "social",
        }

        # 抽取所有 h1/h2/h3/p/li/td/th,过滤短文本和噪声
        chunks = []
        for tag in root.find_all(["h1", "h2", "h3", "p", "li", "td", "th"]):
            text = tag.get_text(" ", strip=True)
            # 长度过滤: 至少40字符,无上限(长段后面会截断)
            if len(text) < 40:
                continue
            # 噪声过滤
            text_lower = text.lower()
            if any(kw in text_lower for kw in _NOISE_KEYWORDS):
                continue
            chunks.append(text)

        # 信息密度排序: 优先保留含有数字、功能关键词的段落
        def _info_density(t: str) -> int:
            score = 0
            # 含数字(价格、百分比、版本号)加分
            import re
            if re.search(r'\d', t):
                score += 2
            # 含功能动词加分
            feature_words = {"support", "enable", "allow", "provide", "offer",
                             "feature", "build", "create", "generate", "analyze",
                             "integrate", "automate", "optimize", "detect"}
            for w in feature_words:
                if w in t.lower():
                    score += 1
            # 长度适中(50-200)加分
            if 50 <= len(t) <= 200:
                score += 1
            return score

        chunks.sort(key=_info_density, reverse=True)

        from datetime import datetime
        observed = datetime.now().strftime("%Y-%m-%d")
        out: list[dict] = []
        # 取前 15 段作为 evidence
        for idx, snippet in enumerate(chunks[:15]):
            claim = snippet if len(snippet) <= 120 else snippet[:117] + "..."
            # 加入 idx 避免同一页面内相同文本段产生相同 evidence_id
            eid = generate_evidence_id(product, f"{url}#{idx}", claim)

            # 根据内容推断更精确的 claim_type
            claim_type = default_claim_type
            snippet_lower = snippet.lower()
            if default_claim_type == "feature_existence":
                if any(w in snippet_lower for w in ("price", "pricing", "free", "$", "per month", "per year", "plan")):
                    claim_type = "pricing"
                elif any(w in snippet_lower for w in ("fast", "speed", "latency", "performance", "benchmark")):
                    claim_type = "performance_quality"
                elif any(w in snippet_lower for w in ("issue", "bug", "problem", "frustrat", "complain", "slow")):
                    claim_type = "user_pain"

            # 根据片段长度和信息量调整置信度
            conf = 0.60
            if len(snippet) > 100:
                conf += 0.05
            if re.search(r'\d', snippet):
                conf += 0.05

            out.append({
                "evidence_id": eid,
                "product": product,
                "claim_type": claim_type,
                "source_type": "official_page",
                "source_bias": "vendor_claim",
                "source_url": url,
                "observed_at": observed,
                "source_freshness": "current",
                "claim": claim,
                "extracted_snippet": snippet,
                "source_reliability": 0.85,
                "claim_relevance": 0.75,
                "evidence_confidence": round(conf, 2),
            })
        return out


class SearchAdapter(SourceAdapter):
    """Tavily 网络搜索 — 自主规划「该去哪搜」并真实抓取 UGC / 第三方来源。

    与 HN/V2EX skills 互补:skills 用平台官方 API 抓特定高价值源(精度),
    SearchAdapter 用 Tavily 广撒网覆盖任意站 + 全 4 类证据(广度)。
    仅在 TAVILY_API_KEY 存在时激活。规划见 source_planner，抓取见 search。
    """

    def __init__(self, domain: Optional[str] = None) -> None:
        self.domain = domain or os.environ.get("DOMAIN")
        # 按产品存检索事件，避免多产品并发共享一个 adapter 时互相覆盖
        self.events_by_product: dict[str, list[dict]] = {}

    def can_fetch(self, product: str) -> bool:
        from . import search
        return search.tavily_available()

    def fetch(self, product: str, focus: str) -> list[dict]:
        from . import search, source_planner
        plan = source_planner.plan_sources(
            product=product,
            competitors=[],
            analysis_focus=[focus] if focus else [],
            domain=self.domain,
        )
        # 把「该去哪搜、为什么」的真实决策实时吐出来(替代轮播文案)
        _CT_CN = {
            "feature_existence": "功能",
            "performance_quality": "性能",
            "pricing": "定价",
            "user_pain": "痛点",
        }
        for q in plan:
            site = q.get("site") or "全网"
            ct = _CT_CN.get(q.get("claim_type"), q.get("claim_type") or "")
            why = q.get("why") or ""
            _emit_progress(
                phase="plan_decision",
                product=product,
                claim_type=q.get("claim_type"),
                message=f"🔍 决定去 {site} 搜「{q.get('query')}」找{ct}证据 —— {why}",
            )
        cfg = source_planner.load_sources_config()
        per_query = int((cfg.get("defaults") or {}).get("results_per_query", 5))
        evidences, events = search.search_plan_to_evidence(product, plan, per_query)
        self.events_by_product[product] = events
        print(f"  [search] plan={len(plan)} queries → {len(evidences)} evidence")
        return evidences


class MockAdapter(SourceAdapter):
    """从 data/sample_sources.json 读取证据 — Demo 兜底"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _resolve_sample_path()
        self._cache: Optional[list[dict]] = None

    def _load(self) -> list[dict]:
        if self._cache is not None:
            return self._cache
        with self.path.open(encoding="utf-8") as f:
            self._cache = json.load(f).get("raw_evidence", [])
        return self._cache

    def can_fetch(self, product: str) -> bool:
        return self.path.exists()

    def fetch(self, product: str, focus: str) -> list[dict]:
        return [e for e in self._load() if e.get("product") == product]


class CacheAdapter(SourceAdapter):
    """data/cache/<product>.json 持久化。同 evidence_id 覆盖,按 TTL 重算 freshness。"""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or _CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, product: str) -> Path:
        # 防路径注入
        safe = "".join(c for c in product if c.isalnum() or c in "._-")
        return self.cache_dir / f"{safe}.json"

    def can_fetch(self, product: str) -> bool:
        return self._path(product).exists()

    def _load(self, product: str) -> list[dict]:
        p = self._path(product)
        if not p.exists():
            return []
        try:
            with p.open(encoding="utf-8") as f:
                return json.load(f) or []
        except (json.JSONDecodeError, OSError):
            return []

    def _dump(self, product: str, evidences: list[dict]) -> None:
        p = self._path(product)
        with p.open("w", encoding="utf-8") as f:
            json.dump(evidences, f, ensure_ascii=False, indent=2)

    def save(self, product: str, evidences: list[dict]) -> None:
        """merge 写入:同 evidence_id 用新数据覆盖,旧 ID 保留"""
        if not evidences:
            return
        existing = {e["evidence_id"]: e for e in self._load(product)}
        for ev in evidences:
            existing[ev["evidence_id"]] = ev
        self._dump(product, list(existing.values()))

    def fetch(self, product: str, focus: str) -> list[dict]:
        """返回该产品全部缓存证据,按 TTL 重算 freshness。relevance 留给 Analyzer 判断。
        中文 focus + 英文 snippet 的过滤极不准确,所以这一层不做过滤。"""
        evidences = self._load(product)
        today = date.today()
        out = []
        for ev in evidences:
            obs = ev.get("observed_at")
            ttl = FRESHNESS_TTL_DAYS.get(ev.get("claim_type", ""), 30)
            try:
                age = (today - date.fromisoformat(obs)).days
                ev = {**ev, "source_freshness": "current" if age < ttl else "stale"}
            except (ValueError, TypeError):
                ev = {**ev, "source_freshness": "unknown"}
            out.append(ev)
        return out


# ────────────────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────────────────

class AdapterRegistry:
    """三层兜底:live → cache → mock"""

    def __init__(self, discovered_urls: Optional[dict[str, dict]] = None) -> None:
        """初始化。

        Args:
            discovered_urls: discover_all_urls() 的返回值，
                             {product: {official_pages: [...], pricing_pages: [...]}}
        """
        self.live_adapters: list[SourceAdapter] = []
        # 真实抓取默认开启;DISABLE_LIVE_FETCH=1 关闭
        if os.environ.get("DISABLE_LIVE_FETCH", "").strip() not in ("1", "true", "True"):
            # 合并 discovered URLs: official_pages + pricing_pages
            dynamic: dict[str, list[str]] = {}
            if discovered_urls:
                for product, info in discovered_urls.items():
                    urls = list(info.get("official_pages") or []) + list(info.get("pricing_pages") or [])
                    if urls:
                        dynamic[product] = urls
            self.live_adapters.append(OfficialPageAdapter(dynamic_urls=dynamic or None))
        # Tavily 网络检索:有 TAVILY_API_KEY 即启用(广度补全,与 skills 互补)
        from . import search as _search
        if _search.tavily_available():
            self.live_adapters.append(SearchAdapter())
            print("  [Registry] SearchAdapter 已启用 (TAVILY_API_KEY 存在)")
        # Skills（HN/V2EX 等高价值源,各自环境变量控制）
        self.skills = create_skill_registry()
        self.cache = CacheAdapter()
        self.mock = MockAdapter()

    def fetch_all(self, product: str, focus: str) -> tuple[list[dict], dict]:
        all_evidences: list[dict] = []
        adapter_events: list[dict] = []

        print(f"\n[Registry] === fetch_all({product}) ===")
        print(f"  live_adapters count: {len(self.live_adapters)}")

        # 第一层:实时
        for adapter in self.live_adapters:
            if not adapter.can_fetch(product):
                print(f"  [live] {type(adapter).__name__}.can_fetch({product}) = False")
                continue
            print(f"  [live] {type(adapter).__name__}.can_fetch({product}) = True")
            try:
                evs = adapter.fetch(product, focus)
                print(f"  [live] fetched {len(evs)} evidence from {type(adapter).__name__}")
                for ev in evs:
                    # SearchAdapter 已标 "search";官网抓取默认 "live"
                    ev.setdefault("collection_source", "live")
                all_evidences.extend(evs)
                self.cache.save(product, evs)
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "success",
                    "count": len(evs),
                })
            except Exception as e:
                print(f"  [live] FAILED: {type(e).__name__}: {e}")
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "failed",
                    "reason": str(e),
                    "fallback": "cache",
                })

        # 第 1.5 层: Skills
        for name, skill in self.skills.all().items():
            if not skill.can_execute([product], product=product, focus=focus):
                print(f"  [skill] {name}.can_execute({product}) = False")
                continue
            print(f"  [skill] {name}.can_execute({product}) = True")
            try:
                evs, skill_meta = skill.execute([product], product=product, focus=focus)
                print(f"  [skill] {name} returned {len(evs)} evidence")
                for ev in evs:
                    ev["collection_source"] = f"skill:{name}"
                all_evidences.extend(evs)
                self.cache.save(product, evs)
                adapter_events.append({
                    "adapter": f"skill:{name}",
                    "status": "success",
                    "count": len(evs),
                    "skill_meta": skill_meta,
                })
            except Exception as e:
                print(f"  [skill] {name} FAILED: {type(e).__name__}: {e}")
                adapter_events.append({
                    "adapter": f"skill:{name}",
                    "status": "failed",
                    "reason": str(e),
                })

        # 第二层:缓存
        missing = REQUIRED_CLAIM_TYPES - {e["claim_type"] for e in all_evidences}
        print(f"  missing claim_types after live: {sorted(missing)}")
        if missing and self.cache.can_fetch(product):
            cached = self.cache.fetch(product, focus)
            cache_hits = [e for e in cached if e["claim_type"] in missing]
            for ev in cache_hits:
                if "collection_source" not in ev:
                    ev["collection_source"] = "cache"
            all_evidences.extend(cache_hits)
            print(f"  [cache] patched {len(cache_hits)} evidence")
            adapter_events.append({"adapter": "CacheAdapter", "status": "patched"})

        # 第三层:Mock
        still_missing = REQUIRED_CLAIM_TYPES - {e["claim_type"] for e in all_evidences}
        print(f"  missing claim_types after cache: {sorted(still_missing)}")
        if still_missing and self.mock.can_fetch(product):
            mock_evs = self.mock.fetch(product, focus)
            mock_hits = [e for e in mock_evs if e["claim_type"] in still_missing]
            for ev in mock_hits:
                ev["collection_source"] = "mock"
            all_evidences.extend(mock_hits)
            print(f"  [mock] filled {len(mock_hits)} evidence for types: {sorted(still_missing)}")
            adapter_events.append({
                "adapter": "MockAdapter",
                "status": "fallback",
                "filled_types": sorted(still_missing),
            })

        all_evidences = dedupe_evidence(all_evidences)
        for ev in all_evidences:
            if "collection_source" not in ev:
                ev["collection_source"] = "unknown"
        coverage = {
            ct: sum(1 for e in all_evidences if e["claim_type"] == ct)
            for ct in REQUIRED_CLAIM_TYPES
        }
        source_summary = {}
        for ev in all_evidences:
            s = ev.get("collection_source", "unknown")
            source_summary[s] = source_summary.get(s, 0) + 1
        missing_claim_types = sorted(REQUIRED_CLAIM_TYPES - {e["claim_type"] for e in all_evidences})
        health = "ok" if not missing_claim_types else ("empty" if not all_evidences else "partial")
        # 收集本产品的 Tavily 检索事件(哪些查询命中哪些 URL)
        search_events: list[dict] = []
        for adapter in self.live_adapters:
            if isinstance(adapter, SearchAdapter):
                search_events = adapter.events_by_product.get(product, [])
        print(f"  result: {len(all_evidences)} evidence, sources: {source_summary}")
        print(f"  coverage: {coverage}")
        # 实时上报本产品采集完成(api 据此累计证据数,前端不再卡 0)
        _emit_progress(
            phase="fetch",
            status="done",
            product=product,
            evidence_count=len(all_evidences),
            source_counts=source_summary,
            coverage=coverage,
            message=f"{product} 已收集 {len(all_evidences)} 条证据",
        )
        return all_evidences, {
            "adapter_events": adapter_events,
            "coverage": coverage,
            "missing_claim_types": missing_claim_types,
            "health": health,
            "search_events": search_events,
        }


_registry: Optional[AdapterRegistry] = None


def get_registry(discovered_urls: Optional[dict[str, dict]] = None) -> AdapterRegistry:
    global _registry
    if _registry is None:
        _registry = AdapterRegistry(discovered_urls=discovered_urls)
    return _registry


def reset_registry() -> None:
    """重置全局 registry。Streamlit 多次运行时需要调用，避免旧配置残留。"""
    global _registry
    _registry = None


# ────────────────────────────────────────────────────────────────────────────
# Node
# ────────────────────────────────────────────────────────────────────────────

_collector_run_count = 0


def collector_node(state: AgentState) -> AgentState:
    """v2.2.1: 多产品并发 + wall-clock timeout 兜底 + URL Discovery"""
    global _collector_run_count
    _collector_run_count += 1
    run_id = _collector_run_count
    print(f"\n[collector_node] ====== RUN #{run_id} ======")

    meta = state["analysis_meta"]
    products = [meta["target_product"]] + list(meta["competitors"])
    focus = meta["analysis_focus"][0] if meta.get("analysis_focus") else ""

    # Step 0: URL Discovery — 让 LLM 为每个产品找到官网 URL
    print(f"[collector] discovering URLs for {products} ...")
    discovered = discover_all_urls(products)
    for p, info in discovered.items():
        src = info.get("source", "?")
        op = info.get("official_pages", [])
        pp = info.get("pricing_pages", [])
        print(f"  {p}: source={src}, official={len(op)}, pricing={len(pp)}")
        if op:
            for url in op:
                print(f"    official: {url}")
        if pp:
            for url in pp:
                print(f"    pricing:  {url}")

    fetched: list[dict] = []
    collection_meta: dict = {"products": {}, "discovered_urls": discovered}
    reset_registry()  # 每次运行重新创建，避免旧 discovered_urls 残留
    registry = get_registry(discovered_urls=discovered)
    print(f"[collector_node] registry.live_adapters={len(registry.live_adapters)}, products={products}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(registry.fetch_all, p, focus): p for p in products}
        done, not_done = wait(futures.keys(), timeout=80)

        for fut in done:
            product = futures[fut]
            try:
                evs, meta_info = fut.result()
                print(f"  [collector_node] {product} future returned {len(evs)} evidence")
            except Exception as e:
                evs, meta_info = [], {
                    "adapter_events": [{"status": "fatal", "reason": str(e)}],
                    "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
                }
                print(f"  [collector_node] {product} future EXCEPTION: {type(e).__name__}: {e}")
            fetched.extend(evs)
            collection_meta["products"][product] = meta_info

        print(f"  [collector_node] done={len(done)}, not_done={len(not_done)}, fetched={len(fetched)}")

        for fut in not_done:
            product = futures[fut]
            fut.cancel()
            collection_meta["products"][product] = {
                "adapter_events": [
                    {"status": "timeout", "reason": "wall-clock 80s exceeded"}
                ],
                "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
            }

    # 打回时:按 requirements 精准追加
    print(f"\n[collector_node] fetched={len(fetched)}, reject_requirements={state.get('reject_requirements')}")
    if state.get("reject_requirements"):
        merged = patch_by_requirements(
            existing=state.get("raw_evidence") or [],
            new=fetched,
            requirements=state["reject_requirements"],
        )
        print(f"[collector_node] after patch: {len(merged)}")
    else:
        merged = fetched

    merged = dedupe_evidence(merged)
    print(f"[collector_node] after dedupe: {len(merged)}")

    # 输出 debug 文件（cap 之前，展示全部 evidence）
    dump_evidence_debug(merged, run_id=run_id)

    # 每个产品按 confidence 取 top 40
    merged = cap_evidence_per_product(merged)

    return {
        **state,
        "raw_evidence": merged,
        "collection_meta": collection_meta,
        "reject_requirements": None,
        "reject_target": None,
    }
