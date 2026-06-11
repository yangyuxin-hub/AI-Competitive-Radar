"""StageReport 统一环节契约 — 见 docs/design-v3.md §3/§4（M0 契约就位）

让每个环节用同一种结构回答"我过了吗 / 哪里坏了 / 怎么修":
- to_stage_report(stage, state): 把 collector/analyzer/writer/reviewer 各自的现有判定
  （quality_audit / schema_draft / report_draft / quality_report）**收编**成同构 StageReport，
  不改任何检查函数、不改控制行为（M0 纯观测加法）。
- gap_owner(gap) / task_key(gap): 确定性推导"这个缺口该回哪个子节点、预算主键是什么"，
  为后续统一控制环（§4 精准打回）打地基；纯派生，不加 state 字段。

设计约束（契约级）:
- status 是环节自己的判定，由 checks/gaps 按统一规则派生（derive_status），禁止下游反推。
- 本模块是**适配层**：只读 state 既有产物，零副作用，失败不抛（调用方 stage_eval 已 try 兜底）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional, TypedDict

Verdict = Literal["pass", "warn", "fail"]
Status = Literal["ok", "degraded", "failed"]
Severity = Literal["error", "warning", "info"]

_CHIP = re.compile(r"\[S[0-9A-F]{7}\]")
_REF = re.compile(r'"(S[0-9A-F]{7})"')

REQUIRED_CLAIM_TYPES = ("feature_existence", "performance_quality", "pricing", "user_pain")


# ────────────────────────────────────────────────────────────────────────────
# Schema（旁路观测/控制对象，不进 schema_draft / 报告正文 / API 报告返回）
# ────────────────────────────────────────────────────────────────────────────

class Check(TypedDict):
    check_id: str
    verdict: Verdict
    severity: Severity
    location: str
    detail: str


class Gap(TypedDict):
    owner_node: str
    product: Optional[str]
    claim_type: Optional[str]
    gap_type: str
    fix: dict
    fixable: bool
    task_key: str


class StageReport(TypedDict):
    stage: str
    node: str
    run_id: Optional[str]
    attempt: int
    status: Status
    produced: dict
    checks: list  # list[Check]
    gaps: list    # list[Gap]
    cost: dict


# ────────────────────────────────────────────────────────────────────────────
# owner 路由表（§5 control_plane.gap_owner 的代码内置默认；缺失回退派生规则）
#   gap_type 与 claim_type 无关的，进 _OWNER_BY_GAP_TYPE（优先级最高）
#   与 claim_type 相关的覆盖缺口，进 _OWNER_BY_CT_AND_TYPE
# ────────────────────────────────────────────────────────────────────────────

_OWNER_BY_GAP_TYPE = {
    "no_official": "fetch_official",
    "pricing_no_number": "fetch_official",
    "bias_all_vendor": "fetch_community",
    "community_missing": "fetch_community",
    "community_low_quality": "fetch_community",
    "total_too_few": "backfill",
}

_OWNER_BY_CT_AND_TYPE = {
    ("feature_existence", "coverage_short"): "fetch_official",
    ("pricing", "coverage_short"): "fetch_official",
    ("user_pain", "coverage_short"): "fetch_community",
    ("performance_quality", "coverage_short"): "fetch_community",
}

# 按 claim_type 的兜底归属（无法精确匹配 gap_type 时）
_OWNER_BY_CT_FALLBACK = {
    "feature_existence": "fetch_official",
    "pricing": "fetch_official",
    "user_pain": "fetch_community",
    "performance_quality": "fetch_community",
}

# Reviewer 错误的 reject_target → owner（analyzer/writer 类缺口不回采集子节点）
_OWNER_BY_REJECT_TARGET = {
    "analyzer": "analyzer",
    "writer": "writer",
}


def gap_owner(gap: dict) -> str:
    """确定性推导缺口归属子节点。优先级:显式 owner_node > gap_type 专属 >
    (claim_type,gap_type) > reject_target(analyzer/writer) > claim_type 兜底 > 'collector'。"""
    if gap.get("owner_node"):
        return gap["owner_node"]
    gt = gap.get("gap_type")
    if gt in _OWNER_BY_GAP_TYPE:
        return _OWNER_BY_GAP_TYPE[gt]
    ct = gap.get("claim_type")
    if (ct, gt) in _OWNER_BY_CT_AND_TYPE:
        return _OWNER_BY_CT_AND_TYPE[(ct, gt)]
    rt = gap.get("reject_target")
    if rt in _OWNER_BY_REJECT_TARGET:
        return _OWNER_BY_REJECT_TARGET[rt]
    if ct in _OWNER_BY_CT_FALLBACK:
        return _OWNER_BY_CT_FALLBACK[ct]
    return "collector"


def task_key(gap: dict) -> str:
    """预算/去重主键: owner:product:claim_type（None 归一为 '*'）。"""
    owner = gap_owner(gap)
    p = gap.get("product") or "*"
    ct = gap.get("claim_type") or "*"
    return f"{owner}:{p}:{ct}"


def _to_gap(raw: dict, *, default_fixable: bool = True) -> Gap:
    """把 quality.py gap 或 reviewer reject_requirement 统一成 Gap。"""
    g: Gap = {
        "owner_node": gap_owner(raw),
        "product": raw.get("product"),
        "claim_type": raw.get("claim_type"),
        "gap_type": raw.get("gap_type") or raw.get("issue_type") or "unknown",
        "fix": raw.get("fix") or {},
        "fixable": bool(raw.get("fixable", default_fixable)),
        "task_key": "",
    }
    g["task_key"] = task_key({**raw, "owner_node": g["owner_node"],
                              "claim_type": g["claim_type"], "product": g["product"]})
    return g


# ────────────────────────────────────────────────────────────────────────────
# status 派生（唯一真相规则；各环节共用，不再各自定义）
# ────────────────────────────────────────────────────────────────────────────

def derive_status(checks: list, gaps: list) -> Status:
    if any(c.get("severity") == "error" for c in checks):
        return "failed"
    if gaps or any(c.get("severity") == "warning" for c in checks):
        return "degraded"
    return "ok"


def status_to_verdict(status: Status) -> Verdict:
    return {"ok": "pass", "degraded": "warn", "failed": "fail"}.get(status, "warn")


def _check(check_id: str, ok: bool, location: str, detail: str = "",
           severity_on_fail: Severity = "error") -> Check:
    return {
        "check_id": check_id,
        "verdict": "pass" if ok else "fail",
        "severity": "info" if ok else severity_on_fail,
        "location": location,
        "detail": "" if ok else detail,
    }


# ────────────────────────────────────────────────────────────────────────────
# 各环节 → checks/gaps/produced 收编（只读既有 state 产物）
# ────────────────────────────────────────────────────────────────────────────

def _collector_parts(state: dict):
    qa = (state.get("collection_meta") or {}).get("quality_audit") or {}
    ev = state.get("raw_evidence") or []
    raw_gaps = qa.get("gaps") or []
    gaps = [_to_gap(g) for g in raw_gaps]
    quantity = qa.get("quantity_gaps") or []
    quality = qa.get("quality_gaps") or []
    checks = [
        _check("coverage_gate", not quantity, "collection_meta.quality_audit",
               f"{len(quantity)} 个数量缺口", severity_on_fail="warning"),
        _check("quality_gate", not quality, "collection_meta.quality_audit",
               f"{len(quality)} 个质量缺口", severity_on_fail="warning"),
        _check("has_evidence", bool(ev), "raw_evidence",
               "未采集到任何证据", severity_on_fail="error"),
    ]
    produced = {
        "evidence": len(ev),
        "avg_quality": qa.get("avg_quality"),
        "quantity_gaps": len(quantity),
        "quality_gaps": len(quality),
    }
    return checks, gaps, produced


def _evidence_planner_parts(state: dict):
    plan = state.get("evidence_plan") or (state.get("analysis_meta") or {}).get("evidence_plan") or {}
    required = plan.get("required_claim_types") or []
    optional = plan.get("optional_claim_types") or []
    tasks = plan.get("evidence_tasks") or []
    checks = [
        _check("plan_has_required_claims", bool(required), "evidence_plan.required_claim_types",
               "未生成必需证据类型", severity_on_fail="error"),
        _check("plan_has_tasks", bool(tasks), "evidence_plan.evidence_tasks",
               "未生成采集任务", severity_on_fail="error"),
    ]
    produced = {
        "intent": plan.get("analysis_intent"),
        "required_claim_types": len(required),
        "optional_claim_types": len(optional),
        "evidence_tasks": len(tasks),
        "required": required,
        "optional": optional,
    }
    return checks, [], produced


def _analyzer_parts(state: dict):
    sd = state.get("schema_draft") or {}
    ev_ids = {e.get("evidence_id") for e in (state.get("raw_evidence") or [])}
    blob = json.dumps(sd, ensure_ascii=False)
    refs = _REF.findall(blob)
    halluc = sum(1 for r in refs if r not in ev_ids)
    feats = (sd.get("feature_tree") or {}).get("features") or []
    # unknown_hits: 只数语义上"未判定"的字段(support_status / winner / gap_type == "unknown"),
    # 不再用 blob.count("unknown") 对整段 JSON 做子串计数(会把 source_freshness/billing_cycle
    # 等无关字段里的 "unknown" 也算进去,虚高且不可解释)。
    unknown_hits = 0
    for feat in feats:
        for pdata in (feat.get("products") or {}).values():
            if (pdata or {}).get("support_status") == "unknown":
                unknown_hits += 1
        gap = feat.get("gap") or {}
        if gap.get("winner") == "unknown":
            unknown_hits += 1
        if gap.get("gap_type") == "unknown":
            unknown_hits += 1
    checks = [
        _check("evidence_ref_integrity", halluc == 0, "schema_draft",
               f"{halluc} 个引用不存在于 raw_evidence（疑似幻觉 ID）", severity_on_fail="error"),
        _check("facts_present", bool(feats), "schema_draft.feature_tree",
               "feature_tree 为空", severity_on_fail="warning"),
    ]
    produced = {
        "evidence_refs": len(refs),
        "hallucination_count": halluc,
        "unknown_hits": unknown_hits,
        "feature_dims": len(feats),
    }
    return checks, [], produced


def _writer_parts(state: dict):
    rpt = state.get("report_draft") or ""
    leak = rpt.count("quality_score")
    checks = [
        _check("report_nonempty", bool(rpt), "report_draft",
               "报告为空", severity_on_fail="error"),
        _check("no_score_leak", leak == 0, "report_draft",
               f"正文出现 quality_score 字面量 {leak} 次", severity_on_fail="warning"),
    ]
    produced = {
        "report_chars": len(rpt),
        "chips": len(_CHIP.findall(rpt)),
        "modules": rpt.count("\n## "),
        "score_leak": leak,
    }
    return checks, [], produced


def _reviewer_parts(state: dict):
    qr = state.get("quality_report") or {}
    errors = qr.get("errors") or []
    warnings = qr.get("warnings") or []
    checks: list = []
    for e in errors:
        checks.append({
            "check_id": e.get("rule") or e.get("issue_type") or "?",
            "verdict": "fail", "severity": "error",
            "location": e.get("location") or "", "detail": e.get("detail") or "",
        })
    for w in warnings:
        checks.append({
            "check_id": w.get("rule") or w.get("issue_type") or "?",
            "verdict": "fail", "severity": "warning",
            "location": w.get("location") or "", "detail": w.get("detail") or "",
        })
    # gaps: 只把 reject_target==collector 的错误升级成可补缺口（带 owner/claim_type）
    gaps = [_to_gap(e) for e in errors if e.get("reject_target") == "collector"]
    produced = {
        "quality_score": qr.get("quality_score"),
        "failed_rules": len(qr.get("failed_rules") or []),
        "warning_rules": len(qr.get("warning_rules") or []),
        "status": state.get("status"),
    }
    return checks, gaps, produced


_PARTS = {
    "evidence_planner": _evidence_planner_parts,
    "collector": _collector_parts,
    "analyzer": _analyzer_parts,
    "writer": _writer_parts,
    "degraded_writer": _writer_parts,   # 旧档案回放兼容(节点已删)
    "guard_revise": _writer_parts,      # M4:修订后重渲染,产物口径同 writer
    "reviewer": _reviewer_parts,
}


def _attempt_for(stage: str, state: dict) -> int:
    rc = state.get("retry_count") or {}
    key = "writer" if stage in ("degraded_writer", "guard_revise") else stage
    return int(rc.get(key, 0)) + 1


_VERDICT_TO_STATUS = {"pass": "ok", "warn": "degraded", "fail": "failed"}
_STATUS_RANK = {"ok": 0, "degraded": 1, "failed": 2}


def _gap_brief(g: dict) -> dict:
    return {
        "task_key": g.get("task_key"),
        "owner_node": g.get("owner_node"),
        "gap_type": g.get("gap_type"),
        "product": g.get("product"),
        "claim_type": g.get("claim_type"),
        "fixable": bool(g.get("fixable", True)),
    }


def aggregate_timeline(rows: list, run_id: Optional[str] = None) -> dict:
    """把 stage_quality.jsonl 行按 run_id 聚合成**有序时间线**(见 design-v3 §6 / M1)。

    与 /api/stage_quality(跨 run 按 stage 聚合)不同:这里保留单次 run 内**逐节点的时序**,
    重试会自然表现为同一 stage 多次出现。run_id 缺省时取最近一次 run(最后出现的 run_id)。
    兼容 M0 加法 schema:优先用 status/checks/gaps,缺失时回退旧 verdict/metrics。
    """
    if run_id is None:
        for r in reversed(rows):
            if r.get("run_id"):
                run_id = r["run_id"]
                break

    entries: list = []
    total_elapsed = 0.0
    total_tokens = 0
    total_llm_calls = 0
    open_gaps = 0
    worst = "ok"
    for r in rows:
        if run_id is not None and r.get("run_id") != run_id:
            continue
        status = r.get("status") or _VERDICT_TO_STATUS.get(r.get("verdict"), "ok")
        checks = r.get("checks") or []
        gaps = r.get("gaps") or []
        el = r.get("elapsed_sec")
        if isinstance(el, (int, float)):
            total_elapsed += el
        cs = {"pass": 0, "warn": 0, "fail": 0}
        for c in checks:
            if c.get("verdict") == "pass":
                cs["pass"] += 1
            elif c.get("severity") == "error":
                cs["fail"] += 1
            else:
                cs["warn"] += 1
        g_brief = [_gap_brief(g) for g in gaps]
        open_gaps += len(g_brief)
        if _STATUS_RANK.get(status, 0) > _STATUS_RANK.get(worst, 0):
            worst = status
        cost = r.get("cost")
        if cost and isinstance(cost.get("tokens"), (int, float)):
            total_tokens += cost["tokens"]
        if cost and isinstance(cost.get("llm_calls"), (int, float)):
            total_llm_calls += cost["llm_calls"]
        entries.append({
            "seq": len(entries) + 1,
            "stage": r.get("stage"),
            "node": r.get("node") or r.get("stage"),
            "status": status,
            "verdict": r.get("verdict"),
            "attempt": r.get("attempt"),
            "elapsed_sec": el,
            "ts": r.get("ts"),
            "produced": r.get("produced") or r.get("metrics") or {},
            "checks_summary": cs,
            "gaps": g_brief,
            "cost": cost,
        })

    return {
        "run_id": run_id,
        "entries": entries,
        "summary": {
            "nodes": len(entries),
            "total_elapsed_sec": round(total_elapsed, 1),
            "worst_status": worst if entries else None,
            "open_gaps": open_gaps,
            "total_tokens": total_tokens,
            "total_llm_calls": total_llm_calls,
        },
    }


def to_stage_report(stage: str, state: dict, *, elapsed_sec: Optional[float] = None,
                    run_id: Optional[str] = None, tokens: Optional[dict] = None) -> Optional[StageReport]:
    """把某 stage 的现有 state 产物收编成统一 StageReport。未知 stage 返回 None。

    tokens: graph._instrument 用 llm.begin_token_accumulator() 收集的本节点 LLM 调用
    token 累计 {prompt_tokens, completion_tokens, calls};非 LLM 节点为 None 或 calls=0。"""
    fn = _PARTS.get(stage)
    if not fn:
        return None
    checks, gaps, produced = fn(state)
    has_tok = bool(tokens and tokens.get("calls"))
    report: StageReport = {
        "stage": stage,
        "node": stage,  # 未拆子节点时 node==stage；Collector 拆分后由子节点各自指定
        "run_id": run_id,
        "attempt": _attempt_for(stage, state),
        "status": derive_status(checks, gaps),
        "produced": produced,
        "checks": checks,
        "gaps": gaps,
        "cost": {
            "elapsed_sec": round(elapsed_sec, 1) if elapsed_sec is not None else None,
            "tokens": (tokens["prompt_tokens"] + tokens["completion_tokens"]) if has_tok else None,
            "prompt_tokens": tokens.get("prompt_tokens") if has_tok else None,
            "completion_tokens": tokens.get("completion_tokens") if has_tok else None,
            "llm_calls": tokens.get("calls") if has_tok else None,
        },
    }
    return report


def stage_timings_from_run(run_id: Optional[str], limit: int = 800,
                           path: Optional[Path] = None) -> list[dict]:
    """从 logs/stage_quality.jsonl 按 run_id 取逐节点耗时,转成 business_value_metrics 需要的
    [{"node", "duration_sec"}, ...]。CLI(run_demo)路径用它补 stage_timings,与 API 路径
    (state["_stage_report"])同源——都来自 graph._instrument 的节点内实测。"""
    if not run_id:
        return []
    p = path or Path("logs/stage_quality.jsonl")
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if r.get("run_id") == run_id:
            rows.append(r)
    return [
        {"node": r.get("node") or r.get("stage"),
         "duration_sec": (r.get("cost") or {}).get("elapsed_sec") or r.get("elapsed_sec") or 0}
        for r in rows
    ]


def node_typical_seconds(defaults: dict, limit: int = 500, min_samples: int = 5,
                         path: Optional[Path] = None) -> dict:
    """按 logs/stage_quality.jsonl 最近 limit 条历史算各节点 elapsed_sec 的 p50,用于前端 ETA。
    某节点样本 < min_samples 时回退 defaults(文件缺失/解析失败也回退,失败静默不抛)。"""
    p = path or Path("logs/stage_quality.jsonl")
    samples: dict[str, list[float]] = {}
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                node = r.get("node") or r.get("stage")
                el = (r.get("cost") or {}).get("elapsed_sec")
                if el is None:
                    el = r.get("elapsed_sec")
                if node and isinstance(el, (int, float)) and el > 0:
                    samples.setdefault(node, []).append(float(el))
        except OSError:
            pass
    result = dict(defaults)
    for node, vals in samples.items():
        if len(vals) >= min_samples:
            vals.sort()
            result[node] = vals[len(vals) // 2]
    return result
