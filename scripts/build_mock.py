# -*- coding: utf-8 -*-
"""用真机采集到的真实证据重建离线 mock 文件(替代旧的过时产品)。
用法: python scripts/build_mock.py coding|pm
balanced 选取:每个 (product × 必需 claim_type) 取质量最高的最多 2 条 + 每产品最多 1 条 market_signal。
"""
import sys, json, pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "coding"

OUT = {
    "coding": ROOT / "data" / "sample_sources.json",
    "pm": ROOT / "data" / "sample_sources_pm.json",
}[DOMAIN]
META = {
    "coding": dict(products=["Cursor", "ClaudeCode", "Trae"], domain="ai_coding", domain_name="AI 编程工具"),
    "pm": dict(products=["Notion", "Linear", "飞书项目"], domain="pm", domain_name="项目协作工具"),
}[DOMAIN]

REQUIRED = ["feature_existence", "performance_quality", "pricing", "user_pain"]
KEYS = ["evidence_id", "product", "claim_type", "source_type", "source_bias", "source_url",
        "observed_at", "source_freshness", "claim", "extracted_snippet",
        "source_reliability", "claim_relevance", "evidence_confidence"]

ev = json.load(open(ROOT / "out" / f"regen_{DOMAIN}" / "raw_evidence.json", encoding="utf-8"))

def score(e):
    return (e.get("quality_score") or e.get("evidence_confidence") or 0,
            e.get("source_reliability") or 0)

buckets = defaultdict(list)
for e in ev:
    buckets[(e.get("product"), e.get("claim_type"))].append(e)

picked = []
for p in META["products"]:
    for ct in REQUIRED:
        cands = sorted(buckets.get((p, ct), []), key=score, reverse=True)
        picked.extend(cands[:2])
    ms = sorted(buckets.get((p, "market_signal"), []), key=score, reverse=True)
    picked.extend(ms[:1])

def slim(e):
    d = {k: e.get(k) for k in KEYS}
    if not d.get("claim"):
        d["claim"] = (e.get("extracted_snippet") or "")[:120]
    return d

picked = [slim(e) for e in picked if e.get("claim") or e.get("extracted_snippet")]

from collections import Counter
dist = Counter(e["product"] for e in picked)
ct_dist = Counter(e["claim_type"] for e in picked)
doc = {
    "_meta": {
        "schema_version": "2.1",
        "domain": META["domain"],
        "domain_name": META["domain_name"],
        "generated_at": "2026-06-17",
        "purpose": f"{META['domain_name']} 离线兜底 — 由真机采集的真实证据筛选(均衡 product×claim_type),"
                   "覆盖刷新后的预设产品;source_type/url 为真实来源。",
        "distribution": {**dict(dist), "by_claim_type": dict(ct_dist)},
    },
    "raw_evidence": picked,
}
json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[{DOMAIN}] 写出 {OUT.name}: {len(picked)} 条 | by_product={dict(dist)} | by_claim={dict(ct_dist)}")
