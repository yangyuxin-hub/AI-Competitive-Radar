"""采集质量门（最小实现）— 见 docs/reviewer-reject-design.md §3.6

「好不好」而非「有没有」:
- score_quality(e): 单条证据确定性质量分 [0,1]（主要复用已有字段 + 规则信号）
- audit_pricing_substance: 硬门①定价含金量（有定价证据但无真实价格数值 → gap）
- audit_bias_balance:     硬门②偏置平衡（痛点/性能类全是厂商自夸 → gap）
- annotate_and_audit:     给每条证据写 quality_score + 跑两条硬门，产出质量审查报告

无 LLM、毫秒级，适合插在采集后、交分析前。阈值集中在文件顶部，便于 v2.3 下沉 config。
"""
from __future__ import annotations

import collections
import re
from typing import Iterable, Optional
from urllib.parse import urlparse

from . import scoring_config as _sc

# ── 阈值/权重:集中在 config/scoring.yaml,缺失回退下方默认(见 scoring_config)──
_DEFAULT_WEIGHTS = {"specificity": 0.28, "integrity": 0.16, "relevance": 0.22,
                    "authority": 0.22, "freshness": 0.12}
Q_LOW = _sc.get("evidence_quality", "q_low", 0.40)          # 低于此判为低质（统计用）
Q_GARBAGE = _sc.get("evidence_quality", "q_garbage", 0.25)  # 低于此可丢弃（本最小版只统计）

REQUIRED_TYPES = ("feature_existence", "performance_quality", "pricing", "user_pain")
COVERAGE_MIN = _sc.get("collection_gate", "coverage_min",
                       {
                           "feature_existence": 2,
                           "performance_quality": 1,
                           "pricing": 1,
                           "user_pain": 1,
                           "market_signal": 1,
                       })
TOTAL_MIN = _sc.get("collection_gate", "total_min", 6)       # 每产品总证据下限
OFFICIAL_MIN = _sc.get("collection_gate", "official_min", 1)  # 每产品官网证据下限
COMMUNITY_MIN_QUALITY = _sc.get("collection_gate", "community_min_quality", 0.55)
COMMUNITY_MIN_SNIPPET_LEN = _sc.get("collection_gate", "community_min_snippet_len", 25)
COMMUNITY_REQUIRED_TYPES = ("user_pain", "performance_quality")
_COMMUNITY_SOURCE_TYPES = {"reddit", "hn", "hacker_news", "v2ex", "g2", "community"}
_COMMUNITY_BIASES = {"user_generated", "community_feedback"}
_COMMUNITY_DOMAINS = (
    "reddit.com",
    "news.ycombinator.com",
    "ycombinator.com",
    "v2ex.com",
    "g2.com",
    "zhihu.com",
    "sspai.com",
    "juejin.cn",
    "community.feishu.cn",
)

# ── 词典/正则 ───────────────────────────────────────────────────────────────
# 营销空话(fluff)：堆 slogan 不给事实
_FLUFF = (
    "革命性", "颠覆", "极致", "领先行业", "完美", "无缝", "强大的", "卓越", "一流", "顶级",
    "seamless", "revolutionary", "world-class", "cutting-edge", "game-chang",
    "state-of-the-art", "best-in-class", "unparalleled", "next-generation", "effortless",
)
# 具体性信号：场景/对比/量化
_CONCRETE = (
    "vs", "对比", "相比", "延迟", "ms", "fps", "准确", "版本", "场景", "项目", "团队",
    "报错", "崩溃", "bug", "token", "档", "seat", "用户", "benchmark", "测评", "实测",
)
# 导航/页脚样板（抓到壳没抓到肉）
_BOILER = (
    "skip to content", "cookie", "©", "all rights reserved", "隐私政策",
    "terms of service", "sign in", "log in", "loading", "加载中", "订阅",
)
# 真实价格数值：$9 / 9 美元 / 9/月 / 9/seat / 19.99 usd …
_PRICE_NUM = re.compile(
    r"(\$\s?\d|\d+\s?(?:美元|元|usd|/\s?(?:mo|month|月|seat|user|年|yr|year))|\d+\.\d+\s?(?:美元|元|usd))",
    re.I,
)
_FREE_TIER = ("免费", "free", "$0", "0 美元", "no cost")
_CONTACT_SALES = ("联系销售", "contact sales", "咨询报价", "定制报价", "custom pricing", "敬请期待", "暂未公布")


