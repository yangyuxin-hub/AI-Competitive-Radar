# -*- coding: utf-8 -*-
"""端到端流程追踪:记录每个阶段的输入/输出/耗时,供流程审计用。一次性脚本。"""
import sys, io, os, json, time, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

LLM_LOG = ROOT / "logs" / "llm_calls.jsonl"
def _llm_lines():
    return LLM_LOG.read_text(encoding="utf-8").count("\n") if LLM_LOG.exists() else 0

USER_INPUT = os.environ.get("TRACE_INPUT", "分析 Cursor 和 Windsurf 在代码补全体验上的差距")
TRACE_TARGET = os.environ.get("TRACE_TARGET", "Cursor")
TRACE_COMPS = [c.strip() for c in os.environ.get("TRACE_COMPETITORS", "Windsurf,GitHubCopilot").split(",") if c.strip()]
TRACE_PROFILE = os.environ.get("TRACE_PROFILE", "balanced")
TRACE_TAG = os.environ.get("TRACE_TAG", "trace_demo")
print("=" * 70); print("流程追踪 · 输入:", USER_INPUT, "| profile:", TRACE_PROFILE); print("=" * 70)

llm_before = _llm_lines()

# ───────────── Stage 0: Intake(意图确认 / 检索词设计 / 竞品发现 / 定目标) ─────────────
from src import intake
t0 = time.time()
# 展示检索词设计:competitor 发现用的 query 模板
tgt_guess = intake._guess_target_for_discovery(USER_INPUT)
print(f"\n[Stage0 intake] 竞品发现检索词(target='{tgt_guess}'):")
for q in [f"{tgt_guess} alternatives 2026", f"best {tgt_guess} competitors", f"AI {tgt_guess} alternative new"]:
    print("   query:", q)
draft = intake.propose(USER_INPUT)
t_intake = time.time() - t0
print(f"[Stage0 intake] 耗时 {t_intake:.1f}s")
print("  品类:", draft.get("domain_name"), "| 意图:", draft.get("analysis_intent"))
print("  目标候选:", draft.get("target_candidates"))
print("  竞品候选:", draft.get("competitors_candidates"))
print("  推荐先选:", draft.get("competitors_suggested"))
print("  焦点:", draft.get("focus_candidates"), "| 推荐:", draft.get("focus_suggested"))
print("  目的:", draft.get("purpose_suggested"))

# 模拟用户确认:取目标 + 指定竞品
target = TRACE_TARGET
competitors = TRACE_COMPS
focus = [draft.get("focus_suggested") or (draft.get("focus_candidates") or ["核心体验"])[0]]
purpose = draft.get("purpose_suggested") or "学习竞品优点,优化自身产品"
intent = draft.get("analysis_intent") or "feature_compare"
print(f"\n[Stage0] 用户确认 → target={target} competitors={competitors} focus={focus}")

# ───────────── Stage 1-5: 图(collector→analyzer→writer→reviewer),逐节点计时 ─────────────
from src.graph import run_demo_streaming
print("\n" + "=" * 70); print("进入图执行,逐节点计时"); print("=" * 70)

stage_times = {}
last = time.time()
final_state = None
for node_name, state_after in run_demo_streaming(
        target_product=target, competitors=competitors, analysis_focus=focus,
        analysis_purpose=purpose, user_input=USER_INPUT, runtime_profile=TRACE_PROFILE,
        analysis_intent=intent):
    dt = time.time() - last
    stage_times[node_name] = stage_times.get(node_name, 0) + dt
    last = time.time()
    final_state = state_after
    # 每节点输出摘要
    ev = state_after.get("raw_evidence") or []
    sd = state_after.get("schema_draft") or {}
    print(f"\n--- 节点 [{node_name}] 完成,耗时 {dt:.1f}s ---")
    if node_name == "collector":
        by_src, by_ct = {}, {}
        for e in ev:
            by_src[e.get("source_type","?")] = by_src.get(e.get("source_type","?"),0)+1
            by_ct[e.get("claim_type","?")] = by_ct.get(e.get("claim_type","?"),0)+1
        print(f"  raw_evidence: {len(ev)} 条 | by_source={by_src} | by_claim={by_ct}")
    elif node_name == "analyzer":
        ft = (sd.get("feature_tree") or {}).get("features") or []
        pm = (sd.get("pricing_model") or {}).get("products") or []
        up = sd.get("user_persona") or {}
        cl = sd.get("competitor_landscape") or {}
        pmap = (sd.get("positioning_map") or {}).get("products") or []
        print(f"  功能维度: {len(ft)} | 定价产品: {len(pm)}({sum(len(p.get('tiers') or []) for p in pm)}档) "
              f"| 痛点: {len(up.get('pain_points') or [])} 正向: {len(up.get('praise_points') or [])} "
              f"| SWOT: {sum(len(sd.get('swot',{}).get(k) or []) for k in ('strengths','weaknesses','opportunities','threats'))} "
              f"| 建议: {len(sd.get('recommendations') or [])} | 竞品格局: {sum(len(cl.get(k) or []) for k in ('direct','indirect','alternative'))} "
              f"| 定位地图: {len(pmap)}")
    elif node_name == "writer":
        rep = state_after.get("report_draft") or ""
        nsec = rep.count("\n## ")
        print(f"  报告: {len(rep)} 字 | {nsec} 个模块")
    elif node_name in ("reviewer", "degraded_writer"):
        qr = state_after.get("quality_report") or {}
        print(f"  status={state_after.get('status')} score={qr.get('quality_score')}/100 "
              f"passed={qr.get('passed_rules')} failed={qr.get('failed_rules')} warn={qr.get('warning_rules')}")

# ───────────── 汇总 ─────────────
print("\n" + "=" * 70); print("阶段耗时汇总"); print("=" * 70)
print(f"  Stage0 intake          {t_intake:6.1f}s")
total = t_intake
for k, v in stage_times.items():
    print(f"  {k:22} {v:6.1f}s")
    total += v
print(f"  {'合计':22} {total:6.1f}s")

# LLM 调用耗时拆解(本次新增的)
print("\n" + "=" * 70); print("本次 LLM 调用拆解(label / 耗时 / tokens)"); print("=" * 70)
lines = LLM_LOG.read_text(encoding="utf-8").splitlines()[llm_before:]
agg = {}
for ln in lines:
    try:
        r = json.loads(ln)
    except Exception:
        continue
    lb = r.get("label","?")
    agg.setdefault(lb, [0,0.0,0,0])
    agg[lb][0]+=1; agg[lb][1]+=r.get("duration_sec",0) or 0
    agg[lb][2]+=r.get("prompt_tokens",0) or 0; agg[lb][3]+=r.get("completion_tokens",0) or 0
llm_total = 0.0
for lb,(n,d,pt,cct) in sorted(agg.items(), key=lambda x:-x[1][1]):
    print(f"  {lb:34} x{n}  {d:6.1f}s  pt={pt} ct={cct}")
    llm_total += d
print(f"  LLM 调用总数 {sum(v[0] for v in agg.values())} | LLM 累计(含并行重叠) {llm_total:.1f}s")

# 落盘报告供查看
out = ROOT / "out" / TRACE_TAG
out.mkdir(parents=True, exist_ok=True)
(out / "report.md").write_text(final_state.get("report_draft") or "", encoding="utf-8")
print(f"\n报告已写: {out/'report.md'}")
