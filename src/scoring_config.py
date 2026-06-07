"""统一评分配置加载器 — 读 config/scoring.yaml。

设计:任何缺失都回退到调用方给的默认值,绝不 break。各评分函数通过本模块取权重/阈值,
集中口径(消除散落硬编码)。进程内缓存一次;测试可 reload(path) 注入。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None  # 无 yaml 时全部走默认值

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.yaml"
_CFG: Optional[dict] = None


def reload(path: Optional[Path] = None) -> dict:
    """重新加载配置(测试可注入临时 path)。"""
    global _CFG
    p = Path(path) if path else _DEFAULT_PATH
    if yaml is None or not p.exists():
        _CFG = {}
        return _CFG
    try:
        _CFG = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        _CFG = {}
    return _CFG


def _cfg() -> dict:
    global _CFG
    if _CFG is None:
        reload()
    return _CFG or {}


def get(section: str, key: str, default: Any) -> Any:
    """取 section.key,缺失回退 default。"""
    sec = _cfg().get(section)
    if isinstance(sec, dict) and key in sec:
        return sec[key]
    return default


def weights(section: str, default: dict) -> dict:
    """取 section.weights;非法/缺失回退 default(并保证 key 齐全:default 的 key 缺则补)。"""
    sec = _cfg().get(section) or {}
    w = sec.get("weights")
    if not isinstance(w, dict) or not w:
        return dict(default)
    merged = dict(default)
    merged.update({k: v for k, v in w.items() if isinstance(v, (int, float))})
    return merged


def reliability(source_key: str, default: float) -> float:
    """源可信度先验。各采集适配器读 scoring.yaml[source_reliability][source_key];
    缺失回退调用方传入的 default(= 各适配器原硬编码值,保证零行为变化)。"""
    v = get("source_reliability", source_key, default)
    return float(v) if isinstance(v, (int, float)) else float(default)


def ttl_days(claim_type: str, default: int) -> int:
    """freshness TTL(天)。collector 按 claim_type 重算 source_freshness;
    缺失回退调用方传入的 default。"""
    v = get("freshness_ttl_days", claim_type, default)
    return int(v) if isinstance(v, (int, float)) else int(default)
