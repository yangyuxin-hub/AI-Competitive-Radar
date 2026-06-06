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
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime
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


def _search_url_candidates(product: str, max_results: int = 6) -> list[dict]:
    """给 url_discovery 接地的真实候选:搜「X official site」「X pricing」,返回 [{title,url}]。
    让 LLM 从真实搜索结果里挑官方域名,而非凭产品名瞎猜(否则会抓回 Uber/无关站当官网)。
    无搜索能力时返回 [](LLM 仍可保守返回空,胜过编造)。"""
    try:
        from . import search
        if not search.search_available():
            return []
        out: list[dict] = []
        seen: set = set()
        for q in (f"{product} official site", f"{product} pricing"):
            try:
                for r in search.web_search(q, max_results=max_results):
                    url = (r.get("url") or "").strip()
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    out.append({"title": (r.get("title") or "").strip()[:80], "url": url})
            except Exception:  # noqa: BLE001
                continue
        return out[:12]
    except Exception:  # noqa: BLE001
        return []


def discover_urls(product: str, products_config: Optional[dict] = None, allow_llm: bool = True) -> dict:
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
    if is_mock_mode() or not allow_llm:
        return {"official_pages": [], "pricing_pages": [], "source": "skipped"}

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

    payload = {"product": product, "language": "en",
               "search_results": _search_url_candidates(product)}
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


def discover_all_urls(products: list[str], allow_llm: bool = True) -> dict[str, dict]:
    """批量发现多个产品的 URL。返回 {product: {official_pages, pricing_pages, source}}

    使用 ThreadPoolExecutor 并发执行。
    """
    cfg = _load_products_config()
    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(discover_urls, p, cfg, allow_llm): p for p in products}
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

RUNTIME_PROFILES = {
    "fast": {
        "live": False,
        "search": False,
        "skills": False,
        "cache": True,
        "url_discovery_llm": False,
        "timeout_sec": 25,
        "max_evidence_per_product": 24,
    },
    "balanced": {
        "live": True,
        "search": True,
        "skills": False,
        "cache": True,
        "url_discovery_llm": False,
        # 官网 SPA(Playwright 渲染大页)抓取较慢,45s 在多产品时易整体超时丢官网证据 → 放宽到 70s
        "timeout_sec": 70,
        "max_evidence_per_product": 32,
    },
    "deep": {
        "live": True,
        "search": True,
        "skills": True,
        "cache": True,
        "url_discovery_llm": True,
        # 技能(HN/V2EX/问卷) + DDG 串行限速较慢,80s 易整体超时→fetched=0;放宽到 180s 让其完成
        "timeout_sec": 180,
        "max_evidence_per_product": 40,
    },
}


def runtime_settings(profile: Optional[str]) -> dict:
    name = (profile or "balanced").strip().lower()
    return {**RUNTIME_PROFILES["balanced"], **RUNTIME_PROFILES.get(name, {})}


