"""能力域权重加载器 — 读 config/feature_weights.yaml。

对偶 scoring_config.py:任何缺失回退内置默认,绝不 break。权重随 analysis_focus
重排是功能树有决策价值的前提;落 config 版本号保证可复现。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "feature_weights.yaml"
_CFG: Optional[dict] = None

# 内置兜底:yaml/文件缺失时仍给一组非空权重(和为 1),保证 skeleton 不塌
_FALLBACK = [
    {"id": "A", "name": "核心能力", "weight": 0.5, "role": "core"},
    {"id": "B", "name": "辅助能力", "weight": 0.5, "role": "core"},
]


def reload(path: Optional[Path] = None) -> dict:
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


def version() -> str:
    v = _cfg().get("version")
    return str(v) if v else "unversioned"


def has_focus(focus: str) -> bool:
    return str(focus or "") in ((_cfg().get("focuses") or {}).keys())


def _normalize(doms: list[dict]) -> list[dict]:
    out = []
    for d in doms or []:
        if not isinstance(d, dict) or "weight" not in d:
            continue
        out.append({
            "id": str(d.get("id", "")),
            "name": str(d.get("name", "")),
            "weight": float(d["weight"]),
            "role": str(d.get("role", "core")),
        })
    return out


def domains_for_focus(focus: str) -> list[dict]:
    cfg = _cfg()
    focuses = cfg.get("focuses") or {}
    doms = _normalize(focuses.get(focus) or [])
    if doms:
        return doms
    default_key = cfg.get("default_focus")
    doms = _normalize(focuses.get(default_key) or [])
    if doms:
        return doms
    return [dict(d) for d in _FALLBACK]
