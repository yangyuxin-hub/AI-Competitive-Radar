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
