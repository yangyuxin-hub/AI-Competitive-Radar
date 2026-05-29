"""FastAPI 后端 — 竞品分析 Agent 工作台

把 src/ 现成函数包成 HTTP/SSE，供 Next.js 前端调用。后端逻辑零改动。

启动:
    ./.venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from queue import Empty, Queue
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

_PIPELINE = ["collector", "analyzer", "writer", "reviewer"]
_STATUS_COPY = {
    "collector": [
        "解析产品与竞品，准备证据采集",
        "检查官方页、缓存与 mock 兜底数据",
        "按 feature / pricing / user_pain 覆盖率整理证据",
    ],
    "analyzer": [
        "把原始证据压成事实层：功能、定价、用户痛点",
        "校验 evidence_id 是否真实存在，避免编造引用",
        "推导 SWOT 与优先级建议，计算 priority_score",
    ],
    "writer": [
        "把结构化 schema 渲染成可读 Markdown",
        "为结论挂上可点击的证据 chip",
    ],
    "reviewer": [
        "运行 R1-R7 质检规则",
        "检查证据链、引用完整性与优先级公式",
    ],
    "degraded_writer": [
        "质量门禁未完全通过，正在生成分层降级报告",
    ],
}

# 长间隙(LLM 调用中)的稳定等待文案 — 单句不轮播，配合计时表明仍在工作
_WAIT_MESSAGE = {
    "collector": "正在联网检索并整理证据",
    "analyzer": "正在深度分析：抽取功能/定价/痛点并推导建议，这一步最慢",
    "writer": "正在把结构化结论渲染成报告",
    "reviewer": "正在跑 R0-R7 质检规则",
    "degraded_writer": "正在生成分层降级报告",
}

app = FastAPI(title="AI Competitive Radar API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────────────────────────────────────────────────────────
# 请求模型
# ────────────────────────────────────────────────────────────────────────────

class ProposeReq(BaseModel):
    user_input: str
    domain_hint: Optional[str] = None


class RunReq(BaseModel):
    target_product: str
    competitors: list[str] = []
    analysis_focus: list[str] = []
    analysis_purpose: Optional[str] = None
    user_input: Optional[str] = None
    runtime_profile: str = "balanced"


def _propose_with_timeout(req: ProposeReq, timeout_sec: Optional[float] = None) -> dict:
    """Intent LLM 产出智能竞品 + reasoning,约需 20-25s(有波动);给足超时让其完成,
    前端「理解意图中…」期间等待。超时仍回退启发式,保证不会卡死首屏。"""
    import os

    if req.domain_hint:
        draft = intake._propose_heuristic(req.user_input, req.domain_hint)  # noqa: SLF001
        draft["_fallback_reason"] = "domain_hint_fast_path"
        return draft

    timeout_sec = timeout_sec or float(os.environ.get("INTAKE_TIMEOUT", "18"))
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(intake.propose, req.user_input, req.domain_hint)
    try:
        return fut.result(timeout=timeout_sec)
    except TimeoutError:
        fut.cancel()
        draft = intake._propose_heuristic(req.user_input, req.domain_hint)  # noqa: SLF001
        draft["_fallback_reason"] = f"intent_llm_timeout_{timeout_sec}s"
        return draft
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# ────────────────────────────────────────────────────────────────────────────
# 意图澄清
# ────────────────────────────────────────────────────────────────────────────

@app.post("/api/intake/propose")
def api_propose(req: ProposeReq):
    draft = _propose_with_timeout(req)
    return {"draft": draft}


@app.post("/api/intake/questions")
def api_questions(req: ProposeReq):
    draft = _propose_with_timeout(req)
    questions = [c.to_dict() for c in intake.build_questions(draft)]
    return {"draft": draft, "questions": questions}


# ────────────────────────────────────────────────────────────────────────────
# 运行分析 (SSE)
# ────────────────────────────────────────────────────────────────────────────

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


_CLAIM_LABELS = {
    "feature_existence": "功能具备性",
    "performance_quality": "性能与质量",
    "pricing": "定价信息",
    "user_pain": "用户痛点",
}
_REQUIRED_CLAIMS = ["feature_existence", "performance_quality", "pricing", "user_pain"]
_SOURCE_LABELS = {"live": "官网抓取", "search": "网络检索", "cache": "缓存", "mock": "兜底样本", "unknown": "其他"}


def _node_detail(node_name: str, state: dict) -> Optional[dict]:
    """节点完成后的结构化中间结果：拿到了什么、缺了什么。"""
    evidence = state.get("raw_evidence") or []
    schema = state.get("schema_draft") or {}

    if node_name == "collector":
        counts = {ct: 0 for ct in _REQUIRED_CLAIMS}
        sources: dict[str, int] = {}
        for e in evidence:
            ct = e.get("claim_type")
            if ct in counts:
                counts[ct] += 1
            src = e.get("collection_source") or "unknown"
            sources[src] = sources.get(src, 0) + 1
        coverage = [
            {"label": _CLAIM_LABELS.get(ct, ct), "count": counts[ct], "ok": counts[ct] > 0}
            for ct in _REQUIRED_CLAIMS
        ]
        meta = state.get("collection_meta") or {}
        products = [
            {
                "product": name,
                "health": info.get("health"),
                "missing": [_CLAIM_LABELS.get(m, m) for m in (info.get("missing_claim_types") or [])],
            }
            for name, info in (meta.get("products") or {}).items()
        ]
        searched: list[dict] = []
        for name, info in (meta.get("products") or {}).items():
            for ev in info.get("search_events") or []:
                if ev.get("status") == "ok" and ev.get("count"):
                    searched.append({
                        "product": name,
                        "query": ev.get("query"),
                        "site": ev.get("site"),
                        "claim_type": _CLAIM_LABELS.get(ev.get("claim_type"), ev.get("claim_type")),
                        "count": ev.get("count"),
                        "urls": (ev.get("urls") or [])[:5],
                    })
        return {
            "kind": "collection",
            "coverage": coverage,
            "missing": [c["label"] for c in coverage if not c["ok"]],
            "sources": [
                {"label": _SOURCE_LABELS.get(s, s), "count": n}
                for s, n in sorted(sources.items(), key=lambda kv: -kv[1])
            ],
            "products": products,
            "searched": searched,
        }

    if node_name == "analyzer":
        feats = (schema.get("feature_tree") or {}).get("features") or []
        recs = schema.get("recommendations") or []
        return {
            "kind": "analysis",
            "features": [f.get("name") for f in feats if f.get("name")],
            "recommendations": [
                {
                    "action": (r.get("action") or "")[:40],
                    "priority": (r.get("priority_score") or {}).get("priority"),
                }
                for r in recs
            ],
        }

    if node_name == "reviewer":
        qr = state.get("quality_report") or {}
        return {
            "kind": "review",
            "passed": qr.get("passed_rules") or [],
            "warnings": qr.get("warning_rules") or [],
            "failed": qr.get("failed_rules") or [],
        }
    return None


def _result_summary(node_name: str, state: dict) -> Optional[str]:
    """节点完成后的「阶段性结果」一句话，供前端活动流展示。"""
    evidence = state.get("raw_evidence") or []
    schema = state.get("schema_draft") or {}
    if node_name == "collector":
        types = {e.get("claim_type") for e in evidence if e.get("claim_type")}
        return f"采集 {len(evidence)} 条证据，覆盖 {len(types)} 类诉求" if evidence else None
    if node_name == "analyzer":
        feats = (schema.get("feature_tree") or {}).get("features") or []
        recs = schema.get("recommendations") or []
        pains = (schema.get("user_persona") or {}).get("pain_points") or []
        parts = []
        if feats:
            parts.append(f"{len(feats)} 个功能维度")
        if pains:
            parts.append(f"{len(pains)} 个痛点")
        if recs:
            parts.append(f"{len(recs)} 条建议")
        return "提取 " + " / ".join(parts) if parts else None
    if node_name in ("writer", "degraded_writer"):
        md = state.get("report_draft") or ""
        return f"生成报告约 {len(md)} 字" if md else None
    if node_name == "reviewer":
        qr = state.get("quality_report") or {}
        if qr.get("quality_score") is not None:
            return f"质检 {qr.get('quality_score')}/100 · {state.get('status')}"
    return None


def _progress_event(node_name: str, state: dict) -> dict:
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
        "result": _result_summary(node_name, state),
        "detail": _node_detail(node_name, state),
    }
    if node_name in ("reviewer", "degraded_writer") and qr:
        event["quality"] = {
            "quality_score": qr.get("quality_score"),
            "passed_rules": qr.get("passed_rules"),
            "failed_rules": qr.get("failed_rules"),
            "warning_rules": qr.get("warning_rules"),
        }
    collection_meta = state.get("collection_meta") or {}
    products = collection_meta.get("products") or {}
    unhealthy = [
        {
            "product": product,
            "health": info.get("health"),
            "missing_claim_types": info.get("missing_claim_types") or [],
        }
        for product, info in products.items()
        if info.get("health") and info.get("health") != "ok"
    ]
    if unhealthy:
        event["collection_health"] = unhealthy
    return event


def _next_node_after(node_name: Optional[str], state: dict) -> str:
    if node_name == "reviewer":
        if state.get("status") == "degraded":
            return "degraded_writer"
        if state.get("status") == "running":
            return state.get("reject_target") or "analyzer"
    if node_name in _PIPELINE:
        idx = _PIPELINE.index(node_name)
        if idx + 1 < len(_PIPELINE):
            return _PIPELINE[idx + 1]
    return "collector"


def _status_event(node_name: str, message: str, elapsed: int, state: Optional[dict] = None) -> dict:
    icon, label = _NODE_META.get(node_name, ("•", node_name))
    state = state or {}
    return {
        "type": "status",
        "node": node_name,
        "icon": icon,
        "label": label,
        "message": message,
        "elapsed_sec": elapsed,
        "evidence_count": len(state.get("raw_evidence") or []),
        "retry_count": state.get("retry_count") or {},
    }


def _merge_status_state(event: dict, state: dict) -> dict:
    """Keep status events numerically consistent with the latest completed node."""
    if not state:
        return event
    evidence_count = len(state.get("raw_evidence") or [])
    if evidence_count and not event.get("evidence_count"):
        event = {**event, "evidence_count": evidence_count}
    if state.get("retry_count") and not event.get("retry_count"):
        event = {**event, "retry_count": state.get("retry_count")}
    return event


def _node_for_label(label: str) -> str:
    if label.startswith(("facts", "derivations")):
        return "analyzer"
    if label.startswith("review"):
        return "reviewer"
    return "collector"


def _llm_status(evt: dict, elapsed: int) -> Optional[dict]:
    label = str(evt.get("label") or "")
    phase = evt.get("phase")
    if phase == "chunk":
        # 流式生成中:实时显示已生成字数(真实「处理过程输出」)
        chars = int(evt.get("chars") or 0)
        step = "事实层" if label.startswith("facts") else (
            "推导层" if label.startswith("derivations") else (
                "质检复核" if label.startswith("review") else "内容"
            )
        )
        return _status_event(_node_for_label(label), f"✍️ 正在生成{step}…已写 {chars} 字", elapsed)
    if label.startswith("url_discovery_"):
        product = label.replace("url_discovery_", "", 1)
        msg = f"{'正在发现' if phase == 'start' else '已完成'} {product} 的官方页和定价页"
        return _status_event("collector", msg, elapsed)
    if label.startswith("source_discovery"):
        msg = "正在规划检索策略：该去哪些站搜哪类证据" if phase == "start" else "检索策略已就绪，开始联网搜索"
        return _status_event("collector", msg, elapsed)
    if label.startswith("facts"):
        msg = "正在调用模型抽取事实层 JSON" if phase == "start" else "事实层 JSON 已返回，准备本地校验"
        return _status_event("analyzer", msg, elapsed)
    if label.startswith("derivations"):
        msg = "正在推导 SWOT 与改进建议" if phase == "start" else "推导层已返回，准备合并报告 schema"
        return _status_event("analyzer", msg, elapsed)
    if label.startswith("review"):
        msg = "正在运行 LLM 质检复核" if phase == "start" else "LLM 质检复核完成"
        return _status_event("reviewer", msg, elapsed)
    return None


def _analyzer_status(evt: dict, elapsed: int) -> dict:
    step = evt.get("step")
    phase = evt.get("phase")
    summary = evt.get("summary")
    if step == "overview":
        msg = summary or "Analyzer 已读取证据，准备抽取事实层"
    elif step == "facts" and phase == "start":
        msg = "Analyzer Step 1：从证据中抽取功能、定价、用户痛点"
    elif step == "facts" and phase == "repair":
        msg = f"事实层自检发现 {evt.get('issues', '?')} 个问题，正在修复引用"
    elif step == "facts" and phase == "fallback":
        msg = f"⚠️ {summary}" if summary else "⚠️ 事实层模型超时，已使用保守降级结果"
    elif step == "facts" and phase == "section_done":
        _names = {"feature_tree": "功能对比", "pricing_model": "定价模型", "user_persona": "用户画像"}
        msg = f"事实层：{_names.get(evt.get('section'), evt.get('section'))} 完成"
    elif step == "facts" and phase == "section_fallback":
        _names = {"feature_tree": "功能对比", "pricing_model": "定价模型", "user_persona": "用户画像"}
        msg = f"⚠️ 事实层：{_names.get(evt.get('section'), evt.get('section'))} 子任务超时，已用兜底"
    elif step == "facts":
        msg = f"✅ 事实层完成 — {summary}" if summary else "事实层完成，进入推导层"
    elif step == "derivations" and phase == "start":
        msg = "Analyzer Step 2：基于事实推导 SWOT 和优先级建议"
    elif step == "derivations" and phase == "repair":
        msg = f"推导层自检发现 {evt.get('issues', '?')} 个问题，正在修复"
    elif step == "derivations" and phase == "fallback":
        msg = f"⚠️ {summary}" if summary else "⚠️ 推导层模型超时，已使用保守降级建议"
    elif step == "derivations":
        msg = f"✅ 推导完成 — {summary}" if summary else "推导层完成"
    else:
        msg = "Analyzer 正在整理结构化结论"
    event = _status_event("analyzer", msg, elapsed)
    event["analysis_step"] = step
    event["analysis_phase"] = phase
    if summary:
        event["analysis_summary"] = summary
    if evt.get("preview") is not None:
        event["analysis_preview"] = evt.get("preview")
    return event


def _run_stream(args: dict):
    """逐节点 + 心跳式 yield SSE 事件，结束后持久化并发 done 事件。"""
    final_state: dict = {}
    q: Queue = Queue()
    started_at = time.time()

    def elapsed() -> int:
        return int(time.time() - started_at)

    def worker() -> None:
        nonlocal final_state
        collected = {"total": 0}

        def _collector_cb(evt: dict) -> None:
            msg = evt.get("message")
            if not msg:
                return
            event = _status_event("collector", msg, elapsed())
            event["collector_phase"] = evt.get("phase")
            for key in ("product", "source_counts", "coverage"):
                if evt.get(key) is not None:
                    event[key] = evt.get(key)
            if evt.get("phase") == "fetch" and evt.get("status") == "done" and evt.get("product"):
                collected["total"] += int(evt.get("evidence_count") or 0)
            event["evidence_count"] = collected["total"]
            q.put({"kind": "status", "event": event})

        try:
            from src import analyzer as analyzer_mod  # noqa: WPS433
            from src import llm as llm_mod  # noqa: WPS433
            from src import collector as collector_mod  # noqa: WPS433

            collector_mod.set_progress_callback(_collector_cb)
            analyzer_mod.set_progress_callback(
                lambda evt: q.put({"kind": "status", "event": _analyzer_status(evt, elapsed())})
            )
            llm_mod.set_llm_callback(
                lambda evt: (
                    q.put({"kind": "status", "event": status})
                    if (status := _llm_status(evt, elapsed())) else None
                )
            )

            for node_name, state in run_demo_streaming(**args):
                final_state = state
                q.put({"kind": "progress", "node": node_name, "state": state})
            q.put({"kind": "finished"})
        except Exception as e:  # noqa: BLE001
            q.put({"kind": "error", "message": str(e)})
        finally:
            try:
                collector_mod.set_progress_callback(None)
                analyzer_mod.set_progress_callback(None)
                llm_mod.set_llm_callback(None)
            except Exception:
                pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    last_state: dict = {}
    last_completed: Optional[str] = None
    current_node = "collector"
    last_hb = 0.0
    stage_timings: list[dict] = []  # 各环节耗时 + 成果(持久化进报告供档案复盘)
    prev_end = 0
    try:
        yield _sse(_status_event("collector", "开始运行，多 Agent 流程已启动", elapsed()))

        while True:
            try:
                item = q.get(timeout=1.0)
            except Empty:
                # 心跳节流:长间隙(LLM 调用中)每 ~4s 才发一次稳定等待文案，避免刷屏淹没真实结论
                if elapsed() - last_hb >= 4:
                    last_hb = elapsed()
                    wait = _WAIT_MESSAGE.get(current_node, "仍在处理中")
                    yield _sse(_status_event(current_node, f"{wait}…（已用 {elapsed()}s）", elapsed(), last_state))
                continue

            kind = item.get("kind")
            if kind == "status":
                yield _sse(_merge_status_state(item["event"], last_state))
                continue
            if kind == "progress":
                node_name = item["node"]
                state = item["state"]
                last_completed = node_name
                last_state = state
                # 记录本环节耗时 + 成果
                now = elapsed()
                icon, label = _NODE_META.get(node_name, ("•", node_name))
                stage_timings.append({
                    "node": node_name,
                    "icon": icon,
                    "label": label,
                    "duration_sec": max(0, now - prev_end),
                    "result": _result_summary(node_name, state),
                })
                prev_end = now
                current_node = _next_node_after(last_completed, state)
                last_hb = elapsed()  # 进入新节点，重置心跳计时
                yield _sse(_progress_event(node_name, state))
                if node_name != "reviewer" or state.get("status") != "passed":
                    nxt = _WAIT_MESSAGE.get(current_node, "继续处理下一步")
                    yield _sse(_status_event(current_node, nxt, elapsed(), last_state))
                continue
            if kind == "error":
                yield _sse({"type": "error", "message": item.get("message") or "unknown error"})
                return
            if kind == "finished":
                break

        report = _persist_report(final_state, stage_timings)
        yield _sse({"type": "done", "report_id": report["report_id"], "report": report})
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "message": str(e)})


@app.post("/api/run")
def api_run(req: RunReq):
    args = {
        "target_product": req.target_product,
        "competitors": req.competitors,
        "analysis_focus": req.analysis_focus,
        "analysis_purpose": req.analysis_purpose,
        "user_input": req.user_input,
        "runtime_profile": req.runtime_profile,
    }
    return StreamingResponse(
        _run_stream(args),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ────────────────────────────────────────────────────────────────────────────
# 档案
# ────────────────────────────────────────────────────────────────────────────

def _load_index() -> list[dict]:
    if _INDEX.exists():
        return json.loads(_INDEX.read_text(encoding="utf-8"))
    return []


def _persist_report(state: dict, stage_timings: Optional[list[dict]] = None) -> dict:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    meta = state.get("analysis_meta") or {}
    now = datetime.now(timezone.utc)
    # report_id 唯一化：原 meta.report_id 可能跨次重复，加时间戳后缀
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
        "stage_timings": stage_timings or [],
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
