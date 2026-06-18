# -*- coding: utf-8 -*-
"""补齐:为刷新后的预设产品跑一次真机采集,顺带把 cache 写新(尤其 Trae 无 cache)。
用法: python scripts/regen_run.py coding | pm
"""
import sys, time, json, pathlib
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env", override=True)
from src.graph import run_demo

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "coding"

CFG = {
    "coding": dict(
        target_product="Cursor",
        competitors=["ClaudeCode", "Trae"],
        analysis_focus=["代码补全体验"],
        user_input="分析 Cursor、Claude Code 和 Trae 在代码补全体验上的差距",
    ),
    "pm": dict(
        target_product="Notion",
        competitors=["Linear", "飞书项目"],
        analysis_focus=["团队任务管理体验"],
        user_input="分析 Notion、Linear 和 飞书项目 在团队任务管理体验上的差距",
    ),
}[DOMAIN]

t0 = time.time()
final = run_demo(runtime_profile="balanced", **CFG)
dur = time.time() - t0

ev = final.get("raw_evidence") or []
qr = final.get("quality_report") or {}
from collections import Counter
print("\n" + "=" * 56)
print(f"[{DOMAIN}] 耗时 {dur:.0f}s · 证据 {len(ev)} 条 · score={qr.get('quality_score')} status={qr.get('status')}")
print("by product:", dict(Counter(e.get("product") for e in ev)))
out = ROOT / "out" / f"regen_{DOMAIN}"
out.mkdir(parents=True, exist_ok=True)
json.dump(ev, open(out / "raw_evidence.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
report = final.get("report_markdown") or final.get("report") or ""
(out / "report.md").write_text(report, encoding="utf-8")
print(f"→ 写出 {out}")
