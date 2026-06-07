"""Analyzer 确定性后处理(sanitize / soften)——从 analyzer.py 抽出的自包含簇。

这些函数对 facts / derivations / schema 做**确定性**清洗,不调 LLM、不发进度事件:
- sanitize_*: 删除不存在/类型不匹配的 evidence_id 引用,重算 priority_score(R2/R5 兜底)
- soften_overgeneralization: 样本不足时把"大量/普遍/多数用户"降级为"部分用户"(R6 兜底)

analyzer.py re-export 这些名字,保证既有 callsite 与测试(`from src.analyzer import ...`)零变化。
"""
from __future__ import annotations

import re


def _filter_evidence_ids(
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict],
    allowed_claim_types: set[str],
) -> tuple[list[str], int]:
    kept: list[str] = []
    dropped = 0
    for eid in evidence_ids or []:
        ev = evidence_by_id.get(eid)
        if ev and ev.get("claim_type") in allowed_claim_types:
            kept.append(eid)
        else:
            dropped += 1
    return kept, dropped


def sanitize_schema_evidence_refs(schema: dict, evidence: list[dict]) -> tuple[dict, int]:
    """Remove evidence IDs that do not exist in the current raw_evidence packet."""
    valid_ids = {e["evidence_id"] for e in evidence if e.get("evidence_id")}
    dropped = 0
    ref_keys = {"evidence_ids", "support_evidence_ids", "representative_evidence_ids"}

    def walk(obj) -> None:
        nonlocal dropped
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                if key in ref_keys and isinstance(value, list):
                    filtered = [eid for eid in value if eid in valid_ids]
                    dropped += len(value) - len(filtered)
                    obj[key] = filtered
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(schema)
    return schema, dropped


def sanitize_derivations(derivations: dict, facts: dict, evidence: list[dict]) -> tuple[dict, int]:
    """确定性修复 derivations(替代昂贵的 LLM 重跑,facts 已用同款策略):
    - 删除 swot/rec 中不存在的 evidence_id
    - 删除 rec 中不在 facts 的 source_feature_ids / source_pain_ids
    - 按 weights 重算 priority_score.final_score,保证 R5 公式自洽
    无法凭空补的(如 rec 一个有效引用都没有)留给 Reviewer 判定。"""
    valid_ids = {e["evidence_id"] for e in evidence if e.get("evidence_id")}
    valid_fids = {
        f["feature_id"] for f in facts.get("feature_tree", {}).get("features", []) if f.get("feature_id")
    }
    valid_pids = {
        p["pain_id"] for p in facts.get("user_persona", {}).get("pain_points", []) if p.get("pain_id")
    }
    dropped = 0

    def _keep(ids: list[str] | None, allowed: set[str]) -> list[str]:
        nonlocal dropped
        src = ids or []
        kept = [x for x in src if x in allowed]
        dropped += len(src) - len(kept)
        return kept

    swot = derivations.get("swot") or {}
    for dim in ("strengths", "weaknesses", "opportunities", "threats"):
        for item in swot.get(dim) or []:
            item["evidence_ids"] = _keep(item.get("evidence_ids"), valid_ids)

    for rec in derivations.get("recommendations") or []:
        rec["evidence_ids"] = _keep(rec.get("evidence_ids"), valid_ids)
        rec["source_feature_ids"] = _keep(rec.get("source_feature_ids"), valid_fids)
        rec["source_pain_ids"] = _keep(rec.get("source_pain_ids"), valid_pids)
        ps = rec.get("priority_score") or {}
        weights = ps.get("weights") or {}
        if weights:
            try:
                ps["final_score"] = round(
                    sum(float(ps.get(k, 0)) * float(w) for k, w in weights.items()), 2
                )
            except (TypeError, ValueError):
                pass
    return derivations, dropped


def sanitize_facts_evidence_refs(facts: dict, evidence: list[dict]) -> tuple[dict, int]:
    """Drop evidence refs whose claim_type cannot satisfy the target schema field.

    This is a deterministic last line of defense for Reviewer R2. LLM repair gets
    the first chance to replace IDs with better ones; this function removes any
    remaining incompatible IDs so full mode does not fail on avoidable type drift.
    """
    evidence_by_id = {e["evidence_id"]: e for e in evidence}
    dropped = 0

    def apply(obj: dict, key: str, allowed: set[str]) -> None:
        nonlocal dropped
        filtered, n = _filter_evidence_ids(obj.get(key) or [], evidence_by_id, allowed)
        obj[key] = filtered
        dropped += n

    for feat in facts.get("feature_tree", {}).get("features", []):
        for pdata in (feat.get("products") or {}).values():
            apply(pdata, "support_evidence_ids", {"feature_existence"})
            qs = pdata.get("quality_score") or {}
            apply(qs, "evidence_ids", {"performance_quality", "user_pain"})
            agg = qs.get("aggregation") or {}
            apply(agg, "representative_evidence_ids", {
                "performance_quality", "user_pain", "feature_existence",
            })
        gap = feat.get("gap") or {}
        apply(gap, "evidence_ids", {"feature_existence", "performance_quality", "user_pain"})

    pricing = facts.get("pricing_model") or {}
    for product in pricing.get("products", []):
        for tier in product.get("tiers") or []:
            apply(tier, "evidence_ids", {"pricing"})
    apply(pricing.get("pricing_gap") or {}, "evidence_ids", {"pricing", "user_pain", "market_signal"})

    persona = facts.get("user_persona") or {}
    for segment in persona.get("user_segments", []):
        apply(segment, "evidence_ids", {"user_pain", "market_signal", "performance_quality"})
    for pain in persona.get("pain_points", []):
        freq = pain.get("frequency") or {}
        apply(freq, "evidence_ids", {"user_pain", "performance_quality"})

    return facts, dropped