def _text_of(e: dict) -> str:
    return f"{e.get('claim','')} {e.get('extracted_snippet','')}"


def _has_digit(t: str) -> bool:
    return bool(re.search(r"\d", t or ""))


def _source_host(e: dict) -> str:
    raw = (e.get("source_url") or e.get("url") or "").strip()
    if not raw:
        return ""
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    return host[4:] if host.startswith("www.") else host


def is_community_evidence(e: dict) -> bool:
    source_type = str(e.get("source_type") or "").strip().lower()
    source_bias = str(e.get("source_bias") or "").strip().lower()
    if source_type in _COMMUNITY_SOURCE_TYPES or source_bias in _COMMUNITY_BIASES:
        return True
    host = _source_host(e)
    return any(host == d or host.endswith("." + d) for d in _COMMUNITY_DOMAINS)


def is_high_quality_community(e: dict) -> bool:
    if not is_community_evidence(e):
        return False
    snippet = (e.get("extracted_snippet") or e.get("claim") or "").strip()
    if len(snippet) < COMMUNITY_MIN_SNIPPET_LEN:
        return False
    q = e.get("quality_score")
    if q is None:
        q = score_quality(e)
    return float(q) >= COMMUNITY_MIN_QUALITY


# ── ① 单条质量分 ─────────────────────────────────────────────────────────────
def score_quality(e: dict) -> float:
    """单条证据质量分 [0,1]。确定性信号 + 复用 claim_relevance/source_reliability/freshness。"""
    text = _text_of(e)
    low = text.lower()

    # 信息量/具体性：有数字 + 命中具体词
    spec = 0.0
    if _has_digit(text):
        spec += 0.5
    spec += min(sum(1 for w in _CONCRETE if w.lower() in low) * 0.12, 0.5)
    spec = min(spec, 1.0)

    # 去营销味：命中 fluff 且无数字 → 重扣；有数字 → 轻扣
    fluff_hits = sum(1 for w in _FLUFF if w.lower() in low)
    fluff_penalty = (min(fluff_hits * 0.04, 0.12) if _has_digit(text)
                     else min(fluff_hits * 0.10, 0.30))

    # 片段完整性
    snip = (e.get("extracted_snippet") or e.get("claim") or "").strip()
    if len(snip) < 25:
        integ = 0.2
    elif any(b in low for b in _BOILER) and not _has_digit(text):
        integ = 0.3
    else:
        integ = 1.0

    rel = float(e.get("claim_relevance") or 0.5)
    auth = float(e.get("source_reliability") or 0.5)
    # A1: freshness 三档 — current/recent=1.0, unknown=0.7(无发布时间不罚太重), stale=0.5
    _fs = e.get("source_freshness")
    fresh = 1.0 if _fs in (None, "current", "recent") else (0.7 if _fs == "unknown" else 0.5)

    w = _sc.weights("evidence_quality", _DEFAULT_WEIGHTS)   # config 可调,缺失回退默认
    score = (w["specificity"] * spec + w["integrity"] * integ + w["relevance"] * rel
             + w["authority"] * auth + w["freshness"] * fresh) - fluff_penalty
    return round(max(0.0, min(1.0, score)), 3)


def has_real_price(e: dict) -> bool:
    """定价证据是否含真实价格数值（或明确免费档）。"""
    t = _text_of(e)
    if _PRICE_NUM.search(t):
        return True
    if any(f in t.lower() for f in _FREE_TIER):
        return True
    return False


