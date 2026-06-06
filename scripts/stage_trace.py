# -*- coding: utf-8 -*-
"""逐环产出 dump,供人工审核每个环节的真实输入输出。
链路: 输入 → 相关竞品/维度目标 → 官网URL/检索站/检索词 → 检索结果 → 清洗结果 → 报告。
env: ST_TARGET / ST_COMPETITORS / ST_FOCUS / ST_DOMAIN / ST_TAG。一次性脚本。"""
import sys, io, os, json, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT); sys.path.insert(0, str(ROOT))

TARGET = os.environ.get("ST_TARGET", "Figma")
COMPS = [c.strip() for c in os.environ.get("ST_COMPETITORS", "Sketch,Canva").split(",") if c.strip()]
FOCUS = os.environ.get("ST_FOCUS", "界面设计协作体验")
TAG = os.environ.get("ST_TAG", "st")
USER_INPUT = f"分析 {TARGET} 与 {', '.join(COMPS)} 在 {FOCUS} 上的差距"
PRODUCTS = [TARGET, *COMPS]

OUT = ROOT / "out" / f"stage_{TAG}"
OUT.mkdir(parents=True, exist_ok=True)
md: list[str] = [f"# 逐环产出 · {TAG}\n", f"**输入**: {USER_INPUT}\n"]


