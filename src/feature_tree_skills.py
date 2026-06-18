"""Feature tree skill loader.

Feature tree skills turn an industry/product category into a stable,
evidence-checkable capability model for competitive analysis. The LLM may later
adapt this model, but the analyzer can already consume skill configs
deterministically for repeatable benchmark reports.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "config" / "feature_tree_skills"
_CACHE: Optional[list[dict]] = None


def reload(directory: Optional[Path] = None) -> list[dict]:
    """Reload all skill yaml files. Missing yaml support returns no skills."""
    global _CACHE
    base = Path(directory) if directory else _DEFAULT_DIR
    if yaml is None or not base.exists():
        _CACHE = []
        return _CACHE
    skills: list[dict] = []
    for path in sorted(base.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and data.get("skill_id"):
            data["_path"] = str(path)
            skills.append(data)
    _CACHE = skills
    return _CACHE


def _skills() -> list[dict]:
    global _CACHE
    if _CACHE is None:
        reload()
    return _CACHE or []


def _meta_text(meta: dict) -> str:
    parts = [
        meta.get("target_product"),
        *(meta.get("competitors") or []),
        *(meta.get("analysis_focus") or []),
        meta.get("analysis_purpose"),
        meta.get("analysis_goal"),
        meta.get("domain"),
        meta.get("category"),
    ]
    return " ".join(str(x) for x in parts if x).lower()


def _keyword_score(skill: dict, meta: dict) -> int:
    text = _meta_text(meta)
    rules = skill.get("selection_rules") or {}
    score = 0
    for field, weight in (
        ("product_keywords", 3),
        ("focus_keywords", 2),
        ("category_keywords", 2),
    ):
        for kw in rules.get(field) or []:
            if str(kw).lower() in text:
                score += weight
    return score


# 命中阈值:单个泛化 focus 关键词(权重 2,如"开发")不足以锁定一个行业 skill——
# 否则"后端开发""游戏开发"会误命中 ai_coding 拿到代码补全功能树(实测 Supabase/Firebase/
# Appwrite 被错配)。要求 ≥3:即至少一个 product_keyword(3),或 category+focus / 两个 focus
# 双信号(2+2)。证据不足时回退 LLM 按证据自适应抽 spine,比硬套错行业的固定树更稳。
_DEFAULT_MIN_SELECT_SCORE = 3


def select_skill(meta: dict) -> Optional[dict]:
    """Return the best matching feature tree skill for the analysis meta.

    仅当置信分达到 skill 的 `selection_rules.min_score`(缺省 3)才命中,
    避免单个泛化关键词把不相干行业误配到某个固定功能树。
    """
    ranked = sorted(
        ((s, _keyword_score(s, meta)) for s in _skills()),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked:
        return None
    top_skill, top_score = ranked[0]
    min_score = (top_skill.get("selection_rules") or {}).get("min_score", _DEFAULT_MIN_SELECT_SCORE)
    try:
        min_score = int(min_score)
    except (TypeError, ValueError):
        min_score = _DEFAULT_MIN_SELECT_SCORE
    if top_score < min_score:
        return None
    return top_skill


def _module_weight(module: dict) -> float:
    try:
        return float(module.get("weight", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _normalized_modules(skill: dict) -> list[dict]:
    modules = [m for m in (skill.get("modules") or []) if isinstance(m, dict) and m.get("name")]
    total = sum(_module_weight(m) for m in modules)
    if total <= 0:
        total = float(len(modules) or 1)
        return [{**m, "weight": round(1.0 / total, 6)} for m in modules]
    return [{**m, "weight": round(_module_weight(m) / total, 6)} for m in modules]


def feature_spine_for_meta(meta: dict) -> Optional[list[dict]]:
    """Return feature spine usable by analyzer feature_fill."""
    skill = select_skill(meta)
    if not skill:
        return None
    out = []
    for index, module in enumerate(_normalized_modules(skill), start=1):
        out.append({
            "feature_id": str(module.get("id") or f"F{index:03d}"),
            "name": str(module.get("name")),
            "name_en": str(module.get("name_en") or ""),
            "source_skill": str(skill.get("skill_id")),
            "core_question": module.get("core_question"),
            "representative_features": list(module.get("representative_features") or []),
            "evidence_requirements": list(module.get("evidence_requirements") or []),
        })
    return out or None


def domains_for_meta(meta: dict) -> Optional[dict]:
    """Return weighted domains from a matching skill.

    Shape intentionally mirrors config/feature_weights.yaml domains so the
    existing feature model can consume it without schema migration.
    """
    skill = select_skill(meta)
    if not skill:
        return None
    domains = []
    for index, module in enumerate(_normalized_modules(skill), start=1):
        domain_id = module.get("domain_id") or (chr(64 + index) if index <= 26 else f"D{index}")
        domains.append({
            "id": str(domain_id),
            "name": str(module.get("name")),
            "weight": float(module.get("weight", 0.0)),
            "role": str(module.get("role", "core")),
            "source_skill": str(skill.get("skill_id")),
            "core_question": module.get("core_question"),
            "evidence_requirements": list(module.get("evidence_requirements") or []),
        })
    return {
        "skill_id": str(skill.get("skill_id")),
        "version": str(skill.get("version") or "unversioned"),
        "category": str(skill.get("category") or skill.get("name") or ""),
        "generation_mode": str(skill.get("generation_mode") or "fixed"),
        "scoring_rubric": dict(skill.get("scoring_rubric") or {}),
        "domains": domains,
    }


def long_tail_feature_keys(meta: dict, weight_threshold: float = 0.05) -> set[str]:
    """Feature ids + names of long-tail modules: non-`core` role AND低于权重阈值。

    这些模块(如 工程集成/模型配置/安全权限/协作交付)权重合计很小、且证据多在
    官网不会逐条写的稀疏页面上,几乎恒为 unknown。按 skill 自己的 principle
    (缺证据只标 unknown,不默认给分),它们应坦然 unknown,而不该触发昂贵的
    gap-refill 整轮重算去追根本不存在的证据。返回 id 与 name 两种键,调用方任配一个即命中。
    """
    skill = select_skill(meta)
    if not skill:
        return set()
    out: set[str] = set()
    for index, module in enumerate(_normalized_modules(skill), start=1):
        role = str(module.get("role", "core")).lower()
        weight = _module_weight(module)
        if role == "core" or weight >= weight_threshold:
            continue
        out.add(str(module.get("id") or f"F{index:03d}"))
        if module.get("name"):
            out.add(str(module["name"]))
    return out


def selected_skill_id(meta: dict) -> Optional[str]:
    skill = select_skill(meta)
    return str(skill.get("skill_id")) if skill else None
