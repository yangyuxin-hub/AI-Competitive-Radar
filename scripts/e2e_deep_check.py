"""真实 LLM·deep 档端到端体检。把关键诊断落到 logs/e2e_deep_report.json。"""
import json
import re
import time
from pathlib import Path

import src.graph as g

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "logs" / "e2e_deep_report.json"


def _collect_ids(state, keys):
    cited = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "evidence_ids" and isinstance(v, list):
                    cited.update(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    for k in keys:
        walk(state.get(k))
    return cited


def main():
    t0 = time.time()
    st = g.run_demo(runtime_profile="deep")
    elapsed = time.time() - t0

    rep = st.get("report_draft") or ""
    chips = re.findall(r"\[S[0-9A-F]{7}\]", rep)
    ev_ids = {e.get("evidence_id") for e in (st.get("raw_evidence") or [])}
    cited = _collect_ids(st, ["feature_tree", "user_persona", "pricing_model", "swot", "recommendations"])
    qr = st.get("quality_report") or {}
    ca = (st.get("collection_meta") or {}).get("quality_audit") or {}

    out = {
        "elapsed_sec": round(elapsed, 1),
        "status": st.get("status"),
        "report_len": len(rep),
        "report_headers": [l for l in rep.splitlines() if l.startswith("#")],
        "chips_count": len(chips),
        "chips_unique": len(set(chips)),
        "score_leak_in_body": "quality_score" in rep,
        "has_data_availability": "数据可得性说明" in rep,
        "raw_evidence_count": len(ev_ids),
        "cited_id_count": len(cited),
        "cited_intersect_existing": len(cited & ev_ids),
        "quality_report": {
            "quality_score": qr.get("quality_score"),
            "passed_rules": qr.get("passed_rules"),
            "failed_rules": qr.get("failed_rules"),
            "warning_rules": qr.get("warning_rules"),
            "errors": qr.get("errors"),
            "warnings": (qr.get("warnings") or [])[:8],
        },
        "quality_audit_gaps": (ca.get("gaps") or []),
        "collection_sources": st.get("collection_meta", {}).get("source_counts")
        if isinstance(st.get("collection_meta"), dict) else None,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # 也保存完整报告 markdown
    (_ROOT / "logs" / "e2e_deep_report.md").write_text(rep, encoding="utf-8")
    print("=== E2E DEEP DONE ===")
    print(json.dumps({k: v for k, v in out.items() if k != "report_headers"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
