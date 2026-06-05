# -*- coding: utf-8 -*-
"""真机验证(场景2):AI 编程工具 feature_compare,看定价/功能/意图在另一领域的表现。"""
import sys, time
sys.path.insert(0, ".")
from src.graph import run_demo

USER_INPUT = "对比 Cursor、Windsurf、GitHub Copilot 的代码补全体验和各自定价,帮我选型"
t0 = time.time()
final = run_demo(
    target_product="Cursor",
    competitors=["Windsurf", "GitHubCopilot"],
    analysis_focus=["代码补全体验"],
    user_input=USER_INPUT,
    runtime_profile="balanced",
)
dur = time.time() - t0

meta = final.get("analysis_meta") or {}
schema = final.get("schema_draft") or {}
qr = final.get("quality_report") or {}
ev = final.get("raw_evidence") or []
cols = ["Cursor", "Windsurf", "GitHubCopilot"]

print("\n" + "=" * 56)
print(f"耗时 {dur:.0f}s  ·  analysis_intent = {meta.get('analysis_intent')}")

feats = (schema.get("feature_tree") or {}).get("features") or []
real = unknown = 0
for f in feats:
    for c in cols:
        pd = (f.get("products") or {}).get(c) or {}
        sc = (pd.get("quality_score") or {}).get("score")
        if pd.get("support_status") == "unknown" or not isinstance(sc, (int, float)) or sc <= 0:
            unknown += 1
        else:
            real += 1
total = len(feats) * len(cols)
print(f"功能格子: {len(feats)} 功能 × {len(cols)} = {total}  →  实测分 {real} / 未评分 {unknown}"
      + (f"  ({real/total:.0%} 有真分)" if total else ""))

pm = (schema.get("pricing_model") or {}).get("products") or []
for p in pm:
    tiers = [(t.get("tier_name"), (t.get("price") or {}).get("normalized_usd_month")) for t in (p.get("tiers") or [])]
    print(f"  定价 {p.get('name')}: {tiers}")

# 定价证据来源(官网 vs 其他)
pr = [e for e in ev if e.get("claim_type") == "pricing"]
off = sum(1 for e in pr if e.get("source_type") == "official_page")
print(f"pricing 证据 {len(pr)} 条,其中官网 {off} 条")

print(f"证据总数 {len(ev)}  ·  质量 score={qr.get('quality_score')} status={qr.get('status') or final.get('status')}")
print("=" * 56)
