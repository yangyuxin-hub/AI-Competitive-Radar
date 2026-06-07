# -*- coding: utf-8 -*-
"""评估过程全链路 walkthrough：真实跑一遍,把每个环节"怎么打分"摊开。
输出 logs/eval_walkthrough.txt(可逐步核查)。"""
import json
from pathlib import Path

import src.graph as g
from src import quality as Q
from src.scoring_config import weights as sc_weights

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "logs" / "eval_walkthrough.txt"
_buf = []


def p(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    _buf.append(line)


def quality_breakdown(e: dict) -> dict:
    """复刻 score_quality 的中间量,用于展示(不改生产代码)。"""
    text = Q._text_of(e)
    low = text.lower()
    spec = 0.0
    if Q._has_digit(text):
        spec += 0.5
    spec += min(sum(1 for w in Q._CONCRETE if w.lower() in low) * 0.12, 0.5)
    spec = min(spec, 1.0)
    fluff_hits = sum(1 for w in Q._FLUFF if w.lower() in low)
    fluff_penalty = (min(fluff_hits * 0.04, 0.12) if Q._has_digit(text)
                     else min(fluff_hits * 0.10, 0.30))
    snip = (e.get("extracted_snippet") or e.get("claim") or "").strip()
    if len(snip) < 25:
        integ = 0.2
    elif any(b in low for b in Q._BOILER) and not Q._has_digit(text):
        integ = 0.3
    else:
        integ = 1.0
    rel = float(e.get("claim_relevance") or 0.5)
    auth = float(e.get("source_reliability") or 0.5)
    fresh = 1.0 if (e.get("source_freshness") in (None, "current", "recent")) else 0.5
    w = sc_weights("evidence_quality", Q._DEFAULT_WEIGHTS)
    contrib = {
        "specificity": (spec, w["specificity"], spec * w["specificity"]),
        "integrity": (integ, w["integrity"], integ * w["integrity"]),
        "relevance": (rel, w["relevance"], rel * w["relevance"]),
        "authority": (auth, w["authority"], auth * w["authority"]),
        "freshness": (fresh, w["freshness"], fresh * w["freshness"]),
    }
    total = sum(c[2] for c in contrib.values()) - fluff_penalty
    return {"contrib": contrib, "fluff_penalty": fluff_penalty,
            "final": round(max(0.0, min(1.0, total)), 3), "has_digit": Q._has_digit(text)}


def main():
    p("=" * 70)
    p("评估过程全链路 walkthrough · 真实 LLM·deep")
    p("=" * 70)
    st = g.run_demo(runtime_profile="deep")

    ev = st.get("raw_evidence") or []
    # ---- 环节1: Collector 证据级质量分 ----
    p("\n" + "#" * 60)
    p("# 环节①  Collector — 单条证据 quality_score [0,1]")
    p("#" * 60)
    p(f"采集到 raw_evidence 共 {len(ev)} 条。证据级分公式(确定性):")
    p("  score = Σ(子分×权重) − 营销味惩罚, 5 子维: 具体性/完整性/相关性/权威性/时效")
    # 选 3 条:一条高分、一条低分、一条中间
    scored = sorted([(Q.score_quality(e), e) for e in ev], key=lambda x: x[0])
    picks = []
    if scored:
        picks = [("最低分", scored[0]), ("中位", scored[len(scored) // 2]), ("最高分", scored[-1])]
    for label, (sc, e) in picks:
        bd = quality_breakdown(e)
        p(f"\n  [{label}] {e.get('evidence_id')} · {e.get('product')} · {e.get('claim_type')} · {e.get('source_bias')}")
        snip = (e.get('extracted_snippet') or e.get('claim') or '')[:80]
        p(f"    片段: {snip}")
        for k, (raw, w, c) in bd["contrib"].items():
            p(f"    {k:<12} 子分={raw:.2f} × 权重={w:.2f} = {c:.3f}")
        p(f"    营销味惩罚 = -{bd['fluff_penalty']:.3f}  (含数字={bd['has_digit']})")
        p(f"    → quality_score = {bd['final']}  (生产值={sc})")

    # ---- 环节1b: 采集验收门 ----
    cm = st.get("collection_meta") or {}
    qa = cm.get("quality_audit") or {}
    p("\n" + "-" * 60)
    p("  采集验收门(整体 pass/fail):")
    p(f"    avg_quality = {qa.get('avg_quality')}")
    p(f"    数量门 gaps = {qa.get('quantity_gaps')}")
    p(f"    质量门 gaps = {qa.get('quality_gaps')}")
    p(f"    未补足 gaps = {qa.get('gaps')}")
    p(f"    判定 passed = {qa.get('passed')}")

    # ---- 环节2: Analyzer 建议优先级 ----
    # 结构化产物在 state["schema_draft"] 下(feature_tree/swot/recommendations 等)
    schema = st.get("schema_draft") or {}
    recs = schema.get("recommendations") or st.get("recommendations") or []
    p("\n" + "#" * 60)
    p("# 环节②  Analyzer — 建议优先级 priority_score (1-5→P0-P3)")
    p("#" * 60)
    p(f"产出 {len(recs)} 条建议。公式: final = .35·pain + .30·impact + .20·feasibility + .15·evidence")
    for r in recs[:3]:
        ps = r.get("priority_score") or {}
        w = ps.get("weights") or {}
        comp = {k: ps.get(k) for k in ("pain_frequency", "business_impact",
                                       "implementation_feasibility", "evidence_confidence")}
        recomputed = sum((comp.get(k) or 0) * (w.get(k) or 0) for k in comp)
        p(f"\n  {r.get('rec_id')} · {(r.get('action') or '')[:50]}")
        for k in comp:
            p(f"    {k:<28} 分={comp[k]} × 权重={w.get(k)}")
        p(f"    → final_score 标注={ps.get('final_score')}  复算={round(recomputed,2)}  priority={ps.get('priority')}")
        p(f"    (R5 会校验:标注与复算误差>0.01 即打回)")

    # ---- 环节3: Reviewer ----
    qr = st.get("quality_report") or {}
    p("\n" + "#" * 60)
    p("# 环节③  Reviewer — 报告质量分 = min(rule_score, 6维加权)")
    p("#" * 60)
    p(f"  passed_rules : {qr.get('passed_rules')}")
    p(f"  failed_rules : {qr.get('failed_rules')}")
    p(f"  warning_rules: {qr.get('warning_rules')}")
    dims = qr.get("quality_dimensions") or {}
    if dims:
        p("\n  6 维可信度加权:")
        for k, v in dims.items():
            p(f"    {k:<16} {v}")
    p(f"\n  errors  ({len(qr.get('errors') or [])}): " +
      "; ".join((x.get('issue_type', '') for x in (qr.get('errors') or [])[:6])))
    p(f"  warnings({len(qr.get('warnings') or [])}): " +
      "; ".join((x.get('issue_type', '') for x in (qr.get('warnings') or [])[:6])))
    p(f"\n  ★ quality_score = {qr.get('quality_score')}   status = {st.get('status')}")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(_buf), encoding="utf-8")
    p(f"\n[written] {OUT}")


if __name__ == "__main__":
    main()
