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
from datetime import date
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

# 启发式兜底时的一句话引导(LLM 路径会用更贴合的 focus_hints 覆盖)
_FOCUS_HINTS_GENERIC = {
    "代码补全体验": "补全的准确率、响应延迟、跨文件理解——AI 编程工具的核心体验差异",
    "团队任务管理体验": "任务拆分、看板/视图、进度跟踪对团队协作效率的影响",
    "界面设计协作体验": "多人实时协同、组件复用、设计到研发交付的顺畅度",
    "通用对话与推理体验": "复杂推理、长上下文、指令遵循与稳定性的综合表现",
    "后端开发体验与定价": "数据库/鉴权等开箱能力、自托管自由度与计费模型",
    "定价策略": "档位划分、计费口径、性价比与企业采购友好度",
    "核心功能完整度": "覆盖核心场景的功能广度与深度,有无明显短板",
    "用户体验与上手成本": "新手门槛、学习曲线、文档与社区支持",
    "集成与生态": "与现有工具链的打通、插件/API 生态的丰富度",
}


# 痛点/流失归因类问题(「为什么流走」「在吐槽什么」)专用焦点 —— 以痛点而非功能跑分打头。
# 这类问题若被框成逐功能 0-5 评分,会逼 analyzer 对无评分证据的格子硬凑「推测」。
_PAIN_FOCUS = ["高频吐槽与核心痛点", "用户流失与迁移动因", "短板与缺失能力"]
_FOCUS_HINTS_GENERIC.update({
    "高频吐槽与核心痛点": "用户最集中抱怨的点——最能解释满意度与口碑差异",
    "用户流失与迁移动因": "用户从哪流向哪、为何迁移——直接回答「为什么流失」",
    "短板与缺失能力": "相对竞品明显缺失或拖后腿的能力,常是流失推手",
})

# 意图类型 → 主打分析目的(把最贴合的目的提到最前)
_PURPOSE_BY_INTENT = {
    "pain_attribution": "理解用户流失原因与核心痛点",
    "selection": "辅助团队/技术选型",
    "market_entry": "评估是否进入该市场",
    "pricing": "定价策略参考",
}
_PAIN_KEYWORDS = ("为什么", "为何", "流失", "流向", "流走", "吐槽", "抱怨", "不满", "差评",
                  "诟病", "弃用", "放弃", "转投", "迁移", "退订", "槽点", "痛点", "缺点", "短板")


def _detect_intent(user_input: str) -> str:
    """粗粒度意图类型,用于把焦点维度拉到对的方向(痛点归因别做成功能跑分)。"""
    t = user_input or ""
    low = t.lower()
    if any(k in t for k in _PAIN_KEYWORDS):
        return "pain_attribution"
    if any(k in t for k in ("选型", "选择", "选谁", "怎么选", "帮我选", "辅助选")):
        return "selection"
    if any(k in t for k in ("进入", "入场", "要不要做", "值不值", "值得")):
        return "market_entry"
    if any(k in low for k in ("价格", "定价", "pricing", "收费", "成本", "性价比")):
        return "pricing"
    return "feature_compare"


def _heuristic_focus_hints(focus_list: list[str]) -> dict:
    return {
        f: _FOCUS_HINTS_GENERIC.get(f, f"对比各产品在「{f}」上的差距")
        for f in focus_list
    }


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
    hints: dict = field(default_factory=dict)  # {选项: 一句话说明},前端逐项展示引导

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "question": self.question,
            "options": self.options,
            "multi": self.multi,
            "suggested": self.suggested,
            "allow_custom": self.allow_custom,
            "hints": self.hints,
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


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _norm_key(s: str) -> str:
    """规范化匹配键:小写 + 去所有空白。'GitHub Copilot' / 'githubcopilot' → 同键。"""
    return "".join((s or "").lower().split())


def _product_alias_map() -> dict:
    """{规范化键: 规范名} —— 来自 products.yaml 的 name + aliases。"""
    out: dict = {}
    for name, meta in load_products().items():
        for k in [name, *(meta.get("aliases") or [])]:
            nk = _norm_key(k)
            if nk and nk not in out:
                out[nk] = name
    return out


