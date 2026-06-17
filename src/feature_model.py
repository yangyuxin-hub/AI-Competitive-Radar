"""确定性功能树派生引擎 —— 把抽取出的功能点事实算成可比指标。

对偶 pricing_model.py:LLM 只把各产品功能「翻译」进统一叶子 schema(填空,不算术),
本模块对该 schema 做确定性派生(加权覆盖率 / winner / 样本内差异点 / 护城河 /
蓝海),每个结论可溯源、同输入恒同输出。

铁律:
  - depth_score 缺失 → None(渲染为 ?),绝不编分。
  - 全员缺 depth_score → winner 不允许判产品,只能 tie/unclear。
  - 覆盖率:unknown 既不进分子也不进分母(单独进 evidence_coverage_rate)。
  - 样本内差异点措辞带「样本内 N 个产品中」,禁止「独占」。
"""
from __future__ import annotations

from typing import Optional

_STATUS_ALIAS = {
    "partially_supported": "partial",
    "not_supported": "unsupported",
}
_VALID_STATUS = {"supported", "partial", "unsupported", "unknown"}
_SUPPORT_SCORE = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}
_EVIDENCE_SCORE = {"official": 1.0, "third_party": 0.8, "user_review": 0.6, "inferred": 0.3}


def normalize_status(status: str) -> str:
    s = _STATUS_ALIAS.get(status, status)
    return s if s in _VALID_STATUS else "unknown"


def support_score(status: str) -> Optional[float]:
    """supported=1 / partial=0.5 / unsupported=0 / unknown→None(排除出覆盖率)。"""
    return _SUPPORT_SCORE.get(normalize_status(status))


def depth_norm(depth_score: Optional[int]) -> Optional[float]:
    """1..5 → 0.2..1.0;None/越界 → None(诚实缺失)。"""
    if depth_score is None:
        return None
    try:
        d = int(depth_score)
    except (TypeError, ValueError):
        return None
    if d < 1 or d > 5:
        return None
    return round(d / 5, 4)


def evidence_level_score(level: str) -> float:
    return _EVIDENCE_SCORE.get(level, 0.3)


def _leaves_of_domain(domain: dict) -> list[dict]:
    """展平 modules→points;无 modules 时退回 domain['points']。"""
    if domain.get("modules"):
        out = []
        for m in domain["modules"]:
            out.extend(m.get("points") or [])
        return out
    return domain.get("points") or []


def domain_coverage(domain: dict, product: str) -> dict:
    leaves = _leaves_of_domain(domain)
    total = len(leaves)
    scores = []
    for leaf in leaves:
        pdata = (leaf.get("products") or {}).get(product) or {}
        s = support_score(pdata.get("support_status", "unknown"))
        if s is not None:
            scores.append(s)
    known = len(scores)
    return {
        "score": round(sum(scores) / known, 4) if known else None,
        "evidence_rate": round(known / total, 4) if total else 0.0,
        "known": known,
        "total": total,
    }


def weighted_coverage(tree: dict, product: str) -> dict:
    """加权覆盖率。unknown 不进 known_only 的分子/分母,单独进 evidence_coverage_rate。"""
    by_domain = []
    num_known = den_known = 0.0
    num_evi = den_evi = 0.0
    for domain in tree.get("domains") or []:
        w = float(domain.get("weight", 0.0))
        cov = domain_coverage(domain, product)
        by_domain.append({"id": domain.get("id"), "name": domain.get("name"),
                          "weight": w, **cov})
        den_evi += w
        num_evi += w * cov["evidence_rate"]
        if cov["score"] is not None:        # 全 unknown 的域整域排除出 known_only
            den_known += w
            num_known += w * cov["score"]
    return {
        "coverage_known_only": round(num_known / den_known, 4) if den_known else None,
        "evidence_coverage_rate": round(num_evi / den_evi, 4) if den_evi else 0.0,
        "by_domain": by_domain,
    }


_WINNER_W = {"support": 0.35, "depth": 0.40, "evidence": 0.15}
_DIFF_BONUS = 0.10
_TIE_EPS = 0.05
_HIGH_MARGIN = 0.15