# 扩大化措辞 → 收敛替换(确定性,不赌小模型自觉遵守 prompt 纪律)。
# R6 语义审计会咬"大量/普遍 + 单条证据"这类过度断言;样本量不足时降级为"部分用户"。
_OVERGEN_SUBS = [
    # 量词 + (可夹少量修饰,如"现有Copilot") + 用户/开发者/反馈 → 部分…
    # {0,10}? 非贪婪且仅匹配中英文(遇标点/空格即止,不跨子句),覆盖"大量现有Copilot用户"这类。
    (re.compile(r"(?:大量|大批|众多|绝大多数|大多数|多数)([一-鿿A-Za-z]{0,10}?)(用户|开发者|反馈)"),
     r"部分\1\2"),
    (re.compile(r"(用户|开发者)普遍"), r"部分\1"),
    (re.compile(r"普遍(反馈|认为|抱怨|遇到|存在|觉得|表示)"), r"部分用户\1"),
    (re.compile(r"广泛(反馈|存在|出现|抱怨|使用)"), r"部分场景\1"),
]


def _soften_text(text):
    """把单条文本里的扩大化量词降级;返回 (新文本, 是否改动)。"""
    if not isinstance(text, str) or not text:
        return text, False
    new = text
    for re_, repl in _OVERGEN_SUBS:
        new = re_.sub(repl, new)
    return new, (new != text)


def _freq_is_truly_frequent(freq: dict) -> bool:
    """高频且 ≥3 独立样本 → 认定真普遍,保留措辞;否则需收敛。"""
    return str(freq.get("level")) == "high" and (freq.get("sample_size") or 0) >= 3


def soften_overgeneralization(schema: dict) -> int:
    """确定性兜底:样本量不足时,把正文各处"大量/普遍/多数用户"降级成"部分用户"。
    R6 收敛——prompt 纪律在小模型上不一定咬得住,代码兜底才可靠。就地修改 schema,返回改写条数。

    覆盖(过度泛化真正高发的字段,各带"证据/样本足够则保留"的门):
      facts        : feature 的 quality_score.basis、user_persona 的 pain_points/praise_points/user_segments
      derivations  : swot 四象限、competitor_landscape(direct/indirect/alternative + selection_rationale)、recommendations
    facts / derivations 任传其一都安全:缺的 key 自动跳过。"""
    changes = 0

    def soft_field(obj: dict, key: str) -> None:
        nonlocal changes
        if isinstance(obj, dict) and isinstance(obj.get(key), str):
            obj[key], c = _soften_text(obj[key])
            changes += int(c)

    # ① feature 质量 basis:无足够用户体验证据(≤2)时不得用"大量/多数用户"
    for feat in (schema.get("feature_tree") or {}).get("features", []) or []:
        for pdata in (feat.get("products") or {}).values():
            qs = pdata.get("quality_score") or {}
            if len(qs.get("evidence_ids") or []) <= 2:
                soft_field(qs, "basis")

    # ② user_persona:痛点 / 表扬点(样本不足才收敛) + 画像(一律收敛)
    persona = schema.get("user_persona") or {}
    for item in (persona.get("pain_points") or []) + (persona.get("praise_points") or []):
        freq = item.get("frequency") or {}
        if _freq_is_truly_frequent(freq):
            continue
        soft_field(item, "description")
        soft_field(freq, "count")
    for seg in persona.get("user_segments", []) or []:
        soft_field(seg, "description")  # 画像难逐属性溯源,量词一律收敛

    # ③ swot 四象限:证据 ≤2 才收敛
    swot = schema.get("swot") or {}
    for quad in ("strengths", "weaknesses", "opportunities", "threats"):
        for item in swot.get(quad, []) or []:
            if len(item.get("evidence_ids") or []) <= 2:
                soft_field(item, "point")

    # ④ 竞品格局:每条 reason(证据 ≤2 才收敛)+ 选品总述(无样本信号,一律收敛)
    landscape = schema.get("competitor_landscape") or {}
    for key in ("direct", "indirect", "alternative"):
        for item in landscape.get(key) or []:
            if len(item.get("evidence_ids") or []) <= 2:
                soft_field(item, "reason")
    soft_field(landscape, "selection_rationale")

    # ⑤ 改进建议:证据 ≤2 时 rationale / action 收敛
    for rec in schema.get("recommendations") or []:
        if len(rec.get("evidence_ids") or []) <= 2:
            soft_field(rec, "rationale")
            soft_field(rec, "action")

    return changes