def _canonicalize_names(names: list[str], amap: Optional[dict] = None) -> list[str]:
    """把名字对齐到 products.yaml 规范名(命中 alias 时),并大小写/空格无关去重。
    未登记的陌生竞品(Penpot/Framer 等)原样保留,不丢。"""
    amap = amap if amap is not None else _product_alias_map()
    seen: set = set()
    out: list[str] = []
    for raw in names or []:
        name = (raw or "").strip()
        if not name:
            continue
        canon = amap.get(_norm_key(name), name)
        ck = _norm_key(canon)
        if ck and ck not in seen:
            seen.add(ck)
            out.append(canon)
    return out


def _canonicalize_draft(draft: dict) -> dict:
    """规范化 target/竞品候选(去重 + 对齐 products.yaml 命名),同步重映射 competitor_hints。
    解决 LLM 偶发把 'GitHub Copilot' 与 'GitHubCopilot' 混排的问题。"""
    amap = _product_alias_map()
    for key in ("target_candidates", "competitors_candidates", "competitors_suggested"):
        if draft.get(key):
            draft[key] = _canonicalize_names(draft[key], amap)
    # target 不应混进竞品候选;竞品候选不含 target
    target = (draft.get("target_candidates") or [""])[0]
    if target:
        draft["competitors_candidates"] = [
            c for c in (draft.get("competitors_candidates") or []) if _norm_key(c) != _norm_key(target)
        ]
    hints = draft.get("competitor_hints") or {}
    if hints:
        remapped: dict = {}
        for k, v in hints.items():
            canon = amap.get(_norm_key(k), (k or "").strip())
            if canon and canon not in remapped:
                remapped[canon] = v
        draft["competitor_hints"] = remapped
    cand = set(draft.get("competitors_candidates") or [])
    if draft.get("competitors_suggested"):
        draft["competitors_suggested"] = [c for c in draft["competitors_suggested"] if c in cand]
    return draft


def _domain_products(dom: dict) -> list[str]:
    return _dedupe([dom.get("target_product", ""), *(dom.get("competitors") or [])])


def _infer_domain_key(
    domains: dict,
    hit_products: list[str],
    domain_hint: Optional[str],
    user_input: str,
) -> Optional[str]:
    """根据显式 hint、命中产品、行业名/焦点词推断本次任务所属 domain。"""
    if domain_hint in domains:
        return domain_hint

    if hit_products:
        ranked: list[tuple[int, str]] = []
        hit_set = set(hit_products)
        for key, dom in domains.items():
            score = len(hit_set & set(_domain_products(dom)))
            if score:
                ranked.append((score, key))
        if ranked:
            ranked.sort(key=lambda x: x[0], reverse=True)
            return ranked[0][1]

    text = (user_input or "").lower()
    for key, dom in domains.items():
        signals = [key, dom.get("name", ""), *(dom.get("analysis_focus") or [])]
        if any(str(s).lower() in text for s in signals if s):
            return key
    return None


def _focus_options_for_domain(dom_cfg: dict, user_input: str) -> list[str]:
    """启发式兜底的焦点选项:以本域配置的焦点为主,只在用户明确提到定价时补「定价策略」,
    不足 3 个才补少量通用维度。不再跨域注入(如设计任务里冒出代码补全/任务管理)。"""
    text = (user_input or "").lower()
    opts: list[str] = list(dom_cfg.get("analysis_focus") or [])
    if any(k in text for k in ("价格", "定价", "pricing", "price", "收费", "成本")):
        opts.append("定价策略")
    # 仅在选项过少时补通用维度兜底,避免「每次都是同一套尾巴」
    for generic in ("核心功能完整度", "用户体验与上手成本"):
        if len(_dedupe(opts)) >= 3:
            break
        opts.append(generic)
    return _dedupe(opts)


def _suggest_focus(dom_cfg: dict, focus_candidates: list[str], user_input: str) -> str:
    text = (user_input or "").lower()
    keyed = [
        (("价格", "定价", "pricing", "price", "收费"), "定价策略"),
        (("代码", "补全", "coding", "autocomplete"), "代码补全体验"),
        (("任务", "项目", "协作", "project", "task"), "团队任务管理体验"),
    ]
    for keywords, focus in keyed:
        if focus in focus_candidates and any(k in text for k in keywords):
            return focus
    return (dom_cfg.get("analysis_focus") or focus_candidates or [""])[0]


