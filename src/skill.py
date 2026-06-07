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
        if disabled.strip() in ("1", "true", "True", "all", "*") or name in disabled_set:
            print(f"[SkillRegistry] {name} disabled by DISABLE_SKILLS env")
            return
        self._skills[name] = skill

    def get(self, name: str) -> Optional[CollectorSkill]:
        return self._skills.get(name)

    def all(self) -> dict[str, CollectorSkill]:
        return dict(self._skills)


def register_all_skills(registry: SkillRegistry) -> None:
    """注册所有可用技能。顺序决定执行顺序。

    HN / V2EX 两个社区 skill 已于评测后下线(2026-06):
    - 实测相关性 HN 42% / V2EX≈0,远低于 Reddit(走 search 的 site:reddit.com,83-100%);
    - 两者从命中帖拉评论/话题时无相关性门,V2EX 甚至把理财热帖灌成竞品证据;
    - 社区证据统一由 **search 路径**承接(source_planner 已配 reddit.com /
      news.ycombinator.com / g2.com 等 site 锚定,且 search 自带相关性门),零损失。
    类文件(hn_skill.py / v2ex_skill.py)保留,如需重启用,或把 V2EX 升级为
    sov2ex.com 全文搜索后,再在此处重新 register。
    """
    # from .hn_skill import HNSkill
    # from .v2ex_skill import V2EXSkill
    # registry.register("hn", HNSkill())
    # registry.register("v2ex", V2EXSkill())
    # 注:问卷/访谈采集已移到 analyzer 默认全档运行(见 analyzer._run_survey),不再作为采集层 skill,
    # 避免受采集 wall-clock 超时影响,并保证 balanced/fast 档也有问卷访谈证据。
    return


def create_skill_registry(enabled: bool = True) -> SkillRegistry:
    """创建并初始化技能注册表。"""
    registry = SkillRegistry()
    if enabled:
        register_all_skills(registry)
    return registry