def cap_evidence_per_product(evidences: list[dict], limit: int = MAX_EVIDENCE_PER_PRODUCT) -> list[dict]:
    """每个产品截顶,但**按 claim_type 均衡**保留,避免高置信的官网/HN 证据
    把低置信的 UGC(Tavily user_pain)整类挤掉 —— 保证 4 类诉求都有代表。

    每个产品每个 claim_type 取 top (limit // 4) 条(按置信度);若某类不足,
    余额回填给其他类,既控总量又保覆盖。
    """
    per_type = max(1, limit // len(REQUIRED_CLAIM_TYPES))
    by_product: dict[str, list[dict]] = {}
    for ev in evidences:
        by_product.setdefault(ev.get("product", "unknown"), []).append(ev)

    result: list[dict] = []
    for product, items in by_product.items():
        if len(items) <= limit:
            result.extend(items)
            continue
        by_ct: dict[str, list[dict]] = {}
        for ev in items:
            by_ct.setdefault(ev.get("claim_type", "?"), []).append(ev)
        for lst in by_ct.values():
            lst.sort(key=lambda e: e.get("evidence_confidence", 0), reverse=True)
        kept: list[dict] = []
        # 第一轮:每类取 top per_type
        for ct, lst in by_ct.items():
            kept.extend(lst[:per_type])
        # 第二轮:还有余额则按置信度从各类剩余里回填
        if len(kept) < limit:
            rest = sorted(
                (e for ct, lst in by_ct.items() for e in lst[per_type:]),
                key=lambda e: e.get("evidence_confidence", 0), reverse=True,
            )
            kept.extend(rest[: limit - len(kept)])
        print(f"  [cap] {product}: {len(items)} → {len(kept)} (按 claim_type 均衡)")
        result.extend(kept)
    return result


_debug_file_path: Optional[Path] = None


def reset_debug_file() -> None:
    """重置 debug 文件路径（每次 graph 执行前调用）"""
    global _debug_file_path
    _debug_file_path = None


def dump_evidence_debug(evidences: list[dict], path: Optional[Path] = None, run_id: int = 0) -> Path:
    """将 evidence 输出到 data/debug/ 下的 debug 文件，按产品分组，跨 run 追加"""
    global _debug_file_path

    print(f"  [debug] dump_evidence_debug called with {len(evidences)} items (run #{run_id})")

    # 首次调用时创建文件路径，后续复用（追加）
    # 落到 data/debug/（已 gitignore），不再污染 data/ 根目录与 git status
    if path is None:
        if _debug_file_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _debug_file_path = _ROOT / "data" / "debug" / f"evidence_debug_{ts}.json"
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


# claim_type 推断:加权关键词打分(替代裸 if/elif 首命中,减少误分)。
_CT_KEYWORDS = {
    "pricing": ("price", "pricing", "per month", "per year", "per user", "/mo", "/user",
                "plan", "plans", "subscription", "billed", "free tier", "定价", "套餐", "免费"),
    "performance_quality": ("fast", "speed", "latency", "performance", "benchmark",
                            "accuracy", "accurate", "reliable", "responsive", "throughput"),
    "user_pain": ("issue", "bug", "problem", "frustrat", "complain", "slow", "crash",
                  "annoying", "lacks", "missing", "cannot", "can't", "fails", "broken", "buggy"),
    "feature_existence": ("support", "feature", "enable", "allow", "provide", "offer",
                          "integrate", "build", "create", "generate", "automate", "collaborate"),
}
# pricing 的真实价格信号词(光出现 plan/free 不算定价,得有币种价或周期价)
_PRICE_SIGNAL_WORDS = ("per month", "per user", "per year", "/mo", "/user", "/yr")


def infer_claim_type(snippet: str, default: str, price_re) -> str:
    """加权关键词打分推断 claim_type(LLM 不可用时的兜底)。pricing 须有真实价格信号兜底,
    避免把含 'plan'/'free' 的功能段误判成定价。无任何信号 → 回退 default。"""
    low = (snippet or "").lower()
    scores = {ct: sum(low.count(kw) for kw in kws) for ct, kws in _CT_KEYWORDS.items()}
    has_price = bool(price_re.search(snippet or "")) or any(w in low for w in _PRICE_SIGNAL_WORDS)
    if not has_price:
        scores["pricing"] = 0  # 没有真实价格信号 → 不判为定价
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else default


_ALLOWED_CT = {"feature_existence", "pricing", "performance_quality", "user_pain"}


def _claim_llm_enabled() -> bool:
    if os.environ.get("COLLECTOR_LLM_CLAIM", "1").strip() in ("0", "false", "False"):
        return False
    try:
        from .llm import is_mock_mode
    except Exception:  # noqa: BLE001
        return False
    return not is_mock_mode() and bool(os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY"))


def classify_claim_types_llm(snippets: list[str]) -> Optional[list[Optional[str]]]:
    """批量给证据片段判 claim_type:一次 LLM 调用,任何语言/行业自适应(替代关键词规则)。
    返回与输入等长的标签列表(某条判不准则 None,由上层回退关键词);整体失败返回 None。"""
    if not snippets:
        return []
    try:
        from .llm import get_llm
        payload = {"snippets": [{"id": i, "text": (s or "")[:200]} for i, s in enumerate(snippets)]}
        sys = (
            "你是证据分类器。把每段产品资料片段归到唯一 claim_type(语言不限):\n"
            "- feature_existence:描述产品具备什么功能/能力\n"
            "- pricing:价格/档位/计费/免费额度/套餐\n"
            "- performance_quality:性能或质量评价(快慢/准确/稳定/好用)\n"
            "- user_pain:用户抱怨/缺陷/痛点/不满\n"
            '只输出 JSON: {"labels":[{"id":0,"claim_type":"feature_existence"}, ...]},每段一条。'
        )
        out = get_llm().call_json(sys, payload, label="collector:claim_type",
                                  max_tokens=1024, timeout=float(os.environ.get("CLAIM_LLM_TIMEOUT", "60")))
        by_id = {d.get("id"): d.get("claim_type") for d in (out.get("labels") or []) if isinstance(d, dict)}
        return [by_id.get(i) if by_id.get(i) in _ALLOWED_CT else None for i in range(len(snippets))]
    except Exception as e:  # noqa: BLE001 — 失败回退关键词,不阻断采集
        print(f"[collector] LLM claim_type 分类失败,回退关键词: {type(e).__name__}: {e}")
        return None


def _reclassify_official_claim_types(evidence: list[dict]) -> int:
    """采集收尾:对官网证据的 claim_type 做一次批量 LLM 精分类(替代关键词,任何语言/行业自适应)。
    放在抓取之后、不在 timed fetch 里 → 不拖慢采集、不触发超时。失败/无 LLM 保留关键词标签。
    返回被修正的条数。"""
    if not _claim_llm_enabled():
        return 0
    idxs = [i for i, e in enumerate(evidence) if e.get("source_type") == "official_page"]
    if not idxs:
        return 0
    changed = 0
    batch = int(os.environ.get("CLAIM_LLM_BATCH", "40"))
    for s in range(0, len(idxs), batch):
        chunk = idxs[s:s + batch]
        labels = classify_claim_types_llm(
            [(evidence[i].get("extracted_snippet") or evidence[i].get("claim") or "") for i in chunk])
        if not labels:
            continue
        for j, i in enumerate(chunk):
            if labels[j] and labels[j] != evidence[i].get("claim_type"):
                evidence[i]["claim_type"] = labels[j]
                changed += 1
    if changed:
        print(f"[collector] 官网证据 claim_type LLM 批量精分类:修正 {changed}/{len(idxs)} 条")
    return changed


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

        # 路径 1:可见 DOM 卡片
        try:
            for node in soup.find_all(string=OfficialPageAdapter._PRICE_RE):
                cur = node.parent
                for _ in range(4):  # 上溯至多 4 层找到含价的卡片容器
                    if cur is None:
                        break
                    txt = cur.get_text(" ", strip=True)
                    if 8 <= len(txt) <= 220 and OfficialPageAdapter._PRICE_RE.search(txt):
                        _add(txt)
                        break
                    cur = cur.parent
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

    def __init__(self, discovered_urls: Optional[dict[str, dict]] = None,
                 runtime_profile: str = "balanced") -> None:
        """初始化。

        Args:
            discovered_urls: discover_all_urls() 的返回值，
                             {product: {official_pages: [...], pricing_pages: [...]}}
        """
        settings = runtime_settings(runtime_profile)
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
            self.live_adapters.append(SearchAdapter())
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
                 runtime_profile: str = "balanced") -> AdapterRegistry:
    global _registry
    if _registry is None:
        _registry = AdapterRegistry(discovered_urls=discovered_urls, runtime_profile=runtime_profile)
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
    runtime_profile = meta.get("runtime_profile") or "balanced"
    settings = runtime_settings(runtime_profile)

    # Step 0: URL Discovery — 让 LLM 自主为每个产品找官网/定价页 URL。
    # 官网是最权威证据源(功能/定价),不能只靠 products.yaml 硬编码 → LLM 发现**默认开启**,
    # 任意新产品都让 LLM(带 web 搜索候选)找官网;mock/无 key/显式 URL_DISCOVERY_LLM=0 才退回配置或跳过。
    from .llm import is_mock_mode as _is_mock
    print(f"[collector] discovering URLs for {products} ...")
    _url_env = os.environ.get("URL_DISCOVERY_LLM", "").strip()
    allow_url_llm = (
        _url_env not in ("0", "false", "False")
        and not _is_mock()
        and bool(os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY"))
    )
    discovered = discover_all_urls(products, allow_llm=allow_url_llm)
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

    # 把发现的官网域名注入 source_planner → 未配置产品的 feature/pricing 检索也锚定官网(与已配置拉平)
    try:
        from urllib.parse import urlparse
        from . import source_planner as _sp
        _disc_domains: dict[str, list[str]] = {}
        for p, info in discovered.items():
            doms: list[str] = []
            for u in (info.get("official_pages") or []) + (info.get("pricing_pages") or []):
                h = (urlparse(u).hostname or "").lower()
                h = h[4:] if h.startswith("www.") else h
                if h and h not in doms:
                    doms.append(h)
            if doms:
                _disc_domains[p] = doms
        _sp.set_discovered_domains(_disc_domains)
        if _disc_domains:
            print(f"[collector] 注入官网域名锚定: {_disc_domains}")
    except Exception as e:  # noqa: BLE001
        print(f"[collector] 注入官网域名失败(忽略): {type(e).__name__}: {e}")

    fetched: list[dict] = []
    collection_meta: dict = {
        "products": {},
        "discovered_urls": discovered,
        "runtime_profile": runtime_profile,
    }
    reset_registry()  # 每次运行重新创建，避免旧 discovered_urls 残留
    registry = get_registry(discovered_urls=discovered, runtime_profile=runtime_profile)
    print(f"[collector_node] registry.live_adapters={len(registry.live_adapters)}, products={products}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(registry.fetch_all, p, focus): p for p in products}
        timeout_sec = int(settings["timeout_sec"])
        done, not_done = wait(futures.keys(), timeout=timeout_sec)

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
                    {"status": "timeout", "reason": f"wall-clock {timeout_sec}s exceeded"}
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

    if os.environ.get("DUMP_FULL_EVIDENCE", "").strip() in ("1", "true", "True"):
        dump_evidence_debug(merged, run_id=run_id)

    # 每个产品按 confidence 取 top N；不同运行模式控制 prompt 体积与等待时间
    merged = cap_evidence_per_product(merged, limit=int(settings["max_evidence_per_product"]))

    # 官网证据 claim_type 批量 LLM 精分类(抓取已完成,不影响超时)
    _reclassify_official_claim_types(merged)
    dump_evidence_debug(merged, run_id=run_id)

    # ── 官网获取 check:官网是最权威源(功能/定价大量靠它),逐产品核对到底抓到没有,没抓到明确报红 ──
    official_check: dict = {}
    for product in products:
        n_off = sum(1 for e in merged
                    if e.get("product") == product and e.get("source_type") == "official_page")
        disc = discovered.get(product) or {}
        had_urls = bool(disc.get("official_pages") or disc.get("pricing_pages"))
        ok = n_off > 0
        official_check[product] = {
            "official_evidence": n_off, "had_urls": had_urls,
            "url_source": disc.get("source", "?"), "ok": ok,
        }
        flag = "✓" if ok else "🚩"
        print(f"[collector][官网check] {flag} {product}: 官网证据 {n_off} 条 "
              f"(URL来源={disc.get('source','?')}, 有URL={had_urls})")
        if not ok and had_urls:
            print(f"  🚩 {product} 找到了官网 URL 却 0 条官网证据 → 抓取/渲染失败,需排查")
        elif not ok:
            print(f"  🚩 {product} 没找到官网 URL → URL 发现失败(LLM/搜索没命中)")
    collection_meta["official_check"] = official_check
    n_missing = sum(1 for v in official_check.values() if not v["ok"])
    if n_missing:
        print(f"[collector][官网check] ⚠️ {n_missing}/{len(products)} 个产品无官网证据,影响功能/定价权威性")

    return {
        **state,
        "raw_evidence": merged,
        "collection_meta": collection_meta,
        "reject_requirements": None,
        "reject_target": None,
    }
