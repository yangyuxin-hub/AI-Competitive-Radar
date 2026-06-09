# -*- coding: utf-8 -*-
"""把逐环 dump 的真实原始内容铺成一份可读 markdown(供人工逐环核对,非总结)。
用法: python scripts/dump_raw_stages.py <stage_tag>  (默认 demo2)
"""
import sys, io, json, pathlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
TAG = sys.argv[1] if len(sys.argv) > 1 else "demo2"
D = ROOT / "out" / f"stage_{TAG}"


def J(f):
    return json.load(open(D / f, encoding="utf-8"))


out = []
def w(s=""):
    out.append(s)


w(f"# 全链路原始数据 · {TAG}")
w("> 每个环节的**真实产出**(非总结),供逐环人工核对。\n")

# ① 意图
d = J("01_intake_draft.json")
w("\n## ① 意图澄清 — LLM 原始输出\n")
w("```json")
w(json.dumps(d, ensure_ascii=False, indent=2))
w("```")

# ② 官网 URL
u = J("02_discovered_urls.json")
w("\n## ② 官网 URL 发现 — 原始\n")
w("```json")
w(json.dumps(u, ensure_ascii=False, indent=2))
w("```")

# ③ 全部检索词
p = J("03_search_plan.json")
w("\n## ③ 检索词 + 锚定站点 — 全部 query\n")
for prod, plan in p.items():
    w(f"\n### {prod}")
    w("| claim_type | query | site | why |")
    w("|---|---|---|---|")
    for q in plan:
        w(f"| {q.get('claim_type')} | {q.get('query')} | {q.get('site') or '(全网)'} | {q.get('why','')} |")

# ④ 采集到的全部证据(原文片段)
ev = J("04_raw_evidence.json")
w(f"\n## ④ 采集结果 — 全部 {len(ev)} 条原始证据\n")
for prod in sorted(set(e.get("product") for e in ev)):
    w(f"\n### {prod}")
    for ct in ["feature_existence", "performance_quality", "pricing", "user_pain"]:
        rows = [e for e in ev if e.get("product") == prod and e.get("claim_type") == ct]
        if not rows:
            continue
        w(f"\n**{ct}** ({len(rows)} 条):")
        for e in rows:
            claim = (e.get("claim") or e.get("extracted_snippet") or "").replace("\n", " ").strip()
            w(f"- `[{e.get('evidence_id')}|{e.get('source_type')}]` {claim[:160]}")
            w(f"    - 出处: {e.get('source_url','')}")

# ⑤ 清洗后(喂 LLM 的版本)
comp = J("05_compact_evidence.json")
w(f"\n## ⑤ 清洗后 — 喂 LLM 的 {len(comp)} 条(分桶 top-K + 近似去重)\n")
for prod in sorted(set(e.get("product") for e in comp)):
    w(f"\n### {prod}")
    for ct in ["feature_existence", "performance_quality", "pricing", "user_pain"]:
        rows = [e for e in comp if e.get("product") == prod and e.get("claim_type") == ct]
        if not rows:
            continue
        w(f"\n**{ct}** ({len(rows)} 条):")
        for e in rows:
            claim = (e.get("claim") or e.get("extracted_snippet") or "").replace("\n", " ").strip()
            w(f"- `[{e.get('evidence_id')}]` {claim[:160]}")

(D / "RAW_all_stages.md").write_text("\n".join(out), encoding="utf-8")
print(f"写出: {D / 'RAW_all_stages.md'} ({len(out)} 行)")
