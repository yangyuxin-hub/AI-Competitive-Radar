"""FastAPI backend for the competitive analysis workspace."""
from __future__ import annotations

import json
import queue
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import StreamingResponse

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from src import intake  # noqa: E402
from src.graph import run_demo_streaming  # noqa: E402

_REPORTS_DIR = _ROOT / "out" / "reports"
_INDEX = _REPORTS_DIR / "index.json"

_NODE_META = {
    "collector": ("📥", "收集证据"),
    "analyzer": ("🧠", "分析结论"),
    "writer": ("✍️", "生成报告"),
    "reviewer": ("🧪", "规则质检"),
    "degraded_writer": ("⚠️", "降级输出"),
}

_COLLECTOR_PHASE_META = {
    "start": ("📥", "采集开始"),
    "url_discovery": ("🌐", "发现入口"),
    "fetch": ("📦", "拉取证据"),
    "complete": ("✅", "采集完成"),
}

app = FastAPI(title="AI Competitive Radar API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProposeReq(BaseModel):
    user_input: str
    domain_hint: Optional[str] = None


class RunReq(BaseModel):
    target_product: str
    competitors: list[str] = []
    analysis_focus: list[str] = []
    analysis_purpose: Optional[str] = None
    user_input: Optional[str] = None


@app.post("/api/intake/propose")
def api_propose(req: ProposeReq):
    draft = intake.propose(req.user_input, req.domain_hint)
    return {"draft": draft}


@app.post("/api/intake/questions")
def api_questions(req: ProposeReq):
    draft = intake.propose(req.user_input, req.domain_hint)
    questions = [c.to_dict() for c in intake.build_questions(draft)]
    return {"draft": draft, "questions": questions}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _collector_event_to_progress(event: dict) -> dict:
    phase = event.get("phase", "fetch")
    icon, label = _COLLECTOR_PHASE_META.get(phase, ("📦", "采集"))
    return {
        "type": "progress",
        "node": "collector",
        "icon": icon,
        "label": label,
        "status": event.get("status") or "running",
        "evidence_count": int(event.get("evidence_count") or 0),
        "retry_count": {},
        "reject_target": None,
        "collector_phase": phase,
        "message": event.get("message"),
        "product": event.get("product"),
        "source_counts": event.get("source_counts"),
        "coverage": event.get("coverage"),
        "official_count": event.get("official_count"),
        "pricing_count": event.get("pricing_count"),
        "product_count": event.get("product_count"),
    }


def _node_progress(node_name: str, state: dict) -> dict:
    icon, label = _NODE_META.get(node_name, ("•", node_name))
    evidence = state.get("raw_evidence") or []
    qr = state.get("quality_report") or {}
    event = {
        "type": "progress",
        "node": node_name,
        "icon": icon,
        "label": label,
        "status": state.get("status"),
        "evidence_count": len(evidence),
        "retry_count": state.get("retry_count") or {},
        "reject_target": state.get("reject_target"),
    }
    if node_name in ("reviewer", "degraded_writer") and qr:
        event["quality"] = {
            "quality_score": qr.get("quality_score"),
            "passed_rules": qr.get("passed_rules"),
            "failed_rules": qr.get("failed_rules"),
            "warning_rules": qr.get("warning_rules"),
        }
    return event


def _run_stream(args: dict):
    events: "queue.Queue[dict | None]" = queue.Queue()
    final_box: dict[str, dict] = {}

    def worker() -> None:
        from src.collector import set_progress_callback as set_collector_progress

        def on_collector(evt: dict) -> None:
            events.put(_collector_event_to_progress(evt))

        set_collector_progress(on_collector)
        try:
            for node_name, state in run_demo_streaming(**args):
                final_box["state"] = state
                events.put(_node_progress(node_name, state))
            report = _persist_report(final_box.get("state") or {})
            events.put({"type": "done", "report_id": report["report_id"], "report": report})
        except Exception as exc:  # noqa: BLE001
            events.put({"type": "error", "message": str(exc)})
        finally:
            set_collector_progress(None)
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        event = events.get()
        if event is None:
            break
        yield _sse(event)


@app.post("/api/run")
def api_run(req: RunReq):
    args = {
        "target_product": req.target_product,
        "competitors": req.competitors,
        "analysis_focus": req.analysis_focus,
        "analysis_purpose": req.analysis_purpose,
        "user_input": req.user_input,
    }
    return StreamingResponse(
        _run_stream(args),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _load_index() -> list[dict]:
    if _INDEX.exists():
        return json.loads(_INDEX.read_text(encoding="utf-8"))
    return []


def _persist_report(state: dict) -> dict:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    meta = state.get("analysis_meta") or {}
    now = datetime.now(timezone.utc)
    base_id = meta.get("report_id") or "CR"
    report_id = f"{base_id}-{now.strftime('%H%M%S')}"
    report = {
        "report_id": report_id,
        "meta": meta,
        "schema_draft": state.get("schema_draft"),
        "report_draft": state.get("report_draft"),
        "quality_report": state.get("quality_report"),
        "raw_evidence": state.get("raw_evidence") or [],
        "status": state.get("status"),
        "created_at": now.isoformat(),
    }
    (_REPORTS_DIR / f"{report_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    qr = state.get("quality_report") or {}
    index = _load_index()
    index.insert(0, {
        "report_id": report_id,
        "target_product": meta.get("target_product"),
        "competitors": meta.get("competitors") or [],
        "analysis_focus": meta.get("analysis_focus") or [],
        "status": state.get("status"),
        "quality_score": qr.get("quality_score"),
        "created_at": now.isoformat(),
    })
    _INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


@app.get("/api/reports")
def api_reports():
    return {"reports": _load_index()}


@app.get("/api/reports/{report_id}")
def api_report(report_id: str):
    path = _REPORTS_DIR / f"{report_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report not found")
    return json.loads(path.read_text(encoding="utf-8"))
