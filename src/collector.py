"""Collector 节点 — 见 docs/design-v2.2.md §五

骨架阶段:只实现 MockAdapter(从 data/sample_sources.json 读)。
真实 OfficialPageAdapter / PricingPageAdapter / RedditAdapter 留到阶段 D。
"""
from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path
from typing import Optional

from .state import AgentState


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
    """打回时:基于结构化 requirements 精准追加证据,不重复塞旧的"""
    existing_ids = {e["evidence_id"] for e in existing}
    needed_types: set[str] = set()
    for r in requirements:
        if r.get("reject_target") != "collector":
            continue
        for ct in r.get("required_claim_types", []) or []:
            needed_types.add(ct)
    patches = [
        e for e in new
        if e["evidence_id"] not in existing_ids
        and e["claim_type"] in needed_types
    ]
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

    def __init__(self, products_config_path: Optional[Path] = None) -> None:
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

    def can_fetch(self, product: str) -> bool:
        return bool(self._urls.get(product))

    def fetch(self, product: str, focus: str) -> list[dict]:
        urls = self._urls.get(product) or []
        evidences: list[dict] = []
        for url in urls:
            try:
                evidences.extend(self._fetch_one(product, url))
            except Exception as e:
                # 不抛,失败的 URL 跳过,Registry 那层会兜底
                print(f"[OfficialPageAdapter] {product} <- {url}: FAIL {e}")
        return evidences

    def _fetch_one(self, product: str, url: str) -> list[dict]:
        html = self._read(url)
        return self._extract(product, url, html)

    def _read(self, url: str) -> str:
        if url.startswith("file://") or url.startswith(("/", ".")) or len(url) >= 2 and url[1] == ":":
            # 本地 fixture
            p = Path(url.replace("file://", "")) if url.startswith("file://") else Path(url)
            return p.read_text(encoding="utf-8")
        # 网络抓取
        import httpx
        headers = {
            "User-Agent": "AICompetitiveRadar/0.1 (+https://github.com/yangyuxin-hub/AI-Competitive-Radar; academic)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0),
                          follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.text

    @staticmethod
    def _extract(product: str, url: str, html: str) -> list[dict]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise RuntimeError("beautifulsoup4 未安装。pip install -r requirements.txt")

        soup = BeautifulSoup(html, "html.parser")
        # 优先取 main,其次 body
        root = soup.find("main") or soup.body or soup
        # 抽取所有 h1/h2/h3/p,过滤短文本
        chunks = []
        for tag in root.find_all(["h1", "h2", "h3", "p", "li"]):
            text = tag.get_text(" ", strip=True)
            if 20 <= len(text) <= 300:
                chunks.append(text)

        from datetime import datetime
        observed = datetime.now().strftime("%Y-%m-%d")
        out: list[dict] = []
        # 取前 5 段作为 evidence(避免过载)
        for snippet in chunks[:5]:
            claim = snippet if len(snippet) <= 80 else snippet[:77] + "..."
            eid = generate_evidence_id(product, url, claim)
            out.append({
                "evidence_id": eid,
                "product": product,
                "claim_type": "feature_existence",
                "source_type": "official_page",
                "source_bias": "vendor_claim",
                "source_url": url,
                "observed_at": observed,
                "source_freshness": "current",
                "claim": claim,
                "extracted_snippet": snippet,
                "source_reliability": 0.85,
                "claim_relevance": 0.70,
                "evidence_confidence": 0.60,
            })
        return out


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
    """三层兜底:live → cache → mock。骨架阶段只挂 cache + mock(live 为空)"""

    def __init__(self) -> None:
        self.live_adapters: list[SourceAdapter] = []
        # 真实抓取默认关闭(避免 demo 时网络抖动);ENABLE_LIVE_FETCH=1 启用
        if os.environ.get("ENABLE_LIVE_FETCH", "").strip() in ("1", "true", "True"):
            self.live_adapters.append(OfficialPageAdapter())
        self.cache = CacheAdapter()
        self.mock = MockAdapter()

    def fetch_all(self, product: str, focus: str) -> tuple[list[dict], dict]:
        all_evidences: list[dict] = []
        adapter_events: list[dict] = []

        # 第一层:实时(骨架阶段为空)
        for adapter in self.live_adapters:
            if not adapter.can_fetch(product):
                continue
            try:
                evs = adapter.fetch(product, focus)
                all_evidences.extend(evs)
                self.cache.save(product, evs)
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "success",
                    "count": len(evs),
                })
            except Exception as e:
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "failed",
                    "reason": str(e),
                    "fallback": "cache",
                })

        # 第二层:缓存
        missing = REQUIRED_CLAIM_TYPES - {e["claim_type"] for e in all_evidences}
        if missing and self.cache.can_fetch(product):
            cached = self.cache.fetch(product, focus)
            all_evidences.extend(e for e in cached if e["claim_type"] in missing)
            adapter_events.append({"adapter": "CacheAdapter", "status": "patched"})

        # 第三层:Mock
        still_missing = REQUIRED_CLAIM_TYPES - {e["claim_type"] for e in all_evidences}
        if still_missing and self.mock.can_fetch(product):
            mock_evs = self.mock.fetch(product, focus)
            all_evidences.extend(e for e in mock_evs if e["claim_type"] in still_missing)
            adapter_events.append({
                "adapter": "MockAdapter",
                "status": "fallback",
                "filled_types": sorted(still_missing),
            })

        all_evidences = dedupe_evidence(all_evidences)
        coverage = {
            ct: sum(1 for e in all_evidences if e["claim_type"] == ct)
            for ct in REQUIRED_CLAIM_TYPES
        }
        return all_evidences, {
            "adapter_events": adapter_events,
            "coverage": coverage,
        }


_registry: Optional[AdapterRegistry] = None


def get_registry() -> AdapterRegistry:
    global _registry
    if _registry is None:
        _registry = AdapterRegistry()
    return _registry


# ────────────────────────────────────────────────────────────────────────────
# Node
# ────────────────────────────────────────────────────────────────────────────

def collector_node(state: AgentState) -> AgentState:
    """v2.2.1: 多产品并发 + wall-clock timeout 兜底"""
    meta = state["analysis_meta"]
    products = [meta["target_product"]] + list(meta["competitors"])
    focus = meta["analysis_focus"][0] if meta.get("analysis_focus") else ""

    fetched: list[dict] = []
    collection_meta: dict = {"products": {}}
    registry = get_registry()

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(registry.fetch_all, p, focus): p for p in products}
        done, not_done = wait(futures.keys(), timeout=25)

        for fut in done:
            product = futures[fut]
            try:
                evs, meta_info = fut.result()
            except Exception as e:
                evs, meta_info = [], {
                    "adapter_events": [{"status": "fatal", "reason": str(e)}],
                    "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
                }
            fetched.extend(evs)
            collection_meta["products"][product] = meta_info

        for fut in not_done:
            product = futures[fut]
            fut.cancel()
            collection_meta["products"][product] = {
                "adapter_events": [
                    {"status": "timeout", "reason": "wall-clock 25s exceeded"}
                ],
                "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
            }

    # 打回时:按 requirements 精准追加
    if state.get("reject_requirements"):
        merged = patch_by_requirements(
            existing=state.get("raw_evidence") or [],
            new=fetched,
            requirements=state["reject_requirements"],
        )
    else:
        merged = fetched

    merged = dedupe_evidence(merged)

    return {
        **state,
        "raw_evidence": merged,
        "collection_meta": collection_meta,
        "reject_requirements": None,
        "reject_target": None,
    }