def sec(title): md.append(f"\n## {title}\n")
def line(s=""): md.append(s)
def dump(name, obj): (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    # ── ① 意图:相关竞品 + 分析维度/目标 ──
    from src import intake
    draft = intake.propose(USER_INPUT)
    dump("01_intake_draft.json", draft)
    sec("① 意图澄清 — 相关竞品 + 分析维度/目标")
    line(f"- **品类**: {draft.get('domain_name')} | **意图**: {draft.get('analysis_intent')}")
    line(f"- **发现的竞品候选**: {draft.get('competitors_candidates')}")
    line(f"- **推荐先选**: {draft.get('competitors_suggested')}")
    line("- **竞品类型标注**:")
    for k, v in (draft.get("competitor_hints") or {}).items():
        line(f"    - {k}: {v}")
    line(f"- **分析维度候选**: {draft.get('focus_candidates')}")
    line(f"- **推荐维度**: {draft.get('focus_suggested')}")
    line(f"- **分析目的**: {draft.get('purpose_suggested')}")
    line(f"- **Agent 判断**: {draft.get('reasoning')}")
    line(f"\n> 实际选定(本次): target={TARGET}, competitors={COMPS}, focus={FOCUS}")

    # ── ② 找到的竞品官网 URL ──
    from src.collector import discover_all_urls
    discovered = discover_all_urls(PRODUCTS, allow_llm=True)
    dump("02_discovered_urls.json", discovered)
    sec("② 竞品官网 URL 发现(LLM 自主)")
    line("| 产品 | 来源 | official_pages | pricing_pages |")
    line("|---|---|---|---|")
    for p in PRODUCTS:
        d = discovered.get(p) or {}
        line(f"| {p} | {d.get('source','?')} | {'<br>'.join(d.get('official_pages') or []) or '—'} "
             f"| {'<br>'.join(d.get('pricing_pages') or []) or '—'} |")

    # ── ③ 检索词 + 检索站点设计 ──
    from src import source_planner as sp
    # 与 collector_node 一致:把②发现的官网域名注入,未配置产品的检索才会锚定官网
    # (否则本表对未配置产品恒显示「全网」,与真实采集行为不符,误导审核)
    from urllib.parse import urlparse
    _disc_domains = {}
    for p in PRODUCTS:
        d = discovered.get(p) or {}
        doms = []
        for u in (d.get("official_pages") or []) + (d.get("pricing_pages") or []):
            h = (urlparse(u).hostname or "").lower()
            h = h[4:] if h.startswith("www.") else h
            if h and h not in doms:
                doms.append(h)
        if doms:
            _disc_domains[p] = doms
    sp.set_discovered_domains(_disc_domains)
    sec("③ 检索词设计 + 锚定站点(逐产品 × claim_type)")
    plan_all = {}
    for p in PRODUCTS:
        plan = sp.plan_sources(p, [x for x in PRODUCTS if x != p], [FOCUS],
                               domain=os.environ.get("ST_DOMAIN") or None)
        plan_all[p] = plan
        line(f"\n**{p}**:")
        line("| claim_type | 检索词 query | 锚定站点 site |")
        line("|---|---|---|")
        for q in plan:
            line(f"| {q.get('claim_type')} | {q.get('query')} | {q.get('site') or '(全网)'} |")
    dump("03_search_plan.json", plan_all)

    # ── ④ 检索/采集结果 ──
    from src.state import build_initial_state
    from src.collector import collector_node
    state = build_initial_state(USER_INPUT, TARGET, COMPS, [FOCUS], runtime_profile="balanced")
    state = collector_node(state)
    evidence = state.get("raw_evidence") or []
    cmeta = state.get("collection_meta") or {}
    dump("04_raw_evidence.json", evidence)
    sec("④ 检索/采集结果")
    by_src, by_ct = {}, {}
    for e in evidence:
        by_src[e.get("source_type", "?")] = by_src.get(e.get("source_type", "?"), 0) + 1
        by_ct[e.get("claim_type", "?")] = by_ct.get(e.get("claim_type", "?"), 0) + 1
    line(f"- **总证据**: {len(evidence)} 条 | **按来源**: {by_src} | **按类型**: {by_ct}")
    line(f"- **官网 check**: {json.dumps(cmeta.get('official_check') or {}, ensure_ascii=False)}")
    line("\n各来源样本(每类 2 条):")
    seen_src = {}
    for e in evidence:
        s = e.get("source_type", "?")
        if seen_src.get(s, 0) >= 2:
            continue
        seen_src[s] = seen_src.get(s, 0) + 1
        txt = (e.get("claim") or e.get("extracted_snippet") or "")[:100].replace("\n", " ")
        line(f"  - `[{e.get('claim_type')}|{s}|{e.get('product')}]` {txt}  ⟨{e.get('source_url','')[:60]}⟩")

    # ── ⑤ 清洗处理结果(喂 LLM 的精简证据) ──
    from src.analyzer import _compact_evidence
    compact = _compact_evidence(evidence)
    dump("05_compact_evidence.json", compact)
    sec("⑤ 清洗处理结果(分桶 top-K + 近似去重,喂 LLM 的版本)")
    line(f"- 全量 {len(evidence)} 条 → 清洗后 {len(compact)} 条")
    cby = {}
    for e in compact:
        key = (e.get("product"), e.get("claim_type"))
        cby[key] = cby.get(key, 0) + 1
    line(f"- 分桶(产品×类型)条数: {dict((f'{k[0]}/{k[1]}', v) for k, v in sorted(cby.items()))}")

    # ── ⑥ 分析 + 最终报告 ──
    from src.analyzer import analyzer_node
    from src.writer import writer_node
    state = analyzer_node(state)
    state = writer_node(state)
    report = state.get("report_draft") or ""
    (OUT / "06_report.md").write_text(report, encoding="utf-8")
    dump("06_schema_draft.json", state.get("schema_draft") or {})
    sec("⑥ 最终报告")
    line(f"- 报告 {len(report)} 字,{report.count(chr(10)+'## ')} 个模块 → `06_report.md`")
    ft = (state.get("schema_draft") or {}).get("feature_tree", {}).get("features", [])
    line(f"- 功能矩阵维度: {[f.get('name') for f in ft]}")
    pm = (state.get("schema_draft") or {}).get("pricing_model", {}).get("products", [])
    line(f"- 定价: " + " | ".join(f"{p.get('name')} {len(p.get('tiers') or [])}档" for p in pm))

    (OUT / "trace.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n✅ 逐环 dump 写到: {OUT/'trace.md'}  (+ 01~06 各环 json/md)")


if __name__ == "__main__":
    main()