def _filter_known_to_domain(items: list[str], products: dict, domain_products: list[str]) -> list[str]:
    """过滤已知但跨域的产品；未知项保留给 LLM/用户自定义场景。"""
    domain_set = set(domain_products)
    return [
        item for item in items
        if item not in products or item in domain_set
    ]


def _scope_draft_to_domain(draft: dict, base: dict, products: dict) -> dict:
    """LLM 草稿如果混进跨域已知产品，用启发式 domain 结果收窄。"""
    domain_products = base.get("_domain_products") or []
    if not domain_products:
        return draft

    out = dict(draft)
    target = _filter_known_to_domain(out.get("target_candidates") or [], products, domain_products)
    comp = _filter_known_to_domain(out.get("competitors_candidates") or [], products, domain_products)
    # 纯 LLM 信任:只用 LLM 给的(域过滤后)列表,不再把 config 写死的老竞品 +base 注入回来——
    # 那会用 config 裸名(Kling/Runway)重造跨语言/变体重复 + 空 hint 噪声。config 竞品已通过
    # known_products 喂给 LLM;它觉得相关会自己带,觉得凉了就不带(隐式剔旧)。base 仅在 LLM 空时兜底。
    out["target_candidates"] = _dedupe(target) or (base.get("target_candidates") or [])
    out["competitors_candidates"] = _dedupe(comp) or (base.get("competitors_candidates") or [])
    out["competitors_suggested"] = [
        c for c in (out.get("competitors_suggested") or base.get("competitors_suggested") or [])
        if c in out["competitors_candidates"]
    ] or (base.get("competitors_suggested") or [])
    out["focus_candidates"] = out.get("focus_candidates") or base.get("focus_candidates")
    out["focus_suggested"] = out.get("focus_suggested") or base.get("focus_suggested")
    out["focus_hints"] = out.get("focus_hints") or base.get("focus_hints") or {}
    out["competitor_hints"] = out.get("competitor_hints") or base.get("competitor_hints") or {}
    out["domain_name"] = out.get("domain_name") or base.get("domain_name")
    out["domain_key"] = out.get("domain_key") or base.get("domain_key")
    return out


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
    # 与 llm.py 的 key 解析对齐:默认 LLM 已切 MiMo(LLM_API_KEY),旧版只认 ARK_API_KEY
    # 会让 MiMo 配置静默退化成纯启发式 → 智能竞品发现(含新锐 AI)整段被跳过。
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY"))


def _guess_target_for_discovery(user_input: str) -> str:
    """竞品发现搜索用的目标名:优先取用户输入里命中的已知产品,否则取整句(让搜索自己消歧)。"""
    text = user_input or ""
    amap = _product_alias_map()
    for nk, canon in amap.items():
        # 用别名原文匹配(不区分大小写),命中即用规范名
        for meta in [load_products().get(canon, {})]:
            for alias in [canon, *(meta.get("aliases") or [])]:
                if alias and alias.lower() in text.lower():
                    return canon
    return text.strip()


def _competitor_web_context(user_input: str, max_results: int = 8) -> list[dict]:
    """实时搜索竞品线索 —— 破解 LLM 训练截止盲区(认不出 Stitch/Claude Designer 这类新锐)。
    搜「X alternatives/competitors 2026」,把标题+摘要交给 intake LLM 去提取主流 + 新锐 AI 玩家。
    无搜索能力时返回 [](启发式/无网络路径不受影响)。可用 INTAKE_DISCOVER=0 关闭。"""
    if os.environ.get("INTAKE_DISCOVER", "1").strip() in ("0", "false", "False"):
        return []
    try:
        from . import search
        if not search.search_available():
            return []
        target = _guess_target_for_discovery(user_input)
        if not target:
            return []
        year = date.today().year
        queries = [
            f"{target} alternatives {year}",
            f"best {target} competitors latest",
            f"AI {target} alternative new",  # 偏向召回 AI 原生/新锐颠覆者
        ]
        seen: set = set()
        out: list[dict] = []
        for q in queries:
            try:
                for r in search.web_search(q, max_results=max_results):
                    title = (r.get("title") or "").strip()
                    key = title.lower()
                    if not title or key in seen:
                        continue
                    seen.add(key)
                    out.append({"title": title, "snippet": (r.get("content") or "")[:200]})
            except Exception:  # noqa: BLE001 — 单条搜索失败不阻断
                continue
            if len(out) >= 18:
                break
        return out[:18]
    except Exception as e:  # noqa: BLE001
        print(f"[intake] 竞品发现搜索失败,跳过: {type(e).__name__}: {e}")
        return []


