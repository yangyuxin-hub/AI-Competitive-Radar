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


# 英文产品/英文源:精简关键词(产品名+品类做锚,关键词只点 1-2 个,避免泛词喧宾夺主)
_CLAIM_KEYWORDS = {
    "feature_existence": "features",
    "pricing": "pricing plans",
    "performance_quality": "review",
    "user_pain": "complaints problems",
}
# 中文产品/中文源:用中文关键词,保持 query 语言一致
_CLAIM_KEYWORDS_CN = {
    "feature_existence": "功能",
    "pricing": "价格 定价",
    "performance_quality": "评测 体验",
    "user_pain": "吐槽 问题 缺点",
}

_DOMAINS_YAML = _ROOT / "config" / "domains.yaml"
_domain_cat_cache: Optional[dict] = None


def _domain_category(domain: Optional[str]) -> tuple[str, str]:
    """域 → (英文品类, 中文品类),用于检索消歧。"Linear" 单独是「线性」,
    拼上 "project management" 才锚定成软件产品,挡掉线性回归/通用页面。"""
    global _domain_cat_cache
    if _domain_cat_cache is None:
        _domain_cat_cache = {}
        try:
            cfg = yaml.safe_load(_DOMAINS_YAML.read_text(encoding="utf-8")) or {}
            for k, v in (cfg.get("domains") or {}).items():
                _domain_cat_cache[k] = (v.get("en_name", "") or "", v.get("name", "") or "")
        except Exception:  # noqa: BLE001
            _domain_cat_cache = {}
    return _domain_cat_cache.get(domain or "", ("", ""))


def _is_cjk(s: str) -> bool:
    return any("一" <= c <= "鿿" for c in (s or ""))


def _build_query(product: str, focus_kw: str, ct: str, cat_en: str, cat_cn: str) -> str:
    """按产品名语种拼检索词,杜绝中英混搭噪声:
    - 英文产品 → 「产品 + 英文品类 + 英文关键词」(丢中文焦点)
    - 中文产品 → 「产品 + 中文品类 + 中文焦点 + 中文关键词」
    焦点词仅在与产品同语种时加入。"""
    cjk = _is_cjk(product)
    cat = cat_cn if cjk else cat_en
    kw = (_CLAIM_KEYWORDS_CN if cjk else _CLAIM_KEYWORDS).get(ct, "")
    parts = [product]
    if cat:
        parts.append(cat)
    if focus_kw and _is_cjk(focus_kw) == cjk:  # 同语种才加焦点,否则丢弃
        parts.append(focus_kw)
    if kw:
        parts.append(kw)
    return " ".join(p for p in parts if p).strip()

# perf/pain 类证据没配 site 时,默认锁定这些用户真实反馈源(挡通用博客/学术站)
_FALLBACK_SITES = {
    "performance_quality": ["reddit.com", "g2.com"],
    "user_pain": ["reddit.com", "news.ycombinator.com", "g2.com"],
}

_PRODUCTS_YAML = _ROOT / "config" / "products.yaml"
_official_domains_cache: Optional[dict] = None


def _product_official_domains(product: str) -> list[str]:
    """products.yaml 里该产品官网/定价页的域名 —— feature/pricing 检索的最权威出处。"""
    global _official_domains_cache
    if _official_domains_cache is None:
        _official_domains_cache = {}
        try:
            from urllib.parse import urlparse
            cfg = yaml.safe_load(_PRODUCTS_YAML.read_text(encoding="utf-8")) or {}
            for name, p in (cfg.get("products") or {}).items():
                ds: set[str] = set()
                for u in (p.get("official_pages") or []) + (p.get("pricing_pages") or []):
                    h = (urlparse(u).hostname or "").lower()
                    h = h[4:] if h.startswith("www.") else h
                    if h:
                        ds.add(h)
                _official_domains_cache[name] = sorted(ds)
        except Exception:  # noqa: BLE001
            _official_domains_cache = {}
    return _official_domains_cache.get(product, [])


def _sites_for_claim(product: str, ct: str, by_ct: dict) -> list[tuple]:
    """该 claim_type 应优先检索的权威源 (site, source_type, bias)。
    修复:旧实现对所有 claim_type 共用 recs[:N](前几条恰是 feature 的无 site 官网页)
    → 所有查询 site 都空 → 全网裸搜捞回学术站/同名页。现按 claim_type 各取对应源。"""
    out: list[tuple] = []
    seen: set[str] = set()

    def add(site: str, st: str, bias: str) -> None:
        if site and site not in seen:
            seen.add(site)
            out.append((site, st, bias))

    # feature/pricing:官网/定价页域名最权威
    if ct in ("feature_existence", "pricing"):
        st = "official_page" if ct == "feature_existence" else "pricing_page"
        for d in _product_official_domains(product):
            add(d, st, "vendor_claim")
    # config 里该 claim_type 配置的带 site 源(reddit/hn/g2 等)
    for r in (by_ct.get(ct) or []):
        if r.get("site"):
            add(r["site"], r.get("source_type", "web_search"), r.get("bias", "third_party"))
    # perf/pain 仍没 site → 用默认用户反馈源兜底
    for d in _FALLBACK_SITES.get(ct, []):
        add(d, "web_search", "user_generated")
    return out


def _heuristic_plan(product: str, focus: list[str], missing: list[str], recs: list[dict],
                    max_per_claim: int, domain: Optional[str] = None) -> list[dict]:
    """无 LLM 时:按 claim_type 锁定对应权威源做定向查询 + 一条全网兜底。
    定向查询若无召回,search._run_one_query 会自动回退全网(由相关性门过滤农场/离题)。"""
    focus_kw = focus[0] if focus else ""
    cat_en, cat_cn = _domain_category(domain)
    by_ct = load_sources_config().get("by_claim_type") or {}
    queries: list[dict] = []
    for ct in missing:
        base_q = _build_query(product, focus_kw, ct, cat_en, cat_cn)
        sites = _sites_for_claim(product, ct, by_ct)[:max_per_claim]
        for site, st, bias in sites:
            queries.append({
                "claim_type": ct, "query": base_q, "site": site,
                "source_type": st, "bias": bias, "why": "权威源定向",
            })
        # 全网兜底一条:相关性门会滤掉离题/农场,补足定向源覆盖不到的角落
        queries.append({
            "claim_type": ct, "query": base_q, "site": "",
            "source_type": "web_search",
            "bias": (by_ct.get(ct) or [{}])[0].get("bias", "third_party"),
            "why": "全网兜底(相关性门过滤)",
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
    return _heuristic_plan(product, analysis_focus, missing, recs, max_per_claim, domain)


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
