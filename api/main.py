"""FastAPI 后端 — 竞品分析 Agent 工作台

把 src/ 现成函数包成 HTTP/SSE，供 Next.js 前端调用。后端逻辑零改动。

启动:
    ./.venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import TimeoutError
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
from src import llm as _llm  # noqa: E402
from src.graph import run_demo_streaming  # noqa: E402
from src.progress import CtxThreadPoolExecutor  # noqa: E402

_REPORTS_DIR = _ROOT / "out" / "reports"
_INDEX = _REPORTS_DIR / "index.json"

_NODE_META = {
    "evidence_planner": ("🧭", "规划证据"),
    "collector": ("📥", "收集证据"),
    "analyzer": ("🧠", "分析结论"),
    "writer": ("✍️", "生成报告"),
    "reviewer": ("🧪", "规则质检"),
    "guard_revise": ("🛡️", "修订出货"),
    "degraded_writer": ("⚠️", "降级输出"),  # 旧档案回放兼容(节点已删)
}

_PIPELINE = ["evidence_planner", "collector", "analyzer", "writer", "reviewer", "guard_revise"]
_STATUS_COPY = {
    "evidence_planner": [
        "识别分析意图，生成本轮证据计划",
        "确定必需证据、增强信号与验收标准",
    ],
    "collector": [
        "解析产品与竞品，准备采集证据",
        "检查官网、缓存与兜底数据",
        "按功能/定价/痛点的覆盖情况整理证据",
    ],
    "analyzer": [
        "梳理事实：功能、定价、用户痛点",
        "核对证据来源，发现空缺则定向补采",
        "推导 SWOT 与优先级建议",
    ],
    "writer": [
        "把结构化结论整理成可读报告",
        "给每条结论挂上可点击的证据标记",
    ],
    "reviewer": [
        "逐条核查质检规则：引用、证据链、冲突",
        "检查证据链、引用完整性与优先级",
    ],
    "degraded_writer": [
        "质量门禁未完全通过，正在生成分层降级报告",
    ],
    "guard_revise": [
        "按质检定位清单做确定性修订，重渲染报告",
    ],
}

# 长间隙(LLM 调用中)的稳定等待文案 — 单句不轮播，配合计时表明仍在工作
_WAIT_MESSAGE = {
    "evidence_planner": "正在规划本轮需要采集的证据",
    "collector": "正在联网检索并整理证据",
    "analyzer": "正在深度分析：梳理事实、定向补采缺口、推导建议，这一步最慢",
    "writer": "正在把结构化结论整理成报告",
    "reviewer": "正在逐条核查质检规则",
    "degraded_writer": "正在生成分层降级报告",
    "guard_revise": "正在按质检结果修订并重渲染报告",
}

# 各节点典型耗时(秒)— 用于给前端算 ETA(预计剩余),让长等待可预期。
# 兜底默认(早期无历史数据时用);有历史后由 node_typical_seconds() 用 stage_quality.jsonl 的 p50 覆盖。
_NODE_TYPICAL_SEC_DEFAULT = {
    "evidence_planner": 1,
    "collector": 55,
    "analyzer": 130,
    "writer": 2,
    "reviewer": 6,
    "degraded_writer": 4,
    "guard_revise": 2,
}

app = FastAPI(title="AI Competitive Radar API")
# CORS:env `API_CORS_ORIGINS`(逗号分隔)优先;否则本地开发放行 localhost/127.0.0.1 任意端口
# (前端 dev 端口经常变,硬编码单一端口会让跨域预检 400)。
_cors_env = os.environ.get("API_CORS_ORIGINS", "").strip()
if _cors_env:
    _cors_kwargs: dict = {"allow_origins": [o.strip() for o in _cors_env.split(",") if o.strip()]}
else:
    _cors_kwargs = {"allow_origin_regex": r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"}
app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **_cors_kwargs,
)


# ────────────────────────────────────────────────────────────────────────────
# 请求模型
# ────────────────────────────────────────────────────────────────────────────

class ProposeReq(BaseModel):
    user_input: str
    domain_hint: Optional[str] = None
    fast: bool = False  # =true 跳过 LLM,只返回启发式草稿(用于渐进式:先秒显,LLM 回来再刷新)


class RunReq(BaseModel):
    target_product: str
    competitors: list[str] = []
    analysis_focus: list[str] = []
    analysis_purpose: Optional[str] = None
    user_input: Optional[str] = None
    runtime_profile: str = "deep"
    analysis_intent: Optional[str] = None  # 前端从 intake 带回;留空则后端按 user_input 推断


def _propose_with_timeout(req: ProposeReq, timeout_sec: Optional[float] = None) -> dict:
    """Intent LLM 产出针对性焦点维度(带引导)+ 全面竞品 + reasoning,约需 20-25s(有波动);
    给足超时让其完成,前端「理解意图中…」期间等待。超时回退启发式,保证不卡死首屏。
    domain_hint(预设场景卡)也走 LLM——保证「针对问题生成」而非套用通用模板;
    它仍会传给启发式兜底用于收窄品类。"""
    import os

    # 渐进式:fast=true 只跑启发式,前端先秒显澄清页,再发非 fast 请求让 LLM 刷新候选
    if req.fast:
        draft = intake._propose_heuristic(req.user_input, req.domain_hint)  # noqa: SLF001
        draft["_fallback_reason"] = "fast_heuristic"
        return draft

    # 现为后台刷新(用户已有秒开的启发式页,不阻塞)→ 给足时间让 LLM 完成精修
    timeout_sec = timeout_sec or float(os.environ.get("INTAKE_TIMEOUT", "45"))
    # intake 发生在 run 创建之前,无 agent_trace_id → 造一个 intake_<ts> 标签做 LLM 调用归因
    # (此前 llm_calls.jsonl 里 intake 调用 run_id=None,trace 工具按 run_id 过滤会漏)。
    # CtxThreadPoolExecutor 把该归因快照传进 worker 线程。
    _llm.set_run_stage(f"intake_{int(time.time() * 1000)}", "intake")
    pool = CtxThreadPoolExecutor(max_workers=1)
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


@app.post("/api/intake/stream")
def api_intake_stream(req: ProposeReq):
    """流式意图澄清:逐字推送模型思考 + 阶段进度,最后给最终 draft + questions。
    替代原来阻塞式的二段 LLM 精修,把 ~20s 的等待变成可见的思考过程。
    前端仍先用 /questions?fast=1 秒开澄清页,再用本流式接口刷新候选。"""
    def gen():
        final_draft: dict = {}
        try:
            # 同 _propose_with_timeout:补 intake 阶段 LLM 调用归因(gen 在本请求线程内执行)
            _llm.set_run_stage(f"intake_{int(time.time() * 1000)}", "intake")
            for kind, payload in intake.propose_stream(req.user_input, req.domain_hint):
                if kind == "status":
                    yield _sse({"type": "status", "text": payload})
                elif kind == "reasoning":
                    yield _sse({"type": "reasoning", "delta": payload})
                elif kind == "draft":
                    final_draft = payload or {}
            questions = [c.to_dict() for c in intake.build_questions(final_draft)]
            yield _sse({"type": "done", "draft": final_draft, "questions": questions})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ────────────────────────────────────────────────────────────────────────────
# 运行分析 (SSE)
# ────────────────────────────────────────────────────────────────────────────

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

def _keepalive() -> str:
    """纯 keep-alive 信号，前端忽略，仅保持连接活跃避免 PaaS 超时。"""
    return ": keepalive\n\n"


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
        return "得到 " + " / ".join(parts) if parts else None
    if node_name in ("writer", "degraded_writer"):
        md = state.get("report_draft") or ""
        return f"生成报告约 {len(md)} 字" if md else None
    if node_name == "guard_revise":
        rev = state.get("guard_revision") or {}
        n = rev.get("changes_total") or 0
        return f"确定性修订 {n} 处并重渲染" if n else "无需修订,直接出货"
    if node_name == "reviewer":
        qr = state.get("quality_report") or {}
        if qr.get("quality_score") is not None:
            _st = {"passed": "通过", "degraded": "降级", "running": "打回重跑"}.get(
                state.get("status"), state.get("status"))
            return f"质检 {qr.get('quality_score')}/100 · {_st}"
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
    if node_name in ("reviewer", "degraded_writer", "guard_revise") and qr:
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
    # M4 直线控制流:reviewer 后恒为 guard_revise(打回/degraded_writer 路由已删)
    if node_name in _PIPELINE:
        idx = _PIPELINE.index(node_name)
        if idx + 1 < len(_PIPELINE):
            return _PIPELINE[idx + 1]
    return "evidence_planner"


def _status_event(node_name: str, message: str, elapsed: int, state: Optional[dict] = None,
                  eta_sec: Optional[int] = None) -> dict:
    icon, label = _NODE_META.get(node_name, ("•", node_name))
    state = state or {}
    evt = {
        "type": "status",
        "node": node_name,
        "icon": icon,
        "label": label,
        "message": message,
        "elapsed_sec": elapsed,
        "evidence_count": len(state.get("raw_evidence") or []),
        "retry_count": state.get("retry_count") or {},
    }
    if eta_sec is not None:
        evt["eta_sec"] = max(0, eta_sec)
    return evt


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
        step = "事实梳理" if label.startswith("facts") else (
            "结论推导" if label.startswith("derivations") else (
                "语义复核" if label.startswith("review") else "内容"
            )
        )
        return _status_event(_node_for_label(label), f"✍️ 正在生成{step}…已写 {chars} 字", elapsed)
    if label.startswith("url_discovery_"):
        product = label.replace("url_discovery_", "", 1)
        msg = f"{'正在发现' if phase == 'start' else '已完成'} {product} 的官方页和定价页"
        return _status_event("collector", msg, elapsed)
    if label.startswith("source_discovery"):
        msg = "正在规划该去哪些网站搜哪类证据" if phase == "start" else "搜索方案已定，开始联网检索"
        return _status_event("collector", msg, elapsed)
    # facts/derivations 的逐子调用 start/done 消息冗余(每个并行 section 都触发一次),
    # 结构化进度已由 analyzer 的 section_done/section_fallback 表达 → 这里不再重复吐。
    if label.startswith(("facts", "derivations")):
        return None
    if label.startswith("review"):
        msg = "正在做 AI 语义复核（抗幻觉）" if phase == "start" else "AI 语义复核完成"
        return _status_event("reviewer", msg, elapsed)
    return None


def _section_fallback_message(scope: str, section_label: str, note: str | None) -> str:
    reason = "子任务失败"
    if note:
        low = note.lower()
        if "timeout" in low or "timed out" in low:
            reason = "子任务超时"
        elif "authentication" in low or "invalid api key" in low or "401" in low:
            reason = "模型鉴权失败"
        elif "json" in low:
            reason = "模型输出解析失败"
        else:
            reason = "模型调用失败"
    return f"⚠️ {scope}：{section_label} {reason}，已用兜底"


def _analyzer_status(evt: dict, elapsed: int) -> dict:
    step = evt.get("step")
    phase = evt.get("phase")
    summary = evt.get("summary")
    note = evt.get("note")
    if step == "overview":
        msg = summary or "已读取证据，开始梳理事实"
    elif step == "facts" and phase == "enrich_start":
        msg = summary or "[分析阶段] 按功能清单为各产品定向补采证据"
    elif step == "facts" and phase == "enrich_done":
        msg = f"✅ {summary}" if summary else "[分析阶段] 定向补采完成"
    elif step == "facts" and phase == "survey_start":
        msg = summary or "[分析阶段] 问卷/访谈 Agent：设计问卷并模拟用户访谈"
    elif step == "facts" and phase == "survey_done":
        msg = f"📋 {summary}" if summary else "[分析阶段] 问卷调研完成"
    elif step == "facts" and phase == "gap_refill_start":
        msg = summary or "[分析阶段] 检测到证据空缺，正在定向补采"
    elif step == "facts" and phase == "gap_refill_done":
        msg = f"🔁 {summary}" if summary else "[分析阶段] 补采完成，重新梳理事实"
    elif step == "facts" and phase == "start":
        msg = "第一步 · 梳理事实：从证据中提炼功能、定价、用户痛点"
    elif step == "facts" and phase == "repair":
        msg = f"事实自检发现 {evt.get('issues', '?')} 处引用问题，正在修正"
    elif step == "facts" and phase == "fallback":
        msg = f"⚠️ {summary}" if summary else "⚠️ 模型超时，事实梳理改用兜底结果"
    elif step == "facts" and phase == "section_done":
        _names = {"feature_tree": "功能对比", "pricing_model": "定价模型", "user_persona": "用户画像"}
        msg = f"事实梳理 · {_names.get(evt.get('section'), evt.get('section'))} 完成"
    elif step == "facts" and phase == "section_fallback":
        _names = {"feature_tree": "功能对比", "pricing_model": "定价模型", "user_persona": "用户画像"}
        msg = _section_fallback_message("事实梳理", _names.get(evt.get("section"), evt.get("section")), note)
    elif step == "facts":
        msg = f"✅ 事实梳理完成 — {summary}" if summary else "事实梳理完成，进入结论推导"
    elif step == "derivations" and phase == "start":
        msg = "第二步 · 推导结论：基于事实生成 SWOT 与优先级建议"
    elif step == "derivations" and phase == "section_done":
        _names = {"swot": "SWOT 四象限", "recommendations": "优先级建议"}
        msg = f"结论推导 · {_names.get(evt.get('section'), evt.get('section'))} 完成"
    elif step == "derivations" and phase == "section_fallback":
        _names = {"swot": "SWOT 四象限", "recommendations": "优先级建议"}
        msg = _section_fallback_message("结论推导", _names.get(evt.get("section"), evt.get("section")), note)
    elif step == "derivations" and phase == "repair":
        msg = f"结论自检发现 {evt.get('issues', '?')} 处问题，正在修正"
    elif step == "derivations" and phase == "fallback":
        msg = f"⚠️ {summary}" if summary else "⚠️ 模型超时，结论推导改用兜底建议"
    elif step == "derivations":
        msg = f"✅ 结论推导完成 — {summary}" if summary else "结论推导完成"
    else:
        msg = "正在整理结构化结论"
    event = _status_event("analyzer", msg, elapsed)
    event["analysis_step"] = step
    event["analysis_phase"] = phase
    if summary:
        event["analysis_summary"] = summary
    if note:
        event["analysis_warning"] = str(note)[:240]
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
            for key in ("product", "source_counts", "coverage", "samples"):
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

    # ETA 用历史 p50(样本不足回退默认),每条流开头算一次。
    try:
        from src.stage_report import node_typical_seconds  # noqa: WPS433
        node_typical_sec = node_typical_seconds(_NODE_TYPICAL_SEC_DEFAULT)
    except Exception:  # noqa: BLE001
        node_typical_sec = _NODE_TYPICAL_SEC_DEFAULT

    last_state: dict = {}
    last_completed: Optional[str] = None
    current_node = "evidence_planner"
    last_hb = 0.0
    stage_timings: list[dict] = []  # 各环节耗时 + 成果(持久化进报告供档案复盘)
    prev_end = 0
    try:
        yield _sse(_status_event("evidence_planner", "开始运行，多 Agent 流程已启动", elapsed()))

        while True:
            try:
                item = q.get(timeout=1.0)
            except Empty:
                # 每次空闲都发 keep-alive 注释行（PaaS 保活），前端忽略
                yield _keepalive()
                # 有内容的心跳节流:每 ~2s 发一次状态文案
                if elapsed() - last_hb >= 2:
                    last_hb = elapsed()
                    wait = _WAIT_MESSAGE.get(current_node, "仍在处理中")
                    in_node = elapsed() - prev_end  # 当前节点已耗时
                    eta = node_typical_sec.get(current_node, 60) - in_node
                    eta_txt = f"，预计还需 ~{max(5, eta)}s" if eta > 3 else "，即将完成"
                    yield _sse(_status_event(
                        current_node, f"{wait}…（已用 {elapsed()}s{eta_txt}）",
                        elapsed(), last_state, eta_sec=eta))
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
                # 记录本环节耗时 + 成果。耗时单源:graph._instrument 已把本节点 StageReport
                # (节点内实测 elapsed_sec/tokens)挂到 state["_stage_report"],不再用 SSE 队列到达
                # 间隔(now - prev_end)重估一遍(那个口径含队列排队延迟,会偏大)。
                now = elapsed()
                icon, label = _NODE_META.get(node_name, ("•", node_name))
                _rep = state.get("_stage_report")
                _cost = (_rep or {}).get("cost") or {}
                duration_sec = _cost.get("elapsed_sec")
                if duration_sec is None:
                    duration_sec = max(0, now - prev_end)
                stage_timings.append({
                    "node": node_name,
                    "icon": icon,
                    "label": label,
                    "duration_sec": duration_sec,
                    "tokens": _cost.get("tokens"),
                    "result": _result_summary(node_name, state),
                })
                prev_end = now
                current_node = _next_node_after(last_completed, state)
                last_hb = elapsed()  # 进入新节点，重置心跳计时
                yield _sse(_progress_event(node_name, state))
                # design-v3 M1:每节点完成推一条统一 StageReport,供时间线 UX 实时点亮。
                # 直接用 graph._instrument 已算好挂在 state 的那份(单一计时口径,含 token 归因),
                # 不在这里重算 to_stage_report。加法事件,前端未知 type 自动忽略;失败静默不阻断主流。
                if _rep:
                    yield _sse({"type": "stage_report", "report": _rep})
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

        # 可选 LLM-as-Judge:在 done 之后单独发,不阻塞报告显示(RUN_JUDGE=1 开启)
        if os.environ.get("RUN_JUDGE", "").strip() in ("1", "true", "True"):
            try:
                from src.judge import score_report  # noqa: WPS433
                yield _sse(_status_event("reviewer", "正在做 LLM 质量评分(4 维)…", elapsed()))
                card = score_report(
                    final_state.get("report_draft") or "",
                    final_state.get("schema_draft") or {},
                    final_state.get("analysis_meta") or {},
                )
                yield _sse({"type": "judge", "report_id": report["report_id"], "scorecard": card})
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "judge_error", "message": str(e)})
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
        "analysis_intent": req.analysis_intent,
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
        # utf-8-sig 容忍 BOM(某些编辑器/写入会带 BOM,否则 json.loads 报错)
        try:
            return json.loads(_INDEX.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[api] index.json 解析失败,按空处理: {e}")
            return []
    return []


def _report_summary(state: dict) -> str:
    schema = state.get("schema_draft") or {}
    meta = state.get("analysis_meta") or {}
    target = meta.get("target_product") or "目标产品"
    recs = schema.get("recommendations") or []
    if recs:
        top = recs[0].get("action") or ""
        if top:
            return f"建议优先处理: {top}"
    gaps = []
    for feat in (schema.get("feature_tree") or {}).get("features") or []:
        gap = feat.get("gap") or {}
        if gap.get("winner") and feat.get("name"):
            gaps.append((float(gap.get("confidence") or 0), feat.get("name"), gap.get("winner")))
    if gaps:
        _conf, name, winner = max(gaps)
        if winner == target:
            return f"结论: {target} 在「{name}」上相对领先。"
        return f"结论: {winner} 在「{name}」上对 {target} 形成主要压力。"
    pricing_gap = (schema.get("pricing_model") or {}).get("pricing_gap") or {}
    if pricing_gap.get("summary"):
        return f"定价判断: {pricing_gap.get('summary')}"
    return "报告已生成，建议查看证据覆盖与结论详情。"


def _persist_report(state: dict, stage_timings: Optional[list[dict]] = None) -> dict:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    meta = state.get("analysis_meta") or {}
    now = datetime.now(timezone.utc)
    # report_id 唯一化：原 meta.report_id 可能跨次重复，加时间戳后缀
    base_id = meta.get("report_id") or "CR"
    report_id = f"{base_id}-{now.strftime('%H%M%S')}"
    # 确定性「任务完成度」指标(零 LLM,不增加延迟)
    completeness = None
    business_value = None
    try:
        from src.judge import completeness_metrics  # noqa: WPS433
        completeness = completeness_metrics(state.get("schema_draft") or {}, meta)
    except Exception as e:  # noqa: BLE001
        print(f"[api] completeness_metrics 失败(忽略): {e}")
    try:
        from src.business_value import business_value_metrics  # noqa: WPS433
        business_value = business_value_metrics(
            state.get("schema_draft") or {}, meta, stage_timings or [], state.get("raw_evidence") or [])
    except Exception as e:  # noqa: BLE001
        print(f"[api] business_value_metrics 失败(忽略): {e}")
    report = {
        "report_id": report_id,
        "meta": meta,
        "summary": _report_summary(state),
        "schema_draft": state.get("schema_draft"),
        "report_draft": state.get("report_draft"),
        "quality_report": state.get("quality_report"),
        "completeness": completeness,
        "business_value": business_value,
        "research_method": (state.get("schema_draft") or {}).get("research_method"),
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
        "summary": report["summary"],
        "runtime_profile": meta.get("runtime_profile"),
        "evidence_count": len(state.get("raw_evidence") or []),
        "status": state.get("status"),
        "quality_score": qr.get("quality_score"),
        "completeness": (completeness or {}).get("overall"),
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


@app.get("/api/stage_quality")
def api_stage_quality(limit: int = 300):
    """各环节质量评估聚合(§8.7):读 logs/stage_quality.jsonl,按 stage 聚合耗时/verdict/最近指标。
    供前端「瓶颈看板」渲染。"""
    p = _ROOT / "logs" / "stage_quality.jsonl"
    if not p.exists():
        return {"stages": [], "recent": []}
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass
    order = ["evidence_planner", "collector", "analyzer", "writer", "degraded_writer", "reviewer", "guard_revise"]
    agg: dict = {}
    for r in rows:
        s = r.get("stage") or "?"
        a = agg.setdefault(s, {"runs": 0, "elapsed_sum": 0.0, "elapsed_n": 0,
                               "verdicts": {}, "last_metrics": None,
                               "tokens_sum": 0, "tokens_n": 0, "llm_calls_sum": 0})
        a["runs"] += 1
        e = r.get("elapsed_sec")
        if isinstance(e, (int, float)):
            a["elapsed_sum"] += e
            a["elapsed_n"] += 1
        v = r.get("verdict") or "?"
        a["verdicts"][v] = a["verdicts"].get(v, 0) + 1
        a["last_metrics"] = r.get("metrics")
        cost = r.get("cost") or {}
        tok = cost.get("tokens")
        if isinstance(tok, (int, float)) and tok > 0:
            a["tokens_sum"] += tok
            a["tokens_n"] += 1
        calls = cost.get("llm_calls")
        if isinstance(calls, (int, float)):
            a["llm_calls_sum"] += calls
    stages = []
    for s in [*order, *[k for k in agg if k not in order]]:
        if s not in agg:
            continue
        a = agg[s]
        stages.append({
            "stage": s, "runs": a["runs"],
            "avg_elapsed": round(a["elapsed_sum"] / a["elapsed_n"], 1) if a["elapsed_n"] else None,
            "verdicts": a["verdicts"], "last_metrics": a["last_metrics"],
            "avg_tokens": round(a["tokens_sum"] / a["tokens_n"]) if a["tokens_n"] else None,
            "total_tokens": a["tokens_sum"] or None,
            "total_llm_calls": a["llm_calls_sum"] or None,
        })
    return {"stages": stages, "recent": rows[-20:]}


def _read_stage_rows(limit: int = 800) -> list[dict]:
    p = _ROOT / "logs" / "stage_quality.jsonl"
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass
    return rows


@app.get("/api/timeline")
def api_timeline(run_id: Optional[str] = None, limit: int = 800):
    """单次 run 的逐节点时间线(design-v3 §6 / M1):按 run_id 聚合 stage_quality.jsonl,
    保留时序(重试=同 stage 多次出现)。run_id 缺省取最近一次 run。"""
    from src.stage_report import aggregate_timeline  # noqa: WPS433
    return aggregate_timeline(_read_stage_rows(limit), run_id=run_id)


@app.get("/api/checklist")
def api_checklist(report_id: Optional[str] = None):
    """一次分析的交付验收清单(design-v3 M1 决策视角):采集/分析/质检三层,
    每项 ✓ 达标 / ✗ 打回补充(owner_node + 修复提示)。report_id 缺省取最近一份报告。"""
    from src.checklist import build_checklist  # noqa: WPS433
    if not report_id:
        idx = _load_index()
        if not idx:
            return {"report_id": None, "target": None, "competitors": [], "status": None,
                    "groups": [], "summary": {"done": 0, "total": 0, "open_repairs": 0,
                                              "blocked": 0, "repairs_by_owner": {}}}
        report_id = idx[0]["report_id"]
    path = _REPORTS_DIR / f"{report_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report not found")
    return build_checklist(json.loads(path.read_text(encoding="utf-8")))
