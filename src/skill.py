"""Collector Skill 接口 + 注册表

Skill 是比 SourceAdapter 更高层的抽象：
- SourceAdapter: 抓取单一来源的原始数据
- Skill: 爬取 + 分析一体化，输出结构化 evidence

Collector 根据分析的产品/行业决定调用哪些 Skill。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional


class CollectorSkill(ABC):
    """Collector 技能基类。每个 Skill 封装一个数据源的完整采集+分析流程。"""

    @abstractmethod
    def can_execute(self, search_terms: list[str], **kwargs) -> bool:
        """判断该技能是否适用于当前采集任务。"""
        ...

    @abstractmethod
    def execute(self, search_terms: list[str], product: str = "", focus: str = "", **kwargs) -> tuple[list[dict], dict]:
        """执行采集，返回 (evidences, meta)。"""
        ...


class SkillRegistry:
    """技能注册表。Collector 通过此表决定使用哪些 Skill。"""

    def __init__(self) -> None:
        self._skills: dict[str, CollectorSkill] = {}

    def register(self, name: str, skill: CollectorSkill) -> None:
        disabled = os.environ.get("DISABLE_SKILLS", "")
        disabled_set = {s.strip() for s in disabled.split(",") if s.strip()}
        if name in disabled_set:
            print(f"[SkillRegistry] {name} disabled by DISABLE_SKILLS env")
            return
        self._skills[name] = skill

    def get(self, name: str) -> Optional[CollectorSkill]:
        return self._skills.get(name)

    def all(self) -> dict[str, CollectorSkill]:
        return dict(self._skills)


def register_all_skills(registry: SkillRegistry) -> None:
    """注册所有可用技能。顺序决定执行顺序。"""
    from .hn_skill import HNSkill
    from .v2ex_skill import V2EXSkill
    registry.register("hn", HNSkill())
    registry.register("v2ex", V2EXSkill())


def create_skill_registry() -> SkillRegistry:
    """创建并初始化技能注册表。"""
    registry = SkillRegistry()
    register_all_skills(registry)
    return registry
