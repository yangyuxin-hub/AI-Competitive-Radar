# -*- coding: utf-8 -*-
"""Detailed end-to-end trace for the competitive analysis pipeline.

Outputs one run directory under out/trace_runs/<TRACE_TAG>-<timestamp>/:
- node_trace.jsonl: input/output AgentState for each graph node
- progress_events.jsonl: collector/analyzer/LLM progress callbacks
- llm_calls_this_run.jsonl: full LLM prompts and raw outputs from this run
- rule_audit.json: post-run quick_validate + reviewer result summary
- final_state.json: final AgentState
- summary.md: human-readable overview
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

LLM_LOG = ROOT / "logs" / "llm_calls.jsonl"


def _json_default(obj: Any) -> str:
    return str(obj)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _append_jsonl(path: Path, data: Any) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, default=_json_default) + "\n")


def _llm_line_count() -> int:
    if not LLM_LOG.exists():
        return 0
    return len(LLM_LOG.read_text(encoding="utf-8").splitlines())


def _summarize_state(state: dict) -> dict:
    schema = state.get("schema_draft") or {}
    qr = state.get("quality_report") or {}
    evidence = state.get("raw_evidence") or []
    features = (schema.get("feature_tree") or {}).get("features") or []
    pricing_products = (schema.get("pricing_model") or {}).get("products") or []
    user_persona = schema.get("user_persona") or {}
    swot = schema.get("swot") or {}
    return {
        "status": state.get("status"),
        "evidence_count": len(evidence),
        "claim_type_counts": _count_by(evidence, "claim_type"),
        "source_type_counts": _count_by(evidence, "source_type"),
        "collection_source_counts": _count_by(evidence, "collection_source"),
        "schema_sections": sorted(k for k in schema.keys() if k != "analysis_meta"),
        "feature_count": len(features),
        "pricing_product_count": len(pricing_products),
        "pricing_tier_count": sum(len(p.get("tiers") or []) for p in pricing_products),
        "pain_point_count": len(user_persona.get("pain_points") or []),
        "recommendation_count": len(schema.get("recommendations") or []),
        "swot_count": sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats")),
        "report_chars": len(state.get("report_draft") or ""),
        "quality_score": qr.get("quality_score"),
        "failed_rules": qr.get("failed_rules") or [],
        "warning_rules": qr.get("warning_rules") or [],
        "passed_rules": qr.get("passed_rules") or [],
        "retry_count": state.get("retry_count") or {},
        "reject_target": state.get("reject_target"),
    }


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda item: (-item[1], item[0])))


def _copy_new_llm_calls(out_dir: Path, before: int) -> list[dict]:
    calls: list[dict] = []
    if not LLM_LOG.exists():
        return calls
    lines = LLM_LOG.read_text(encoding="utf-8").splitlines()[before:]
    dst = out_dir / "llm_calls_this_run.jsonl"
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        calls.append(rec)
        _append_jsonl(dst, rec)
    return calls


def _llm_summary(calls: list[dict]) -> list[dict]:
    rows: dict[str, dict] = {}
    for rec in calls:
        label = rec.get("label") or "unknown"
        row = rows.setdefault(label, {
            "label": label,
            "count": 0,
            "duration_sec_total": 0.0,
            "prompt_tokens_total": 0,
            "completion_tokens_total": 0,
        })
        row["count"] += 1
        row["duration_sec_total"] += float(rec.get("duration_sec") or 0)
        row["prompt_tokens_total"] += int(rec.get("prompt_tokens") or 0)
        row["completion_tokens_total"] += int(rec.get("completion_tokens") or 0)
    return sorted(rows.values(), key=lambda r: (-r["duration_sec_total"], r["label"]))


def _post_run_rule_audit(final_state: dict) -> dict:
    from src.analyzer import quick_validate_derivations, quick_validate_facts

    schema = final_state.get("schema_draft") or {}
    evidence = final_state.get("raw_evidence") or []
    meta = final_state.get("analysis_meta") or {}
    facts = {
        "feature_tree": schema.get("feature_tree") or {},
        "pricing_model": schema.get("pricing_model") or {},
        "user_persona": schema.get("user_persona") or {},
    }
    derivations = {
        "swot": schema.get("swot") or {},
        "recommendations": schema.get("recommendations") or [],
    }
    quality_report = final_state.get("quality_report") or {}
    collection_meta = final_state.get("collection_meta") or {}
    return {
        "quick_validate_facts_issues": quick_validate_facts(facts, evidence, meta),
        "quick_validate_derivations_issues": quick_validate_derivations(derivations, facts, evidence),
        "reviewer_quality_report": quality_report,
        "collection_meta": collection_meta,
        "retry_count": final_state.get("retry_count") or {},
        "final_status": final_state.get("status"),
    }


def _write_summary(out_dir: Path, args: dict, node_records: list[dict], llm_calls: list[dict], rule_audit: dict) -> None:
    final = node_records[-1]["output_summary"] if node_records else {}
    lines = [
        "# Detailed Pipeline Trace",
        "",
        "## Run Input",
        "",
        f"- user_input: `{args['user_input']}`",
        f"- target_product: `{args['target_product']}`",
        f"- competitors: `{', '.join(args['competitors'])}`",
        f"- analysis_focus: `{', '.join(args['analysis_focus'])}`",
        f"- runtime_profile: `{args['runtime_profile']}`",
        f"- analysis_intent: `{args.get('analysis_intent')}`",
        "",
        "## Node Flow",
        "",
    ]
    for rec in node_records:
        s = rec["output_summary"]
        lines.append(
            f"- `{rec['node']}` {rec['duration_sec']:.1f}s: "
            f"status={s.get('status')}, evidence={s.get('evidence_count')}, "
            f"features={s.get('feature_count')}, recs={s.get('recommendation_count')}, "
            f"quality={s.get('quality_score')}"
        )
    lines += [
        "",
        "## Rule Handling",
        "",
        f"- final_status: `{rule_audit.get('final_status')}`",
        f"- retry_count: `{rule_audit.get('retry_count')}`",
        f"- facts quick_validate issues: `{len(rule_audit.get('quick_validate_facts_issues') or [])}`",
        f"- derivations quick_validate issues: `{len(rule_audit.get('quick_validate_derivations_issues') or [])}`",
    ]
    qr = rule_audit.get("reviewer_quality_report") or {}
    lines += [
        f"- reviewer score: `{qr.get('quality_score')}`",
        f"- passed_rules: `{qr.get('passed_rules')}`",
        f"- failed_rules: `{qr.get('failed_rules')}`",
        f"- warning_rules: `{qr.get('warning_rules')}`",
        "",
        "## LLM Calls",
        "",
    ]
    for row in _llm_summary(llm_calls):
        lines.append(
            f"- `{row['label']}` x{row['count']}: "
            f"{row['duration_sec_total']:.1f}s, "
            f"prompt_tokens={row['prompt_tokens_total']}, "
            f"completion_tokens={row['completion_tokens_total']}"
        )
    lines += [
        "",
        "## Output Files",
        "",
        "- `node_trace.jsonl`: each graph node input/output AgentState",
        "- `progress_events.jsonl`: collector/analyzer/LLM progress callbacks",
        "- `llm_calls_this_run.jsonl`: full model system prompt, user payload, raw output",
        "- `rule_audit.json`: quick_validate and reviewer details",
        "- `final_state.json`: final AgentState",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    from src import analyzer as analyzer_mod
    from src import collector as collector_mod
    from src import intake
    from src import llm as llm_mod
    from src.collector import reset_debug_file
    from src.graph import _resolve_run_args, build_app
    from src.state import build_initial_state

    user_input = os.environ.get("TRACE_INPUT", "分析 Cursor 和 Windsurf 在代码补全体验上的差距")
    trace_target = os.environ.get("TRACE_TARGET", "Cursor")
    trace_competitors = [
        c.strip()
        for c in os.environ.get("TRACE_COMPETITORS", "Windsurf,GitHubCopilot").split(",")
        if c.strip()
    ]
    trace_profile = os.environ.get("TRACE_PROFILE", "fast")
    trace_tag = os.environ.get("TRACE_TAG", "doubao_seed_trace")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "out" / "trace_runs" / f"{trace_tag}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    progress_path = out_dir / "progress_events.jsonl"
    node_trace_path = out_dir / "node_trace.jsonl"
    started = time.time()
    llm_before = _llm_line_count()

    def record_progress(source: str, event: dict) -> None:
        _append_jsonl(progress_path, {
            "elapsed_sec": round(time.time() - started, 2),
            "source": source,
            "event": event,
        })

    collector_mod.set_progress_callback(lambda event: record_progress("collector", event))
    analyzer_mod.set_progress_callback(lambda event: record_progress("analyzer", event))
    llm_mod.set_llm_callback(lambda event: record_progress("llm", event))

    node_records: list[dict] = []
    final_state: dict | None = None

    try:
        # Stage 0: run intake so its prompt/output is also captured in llm_calls_this_run.jsonl.
        intake_started = time.time()
        draft = intake.propose(user_input)
        record_progress("intake", {
            "phase": "done",
            "duration_sec": round(time.time() - intake_started, 2),
            "draft": draft,
        })

        focus = [draft.get("focus_suggested") or (draft.get("focus_candidates") or ["代码补全体验"])[0]]
        purpose = draft.get("purpose_suggested") or "学习竞品优点,优化自身产品"
        intent = draft.get("analysis_intent") or "feature_compare"

        target, competitors, focus, purpose, resolved_input, intent = _resolve_run_args(
            trace_target,
            trace_competitors,
            focus,
            purpose,
            user_input,
            intent,
        )
        args = {
            "target_product": target,
            "competitors": competitors,
            "analysis_focus": focus,
            "analysis_purpose": purpose,
            "user_input": resolved_input,
            "runtime_profile": trace_profile,
            "analysis_intent": intent,
        }
        _write_json(out_dir / "run_args.json", args)

        reset_debug_file()
        app = build_app()
        current_state = build_initial_state(**args)
        _write_json(out_dir / "initial_state.json", current_state)

        last_node_finished = time.time()
        for event in app.stream(current_state, config={"recursion_limit": 50}):
            for node_name, state_after in event.items():
                now = time.time()
                input_state = copy.deepcopy(current_state)
                output_state = copy.deepcopy(state_after)
                current_state = output_state
                duration = now - last_node_finished
                last_node_finished = now
                rec = {
                    "elapsed_sec": round(time.time() - started, 2),
                    "node": node_name,
                    "duration_sec": round(duration, 2),
                    "input_summary": _summarize_state(input_state),
                    "output_summary": _summarize_state(output_state),
                    "input_state": input_state,
                    "output_state": output_state,
                }
                node_records.append(rec)
                _append_jsonl(node_trace_path, rec)
                final_state = output_state

        if final_state is None:
            raise RuntimeError("graph finished without yielding any node")

        rule_audit = _post_run_rule_audit(final_state)
        _write_json(out_dir / "rule_audit.json", rule_audit)
        _write_json(out_dir / "final_state.json", final_state)
        if final_state.get("report_draft"):
            (out_dir / "report.md").write_text(final_state["report_draft"], encoding="utf-8")

        llm_calls = _copy_new_llm_calls(out_dir, llm_before)
        _write_json(out_dir / "llm_summary.json", _llm_summary(llm_calls))
        _write_summary(out_dir, args, node_records, llm_calls, rule_audit)

        print(f"TRACE_DIR={out_dir}")
        print(f"STATUS={final_state.get('status')}")
        print(f"QUALITY_SCORE={(final_state.get('quality_report') or {}).get('quality_score')}")
        print(f"LLM_CALLS={len(llm_calls)}")
        print(f"NODE_COUNT={len(node_records)}")
        return 0
    finally:
        collector_mod.set_progress_callback(None)
        analyzer_mod.set_progress_callback(None)
        llm_mod.set_llm_callback(None)


if __name__ == "__main__":
    raise SystemExit(main())
