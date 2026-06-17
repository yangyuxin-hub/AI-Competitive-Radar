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
