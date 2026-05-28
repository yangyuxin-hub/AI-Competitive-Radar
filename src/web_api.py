"""FastAPI facade for the React research workspace.

This module keeps the existing LangGraph pipeline intact and exposes a small
HTTP/SSE surface for the new frontend. v1 is intentionally single-user because
the current runtime still relies on process-level environment variables and
global callbacks.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from . import intake

_ROOT = Path(__file__).resolve().parent.parent
_CHIP_RE = re.compile(r"\[(S[0-9A-F]{7})\]")


class IntakeRequest(BaseModel):
    message: str
    domain_key: str = "ai_coding"


class RunSettings(BaseModel):
    domain_key: str = "ai_coding"
    reviewer_mode: Literal["minimal", "full"] = "minimal"
    use_mock: bool = True
    enable_live: bool = False
    demo_loop: bool = False


class RunMeta(BaseModel):
    target_product: str
    competitors: list[str] = Field(default_factory=list)
    analysis_focus: list[str] = Field(default_factory=list)
    analysis_purpose: str = ""
    user_input: str = ""


class CreateRunRequest(BaseModel):
    meta: RunMeta
    settings: RunSettings = Field(default_factory=RunSettings)


class FollowupRequest(BaseModel):
    message: str


@dataclass
class RunRecord:
    run_id: str
    meta: dict[str, Any]
    settings: dict[str, Any]
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    events: list[dict[str, Any]] = field(default_factory=list)
    final_state: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    event_queue: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)
    thread: Optional[threading.Thread] = None
    report_versions: list[dict[str, Any]] = field(default_factory=list)


app = FastAPI(title="Competitive Radar API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_RUNS: dict[str, RunRecord] = {}
_RUNS_LOCK = threading.Lock()
_ACTIVE_RUN_ID: Optional[str] = None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_domains() -> dict[str, Any]:
    return _load_yaml(_ROOT / "config" / "domains.yaml").get("domains") or {}


def _load_products() -> dict[str, Any]:
    return _load_yaml(_ROOT / "config" / "products.yaml").get("products") or {}


def _now() -> str:
    return datetime.now().isoformat()


def _new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


def _public_run(record: RunRecord, include_events: bool = True) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "meta": record.meta,
        "settings": record.settings,
        "final_state": record.final_state,
        "error": record.error,
        "events": record.events if include_events else [],
        "report_versions": record.report_versions,
    }


def _dump_model(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _emit(record: RunRecord, event: str, data: dict[str, Any]) -> None:
    payload = {
        "event": event,
        "data": data,
        "timestamp": _now(),
    }
    record.events.append(payload)
    record.updated_at = payload["timestamp"]
    record.event_queue.put(payload)


def _sse(payload: dict[str, Any]) -> str:
    return (
        f"event: {payload['event']}\n"
        f"data: {json.dumps(payload['data'], ensure_ascii=False)}\n\n"
    )


def _configure_runtime(settings: dict[str, Any]) -> None:
    os.environ["DOMAIN"] = settings.get("domain_key") or "ai_coding"
    os.environ["REVIEWER_MODE"] = settings.get("reviewer_mode") or "minimal"

    if settings.get("use_mock", True):
        os.environ["ANALYZER_MOCK"] = "1"
    else:
        os.environ.pop("ANALYZER_MOCK", None)

    if settings.get("enable_live"):
        os.environ["ENABLE_LIVE_FETCH"] = "1"
    else:
        os.environ.pop("ENABLE_LIVE_FETCH", None)

    if settings.get("demo_loop"):
        os.environ["DEMO_LOOP"] = "1"
    else:
        os.environ.pop("DEMO_LOOP", None)


def _hit_products(user_input: str) -> list[str]:
    products = _load_products()
    text = (user_input or "").lower()
    hits: list[str] = []
    for name, cfg in products.items():
        candidates = [name] + list(cfg.get("aliases") or [])
        if any(c and c.lower() in text for c in candidates):
            hits.append(name)
    return hits


def _infer_focus(user_input: str) -> Optional[str]:
    text = (user_input or "").lower()
    rules = [
        (("代码补全", "补全体验", "autocomplete", "completion"), "代码补全体验"),
        (("定价", "价格", "pricing", "price"), "定价策略"),
        (("任务管理", "项目管理", "协作"), "团队任务管理体验"),
        (("功能", "feature", "能力"), "核心功能完整度"),
        (("上手", "易用", "体验", "ux"), "用户体验与上手成本"),
        (("集成", "生态", "插件"), "集成与生态"),
        (("痛点", "差评", "用户反馈"), "用户痛点"),
    ]
    for keywords, focus in rules:
        if any(k.lower() in text for k in keywords):
            return focus
    return None


def _question_default(questions: list[dict[str, Any]], key: str) -> Any:
    for q in questions:
        if q.get("key") == key:
            suggested = q.get("suggested") or []
            return suggested if q.get("multi") else (suggested[0] if suggested else "")
    return []


def _direct_meta_from_prompt(
    user_input: str,
    questions: list[dict[str, Any]],
    domain_key: str,
) -> Optional[dict[str, Any]]:
    hits = _hit_products(user_input)
    focus = _infer_focus(user_input)
    if len(hits) < 2 or not focus:
        return None

    domains = _load_domains()
    dom_cfg = domains.get(domain_key, {})
    answers = {
        "target": hits[0],
        "competitors": hits[1:],
        "focus": focus,
        "purpose": _question_default(questions, "purpose")
        or dom_cfg.get("analysis_purpose")
        or "学习竞品优点，优化自身产品",
        "persist": "仅本次运行",
    }
    return intake.assemble_meta(answers, user_input=user_input)


def _run_worker(record: RunRecord) -> None:
    global _ACTIVE_RUN_ID
    record.status = "running"
    _emit(record, "run_started", {"run_id": record.run_id, "meta": record.meta})
    _configure_runtime(record.settings)

    try:
        from .analyzer import set_progress_callback
        from .collector import reset_registry
        from .graph import run_demo_streaming
        from .llm import set_llm_callback

        reset_registry()

        def on_analyzer(evt: dict[str, Any]) -> None:
            _emit(record, "analyzer_step", evt)

        def on_llm(evt: dict[str, Any]) -> None:
            _emit(record, "llm_usage", evt)

        set_progress_callback(on_analyzer)
        set_llm_callback(on_llm)

        final_state = None
        last_t = time.time()
        for node_name, state_after in run_demo_streaming(
            target_product=record.meta.get("target_product"),
            competitors=list(record.meta.get("competitors") or []),
            analysis_focus=list(record.meta.get("analysis_focus") or []),
            analysis_purpose=record.meta.get("analysis_purpose"),
            user_input=record.meta.get("user_input"),
        ):
            now = time.time()
            qr = state_after.get("quality_report") or {}
            raw_ev = state_after.get("raw_evidence") or []
            summary = {
                "status": state_after.get("status"),
                "reject_target": state_after.get("reject_target"),
                "retry_count": state_after.get("retry_count") or {},
                "quality_score": qr.get("quality_score"),
                "error_count": len(qr.get("errors") or []),
                "warning_count": len(qr.get("warnings") or []),
                "evidence_count": len(raw_ev),
                "duration": round(now - last_t, 3),
            }
            last_t = now
            _emit(
                record,
                "node_completed",
                {
                    "run_id": record.run_id,
                    "node": node_name,
                    "summary": summary,
                },
            )
            final_state = state_after

        record.final_state = final_state
        record.status = (final_state or {}).get("status") or "completed"
        report_md = (final_state or {}).get("report_draft") or ""
        if report_md:
            record.report_versions = [
                {
                    "version": 1,
                    "title": "原始报告",
                    "report_md": report_md,
                    "created_at": _now(),
                }
            ]
        qr = (final_state or {}).get("quality_report") or {}
        _emit(
            record,
            "final",
            {
                "run_id": record.run_id,
                "status": record.status,
                "quality_score": qr.get("quality_score"),
            },
        )
    except Exception as exc:
        record.status = "error"
        record.error = f"{type(exc).__name__}: {exc}"
        _emit(record, "error", {"run_id": record.run_id, "error": record.error})
    finally:
        try:
            from .analyzer import set_progress_callback
            from .llm import set_llm_callback

            set_progress_callback(None)
            set_llm_callback(None)
        except Exception:
            pass
        with _RUNS_LOCK:
            if _ACTIVE_RUN_ID == record.run_id:
                _ACTIVE_RUN_ID = None
        record.event_queue.put({"event": "__close__", "data": {}, "timestamp": _now()})


def _chip_ids(text: str) -> list[str]:
    return _CHIP_RE.findall(text or "")


def _answer_followup(record: RunRecord, message: str) -> dict[str, Any]:
    text = (message or "").strip()
    lowered = text.lower()
    final_state = record.final_state or {}

    if any(k in text for k in ("重新分析", "换成", "新增竞品", "增加竞品", "对比")):
        return {
            "action": "new_intake",
            "message": "这个请求改变了研究范围，需要重新确认目标产品、竞品和分析焦点。",
            "intake_seed": text,
        }

    if "下载" in text or "download" in lowered:
        return {
            "action": "answer",
            "message": "右侧报告区可以下载 report.md、quality_report.json 和 schema_draft.json。",
        }

    if "证据" in text or "引用" in text or "evidence" in lowered:
        raw_ev = final_state.get("raw_evidence") or []
        return {
            "action": "answer",
            "message": f"当前报告包含 {len(raw_ev)} 条 raw_evidence。点击报告中的 [SXXXXXXX] chip 可以查看对应原始片段。",
        }

    if "质量" in text or "评分" in text or "质检" in text:
        qr = final_state.get("quality_report") or {}
        return {
            "action": "answer",
            "message": (
                f"当前质量分为 {qr.get('quality_score', '?')}/100，"
                f"错误 {len(qr.get('errors') or [])} 条，"
                f"警告 {len(qr.get('warnings') or [])} 条。"
            ),
        }

    if any(k in text for k in ("修改", "改成", "更简洁", "答辩", "老板", "汇报", "压缩", "润色")):
        latest = record.report_versions[-1]["report_md"] if record.report_versions else (
            final_state.get("report_draft") or ""
        )
        revised = (
            latest
            + "\n\n---\n\n"
            + "## 用户反馈改写说明\n\n"
            + f"- 改写要求：{text}\n"
            + "- v1 前端改造阶段先保留事实与证据链，只追加改写说明；后续接入 report_revision prompt 后可生成完整改写版。\n"
        )
        valid_ids = {e.get("evidence_id") for e in final_state.get("raw_evidence") or []}
        missing = [eid for eid in _chip_ids(revised) if eid not in valid_ids]
        if missing:
            return {
                "action": "answer",
                "message": f"改写结果引用了不存在的 evidence_id：{', '.join(sorted(set(missing)))}，已阻止保存。",
            }
        version = len(record.report_versions) + 1
        item = {
            "version": version,
            "title": f"改写版本 {version}",
            "report_md": revised,
            "created_at": _now(),
        }
        record.report_versions.append(item)
        return {
            "action": "revision",
            "message": "已生成一个保留证据链的改写版本。",
            "version": version,
            "report_md": revised,
        }

    return {
        "action": "answer",
        "message": "当前版本支持解释证据、质检、下载，也可以要求调整报告表达；改变研究范围会重新进入需求确认。",
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    domains = _load_domains()
    return {
        "ok": True,
        "service": "competitive-radar-api",
        "domains": list(domains.keys()),
        "default_domain": os.environ.get("DOMAIN", "ai_coding"),
    }


@app.get("/api/domains")
def domains() -> dict[str, Any]:
    products = _load_products()
    return {
        "domains": _load_domains(),
        "products": list(products.keys()),
    }


@app.post("/api/intake")
def make_intake(req: IntakeRequest) -> dict[str, Any]:
    questions = [c.to_dict() for c in intake.intake_questions(req.message, req.domain_key)]
    meta = _direct_meta_from_prompt(req.message, questions, req.domain_key)
    if meta:
        return {"mode": "direct", "meta": meta, "questions": []}
    return {
        "mode": "clarify",
        "meta": None,
        "questions": [
            q for q in questions if q.get("key") in {"target", "competitors", "focus", "purpose"}
        ],
    }


@app.post("/api/runs")
def create_run(req: CreateRunRequest) -> dict[str, Any]:
    global _ACTIVE_RUN_ID
    meta = _dump_model(req.meta)
    if not meta.get("target_product") or not meta.get("competitors"):
        raise HTTPException(status_code=400, detail="target_product and competitors are required")
    if not meta.get("analysis_focus"):
        raise HTTPException(status_code=400, detail="analysis_focus is required")

    with _RUNS_LOCK:
        if _ACTIVE_RUN_ID is not None:
            raise HTTPException(status_code=409, detail=f"run {_ACTIVE_RUN_ID} is still active")
        run_id = _new_run_id()
        record = RunRecord(
            run_id=run_id,
            meta=meta,
            settings=_dump_model(req.settings),
        )
        _RUNS[run_id] = record
        _ACTIVE_RUN_ID = run_id
        record.thread = threading.Thread(target=_run_worker, args=(record,), daemon=True)
        record.thread.start()
    return {"run_id": run_id, "status": record.status}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str) -> StreamingResponse:
    record = _RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")

    def gen():
        idx = 0
        last_keepalive = time.time()
        while True:
            while idx < len(record.events):
                payload = record.events[idx]
                idx += 1
                yield _sse(payload)
                if payload["event"] in {"final", "error"}:
                    return
            if time.time() - last_keepalive >= 15:
                last_keepalive = time.time()
                yield ": keepalive\n\n"
            time.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    record = _RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _public_run(record)


@app.post("/api/runs/{run_id}/followups")
def followup(run_id: str, req: FollowupRequest) -> dict[str, Any]:
    record = _RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not record.final_state:
        raise HTTPException(status_code=409, detail="run has not completed")
    return _answer_followup(record, req.message)
