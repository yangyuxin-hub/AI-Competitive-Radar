"""各环节质量评估埋点 — 见 docs/reviewer-reject-design.md §8.7

给 collector/analyzer/writer/reviewer 每个节点末尾打可观测分，落 logs/stage_quality.jsonl，
每 run 每段一行，用于暴露瓶颈(哪段耗时高/质量低/有幻觉)。失败静默，绝不阻断主流程。

verdict/metrics(legacy)与 status/checks/gaps/produced(v3 StageReport)**同源**:
全部由 stage_report.to_stage_report() 一次计算派生(status_to_verdict 映射 verdict,
produced 即 metrics),不再各自维护一套判定逻辑,避免两套指标互相矛盾。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOG = Path("logs/stage_quality.jsonl")


def log_stage_quality(stage: str, state: dict, elapsed_sec: Optional[float] = None,
                      run_id: Optional[str] = None, path: Optional[Path] = None,
                      tokens: Optional[dict] = None) -> Optional[dict]:
    """计算该 stage 的可观测指标并 append 到 jsonl。返回写入的记录(失败返回 None)。
    verdict/metrics 与 status/checks/gaps/produced/cost 全部由 to_stage_report 单源派生。"""
    try:
        from .stage_report import status_to_verdict, to_stage_report

        report = to_stage_report(stage, state, elapsed_sec=elapsed_sec, run_id=run_id, tokens=tokens)
        if report is None:
            return None
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "stage": stage,
            "node": report["node"],
            "verdict": status_to_verdict(report["status"]),
            "elapsed_sec": round(elapsed_sec, 1) if elapsed_sec is not None else None,
            "metrics": report["produced"],          # legacy 别名,= produced
            "status": report["status"],
            "attempt": report["attempt"],
            "produced": report["produced"],
            "checks": report["checks"],
            "gaps": report["gaps"],
            "cost": report["cost"],
        }
        p = Path(path) if path else _LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception:  # noqa: BLE001
        return None
