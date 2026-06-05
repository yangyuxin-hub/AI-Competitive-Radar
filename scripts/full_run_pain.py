"""真机验证:那条流失问题跑完整流水线,看推测/离题实际表现。"""
import sys, json, time
sys.path.insert(0, ".")
from urllib.parse import urlparse
from src.graph import run_demo

USER_INPUT = "用户为什么从 Notion 流向 Asana 或 Linear，看看大家主要在吐槽什么"
t0 = time.time()
final = run_demo(
    target_product="Notion",
    competitors=["Asana", "Linear"],
    analysis_focus=["高频吐槽与核心痛点"],
    user_input=USER_INPUT,
    runtime_profile="balanced",
)
dur = time.time() - t0

meta = final.get("analysis_meta") or {}
schema = final.get("schema_draft") or {}
qr = final.get("quality_report") or {}
ev = final.get("raw_evidence") or []
cols = ["Notion", "Asana", "Linear"]

print("\n" + "=" * 56)
print(f"耗时 {dur:.0f}s  ·  analysis_intent = {meta.get('analysis_intent')}")

# 功能矩阵:实测分 vs 未评分(痛点意图下前端把未评分显示为「未评分」而非「推测」)
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
print(f"功能格子: {len(feats)} 功能 × 3 = {total}  →  实测分 {real} / 未评分 {unknown}"
      + (f"  ({real/total:.0%} 有真分)" if total else ""))

# 定价
pm = (schema.get("pricing_model") or {}).get("products") or []
for p in pm:
    tiers = [t.get("price") for t in (p.get("tiers") or [])]
    print(f"  定价 {p.get('name')}: {tiers[:4]}")

# 证据离题率(search 渠道,产品名是否真出现)
def offtopic(e):
    al = {"notion": "notion", "asana": "asana", "linear": "linear"}.get(
        (e.get("product") or "").lower()[:6], (e.get("product") or "").lower())
    t = ((e.get("claim") or "") + " " + (e.get("extracted_snippet") or "")).lower()
    return al not in t
search_ev = [e for e in ev if e.get("collection_source") == "search"]
off = sum(offtopic(e) for e in search_ev)
print(f"证据总数 {len(ev)}  ·  search 渠道 {len(search_ev)} 条,离题 {off} "
      + (f"({off/len(search_ev):.0%})" if search_ev else ""))

print(f"质量: score={qr.get('quality_score')} status={qr.get('status') or final.get('status')}")
print("=" * 56)

json.dump(final, open("data/debug/full_run_pain.json", "w", encoding="utf-8"),
          ensure_ascii=False, default=str)
print("SAVED data/debug/full_run_pain.json")
