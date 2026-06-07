"""各环节质量评估埋点 — 见 docs/reviewer-reject-design.md §8.7

给 collector/analyzer/writer/reviewer 每个节点末尾打可观测分，落 logs/stage_quality.jsonl，
每 run 每段一行，用于暴露瓶颈(哪段耗时高/质量低/有幻觉)。失败静默，绝不阻断主流程。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOG = Path("logs/stage_quality.jsonl")
_CHIP = re.compile(r"\[S[0-9A-F]{7}\]")
_REF = re.compile(r'"(S[0-9A-F]{7})"')


def _collector_metrics(state: dict):
    qa = (state.get("collection_meta") or {}).get("quality_audit") or {}
    ev = state.get("raw_evidence") or []
    avgq = qa.get("avg_quality")
    metrics = {
        "evidence": len(ev),
        "avg_quality": avgq,
        "quantity_gaps": len(qa.get("quantity_gaps") or []),
        "quality_gaps": len(qa.get("quality_gaps") or []),
        "gaps_open": len(qa.get("gaps") or []),
    }
    verdict = "pass" if qa.get("passed") else ("warn" if ev else "fail")
    return metrics, verdict


def _analyzer_metrics(state: dict):
    sd = state.get("schema_draft") or {}
    ev_ids = {e.get("evidence_id") for e in (state.get("raw_evidence") or [])}
    blob = json.dumps(sd, ensure_ascii=False)
    refs = _REF.findall(blob)
    halluc = sum(1 for r in refs if r not in ev_ids)
    feats = (sd.get("feature_tree") or {}).get("features") or []
    metrics = {
        "evidence_refs": len(refs),
        "hallucination_count": halluc,      # 应为 0(核心原则#4)
        "unknown_hits": blob.count("unknown"),
        "feature_dims": len(feats),
    }
    verdict = "fail" if halluc > 0 else ("pass" if feats else "warn")
    return metrics, verdict


def _writer_metrics(state: dict):
    rpt = state.get("report_draft") or ""
    metrics = {
        "report_chars": len(rpt),
        "chips": len(_CHIP.findall(rpt)),
        "modules": rpt.count("\n## "),
        "score_leak": rpt.count("quality_score"),   # 应为 0(正文禁泄)
    }
    verdict = "fail" if not rpt else ("warn" if metrics["score_leak"] else "pass")
    return metrics, verdict


def _reviewer_metrics(state: dict):
    qr = state.get("quality_report") or {}
    metrics = {
        "quality_score": qr.get("quality_score"),
        "failed_rules": len(qr.get("failed_rules") or []),
        "warning_rules": len(qr.get("warning_rules") or []),
        "status": state.get("status"),
    }
    verdict = "pass" if not (qr.get("failed_rules")) else "warn"
    return metrics, verdict


_DISPATCH = {
    "collector": _collector_metrics,
    "analyzer": _analyzer_metrics,
    "writer": _writer_metrics,
    "degraded_writer": _writer_metrics,
    "reviewer": _reviewer_metrics,
}


def log_stage_quality(stage: str, state: dict, elapsed_sec: Optional[float] = None,
                      run_id: Optional[str] = None, path: Optional[Path] = None) -> Optional[dict]:
    """计算该 stage 的可观测指标并 append 到 jsonl。返回写入的记录(失败返回 None)。"""
    try:
        fn = _DISPATCH.get(stage)
        if not fn:
            return None
        metrics, verdict = fn(state)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id, "stage": stage, "verdict": verdict,
            "elapsed_sec": round(elapsed_sec, 1) if elapsed_sec is not None else None,
            "metrics": metrics,
        }
        p = Path(path) if path else _LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception:  # noqa: BLE001
        return None