# 提取产品名时要剔的噪声词(榜单/通用/月份,不是产品)
_NAME_STOPWORDS = {
    "free", "new", "the", "and", "for", "with", "of", "in", "to", "your", "our",
    "why", "how", "what", "when", "where", "this", "that", "it", "its",
    "tools", "tool", "app", "apps", "software", "ai", "platform",
    "pricing", "price", "plan", "plans", "features", "feature",
    "paid", "discover", "learn", "read", "more", "here", "get", "see",
    "plus", "pro", "team", "all", "best", "top",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}
# 纯榜单措辞——真实产品名里绝不会出现,命中任一词即整条丢弃
_LISTICLE_MARKERS = {
    "best", "top", "vs", "vs.", "alternative", "alternatives",
    "competitor", "competitors", "review", "reviews", "compare",
    "comparison", "guide", "list", "roundup",
}


def _extract_candidate_names_from_signals(signals: list[dict], target: str, limit: int = 6) -> list[str]:
    """无 LLM 时的轻量竞品名抽取:从「X alternatives 2026」搜来的标题/摘要里,
    挖出现≥2 次的「品牌名样式 token」(首字母大写/带数字如 v0/驼峰如 GitHub)。
    频次≥2 压噪;再叠加榜单措辞/含 target 名/月份/通用词三道过滤,挡掉
    'Best Cursor''Cursor Alternatives''March''Paid' 这类伪产品名。"""
    import re
    target_low = (target or "").lower().replace(" ", "")
    # 品牌 token:首字母大写词(可含内部大写/数字,如 GitHub、v0、GPT-4),或纯数字+字母如 v0
    tok_re = re.compile(r"\b([A-Z][A-Za-z0-9.]+(?:\s[A-Z][A-Za-z0-9.]+)?|v\d[\w.]*)\b")
    freq: dict[str, int] = {}
    disp: dict[str, str] = {}
    for sig in signals or []:
        blob = f"{sig.get('title', '')} {sig.get('snippet', '')}"
        for m in tok_re.finditer(blob):
            name = m.group(1).strip(" .")
            low = name.lower().replace(" ", "")
            words = name.lower().split()
            if (len(name) < 2 or low in _NAME_STOPWORDS or low == target_low
                    or (target_low and target_low in low)          # 'Best Cursor'/'Cursor Alternatives'
                    or any(w in _LISTICLE_MARKERS for w in words)   # 榜单措辞
                    or all(w in _NAME_STOPWORDS for w in words)):
                continue
            freq[low] = freq.get(low, 0) + 1
            disp.setdefault(low, name)
    ranked = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)
    return [disp[k] for k, n in ranked if n >= 2][:limit]


