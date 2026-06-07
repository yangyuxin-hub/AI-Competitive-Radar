# -*- coding: utf-8 -*-
"""新域端到端测试:Suno vs Udio vs StableAudio(AI 音乐生成质量与易用性)。
把每个阶段产物逐一落盘到 out/suno_test/,并打印评估打分摊开。"""
import json
from pathlib import Path

import src.graph as g
from src import quality as Q
from src.scoring_config import weights as sc_weights

_ROOT = Path(__file__).resolve().parent.parent
OUTDIR = _ROOT / "out" / "suno_test"
OUTDIR.mkdir(parents=True, exist_ok=True)


def dump(name, obj):
    p = OUTDIR / name
    if isinstance(obj, str):
        p.write_text(obj, encoding="utf-8")
    else:
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [dump] {p.name}  ({p.stat().st_size} bytes)")


def qbd(e):
    text = Q._text_of(e); low = text.lower()
    spec = 0.0
    if Q._has_digit(text): spec += 0.5
    spec += min(sum(1 for w in Q._CONCRETE if w.lower() in low) * 0.12, 0.5); spec = min(spec, 1.0)
    fh = sum(1 for w in Q._FLUFF if w.lower() in low)
    fp = (min(fh*0.04,0.12) if Q._has_digit(text) else min(fh*0.10,0.30))
    snip = (e.get("extracted_snippet") or e.get("claim") or "").strip()
    integ = 0.2 if len(snip)<25 else (0.3 if (any(b in low for b in Q._BOILER) and not Q._has_digit(text)) else 1.0)
    rel=float(e.get("claim_relevance") or 0.5); auth=float(e.get("source_reliability") or 0.5)
    fresh=1.0 if e.get("source_freshness") in (None,"current","recent") else 0.5
    w=sc_weights("evidence_quality",Q._DEFAULT_WEIGHTS)
    tot=spec*w["specificity"]+integ*w["integrity"]+rel*w["relevance"]+auth*w["authority"]+fresh*w["freshness"]-fp
    return {"spec":round(spec,2),"integ":integ,"rel":rel,"auth":auth,"fresh":fresh,"fluff":round(fp,3),"final":round(max(0,min(1,tot)),3)}


def main():
    print("="*70)
    print("新域测试:Suno vs Udio vs StableAudio · AI 音乐生成质量与易用性 · deep")
    print("="*70)
    st = g.run_demo(
        target_product="Suno",
        competitors=["Udio", "StableAudio"],
        analysis_focus=["AI 音乐生成质量与易用性"],
        analysis_purpose="对比 AI 作曲工具的质量与易用性,辅助创作者选型",
        runtime_profile="deep",
    )

    print("\n--- 逐阶段落盘 → out/suno_test/ ---")
    meta = st.get("analysis_meta") or {}
    dump("01_args.json", {"target": meta.get("target_product"),
                          "competitors": meta.get("competitors"),
                          "analysis_focus": meta.get("analysis_focus"),
                          "status": st.get("status"),
                          "retry_count": st.get("retry_count")})
    ev = st.get("raw_evidence") or []
    dump("02_raw_evidence.json", ev)
    cm = st.get("collection_meta") or {}
    dump("03_collection_meta.json", cm)
    schema = st.get("schema_draft") or {}
    dump("04_schema_draft.json", schema)
    qr = st.get("quality_report") or {}
    dump("05_quality_report.json", qr)
    dump("06_report.md", st.get("report_draft") or "")

    # ---- 评估摊开 ----
    print("\n" + "#"*60)
    print("# 评估打分摊开")
    print("#"*60)

    # 证据分布
    from collections import Counter
    by_prod = Counter(e.get("product") for e in ev)
    by_type = Counter(e.get("claim_type") for e in ev)
    by_src = Counter(e.get("source_type") for e in ev)
    print(f"\n证据 {len(ev)} 条")
    print("  按产品:", dict(by_prod))
    print("  按类型:", dict(by_type))
    print("  按来源:", dict(by_src))

    scored = sorted([(Q.score_quality(e), e) for e in ev], key=lambda x: x[0])
    if scored:
        print("\n证据质量分(最低/中位/最高):")
        for lab, (s, e) in [("最低", scored[0]), ("中位", scored[len(scored)//2]), ("最高", scored[-1])]:
            b = qbd(e)
            print(f"  [{lab} {s}] {e.get('evidence_id')} {e.get('product')}/{e.get('claim_type')} | spec{b['spec']} integ{b['integ']} rel{b['rel']} auth{b['auth']} fresh{b['fresh']} fluff-{b['fluff']}")
            print(f"        {(e.get('extracted_snippet') or e.get('claim') or '')[:75]}")

    qa = cm.get("quality_audit") or {}
    print(f"\n采集验收门: avg_quality={qa.get('avg_quality')} 数量gap={qa.get('quantity_gaps')} 质量gap={qa.get('quality_gaps')} 未补gap={qa.get('gaps')} passed={qa.get('passed')}")

    feats = (schema.get("feature_tree") or {}).get("features") or []
    print(f"\n功能树: {len(feats)} 个功能点")
    for f in feats[:6]:
        gap = f.get("gap") or {}
        print(f"  {f.get('feature_id')} {f.get('name')[:24]} → winner={gap.get('winner')} ({gap.get('gap_type')}) conf={gap.get('confidence')}")

    recs = schema.get("recommendations") or []
    print(f"\n建议 {len(recs)} 条:")
    for r in recs:
        ps = r.get("priority_score") or {}
        print(f"  {r.get('rec_id')} {ps.get('priority')} final={ps.get('final_score')} | {(r.get('action') or '')[:55]}")

    print(f"\n报告质量分: {qr.get('quality_score')} | passed={qr.get('passed_rules')}")
    print(f"  failed={qr.get('failed_rules')} warning={qr.get('warning_rules')}")
    dims = qr.get("quality_dimensions") or {}
    for k, v in dims.items():
        print(f"    {v.get('label', k)}: {v.get('score')}")
    print(f"\n★ status={st.get('status')}  quality_score={qr.get('quality_score')}")
    print(f"\n全部产物在 out/suno_test/")


if __name__ == "__main__":
    main()