def _leaf_product_score(pdata: dict) -> Optional[float]:
    """unknown 不参与比较。加权 support/depth/evidence + differentiator bonus。"""
    s = support_score(pdata.get("support_status", "unknown"))
    if s is None:                      # unknown 不参与比较
        return None
    d = depth_norm(pdata.get("depth_score"))
    e = evidence_level_score(pdata.get("evidence_level", "inferred"))
    bonus = _DIFF_BONUS if pdata.get("differentiator") else 0.0
    return (_WINNER_W["support"] * s
            + _WINNER_W["depth"] * (d if d is not None else 0.0)
            + _WINNER_W["evidence"] * e
            + bonus)


def feature_winner(point: dict, products: list[str]) -> dict:
    """比较产品在某个功能点的胜出度。

    保守口径：缺深度 → tie/unclear。
    - winner ∈ {product_name, "tie", "unclear"}
    - confidence ∈ {"high", "medium", "low"}
    """
    prods = (point.get("products") or {})
    scored, any_depth = {}, False
    for p in products:
        pdata = prods.get(p) or {}
        sc = _leaf_product_score(pdata)
        if sc is None:
            continue
        scored[p] = sc
        if depth_norm(pdata.get("depth_score")) is not None:
            any_depth = True
    if not scored:
        return {"winner": "unclear", "reason": "所有产品该能力均无证据(unknown)", "confidence": "low"}
    if not any_depth:
        return {"winner": "tie",
                "reason": "仅有支持度证据、无任何深度评分,不足以强判优劣",
                "confidence": "low"}
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    top_p, top_s = ranked[0]
    second_s = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_s - second_s
    if margin < _TIE_EPS:
        return {"winner": "tie", "reason": f"{top_p} 与次优分差 {margin:.2f}<{_TIE_EPS},判平",
                "confidence": "medium"}
    confidence = "high" if margin >= _HIGH_MARGIN else "medium"
    return {"winner": top_p, "reason": f"{top_p} 综合分领先次优 {margin:.2f}",
            "confidence": confidence}


_STRONG = 0.7
_BREADTH_BROAD = 0.8


def differentiation_matrix(tree: dict, products: list[str]) -> list[dict]:
    """样本内差异点:某能力上仅一个产品做到位(differentiator 或独家 supported)。
    措辞强制带「样本内 N 个产品中」,不绝对化为「独占」。"""
    n = len(products)
    rows = []
    for domain in tree.get("domains") or []:
        for leaf in _leaves_of_domain(domain):
            prods = leaf.get("products") or {}
            flagged = [p for p in products if (prods.get(p) or {}).get("differentiator")]
            if len(flagged) != 1:
                # 退一步:仅一个产品 supported、其余非 supported,也算样本内差异点
                supported = [p for p in products
                             if support_score((prods.get(p) or {}).get("support_status", "unknown")) == 1.0]
                if len(supported) != 1:
                    continue
                flagged = supported
            p = flagged[0]
            rows.append({
                "feature_id": leaf.get("id"), "name": leaf.get("name"), "product": p,
                "note": f"样本内 {n} 个产品中,仅 {p} 在「{leaf.get('name')}」做到位",
            })
    return rows


def product_archetype(tree: dict, product: str) -> str:
    """产品原型分类。

    原型 ∈ {全能型, 专精型, 工具型, 平台型, 数据不足}
    - 全能型：覆盖广 + 强度强（breadth >= 0.8 && strong_ratio >= 0.7）、无平台域强项
    - 平台型：同全能 + 有平台/生态域强项（role in ["platform", "ecosystem"]）
    - 专精型：覆盖小 + 仅 1-2 个强项、强项占比 <= 50%
    - 工具型：其他
    - 数据不足：所有域都无数据(all unknown)
    """
    domains = tree.get("domains") or []
    covs = [(d, domain_coverage(d, product)) for d in domains]
    present = [(d, c) for d, c in covs if c["score"] is not None]
    if not present:
        return "数据不足"
    strong = [(d, c) for d, c in present if c["score"] >= _STRONG]
    breadth = len(present) / len(domains) if domains else 0.0
    strong_ratio = len(strong) / len(present) if present else 0.0
    # 平台型:生态/平台域强 + 覆盖较广
    has_platform = any(d.get("role") in ("platform", "ecosystem") and c["score"] >= _STRONG
                       for d, c in present)
    if breadth >= _BREADTH_BROAD and strong_ratio >= _STRONG:
        return "平台型" if has_platform else "全能型"
    if strong and len(strong) <= 2 and strong_ratio <= 0.5:
        return "专精型"
    return "工具型"
