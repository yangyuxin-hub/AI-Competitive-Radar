"""intake — 意图问询层(Planner 雏形,见 docs/report-improvement-roadmap.md §8)

把用户一句话意图 → 一组「选择题」,让用户点选完成背景对齐,产出完整的运行参数
(target_product / competitors / analysis_focus / analysis_purpose)。前端与 CLI 复用。

设计要点:
- 不让用户硬填字段,而是 agent 提出候选 + 推荐,用户做选择题(含多选)。
- 有 LLM(非 mock 且有 ARK_API_KEY)→ call_json 智能抽取意图 + 推荐同类竞品。
- 无 key / mock 模式 → 启发式:从 products.yaml / domains.yaml 已知产品出选项,
  仍保持「主动问询」体验,只是不智能推荐陌生竞品。
- 「是否存为新行业」本身也是最后一道选择题(把人决策点都做成选择)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_PRODUCTS_YAML = _ROOT / "config" / "products.yaml"
_DOMAINS_YAML = _ROOT / "config" / "domains.yaml"

# 跨行业通用的兜底候选(启发式无 LLM 时也能给出像样的选项)
_FALLBACK_FOCUS = [
    "代码补全体验",
    "团队任务管理体验",
    "定价策略",
    "核心功能完整度",
    "用户体验与上手成本",
    "集成与生态",
]
_FALLBACK_PURPOSE = [
    "学习竞品优点,优化自身产品",
    "寻找差异化定位机会",
    "定价策略参考",
    "评估是否进入该市场",
]


# ────────────────────────────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class Choice:
    """一道选择题。前端渲染成 radio(multi=False)/multiselect(multi=True)。"""

    key: str  # 'target' | 'competitors' | 'focus' | 'purpose' | 'persist'
    question: str
    options: list[str]
    multi: bool = False
    suggested: list[str] = field(default_factory=list)  # 预选/推荐项
    allow_custom: bool = True  # 前端是否允许「其他」自由输入

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "question": self.question,
            "options": self.options,
            "multi": self.multi,
            "suggested": self.suggested,
            "allow_custom": self.allow_custom,
        }


# ────────────────────────────────────────────────────────────────────────────
# 配置读取
# ────────────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def load_products() -> dict:
    return _load_yaml(_PRODUCTS_YAML).get("products") or {}


def load_domains() -> dict:
    return _load_yaml(_DOMAINS_YAML).get("domains") or {}


def _known_product_names() -> list[str]:
    return list(load_products().keys())


def _all_focus_options() -> list[str]:
    """合并 domains.yaml 里出现过的所有 analysis_focus + 兜底通用项,去重保序。"""
    seen: list[str] = []
    for dom in load_domains().values():
        for f in dom.get("analysis_focus") or []:
            if f not in seen:
                seen.append(f)
    for f in _FALLBACK_FOCUS:
        if f not in seen:
            seen.append(f)
    return seen


# ────────────────────────────────────────────────────────────────────────────
# 意图草拟:LLM 优先,启发式兜底
# ────────────────────────────────────────────────────────────────────────────

_PROMPTS_DIR = _ROOT / "prompts"

_prompt_cache: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    if name in _prompt_cache:
        return _prompt_cache[name]
    path = _PROMPTS_DIR / f"{name}.md"
    # 从 .md prompt 文件中提取 ## SYSTEM 之后的内容作为 system prompt
    text = path.read_text(encoding="utf-8")
    marker = "## SYSTEM"
    idx = text.find(marker)
    if idx >= 0:
        text = text[idx + len(marker):].strip()
    _prompt_cache[name] = text
    return text


def _llm_available() -> bool:
    try:
        from .llm import is_mock_mode
    except Exception:
        return False
    if is_mock_mode():
        return False
    return bool(os.environ.get("ARK_API_KEY"))


def _propose_via_llm(user_input: str) -> Optional[dict]:
    try:
        from .llm import get_llm
        draft = get_llm().call_json(
            system_prompt=_load_prompt("intake"),
            user_payload={
                "user_input": user_input,
                "known_products": _known_product_names(),
            },
            max_tokens=1024,
            label="intake",
        )
        if isinstance(draft, dict) and draft.get("target_candidates"):
            return draft
    except Exception as e:  # 网络/解析失败 → 回退启发式
        print(f"[intake] LLM 草拟失败,回退启发式: {e}")
    return None


def _propose_heuristic(user_input: str, domain_hint: Optional[str]) -> dict:
    """无 LLM 时:用 products.yaml / domains.yaml 拼候选。"""
    products = load_products()
    domains = load_domains()
    text = (user_input or "").lower()

    # 命中检测:product key 或 alias 出现在用户输入里
    def _hit(name: str) -> bool:
        cands = [name] + (products.get(name, {}).get("aliases") or [])
        return any(c.lower() in text for c in cands if c)

    hit_products = [p for p in products if _hit(p)]
    dom_cfg = domains.get(domain_hint or "", {})

    # target 候选:命中的产品在前,然后是行业默认 target,再补其它已知产品
    target_candidates: list[str] = list(hit_products)
    if dom_cfg.get("target_product") and dom_cfg["target_product"] not in target_candidates:
        target_candidates.append(dom_cfg["target_product"])
    for p in products:
        if p not in target_candidates:
            target_candidates.append(p)

    target = target_candidates[0] if target_candidates else ""
    competitors_candidates = [p for p in products if p != target]
    competitors_suggested = [
        c for c in (dom_cfg.get("competitors") or []) if c != target
    ][:3] or competitors_candidates[:2]

    focus_candidates = _all_focus_options()
    focus_suggested = (dom_cfg.get("analysis_focus") or focus_candidates or [""])[0]

    return {
        "domain_name": dom_cfg.get("name", ""),
        "target_candidates": target_candidates,
        "competitors_candidates": competitors_candidates,
        "competitors_suggested": competitors_suggested,
        "focus_candidates": focus_candidates,
        "focus_suggested": focus_suggested,
        "purpose_candidates": _FALLBACK_PURPOSE,
        "purpose_suggested": dom_cfg.get("analysis_purpose") or _FALLBACK_PURPOSE[0],
    }


def propose(user_input: str, domain_hint: Optional[str] = None) -> dict:
    """产出意图草稿(各字段候选 + 推荐)。LLM 优先,失败回退启发式。"""
    if _llm_available():
        draft = _propose_via_llm(user_input)
        if draft:
            # LLM 没给的字段用启发式补齐,保证下游不缺键
            base = _propose_heuristic(user_input, domain_hint)
            for k, v in base.items():
                draft.setdefault(k, v)
            return draft
    return _propose_heuristic(user_input, domain_hint)


# ────────────────────────────────────────────────────────────────────────────
# 草稿 → 选择题
# ────────────────────────────────────────────────────────────────────────────

def build_questions(draft: dict) -> list[Choice]:
    target_opts = draft.get("target_candidates") or []
    comp_opts = draft.get("competitors_candidates") or []
    comp_sug = [c for c in (draft.get("competitors_suggested") or []) if c in comp_opts]
    focus_opts = draft.get("focus_candidates") or []
    focus_sug = draft.get("focus_suggested") or (focus_opts[0] if focus_opts else "")
    purpose_opts = draft.get("purpose_candidates") or _FALLBACK_PURPOSE
    purpose_sug = draft.get("purpose_suggested") or purpose_opts[0]

    return [
        Choice(
            key="target",
            question="要分析的目标产品是哪个?",
            options=target_opts,
            multi=False,
            suggested=[target_opts[0]] if target_opts else [],
        ),
        Choice(
            key="competitors",
            question="对比哪些竞品?(可多选,建议覆盖不同竞争逻辑)",
            options=comp_opts,
            multi=True,
            suggested=comp_sug,
        ),
        Choice(
            key="focus",
            question="这次分析的焦点维度是什么?",
            options=focus_opts,
            multi=False,
            suggested=[focus_sug] if focus_sug else [],
        ),
        Choice(
            key="purpose",
            question="分析目的是什么?(影响建议的取向)",
            options=purpose_opts,
            multi=False,
            suggested=[purpose_sug] if purpose_sug else [],
        ),
        Choice(
            key="persist",
            question="这套配置要不要存成可复用的行业域?",
            options=["仅本次运行", "保存为新行业(写回 config,下次 DOMAIN= 复用)"],
            multi=False,
            suggested=["仅本次运行"],
            allow_custom=False,
        ),
    ]


def intake_questions(user_input: str, domain_hint: Optional[str] = None) -> list[Choice]:
    """便捷入口:一句话意图 → 选择题列表。"""
    return build_questions(propose(user_input, domain_hint))


# ────────────────────────────────────────────────────────────────────────────
# 选择题答案 → 运行参数 / 持久化
# ────────────────────────────────────────────────────────────────────────────

def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(x) for x in v if str(x).strip()]


def assemble_meta(answers: dict, user_input: Optional[str] = None) -> dict:
    """把选择题答案拼成 run_demo / build_initial_state 需要的参数。

    answers 形如 {target: str, competitors: [..], focus: str|[..], purpose: str}
    返回 {target_product, competitors, analysis_focus, analysis_purpose, user_input}
    """
    target = (answers.get("target") or "").strip() if isinstance(answers.get("target"), str) \
        else (_as_list(answers.get("target"))[:1] or [""])[0]
    competitors = [c for c in _as_list(answers.get("competitors")) if c != target]
    focus = _as_list(answers.get("focus")) or ["核心功能完整度"]
    purpose = answers.get("purpose")
    if isinstance(purpose, list):
        purpose = purpose[0] if purpose else None
    purpose = (purpose or "学习竞品优点,优化自身产品")

    ui = user_input or (
        f"分析 {target} 与 {', '.join(competitors) or '同类竞品'} 在 {focus[0]} 上的差距"
    )
    return {
        "target_product": target,
        "competitors": competitors,
        "analysis_focus": focus,
        "analysis_purpose": purpose,
        "user_input": ui,
    }


def slugify_domain(seed: str) -> str:
    """生成 domain key:取英文/数字,转小写,空格→下划线。兜底 custom。"""
    import re
    s = re.sub(r"[^0-9a-zA-Z]+", "_", (seed or "").strip()).strip("_").lower()
    return s or "custom"


def wants_persist(answers: dict) -> bool:
    val = answers.get("persist")
    if isinstance(val, list):
        val = val[0] if val else ""
    return bool(val) and "保存" in str(val)


def persist_domain(
    meta: dict,
    domain_key: Optional[str] = None,
    domain_name: Optional[str] = None,
) -> str:
    """把一套 meta 写回 domains.yaml(+ 给缺失产品在 products.yaml 补占位条目)。

    返回最终使用的 domain_key。已存在同名 key 时追加数字后缀避免覆盖。
    """
    import yaml

    target = meta["target_product"]
    competitors = meta.get("competitors") or []
    key = domain_key or slugify_domain(domain_name or target)

    # domains.yaml
    domains_doc = _load_yaml(_DOMAINS_YAML) or {}
    domains_doc.setdefault("domains", {})
    base_key, i = key, 2
    while key in domains_doc["domains"]:
        key = f"{base_key}_{i}"
        i += 1
    domains_doc["domains"][key] = {
        "name": domain_name or f"{target} 竞品分析",
        "sample_path": f"data/sample_sources_{key}.json",
        "target_product": target,
        "competitors": competitors,
        "analysis_focus": meta.get("analysis_focus") or [],
        "analysis_purpose": meta.get("analysis_purpose") or "",
    }
    with _DOMAINS_YAML.open("w", encoding="utf-8") as f:
        yaml.safe_dump(domains_doc, f, allow_unicode=True, sort_keys=False)

    # products.yaml:为没登记过的产品补占位条目(official/pricing 页留空,待补)
    products_doc = _load_yaml(_PRODUCTS_YAML) or {}
    products_doc.setdefault("products", {})
    changed = False
    for name in [target, *competitors]:
        if name and name not in products_doc["products"]:
            products_doc["products"][name] = {
                "aliases": [name],
                "official_pages": [],
                "pricing_pages": [],
            }
            changed = True
    if changed:
        with _PRODUCTS_YAML.open("w", encoding="utf-8") as f:
            yaml.safe_dump(products_doc, f, allow_unicode=True, sort_keys=False)

    return key


# ────────────────────────────────────────────────────────────────────────────
# CLI 向导
# ────────────────────────────────────────────────────────────────────────────

def _ask_cli(choice: Choice) -> object:
    print(f"\n{choice.question}")
    for idx, opt in enumerate(choice.options, 1):
        mark = " (推荐)" if opt in choice.suggested else ""
        print(f"  {idx}. {opt}{mark}")
    if choice.allow_custom:
        print("  0. 其他(自定义输入)")
    raw = input("选择(多选用逗号分隔,回车=用推荐): " if choice.multi else "选择(回车=用推荐): ").strip()

    if not raw:
        return choice.suggested if choice.multi else (choice.suggested[0] if choice.suggested else "")

    picks: list[str] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok == "0" and choice.allow_custom:
            picks.append(input("  自定义值: ").strip())
        elif tok.isdigit() and 1 <= int(tok) <= len(choice.options):
            picks.append(choice.options[int(tok) - 1])
        elif tok:
            picks.append(tok)  # 直接输了文本
    return picks if choice.multi else (picks[0] if picks else "")


def main() -> int:
    print("=" * 60)
    print("竞品分析 · 意图问询向导(intake)")
    print("=" * 60)
    user_input = input("\n用一句话描述你想分析什么(可留空): ").strip()

    questions = intake_questions(user_input)
    answers: dict = {}
    for q in questions:
        answers[q.key] = _ask_cli(q)

    meta = assemble_meta(answers, user_input=user_input or None)
    print("\n--- 解析结果 ---")
    print(f"目标产品: {meta['target_product']}")
    print(f"竞品:     {', '.join(meta['competitors']) or '(无)'}")
    print(f"分析焦点: {', '.join(meta['analysis_focus'])}")
    print(f"分析目的: {meta['analysis_purpose']}")

    if wants_persist(answers):
        key = persist_domain(meta)
        print(f"\n✅ 已写回 config,新行业 key = {key}")
        print(f"   下次复用:  DOMAIN={key} python -m src.graph")
        print(f"   记得补数据: data/sample_sources_{key}.json(或设 ENABLE_LIVE_FETCH=1 实时抓取)")
    else:
        print("\n(仅本次运行,未写盘)")
        print(f"运行: python -m src.graph  # 用上面的参数,或在前端侧栏对应填入")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
