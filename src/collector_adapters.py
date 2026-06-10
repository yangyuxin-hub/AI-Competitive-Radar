"""Collector 采集适配器 + AdapterRegistry — 三层 DAG 中间层。

依赖 collector_common 的 helper/常量/进度(单向,无环)。OfficialPage/Search/Mock/Cache 四适配器
+ 注册表三层降级(live→cache→mock)。collector.py re-export 适配器类与 get/reset_registry。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional
from . import scoring_config
from .skill import create_skill_registry
from .collector_common import (
    FRESHNESS_TTL_DAYS,
    REQUIRED_CLAIM_TYPES,
    _CACHE_DIR,
    _ROOT,
    _emit_progress,
    _reclassify_official_claim_types,
    _resolve_sample_path,
    cap_evidence_per_product,
    dedupe_evidence,
    discover_all_urls,
    generate_evidence_id,
    infer_claim_type,
    runtime_settings,
)


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
                # 官网页 + 定价页都要抓:定价页是价格最权威出处,旧版漏加导致定价只能靠二手聚合站
                urls = list(p.get("official_pages") or []) + list(p.get("pricing_pages") or [])
                seen: set[str] = set()
                self._urls[name] = [u for u in urls if not (u in seen or seen.add(u))]
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

    @staticmethod
    def _is_pricing_url(url: str) -> bool:
        u = url.lower()
        return "pric" in u or "plan" in u

    @classmethod
    def _has_price_evidence(cls, evidences: list[dict]) -> bool:
        """结果里是否含真实价格信号(价格 token 或显式 Free/联系销售档)。
        定价页抓到一堆导航/页脚却没价 → 视为"没真抓到",触发 JS 渲染兜底。"""
        for ev in evidences:
            txt = ev.get("claim", "") or ev.get("extracted_snippet", "")
            low = txt.lower()
            if cls._PRICE_RE.search(txt) or any(
                w in low for w in ("free", "per month", "per user", "/mo", "/user", "contact sales", "免费")
            ):
                return True
        return False

    def _fetch_one(self, product: str, url: str) -> list[dict]:
        print(f"  [OfficialPageAdapter] fetching {url} ...")
        html = self._read(url)
        print(f"  [OfficialPageAdapter] got {len(html)} chars from {url}")
        result = self._extract(product, url, html)
        print(f"  [OfficialPageAdapter] extracted {len(result)} evidence from {url}")

        # SPA 兜底: 用 Playwright 渲染 JS 重试。两种触发条件——
        #   1) httpx 一条都没抓到(普通页空壳);
        #   2) 定价页抓到了 chunk 但没有任何价格信号(导航/页脚噪声,真实档位价是 JS 渲染的)。
        # 条件 2 是关键:旧版只判 `not result`,定价页只要有页脚废话就永不触发,价格永远抓不到。
        needs_render = url.startswith("http") and (
            not result or (self._is_pricing_url(url) and not self._has_price_evidence(result))
        )
        if needs_render:
            reason = "0 evidence" if not result else "定价页无价格信号"
            print(f"  [OfficialPageAdapter] {reason}, retrying with Playwright ...")
            try:
                html_pw = self._read_playwright(url)
                print(f"  [OfficialPageAdapter] Playwright got {len(html_pw)} chars from {url}")
                rendered = self._extract(product, url, html_pw)
                print(f"  [OfficialPageAdapter] Playwright extracted {len(rendered)} evidence from {url}")
                # 渲染结果更优(有价格信号 / 更多证据)才替换,避免渲染失败反而清空已有证据
                if rendered and (self._has_price_evidence(rendered) or len(rendered) >= len(result)):
                    result = rendered
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
                page.wait_for_timeout(int(os.environ.get("PLAYWRIGHT_RENDER_WAIT_MS", "1200")))
                html = page.content()
                return html
            finally:
                browser.close()

    # 价格 token:符号价($10/￥99/€13.49)+ 无符号货币码价(10 USD / USD 10)。
    # 很多定价页(尤其非美区)不带货币符号,旧版只认 $￥€£ 会整页漏价。
    _PRICE_RE = re.compile(
        r"[\$￥€£]\s?\d[\d.,]*"
        r"|\b\d[\d.,]*\s?(?:USD|EUR|GBP|CNY|RMB)\b"
        r"|\b(?:USD|EUR|GBP|CNY|RMB)\s?\d[\d.,]*",
        re.IGNORECASE,
    )

    @staticmethod
    def _price_snippets(soup, html: str) -> list[str]:
        """定价页专用:抓真实档位价。两条路径互补——
        1) 可见 DOM:对每个含价格 token 的文本节点,上溯到「卡片级」容器(8~220 字符),
           得到「Plus $10 per user/month …」这类 plan名+价 的片段(突破 40 字符门槛);
        2) 内嵌 JSON(__NEXT_DATA__ / ld+json):Notion/Asana 把价放 JSON 里,DOM 兜不住时补。
        返回去重后的短片段列表(供 _extract 置顶为 pricing 证据)。"""
        snips: list[str] = []
        seen: set[str] = set()

        def _add(t: str) -> None:
            t = (t or "").strip()
            if not t or len(t) > 220 or not OfficialPageAdapter._PRICE_RE.search(t):
                return
            key = t[:120]
            if key not in seen:
                seen.add(key)
                snips.append(t)

        # 档位名标题里要排除的非档名短标题(价格切换/导航类),避免误把它当档名
        _NAME_NOISE = ("pricing", "plans", "compare", "faq", "billing", "save",
                       "month", "year", "annual", "per user", "vs ")

        # 路径 1:可见 DOM 卡片。价格节点向上(≤6层)同时找两样东西——
        #   a) 最小「含价容器」(8~220字)→ 价格行文本;
        #   b) 最近的「含短标题(h1-h4,2~30字)的祖先」→ 该档位名。
        # 关键:档位名 <h3>(如 Starter/Personal)是价格节点的兄弟、不在最小含价容器内,
        # 旧版只上溯 4 层 + 卡 220 字 → 够不到档位卡(往往 1000+ 字)→ 价有名丢。
        # 通用做法:不写死档名,只认「定价卡自带的短标题」,适配任何产品定价页。
        try:
            for node in soup.find_all(string=OfficialPageAdapter._PRICE_RE):
                price_txt = ""
                tier_name = ""
                cur = node.parent
                for _ in range(6):
                    if cur is None:
                        break
                    txt = cur.get_text(" ", strip=True)
                    if not price_txt and 8 <= len(txt) <= 220 and OfficialPageAdapter._PRICE_RE.search(txt):
                        price_txt = txt
                    if not tier_name:
                        for h in cur.find_all(["h1", "h2", "h3", "h4"]):
                            ht = h.get_text(" ", strip=True)
                            if 2 <= len(ht) <= 30 and not any(w in ht.lower() for w in _NAME_NOISE):
                                tier_name = ht
                                break
                    if price_txt and tier_name:
                        break
                    cur = cur.parent
                if not price_txt:
                    continue
                if tier_name and tier_name.lower() not in price_txt.lower():
                    combined = f"{tier_name} · {price_txt}"
                    _add(combined if len(combined) <= 220 else price_txt)
                else:
                    _add(price_txt)
        except Exception:  # noqa: BLE001 — 提取尽力而为,失败不阻断
            pass

        # 路径 2:内嵌 JSON 里的 price/amount + 货币
        try:
            for sc in soup.find_all("script"):
                raw = sc.string or sc.get_text() or ""
                if not raw or ('"price"' not in raw and '"amount"' not in raw and "priceCurrency" not in raw):
                    continue
                for m in re.finditer(
                    r'"(?:name|tier|plan|title)"\s*:\s*"([^"]{1,40})"[^{}]{0,200}?'
                    r'"(?:price|amount)"\s*:\s*"?(\d[\d.]*)"?', raw):
                    _add(f"{m.group(1)}: ${m.group(2)}")
                if len(snips) >= 12:
                    break
        except Exception:  # noqa: BLE001
            pass

        return snips[:12]

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

        # 定价页专用:价格藏在短 <span>$10</span> 或内嵌 JSON,会被上面 40 字符门槛漏掉。
        # 单独抓「含价格 token 的卡片级文本」放到最前,确保真实档位价进证据。
        if default_claim_type == "pricing":
            price_snips = OfficialPageAdapter._price_snippets(soup, html)
            chunks = price_snips + [c for c in chunks if c not in price_snips]

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

        observed = datetime.now().strftime("%Y-%m-%d")
        out: list[dict] = []
        # claim_type 用关键词快速定标(抓取阶段要快,不在 timed fetch 里塞 LLM 调用——否则 SPA 大页
        # 叠加 25s/页 的 LLM 调用会冲爆 wall-clock 超时,官网证据反被砍。LLM 精分类移到 collector_node
        # 收尾做一次批量(见 _reclassify_official_claim_types),不阻塞抓取。
        for idx, snippet in enumerate(chunks[:15]):
            claim = snippet if len(snippet) <= 120 else snippet[:117] + "..."
            # 加入 idx 避免同一页面内相同文本段产生相同 evidence_id
            eid = generate_evidence_id(product, f"{url}#{idx}", claim)

            claim_type = infer_claim_type(snippet, default_claim_type, OfficialPageAdapter._PRICE_RE)

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
                "source_freshness": "unknown",  # A1: 官网无发布时间，标 unknown
                "claim": claim,
                "extracted_snippet": snippet,
                "source_reliability": scoring_config.reliability("official_page", 0.85),
                "claim_relevance": scoring_config.get("claim_relevance_prior", "official_page", 0.75),
                "evidence_confidence": round(conf, 2),
            })
        return out


class SearchAdapter(SourceAdapter):
    """Tavily 网络搜索 — 自主规划「该去哪搜」并真实抓取 UGC / 第三方来源。

    与 HN/V2EX skills 互补:skills 用平台官方 API 抓特定高价值源(精度),
    SearchAdapter 用 Tavily 广撒网覆盖任意站 + 全 4 类证据(广度)。
    仅在 TAVILY_API_KEY 存在时激活。规划见 source_planner，抓取见 search。
    """

    def __init__(self, domain: Optional[str] = None, evidence_plan: Optional[dict] = None) -> None:
        self.domain = domain or os.environ.get("DOMAIN")
        self.evidence_plan = evidence_plan or {}
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
            evidence_plan=self.evidence_plan,
        )
        # 实时吐出「该去搜什么」——每个产品一行人话,只说产品+证据类型,
        # 不堆原始 query 串和内部规划术语(详细 query/URL 在完成卡的「本步产出」里看)。
        _CT_CN = {
            "feature_existence": "功能",
            "performance_quality": "体验",
            "pricing": "定价",
            "user_pain": "痛点",
        }
        _seen_ct: list[str] = []
        for q in plan:
            ct = _CT_CN.get(q.get("claim_type"), q.get("claim_type") or "")
            if ct and ct not in _seen_ct:
                _seen_ct.append(ct)
        if _seen_ct:
            _emit_progress(
                phase="plan_decision",
                product=product,
                message=f"🔍 联网检索 {product} 的{' / '.join(_seen_ct)}证据",
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

    def __init__(self, cache_dir: Optional[Path] = None, enabled: bool = True) -> None:
        self.disabled = (
            not enabled
            or os.environ.get("DISABLE_CACHE", "").strip() in ("1", "true", "True")
        )
        self.cache_dir = cache_dir or Path(os.environ.get("CACHE_DIR", str(_CACHE_DIR)))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, product: str) -> Path:
        # 防路径注入
        safe = "".join(c for c in product if c.isalnum() or c in "._-")
        return self.cache_dir / f"{safe}.json"

    def can_fetch(self, product: str) -> bool:
        if self.disabled:
            return False
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
        if self.disabled or not evidences:
            return
        existing = {e["evidence_id"]: e for e in self._load(product)}
        for ev in evidences:
            existing[ev["evidence_id"]] = ev
        self._dump(product, list(existing.values()))

    def fetch(self, product: str, focus: str) -> list[dict]:
        """返回该产品全部缓存证据,按 TTL 重算 freshness。优先用 published_at,回退 observed_at。"""
        from .collector_common import compute_freshness
        evidences = self._load(product)
        out = []
        for ev in evidences:
            ct = ev.get("claim_type", "")
            # A1: 优先用发布时间，回退到抓取时间
            date_str = ev.get("published_at") or ev.get("observed_at")
            ev = {**ev, "source_freshness": compute_freshness(date_str, ct)}
            out.append(ev)
        return out


# ────────────────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────────────────

class AdapterRegistry:
    """三层兜底:live → cache → mock"""

    def __init__(self, discovered_urls: Optional[dict[str, dict]] = None,
                 runtime_profile: str = "deep",
                 evidence_plan: Optional[dict] = None) -> None:
        """初始化。

        Args:
            discovered_urls: discover_all_urls() 的返回值，
                             {product: {official_pages: [...], pricing_pages: [...]}}
        """
        settings = runtime_settings(runtime_profile)
        self.evidence_plan = evidence_plan or {}
        try:
            from .evidence_plan import planned_claim_types_from_plan, required_claim_types_from_plan
            self.required_claim_types = set(required_claim_types_from_plan(self.evidence_plan, REQUIRED_CLAIM_TYPES))
            self.planned_claim_types = set(planned_claim_types_from_plan(self.evidence_plan, REQUIRED_CLAIM_TYPES))
        except Exception:  # noqa: BLE001
            self.required_claim_types = set(REQUIRED_CLAIM_TYPES)
            self.planned_claim_types = set(REQUIRED_CLAIM_TYPES)
        self.live_adapters: list[SourceAdapter] = []
        # 真实抓取默认开启;DISABLE_LIVE_FETCH=1 关闭
        live_disabled = (
            not settings["live"]
            or os.environ.get("DISABLE_LIVE_FETCH", "").strip() in ("1", "true", "True")
        )
        if not live_disabled:
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
        if not live_disabled and settings["search"] and _search.tavily_available():
            self.live_adapters.append(SearchAdapter(evidence_plan=self.evidence_plan))
            print("  [Registry] SearchAdapter 已启用 (TAVILY_API_KEY 存在)")
        # Skills（HN/V2EX 等高价值源,各自环境变量控制）
        skills_enabled = (
            bool(settings["skills"])
            or os.environ.get("ENABLE_SKILLS", "").strip() in ("1", "true", "True")
        )
        self.skills = create_skill_registry(enabled=skills_enabled)
        self.cache = CacheAdapter(enabled=bool(settings["cache"]))
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

        # 第 1.5 层: Skills — 并行执行。各 skill 独立做 LLM 关键词+抓取(HN ~14s、V2EX ~17s),
        # 串行会把两者叠加;并行后该产品的 skill 阶段 ≈ max(各 skill)。
        # 结果回主线程后再 extend/cache.save → 规避并发写同一缓存文件的竞态。
        applicable = [
            (name, skill) for name, skill in self.skills.all().items()
            if skill.can_execute([product], product=product, focus=focus)
        ]
        for name, _ in applicable:
            print(f"  [skill] {name}.can_execute({product}) = True")

        def _run_skill(item):
            name, skill = item
            evs, skill_meta = skill.execute([product], product=product, focus=focus)
            return name, evs, skill_meta

        if applicable:
            with ThreadPoolExecutor(max_workers=len(applicable)) as sp:
                sfuts = {sp.submit(_run_skill, it): it[0] for it in applicable}
                for sfut in as_completed(sfuts):
                    name = sfuts[sfut]
                    try:
                        _name, evs, skill_meta = sfut.result()
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
        missing = self.required_claim_types - {e["claim_type"] for e in all_evidences}
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
        still_missing = self.required_claim_types - {e["claim_type"] for e in all_evidences}
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
            for ct in self.planned_claim_types
        }
        source_summary = {}
        for ev in all_evidences:
            s = ev.get("collection_source", "unknown")
            source_summary[s] = source_summary.get(s, 0) + 1
        missing_claim_types = sorted(self.required_claim_types - {e["claim_type"] for e in all_evidences})
        health = "ok" if not missing_claim_types else ("empty" if not all_evidences else "partial")
        # 收集本产品的 Tavily 检索事件(哪些查询命中哪些 URL)
        search_events: list[dict] = []
        for adapter in self.live_adapters:
            if isinstance(adapter, SearchAdapter):
                search_events = adapter.events_by_product.get(product, [])
        print(f"  result: {len(all_evidences)} evidence, sources: {source_summary}")
        print(f"  coverage: {coverage}")
        # 抓到的代表性内容样本(让采集阶段不只显示计数,用户能看到"抓到了什么")
        samples: list[dict] = []
        for ev in all_evidences:
            txt = (ev.get("claim") or ev.get("extracted_snippet") or "").strip().replace("\n", " ")
            if txt:
                samples.append({
                    "product": product,
                    "source": ev.get("collection_source", "?"),
                    "text": txt[:80],
                })
            if len(samples) >= 4:
                break
        # 实时上报本产品采集完成(api 据此累计证据数,前端不再卡 0)
        _emit_progress(
            phase="fetch",
            status="done",
            product=product,
            evidence_count=len(all_evidences),
            source_counts=source_summary,
            coverage=coverage,
            samples=samples,
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


def get_registry(discovered_urls: Optional[dict[str, dict]] = None,
                 runtime_profile: str = "deep",
                 evidence_plan: Optional[dict] = None) -> AdapterRegistry:
    global _registry
    if _registry is None:
        _registry = AdapterRegistry(
            discovered_urls=discovered_urls,
            runtime_profile=runtime_profile,
            evidence_plan=evidence_plan,
        )
    return _registry


def reset_registry() -> None:
    """重置全局 registry。Streamlit 多次运行时需要调用，避免旧配置残留。"""
    global _registry
    _registry = None
