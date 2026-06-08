"""Collector 基座 — 叶子 helper / 常量 / 进度通道 / URL discovery。

三层 DAG 的最底层(collector_common ← collector_adapters ← collector):本模块不依赖
适配器与 node 编排,只提供纯函数与共享状态。collector.py re-export 全部公共名保 back-compat。
进度通道单例置于此,三层共享(api 注册 collector.set_progress_callback 即作用于此)。
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
from .progress import ProgressChannel


# ────────────────────────────────────────────────────────────────────────────
# 进度回调(供 api SSE 实时展示采集思考/进度)
# ────────────────────────────────────────────────────────────────────────────

_PROGRESS = ProgressChannel()


def set_progress_callback(cb: Optional[Callable[[dict], None]]) -> None:
    _PROGRESS.set_callback(cb)


def _emit_progress(**event) -> None:
    _PROGRESS.emit(**event)


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

    # 台账复用(§8.6 读取点):历史学过该产品的官网/定价页 → 直接命中,跳过 LLM 发现(省 deep 档每产品一次 LLM)
    if os.environ.get("LEDGER_REUSE", "1") not in ("0", "false", "False"):
        try:
            from . import source_ledger
            kp = source_ledger.known_pages(product)
            if kp and (kp["official_pages"] or kp["pricing_pages"]):
                print(f"[collector] {product} URL 命中台账(跳过 LLM 发现): "
                      f"official={len(kp['official_pages'])}, pricing={len(kp['pricing_pages'])}")
                return {**kp, "source": "ledger"}
        except Exception as _le:  # noqa: BLE001
            print(f"[collector] 台账查询失败(忽略): {type(_le).__name__}: {_le}")

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
    name = (profile or "deep").strip().lower()
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
        # 质量加权:优先按 quality_score(质量门打的分)保留,无则回退 evidence_confidence
        _q = lambda e: (e.get("quality_score") if e.get("quality_score") is not None
                        else e.get("evidence_confidence", 0))
        for lst in by_ct.values():
            lst.sort(key=_q, reverse=True)
        kept: list[dict] = []
        # 第一轮:每类取 top per_type
        for ct, lst in by_ct.items():
            kept.extend(lst[:per_type])
        # 第二轮:还有余额则按质量分从各类剩余里回填
        if len(kept) < limit:
            rest = sorted(
                (e for ct, lst in by_ct.items() for e in lst[per_type:]),
                key=_q, reverse=True,
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
    "market_signal": ("users", "subscribers", "revenue", "arr", "mrr", "funding", "valuation",
                      "employees", "traffic", "downloads", "lawsuit", "settlement", "partnership"),
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


_ALLOWED_CT = {"feature_existence", "pricing", "performance_quality", "user_pain", "market_signal"}


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
            "- market_signal:用户量/付费用户/收入/融资/估值/员工/法律风险/合作等市场或公司信号\n"
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