# ── ② 硬门 ───────────────────────────────────────────────────────────────────
def audit_pricing_substance(evidence: list[dict], products: list[str]) -> list[dict]:
    """硬门①：某产品有定价证据但无任何真实价格数值（全'联系销售'/营销页）→ gap。
    （0 条定价 = 数量门的事，这里只管'有但没含金量'。）"""
    gaps = []
    for p in products:
        pe = [e for e in evidence if e.get("product") == p and e.get("claim_type") == "pricing"]
        if not pe:
            continue
        if not any(has_real_price(e) for e in pe):
            gaps.append({
                "stage": "collector", "product": p, "claim_type": "pricing",
                "gap_type": "pricing_no_number",
                "reason": f"{p} 有 {len(pe)} 条定价证据但无任何真实价格数值(疑似全'联系销售'/营销页)",
                "fix": {"strategy": "recollect", "ladder": "L3_render_official_pricing",
                        "query_hint": f"{p} pricing per month plans", "source_hint": ["pricing_page"]},
            })
    return gaps


def audit_bias_balance(
    evidence: list[dict], products: list[str],
    types: tuple = ("user_pain", "performance_quality"),
) -> list[dict]:
    """硬门②：某产品的痛点/性能类证据全是厂商自夸(vendor_claim)→ 缺真实用户/第三方视角 → gap。"""
    gaps = []
    for p in products:
        for ct in types:
            be = [e for e in evidence if e.get("product") == p and e.get("claim_type") == ct]
            if not be:
                continue
            if all(e.get("source_bias") == "vendor_claim" for e in be):
                q = ("complaints problems" if ct == "user_pain" else "review benchmark")
                gaps.append({
                    "stage": "collector", "product": p, "claim_type": ct,
                    "gap_type": "bias_all_vendor",
                    "reason": f"{p} 的 {ct} 证据 {len(be)} 条全是厂商自夸(vendor_claim),缺真实用户/第三方视角",
                    "fix": {"strategy": "recollect", "ladder": "L0_search_ugc",
                            "query_hint": f"{p} {q}", "source_hint": ["reddit.com", "g2.com"]},
                })
    return gaps


def audit_community_feedback(
    evidence: list[dict], products: list[str],
    types: tuple = COMMUNITY_REQUIRED_TYPES,
) -> list[dict]:
    """Require usable community feedback for pain/performance claims when that claim type exists."""
    gaps = []
    for p in products:
        for ct in types:
            be = [e for e in evidence if e.get("product") == p and e.get("claim_type") == ct]
            if not be:
                continue
            community = [e for e in be if is_community_evidence(e)]
            if any(is_high_quality_community(e) for e in community):
                continue
            q = (
                "complaints problems reddit hacker news g2"
                if ct == "user_pain"
                else "review benchmark reddit hacker news g2"
            )
            fix = {
                "strategy": "recollect",
                "ladder": "L0_search_community",
                "query_hint": f"{p} {q}",
                "source_hint": ["reddit.com", "news.ycombinator.com", "g2.com", "v2ex.com"],
                "bias": "user_generated",
            }
            if community:
                gaps.append({
                    "stage": "collector", "product": p, "claim_type": ct,
                    "gap_type": "community_low_quality",
                    "reason": (
                        f"{p} 的 {ct} 有 {len(community)} 条社区证据,但没有达到 "
                        f"quality>={COMMUNITY_MIN_QUALITY} 且 snippet>={COMMUNITY_MIN_SNIPPET_LEN} 字符"
                    ),
                    "fix": fix,
                })
            else:
                gaps.append({
                    "stage": "collector", "product": p, "claim_type": ct,
                    "gap_type": "community_missing",
                    "reason": f"{p} 的 {ct} 缺少 Reddit/HN/G2/V2EX 等社区或评论站点证据",
                    "fix": fix,
                })
    return gaps