def _propose_via_llm(user_input: str) -> Optional[dict]:
    try:
        from .llm import get_llm
        signals = _competitor_web_context(user_input)
        if signals:
            print(f"[intake] 竞品发现:实时搜到 {len(signals)} 条线索,交给 LLM 提取主流+新锐")
        draft = get_llm().call_json(
            system_prompt=_load_prompt("intake"),
            user_payload={
                "user_input": user_input,
                "current_date": date.today().isoformat(),
                "known_products": _known_product_names(),
                "web_competitor_signals": signals,
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
    domain_key = _infer_domain_key(domains, hit_products, domain_hint, user_input)
    dom_cfg = domains.get(domain_key or "", {})
    scoped_products = _domain_products(dom_cfg) if dom_cfg else list(products)
    if not scoped_products:
        scoped_products = list(products)

    # target 候选:命中的产品在前,然后是行业默认 target,再补其它已知产品
    target_candidates: list[str] = [p for p in hit_products if p in scoped_products]
    if dom_cfg.get("target_product") and dom_cfg["target_product"] not in target_candidates:
        target_candidates.append(dom_cfg["target_product"])
    for p in scoped_products:
        if p not in target_candidates:
            target_candidates.append(p)

    target = target_candidates[0] if target_candidates else ""
    competitors_candidates = [p for p in scoped_products if p != target]
    explicit_competitors = [p for p in hit_products if p != target and p in competitors_candidates]
    # 推荐尽量覆盖:命中的竞品 + 本域配置竞品 + 其余候选,去重后取前 4(不再只给 2-3)
    _sug_pool = explicit_competitors + [
        c for c in (dom_cfg.get("competitors") or []) if c != target
    ] + competitors_candidates
    competitors_suggested = _dedupe([c for c in _sug_pool if c in competitors_candidates])[:4]

    comp_hints: dict = {c: "同品类竞品" for c in competitors_candidates}
    # 无 LLM 才跑启发式竞品发现:从实时搜索里挖配置外玩家,补进候选并标【实时发现】。
    # 仅在 LLM 不可用时启用——LLM 路径也会把本函数当 base 合并,跑发现会把噪声名泄漏进
    # 干净的 LLM 草稿(经 _scope_draft_to_domain 拼接)。LLM 在时,发现交给读完整 prompt 的它。
    if not _llm_available():
        try:
            _known_keys = {_norm_key(c) for c in competitors_candidates} | {_norm_key(target)}
            for name in _extract_candidate_names_from_signals(
                    _competitor_web_context(user_input), target):
                if _norm_key(name) not in _known_keys:
                    competitors_candidates.append(name)
                    comp_hints[name] = "【实时发现】搜索召回的同品类玩家,可能含新锐 AI"
                    _known_keys.add(_norm_key(name))
        except Exception as e:  # noqa: BLE001 — 发现失败不阻断启发式主流程
            print(f"[intake] 启发式竞品发现跳过: {type(e).__name__}: {e}")

    intent = _detect_intent(user_input)
    focus_candidates = _focus_options_for_domain(dom_cfg, user_input) if dom_cfg else _all_focus_options()
    focus_suggested = _suggest_focus(dom_cfg, focus_candidates, user_input)
    # 痛点/流失归因类问题:把痛点维度提到最前,别让"功能跑分"维度当主角(那会逼出一堆「推测」)
    if intent == "pain_attribution":
        focus_candidates = _dedupe(_PAIN_FOCUS + focus_candidates)
        focus_suggested = _PAIN_FOCUS[0]

    # purpose 按意图微调(启发式兜底;LLM 路径会给更贴合的)
    purpose_candidates = list(_FALLBACK_PURPOSE)
    lead_purpose = _PURPOSE_BY_INTENT.get(intent)
    if lead_purpose:
        purpose_candidates = [lead_purpose, *[p for p in purpose_candidates if p != lead_purpose]]
    dom_purpose = dom_cfg.get("analysis_purpose")
    if dom_purpose and intent in ("feature_compare",):
        # 仅功能对比类沿用本域默认目的;痛点/选型等意图以意图目的优先
        purpose_candidates = _dedupe([dom_purpose, *purpose_candidates])
    purpose_suggested = purpose_candidates[0]

    return {
        "domain_key": domain_key or "",
        "domain_name": dom_cfg.get("name", ""),
        "analysis_intent": intent,
        "target_candidates": target_candidates,
        "competitors_candidates": competitors_candidates,
        "competitors_suggested": competitors_suggested,
        "competitor_hints": comp_hints,
        "focus_candidates": focus_candidates,
        "focus_hints": _heuristic_focus_hints(focus_candidates),
        "focus_suggested": focus_suggested,
        "purpose_candidates": purpose_candidates,
        "purpose_suggested": purpose_suggested,
        "_domain_products": scoped_products if dom_cfg else [],
        "_hit_products": hit_products,
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
            return _canonicalize_draft(_scope_draft_to_domain(draft, base, load_products()))
    return _canonicalize_draft(_propose_heuristic(user_input, domain_hint))


def _partial_json_string(acc: str, field: str) -> Optional[str]:
    """从(可能尚未闭合的)JSON 文本里取出某 string 字段的当前值,用于流式逐字显示。
    定位字段名后的冒号与起始引号,读到下一个未转义引号(或文本末尾,即还没生成完)。
    轻量反转义 \\n \\t \\" \\\\,够展示用。"""
    import re
    m = re.search(r'"' + re.escape(field) + r'"\s*:\s*"', acc)
    if not m:
        return None
    i = m.end()
    out: list[str] = []
    while i < len(acc):
        ch = acc[i]
        if ch == "\\" and i + 1 < len(acc):
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(acc[i + 1], acc[i + 1]))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out)


def propose_stream(user_input: str, domain_hint: Optional[str] = None):
    """流式版 propose。生成器,逐步 yield 给上层(SSE)推送:
        ("status", text)      阶段性真实进度(检索竞品线索 / 识别玩家)
        ("reasoning", delta)  模型思考增量(逐字;优先用思维链通道,否则从 JSON reasoning 字段抠)
        ("draft", dict)       最终草稿(与 propose() 返回同构)
    无 LLM / 流式失败 → 直接给启发式 draft,不阻塞首屏。"""
    base = _propose_heuristic(user_input, domain_hint)
    if not _llm_available():
        yield ("draft", _canonicalize_draft(base))
        return

    yield ("status", "正在检索竞品线索…")
    signals = _competitor_web_context(user_input)
    if signals:
        yield ("status", f"命中 {len(signals)} 条竞品线索,交给模型识别主流 + 新锐玩家…")
    else:
        yield ("status", "正在分析意图、竞品格局与焦点维度…")

    draft: Optional[dict] = None
    try:
        from .llm import get_llm
        acc: list[str] = []
        emitted = 0          # 已 yield 的 JSON-reasoning 字符数(无思维链通道时的兜底游标)
        saw_reasoning = False
        for kind, payload in get_llm().stream_json(
                system_prompt=_load_prompt("intake"),
                user_payload={
                    "user_input": user_input,
                    "current_date": date.today().isoformat(),
                    "known_products": _known_product_names(),
                    "web_competitor_signals": signals,
                },
                max_tokens=1024, label="intake"):
            if kind == "reasoning":
                saw_reasoning = True
                yield ("reasoning", payload)
            elif kind == "answer":
                acc.append(payload)
                if not saw_reasoning:
                    # 模型无独立思维链通道 → 从 JSON 的 reasoning 字段(已前置)里抠出来逐字推
                    cur = _partial_json_string("".join(acc), "reasoning")
                    if cur and len(cur) > emitted:
                        yield ("reasoning", cur[emitted:])
                        emitted = len(cur)
            elif kind == "done":
                if isinstance(payload, dict) and payload.get("target_candidates"):
                    draft = payload
    except Exception as e:  # noqa: BLE001 — 流式失败回退启发式,不让澄清页卡死
        print(f"[intake] 流式草拟失败,回退启发式: {type(e).__name__}: {e}")

    if draft:
        for k, v in base.items():
            draft.setdefault(k, v)
        final = _canonicalize_draft(_scope_draft_to_domain(draft, base, load_products()))
    else:
        final = _canonicalize_draft(base)
    yield ("draft", final)


# ────────────────────────────────────────────────────────────────────────────
# 草稿 → 选择题
# ────────────────────────────────────────────────────────────────────────────

def build_questions(draft: dict) -> list[Choice]:
    target_opts = draft.get("target_candidates") or []
    comp_opts = draft.get("competitors_candidates") or []
    comp_sug = [c for c in (draft.get("competitors_suggested") or []) if c in comp_opts]
    focus_opts = draft.get("focus_candidates") or []
    focus_sug = draft.get("focus_suggested") or (focus_opts[0] if focus_opts else "")
    focus_hints = draft.get("focus_hints") or {}
    comp_hints = draft.get("competitor_hints") or {}
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
            hints={k: v for k, v in comp_hints.items() if k in comp_opts},
        ),
        Choice(
            key="focus",
            question="这次分析的焦点维度是什么?(可多选)",
            options=focus_opts,
            multi=True,
            suggested=[focus_sug] if focus_sug else [],
            hints={k: v for k, v in focus_hints.items() if k in focus_opts},
        ),
        Choice(
            key="purpose",
            question="分析目的是什么?(可多选,影响建议的取向)",
            options=purpose_opts,
            multi=True,
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
    purpose_list = _as_list(answers.get("purpose"))
    purpose = " / ".join(purpose_list) if purpose_list else "学习竞品优点,优化自身产品"

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
