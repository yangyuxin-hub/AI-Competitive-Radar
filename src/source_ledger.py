"""高质量源台账（Source Ledger）— 见 docs/reviewer-reject-design.md §8.6

学习「哪个 domain 在哪个品类/产品下出过高质量证据」，持久化复用，
把「每次重新发现官网/社区」变成「命中历史高质量源」。

- record(evidence, category, products): 本轮 quality_score≥QBAR 的证据按 (品类,桶,domain) 累计(EWMA)
- top_official(category, product):  历史高质量官网 domain → 给 URL discovery 跳过 LLM
- top_sites(category, bucket):      某桶(community/review)高分 domain → 给 source_planner 优先锚定

纯文件存储，所有写操作 merge（不覆盖）。失败静默（台账是优化项，绝不阻断采集）。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

_LEDGER_PATH = Path("data/source_ledger.json")
QBAR = 0.65        # 只学习高质量证据
ALPHA = 0.3        # EWMA 学习率
TOPK = 5

# source_type / bias → 桶
_OFFICIAL_TYPES = {"official_page", "pricing_page"}
_COMMUNITY_TYPES = {"reddit", "hn", "v2ex"}
_REVIEW_DOMAINS = {"g2.com", "capterra.com", "trustradius.com"}
_SKIP_TYPES = {"simulated_interview"}  # 合成证据不入台账


def _path(path: Optional[Path]) -> Path:
    return Path(path) if path else _LEDGER_PATH


def _domain_of(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:  # noqa: BLE001
        return ""


def _bucket_of(e: dict) -> Optional[str]:
    st = e.get("source_type", "")
    if st in _SKIP_TYPES or e.get("source_bias") == "synthetic":
        return None
    if st in _OFFICIAL_TYPES:
        return "official"
    if st in _COMMUNITY_TYPES or e.get("source_bias") in ("community_feedback", "user_generated"):
        return "community"
    dom = _domain_of(e.get("source_url", ""))
    if dom in _REVIEW_DOMAINS or e.get("source_bias") == "third_party":
        return "review"
    return "web"


def load(path: Optional[Path] = None) -> dict:
    p = _path(path)
    if not p.exists():
        return {"by_category": {}, "by_product": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"by_category": {}, "by_product": {}}


def save(led: dict, path: Optional[Path] = None) -> None:
    p = _path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _score(entry: dict) -> float:
    """排序键:质量 × log(1+hits) × recency(此处 recency 简化为 hits 近似)。"""
    return entry.get("q", 0.0) * math.log1p(entry.get("hits", 0))


def record(evidence: list[dict], category: str, products: Optional[list[str]] = None,
           now: Optional[str] = None, path: Optional[Path] = None) -> dict:
    """把本轮高质量证据(quality_score≥QBAR)按 domain 累计进台账(EWMA)。返回更新后的 led。"""
    led = load(path)
    cat = led["by_category"].setdefault(category or "default", {})
    by_prod = led["by_product"]
    for e in evidence:
        q = e.get("quality_score")
        if q is None or q < QBAR:
            continue
        bucket = _bucket_of(e)
        if not bucket:
            continue
        dom = _domain_of(e.get("source_url", ""))
        if not dom:
            continue
        lst = cat.setdefault(bucket, [])
        hit = next((x for x in lst if x["domain"] == dom), None)
        if hit:
            hit["q"] = round(ALPHA * q + (1 - ALPHA) * hit["q"], 3)
            hit["hits"] += 1
        else:
            lst.append({"domain": dom, "q": round(q, 3), "hits": 1})
        if now:
            (hit or lst[-1])["last"] = now
        # by_product 索引
        prod = e.get("product")
        if prod:
            pb = by_prod.setdefault(prod, {})
            doms = pb.setdefault(bucket, [])
            if dom not in doms:
                doms.append(dom)
            # 记住实际成功命中的官网/定价页 URL,供 URL discovery 精确复用(跳过 LLM)
            st = e.get("source_type")
            if st in ("official_page", "pricing_page"):
                key = "pricing_pages" if st == "pricing_page" else "official_pages"
                urls = pb.setdefault(key, [])
                u = e.get("source_url", "")
                if u.startswith("http") and u not in urls:
                    urls.append(u)
                    del urls[8:]
    # 每桶 top-K，按综合分排序
    for bucket, lst in cat.items():
        lst.sort(key=_score, reverse=True)
        del lst[TOPK:]
    save(led, path)
    return led


def top_official(category: str, product: Optional[str] = None,
                 k: int = 3, path: Optional[Path] = None) -> list[str]:
    """历史高质量官网 domain（产品级优先，品类级兜底）→ 给 URL discovery 跳过 LLM。"""
    led = load(path)
    out: list[str] = []
    if product:
        out.extend((led["by_product"].get(product) or {}).get("official", []))
    for x in (led["by_category"].get(category or "default", {}).get("official", [])):
        if x["domain"] not in out:
            out.append(x["domain"])
    return out[:k]


def known_pages(product: str, path: Optional[Path] = None) -> Optional[dict]:
    """历史学过的该产品官网/定价页 URL（实际成功命中的）→ 给 URL discovery 跳过 LLM。
    无记录返回 None。"""
    led = load(path)
    pb = led["by_product"].get(product) or {}
    op = pb.get("official_pages") or []
    pp = pb.get("pricing_pages") or []
    if op or pp:
        return {"official_pages": op[:3], "pricing_pages": pp[:3]}
    return None


def top_sites(category: str, bucket: str = "community",
              k: int = 3, path: Optional[Path] = None) -> list[str]:
    """某桶(community/review)历史高分 domain → 给 source_planner 优先 site 锚定。"""
    led = load(path)
    lst = led["by_category"].get(category or "default", {}).get(bucket, [])
    return [x["domain"] for x in lst[:k]]