# ── 数量门（§3.5：覆盖 / 官网 / 总量）─────────────────────────────────────────
_TYPE_QUERY = {
    "feature_existence": ("{p} features capabilities", "", "feature_existence", "vendor_claim"),
    "performance_quality": ("{p} review benchmark performance", "g2.com", "performance_quality", "third_party"),
    "pricing": ("{p} pricing plans per month", "", "pricing", "vendor_claim"),
    "user_pain": ("{p} complaints problems", "reddit.com", "user_pain", "user_generated"),
    "market_signal": ("{p} users revenue funding valuation employees", "", "market_signal", "third_party"),
}


def audit_coverage(evidence: list[dict], products: list[str], required_types: Optional[Iterable[str]] = None) -> list[dict]:
    """数量门：每产品逐类覆盖 + 总量 + 官网证据是否达标 → gap。"""
    gaps = []
    required = tuple(required_types or REQUIRED_TYPES)
    for p in products:
        pe = [e for e in evidence if e.get("product") == p]
        by_ct = collections.Counter(e.get("claim_type") for e in pe)
        for ct in required:
            need = int(COVERAGE_MIN.get(ct, 1))
            if by_ct.get(ct, 0) < need:
                q, site, _, bias = _TYPE_QUERY.get(ct, (f"{{p}} {ct}", "", ct, "third_party"))
                gaps.append({
                    "stage": "collector", "product": p, "claim_type": ct,
                    "gap_type": "coverage_short",
                    "reason": f"{p} 的 {ct} 仅 {by_ct.get(ct, 0)} 条 < 阈值 {need}",
                    "fix": {"strategy": "recollect", "ladder": "L0_search",
                            "query_hint": q.format(p=p), "source_hint": [site] if site else [""],
                            "bias": bias},
                })
        if len(pe) < TOTAL_MIN:
            gaps.append({
                "stage": "collector", "product": p, "claim_type": None,
                "gap_type": "total_too_few",
                "reason": f"{p} 总证据 {len(pe)} 条 < 阈值 {TOTAL_MIN}，样本过薄",
                "fix": {"strategy": "recollect", "ladder": "L1_broad_search",
                        "query_hint": f"{p} review", "source_hint": [""]},
            })
        n_off = sum(1 for e in pe if e.get("source_type") in ("official_page", "pricing_page"))
        if n_off < OFFICIAL_MIN:
            gaps.append({
                "stage": "collector", "product": p, "claim_type": "feature_existence",
                "gap_type": "no_official",
                "reason": f"{p} 官网证据 {n_off} 条 < {OFFICIAL_MIN}，功能/定价缺权威源",
                "fix": {"strategy": "recollect", "ladder": "L3_render_official",
                        "query_hint": f"{p} official site features pricing", "source_hint": ["official_page"]},
            })
    return gaps


# ── 入口：打分 + 审查 ────────────────────────────────────────────────────────
def annotate_and_audit(evidence: list[dict], meta: dict) -> dict:
    """给每条证据写 quality_score，跑两条硬门，返回质量审查报告。"""
    from .evidence_plan import required_claim_types_for_meta

    products = [meta.get("target_product"), *(meta.get("competitors") or [])]
    products = [p for p in products if p]
    required = required_claim_types_for_meta(meta)
    for e in evidence:
        e["quality_score"] = score_quality(e)
    quantity_gaps = audit_coverage(evidence, products, required)                 # 数量门
    quality_gaps = (
        audit_pricing_substance(evidence, products)
        + audit_bias_balance(evidence, products)
        + audit_community_feedback(evidence, products)
    )  # 质量门
    all_gaps = quantity_gaps + quality_gaps
    avg = round(sum(e["quality_score"] for e in evidence) / len(evidence), 3) if evidence else 0.0
    return {
        "avg_quality": avg,
        "low_quality_count": sum(1 for e in evidence if e["quality_score"] < Q_LOW),
        "garbage_count": sum(1 for e in evidence if e["quality_score"] < Q_GARBAGE),
        "quantity_gaps": quantity_gaps,
        "quality_gaps": quality_gaps,
        "gaps": all_gaps,            # 数量+质量合并,供自愈循环统一消费
        "passed": not all_gaps,
    }
