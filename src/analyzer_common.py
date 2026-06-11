"""Analyzer 基座 — 叶子 helper / 预览渲染 / 证据压缩 / prompt 加载 / 进度通道。

三层 DAG 最底层(analyzer_common ← analyzer_fallback/analyzer ← ...):不依赖 LLM 调用步骤
与 node 编排,只提供纯函数与共享进度通道。analyzer.py re-export 全部名字保 back-compat。
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Optional

from .progress import ProgressChannel
from .textutil import smart_truncate

# 四类必备 claim_type(覆盖审计/缺口扫描共用),与 collector.REQUIRED_CLAIM_TYPES 同源语义
_REQUIRED_CT = ("feature_existence", "performance_quality", "pricing", "user_pain")


def _is_demo_loop() -> bool:
    return os.environ.get("DEMO_LOOP", "").strip() in ("1", "true", "True")


# ───────────────────────────────────────────────────────────────────────
# 进度回调
# ───────────────────────────────────────────────────────────────────────

_PROGRESS = ProgressChannel()


def set_progress_callback(cb) -> None:
    """注册 Analyzer 进度事件回调。事件字典:
    {step: 'facts'|'derivations', phase: 'start'|'done'|'repair', issues?, attempt?}
    回调失败不影响主流程。"""
    _PROGRESS.set_callback(cb)


def _emit_progress(**evt) -> None:
    _PROGRESS.emit(**evt)


def _facts_summary(facts: dict) -> str:
    feats = [f.get("name") for f in (facts.get("feature_tree") or {}).get("features", []) if f.get("name")]
    pains = (facts.get("user_persona") or {}).get("pain_points") or []
    parts = []
    if feats:
        parts.append("功能维度：" + "、".join(feats[:4]))
    if pains:
        parts.append(f"识别 {len(pains)} 个用户痛点")
    return "；".join(parts)


def _der_summary(der: dict) -> str:
    recs = der.get("recommendations") or []
    swot = der.get("swot") or {}
    n_s = len(swot.get("strengths") or [])
    n_w = len(swot.get("weaknesses") or [])
    top = recs[0].get("action") if recs else ""
    parts = [f"SWOT：{n_s} 优势 / {n_w} 劣势", f"{len(recs)} 条改进建议"]
    if top:
        parts.append(f"首要：{top[:30]}")
    return "；".join(parts)


_CLAIM_LABELS = {
    "feature_existence": "功能具备性",
    "performance_quality": "性能与质量",
    "pricing": "定价信息",
    "user_pain": "用户痛点",
    "market_signal": "市场信号",
}


def _short(text: object, limit: int = 72) -> str:
    s = str(text or "").strip().replace("\n", " ")
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _target_products(meta: dict) -> list[str]:
    products = [meta.get("target_product"), *(meta.get("competitors") or [])]
    return [str(p) for p in products if p]


def _evidence_ids(
    evidence: list[dict],
    claim_types: set[str] | tuple[str, ...] | list[str],
    product: Optional[str] = None,
    limit: int = 4,
) -> list[str]:
    wanted = set(claim_types)
    ids: list[str] = []
    for ev in evidence:
        if product and ev.get("product") != product:
            continue
        if ev.get("claim_type") not in wanted:
            continue
        eid = ev.get("evidence_id")
        if eid and eid not in ids:
            ids.append(eid)
        if len(ids) >= limit:
            break
    return ids


def _evidence_preview(evidence: list[dict], meta: dict) -> dict:
    products = _target_products(meta)
    by_product = [
        {
            "product": product,
            "count": sum(1 for ev in evidence if ev.get("product") == product),
        }
        for product in products
    ]
    by_claim: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for ev in evidence:
        ct = ev.get("claim_type") or "unknown"
        by_claim[ct] = by_claim.get(ct, 0) + 1
        src = ev.get("collection_source") or ev.get("source_type") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    signals: list[str] = []
    for claim_type in ("user_pain", "performance_quality", "feature_existence", "pricing"):
        for ev in evidence:
            if ev.get("claim_type") != claim_type:
                continue
            text = _short(ev.get("claim") or ev.get("extracted_snippet"), 68)
            if not text:
                continue
            signal = f"{ev.get('product', 'unknown')}: {text}"
            if signal not in signals:
                signals.append(signal)
            if len(signals) >= 4:
                break
        if len(signals) >= 4:
            break
    return {
        "kind": "overview",
        "evidence": {
            "products": by_product,
            "claim_types": [
                {"label": _CLAIM_LABELS.get(k, k), "count": v}
                for k, v in sorted(by_claim.items(), key=lambda kv: -kv[1])
            ],
            "sources": [
                {"label": str(k), "count": v}
                for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])[:5]
            ],
        },
        "signals": signals,
    }


def _facts_preview(facts: dict) -> dict:
    pricing = []
    for product in (facts.get("pricing_model") or {}).get("products") or []:
        tiers = product.get("tiers") or []
        if tiers:
            pricing.append(f"{product.get('name')}: {len(tiers)} 个价位")
        else:
            pricing.append(f"{product.get('name')}: 暂无价位")
    return {
        "kind": "facts",
        "features": [
            _short(f.get("name"), 32)
            for f in (facts.get("feature_tree") or {}).get("features", [])
            if f.get("name")
        ][:6],
        "pain_points": [
            _short(p.get("description"), 56)
            for p in (facts.get("user_persona") or {}).get("pain_points", [])
            if p.get("description")
        ][:5],
        "pricing": pricing[:5],
    }


def _derivations_preview(der: dict) -> dict:
    swot = der.get("swot") or {}
    return {
        "kind": "derivations",
        "recommendations": [
            {
                "action": _short(r.get("action"), 72),
                "priority": (r.get("priority_score") or {}).get("priority"),
            }
            for r in (der.get("recommendations") or [])[:5]
        ],
        "swot": {
            "strengths": len(swot.get("strengths") or []),
            "weaknesses": len(swot.get("weaknesses") or []),
            "opportunities": len(swot.get("opportunities") or []),
            "threats": len(swot.get("threats") or []),
        },
    }


def _safe_price_tier(product: str, evidence_ids: list[str]) -> dict:
    return {
        "tier_name": "待确认价位",
        "billing_cycle": "unknown",
        "price": {"amount": None, "currency": "USD", "normalized_usd_month": None},
        "limits": [],
        "display_limits": "模型调用超时后保守保留定价证据，详情以来源为准",
        "observed_at": "",
        "source_freshness": "unknown",
        "evidence_ids": evidence_ids,
    }




def _norm_tokens(text: str) -> set:
    """归一化成 token 集合(小写、仅留字母数字),用于近似去重相似度。"""
    import re
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _near_dup(a: set, b: set, thresh: float = 0.82) -> bool:
    """两段文本的 token Jaccard ≥ thresh 视为近似重复(同一吐槽的不同措辞)。
    不同档位定价('$10/mo Plus' vs '$20/mo Pro')token 差异大,不会被误判。"""
    if not a or not b:
        return False
    union = len(a | b)
    return union > 0 and len(a & b) / union >= thresh


def prompt_slim_enabled() -> bool:
    """ANALYZER_PROMPT_SLIM=1 → 启用 payload 瘦身(meta 白名单 + derivations 按 section 过滤证据)。

    未设置=完全沿用旧 payload(零行为变化)。实测旧口径下全 run prompt 21.6 万 token,
    其中 derivations 四 section 各带逐字节相同的 45.5k 字符证据(~35%),meta 的
    evidence_plan(采集规划)对 Analyzer LLM 无用却全员搭车。"""
    return os.environ.get("ANALYZER_PROMPT_SLIM", "").strip() in ("1", "true", "True")


# LLM 真正用得上的 meta 字段;evidence_plan/trace 类是 Collector/可观测性的内务,不进 prompt
_LLM_META_KEYS = (
    "report_id", "target_product", "competitors", "analysis_focus",
    "analysis_purpose", "analysis_intent", "data_cutoff",
)


def _slim_meta_for_llm(meta: dict) -> dict:
    return {k: meta[k] for k in _LLM_META_KEYS if k in meta}


def llm_meta(meta: dict) -> dict:
    """payload 组装统一入口:flag 开 → 白名单瘦身;关 → 原样透传。"""
    return _slim_meta_for_llm(meta) if prompt_slim_enabled() else meta


# derivations 各 section 真正需要的证据类型;None=全类型(swot 要全景)。
# 引用合法性不受影响:过滤后是全量证据的子集,R1/R9/sanitize 校验的是全量池。
_DERIV_SECTION_CLAIM_TYPES: dict[str, Optional[set]] = {
    "swot": None,
    "recommendations": {"user_pain", "performance_quality", "feature_existence", "pricing"},
    "positioning_map": {"pricing", "feature_existence", "performance_quality"},
    "competitor_landscape": {"feature_existence", "performance_quality", "market_signal"},
}


def compact_evidence_for_deriv(evidence: list[dict], section: str) -> list[dict]:
    """derivations 专用:按 section 过滤 claim_type + 更紧的 top-K/片段长。

    derivations 主要基于 facts 推导,证据是佐证而非全集,
    用 ANALYZER_DERIV_MAX_PER_TYPE(默认5)/ANALYZER_DERIV_SNIPPET_LEN(默认140)收口。"""
    types = _DERIV_SECTION_CLAIM_TYPES.get(section)
    sub = evidence if types is None else [e for e in evidence if e.get("claim_type") in types]
    per_type = int(os.environ.get("ANALYZER_DERIV_MAX_PER_TYPE", "5"))
    snip = int(os.environ.get("ANALYZER_DERIV_SNIPPET_LEN", "140"))
    return _compact_evidence(sub, per_type=per_type, snip=snip)


def _compact_evidence(evidence: list[dict], per_type: Optional[int] = None,
                      snip: Optional[int] = None) -> list[dict]:
    """给 LLM 的精简证据:按 (claim_type × 产品) 各取 top-K(按可信度)+ 近似去重 + 截短片段。
    防止证据过多时 prompt 爆炸 → 调用超时。全量证据仍用于本地 evidence_id 校验。

    关键:**按产品分桶**取 top-K,而非按 claim_type 全局取——否则多产品分析里,
    低可信度产品(如官网 pricing 0.65 < 聚合站)会被全局 top-8 挤光,导致该产品整块为空
    (实测 Linear 官网定价 $0/$10/$16 在,却因全局截断没喂给 LLM → 报告定价空)。
    feature_tree 调用前已按产品过滤,分桶天然单产品、行为不变。

    去冗余:每桶先按可信度排序,再做近似去重——8 个槽位装 8 个**不同的点**,
    而非同一吐槽的 8 种措辞,提升喂给 LLM 的信息密度。空片段直接丢。
    可调:ANALYZER_MAX_EVIDENCE_PER_TYPE(默认8,现为每产品每类)、ANALYZER_SNIPPET_LEN(默认180)、
    ANALYZER_NEARDUP_THRESH(默认0.82,设 1.1 等于关闭近似去重)。"""
    if per_type is None:
        per_type = int(os.environ.get("ANALYZER_MAX_EVIDENCE_PER_TYPE", "8"))
    if snip is None:
        snip = int(os.environ.get("ANALYZER_SNIPPET_LEN", "180"))
    thresh = float(os.environ.get("ANALYZER_NEARDUP_THRESH", "0.82"))
    by_key: dict[tuple, list[dict]] = {}
    for e in evidence:
        by_key.setdefault((e.get("claim_type", "?"), e.get("product")), []).append(e)
    out: list[dict] = []
    # 单轨:统一按 quality_score 排序(质量门口径),stale 证据降权——
    # 时效性差的证据排到同 claim_type 桶尾,让 current 证据优先进 top-K。
    # pricing 类(TTL=7d)对时效最敏感,stale 定价几乎无参考价值,直接末位。
    def _rank_key(e):
        q = e.get("quality_score")
        base = q if q is not None else (e.get("evidence_confidence", 0) or 0)
        freshness = e.get("source_freshness")
        if freshness == "stale":
            # stale 证据降 0.15 分(约一个质量等级),确保被 current 挤出 top-K
            base -= 0.15
        elif freshness == "unknown":
            base -= 0.05
        if e.get("_recalled"):
            # 池内回捞(evidence_gaps.recall_from_pool)的证据是缺口定向捞回的,
            # 必须进 prompt 视野,否则回捞轮白跑 → 直接顶到桶首
            base += 1.0
        return base
    for lst in by_key.values():
        ranked = sorted(lst, key=_rank_key, reverse=True)
        kept_tok: list[set] = []
        for e in ranked:
            text = (e.get("extracted_snippet") or e.get("claim") or "").strip()
            if not text:
                continue
            tok = _norm_tokens(text)
            if any(_near_dup(tok, kt, thresh) for kt in kept_tok):
                continue  # 近似重复,已有更高可信度的代表
            kept_tok.append(tok)
            # 回捞证据放宽截断:默认 snip 常把档位价/功能细节切掉,正是假性缺口的成因
            if e.get("_recalled"):
                from .evidence_gaps import RECALL_SNIPPET_LEN
                snip_e = max(snip, RECALL_SNIPPET_LEN)
            else:
                snip_e = snip
            out.append({
                "evidence_id": e.get("evidence_id"),
                "product": e.get("product"),
                "claim_type": e.get("claim_type"),
                "source_bias": e.get("source_bias"),
                "source_freshness": e.get("source_freshness"),
                "observed_at": e.get("observed_at"),
                "claim": e.get("claim"),
                "extracted_snippet": smart_truncate(e.get("extracted_snippet") or "", snip_e),
            })
            if len(kept_tok) >= per_type:
                break
    return out


_ROOT = Path(__file__).resolve().parent.parent
_PROMPTS_DIR = _ROOT / "prompts"


# ────────────────────────────────────────────────────────────────────────────
# Prompt 加载
# ────────────────────────────────────────────────────────────────────────────

_prompt_cache: dict[str, str] = {}


def load_prompt(name: str) -> str:
    if name in _prompt_cache:
        return _prompt_cache[name]
    path = _PROMPTS_DIR / f"{name}.md"
    _prompt_cache[name] = path.read_text(encoding="utf-8")
    return _prompt_cache[name]


# ────────────────────────────────────────────────────────────────────────────
# evidence 引用遍历(Analyzer 自校验 + Reviewer R1 共用)
# ────────────────────────────────────────────────────────────────────────────


# Analyzer facts 三 section 的 claim_types + 分段指令(facts step / 缺口补采共用)
# facts 三个顶层 section 各自独立 → 拆成并行子问答,每个只喂相关 claim_type、只输出该 section,
# 输出量天然减半/三分,既躲开"顶满 max_tokens 被截断 → 残缺 JSON"悬崖,又并行更快。
# 注意:feature_tree 是跨产品对比(gap.winner),所以按 section 拆而不是按产品拆,保对比结构完整。
_FACTS_SECTIONS = {
    "feature_tree": {
        "claim_types": ("feature_existence", "performance_quality", "user_pain"),
        "instruct": "本次任务只输出 `feature_tree` 这一个顶层字段(覆盖所有有证据的功能,每个 feature 必须覆盖 "
                    "target + ≥1 competitor)。不要输出 pricing_model / user_persona。",
    },
    "pricing_model": {
        "claim_types": ("pricing", "user_pain", "market_signal"),
        "instruct": "本次任务只输出 `pricing_model` 这一个顶层字段(覆盖所有有定价证据的产品 + pricing_gap)。"
                    "不要输出 feature_tree / user_persona。\n"
                    "定价提取要求:\n"
                    "- **逐档列全**:从免费档到最高付费档,证据里出现的每个**命名套餐**都要单列一条 tier"
                    "(如 Free / Plus / Business / Enterprise),不要只取一档、不要合并。\n"
                    "- **同一套餐的年付/月付算同一档**:price 用**月度归一**值(normalized_usd_month);"
                    "年付价请换算成每月(年付总额/12 或证据直接给的「per month billed annually」数),"
                    "可在 billing_cycle / note 注明另一计费周期价。\n"
                    "- 每档:{tier_name, segment, price:{amount,currency,normalized_usd_month}, evidence_ids}。"
                    "价格只能来自 pricing 证据,拿不准的档宁可不列,**不要编价**。\n"
                    "- **segment(面向哪类用户)**:据定价页的档位定位归类——`个人`(Free/Personal/Individual/Pro 个人版)、"
                    "`团队`(Team/按人计费的中间档)、`企业`(Enterprise/Business/联系销售档)、`通用`(无法判断时)。"
                    "这样定价表能直接回答「不同规模用户各该选哪档」。\n"
                    "- 免费档只列一条(amount=0);不要重复输出同价档位。",
    },
    "user_persona": {
        "claim_types": ("user_pain", "market_signal", "performance_quality"),
        "instruct": "本次任务只输出 `user_persona` 这一个顶层字段"
                    "(user_segments + pain_points + praise_points)。不要输出 feature_tree / pricing_model。\n"
                    "- pain_points:高频负向反馈/痛点(保持原结构);\n"
                    "- praise_points:高频正向反馈/被用户反复称赞的亮点。每条 {praise_id:PR001..,"
                    "description, frequency:{level,count,sample_size,evidence_ids}, affected_products, confidence}。\n"
                    "- praise_points.frequency.evidence_ids 优先用 performance_quality 的正面证据;"
                    "**无正向证据就给空数组,严禁为凑数把痛点/负面证据塞进来**。",
    },
}
