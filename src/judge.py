"""LLM-as-Judge 报告质量评测(离线 harness)

定位:R1-R7 查「内部自洽」(引用/推理链/冲突/时效),judge 查「是不是一份好分析」
(准确性/洞察力/实用性/聚焦度)。两者互补,judge 不替代 R1-R7,产出独立的 analysis_quality。

设计:确定性信号(代码算,可复现) + LLM 1-5 锚定打分(temp=0),前者喂给后者当锚,压方差。
目的是「优化报告」——评分卡里的 fix_suggestion 直接指向该改哪段 prompt(prompts/analyzer_*.md)。

用法:
    python -m src.judge out/ai_coding                 # 评测某域已生成的报告
    python -m src.judge --report a.md --schema a.json  # 指定文件
设计文档:docs/quality-judge-design.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_RUBRIC_PATH = _ROOT / "config" / "quality_rubric.yaml"

# 实用性看这几个「可落地字段」(roadmap §7);当前 schema 可能尚未有,缺失会如实压低 actionability
_ACTION_FIELDS = ["expected_impact", "success_metric", "risk", "time_horizon"]


# ────────────────────────────────────────────────────────────────────────────
# rubric
# ────────────────────────────────────────────────────────────────────────────

def load_rubric() -> dict:
    import yaml
    with _RUBRIC_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def weights_for_purpose(rubric: dict, purpose: str) -> dict:
    pw = rubric.get("purpose_weights") or {}
    for key, w in pw.items():
        if key != "default" and key and key in (purpose or ""):
            return w
    return pw.get("default") or {}


# ────────────────────────────────────────────────────────────────────────────
# 确定性信号(不调 LLM,可独立测试)
# ────────────────────────────────────────────────────────────────────────────

def _iter_feature_rows(schema: dict) -> list[dict]:
    """feature_tree 可能是 {category, features:[]} 或 [ {..}, .. ]。统一拍平成 feature 行。"""
    ft = schema.get("feature_tree")
    rows: list[dict] = []
    blocks = ft if isinstance(ft, list) else [ft] if isinstance(ft, dict) else []
    for blk in blocks:
        if isinstance(blk, dict):
            rows.extend(blk.get("features") or [])
    return rows


def _swot_item_count(schema: dict) -> int:
    swot = schema.get("swot") or {}
    if not isinstance(swot, dict):
        return 0
    n = 0
    for k in ("strengths", "weaknesses", "opportunities", "threats"):
        v = swot.get(k)
        if isinstance(v, list):
            n += len(v)
    return n


def _ratio(num: int, den: int) -> float:
    return round(num / den, 3) if den else 0.0


def extract_features(schema: dict, meta: dict) -> dict:
    """算出可量化、可复现的客观信号。既进评分卡,也喂给 LLM 当打分锚点。"""
    feature_rows = _iter_feature_rows(schema)
    recs = schema.get("recommendations") or []
    swot_n = _swot_item_count(schema)

    # 准确性锚:有证据支撑的结论占比
    quality_scores = []
    for row in feature_rows:
        for prod in (row.get("products") or {}).values():
            qs = prod.get("quality_score")
            if isinstance(qs, dict):
                quality_scores.append(qs)
    qs_with_ev = sum(
        1 for qs in quality_scores
        if (qs.get("aggregation") or {}).get("representative_evidence_ids") or qs.get("evidence_ids")
    )
    recs_with_ev = sum(1 for r in recs if r.get("evidence_ids"))
    claim_total = len(quality_scores) + len(recs)
    claim_with_ev = qs_with_ev + recs_with_ev

    # 实用性锚:建议含可落地字段的占比 + 含优先级的占比
    recs_with_priority = sum(1 for r in recs if r.get("priority_score"))
    recs_with_action_fields = sum(
        1 for r in recs if any(r.get(f) for f in _ACTION_FIELDS)
    )

    # 洞察力锚:推导结论(swot + 建议)相对原始功能行的密度
    derived = swot_n + len(recs)

    return {
        "n_feature_rows": len(feature_rows),
        "n_recommendations": len(recs),
        "n_swot_items": swot_n,
        "evidence_coverage_ratio": _ratio(claim_with_ev, claim_total),
        "insight_density": _ratio(derived, len(feature_rows)),
        "recs_with_priority_ratio": _ratio(recs_with_priority, len(recs)),
        "recs_with_action_fields_ratio": _ratio(recs_with_action_fields, len(recs)),
        "missing_action_fields": [
            f for f in _ACTION_FIELDS
            if not any(r.get(f) for r in recs)
        ],
        "analysis_focus": meta.get("analysis_focus") or [],
        "feature_names": [r.get("name") for r in feature_rows if r.get("name")],
    }


def completeness_metrics(schema: dict, meta: dict) -> dict:
    """确定性「任务完成度」评分(零 LLM,可随报告即时附带)。
    衡量这份竞品分析「做全了没有」,与 R1-R7(自洽)、judge(好不好)互补。"""
    feats = extract_features(schema, meta)
    products = [p for p in [meta.get("target_product"), *(meta.get("competitors") or [])] if p]
    rows = _iter_feature_rows(schema)

    cells = filled = decided = 0
    for row in rows:
        pb = row.get("products") or {}
        for p in products:
            cells += 1
            d = pb.get(p)
            if isinstance(d, dict) and d.get("support_status") not in (None, "", "unknown"):
                filled += 1
        winner = (row.get("gap") or {}).get("winner")
        if winner and winner != "unknown":
            decided += 1

    matrix_fill = _ratio(filled, cells)
    gap_decisiveness = _ratio(decided, len(rows))
    has_pricing = 1.0 if (schema.get("pricing_model") or {}).get("products") else 0.0
    has_pains = 1.0 if (schema.get("user_persona") or {}).get("pain_points") else 0.0
    feature_depth = min(1.0, len(rows) / 4)  # 4+ 个对比功能算到顶

    overall = round(100 * (
        0.25 * feats["evidence_coverage_ratio"]
        + 0.20 * matrix_fill
        + 0.20 * gap_decisiveness
        + 0.15 * feats["recs_with_priority_ratio"]
        + 0.10 * feature_depth
        + 0.05 * has_pricing
        + 0.05 * has_pains
    ), 1)

    return {
        "overall": overall,
        "aspects": [
            {"key": "feature_coverage", "label": "功能对比覆盖", "value": matrix_fill,
             "detail": f"{filled}/{cells} 个产品×功能单元格有结论"},
            {"key": "evidence", "label": "结论溯源率", "value": feats["evidence_coverage_ratio"],
             "detail": "有 evidence 支撑的结论占比"},
            {"key": "gap_decisiveness", "label": "差距判定率", "value": gap_decisiveness,
             "detail": f"{decided}/{len(rows)} 个功能给出了明确胜负"},
            {"key": "rec_priority", "label": "建议带优先级", "value": feats["recs_with_priority_ratio"]},
            {"key": "rec_actionable", "label": "建议含落地字段", "value": feats["recs_with_action_fields_ratio"],
             "detail": "动作/收益/指标/风险/周期"},
        ],
        "counts": {
            "features": feats["n_feature_rows"],
            "recommendations": feats["n_recommendations"],
            "swot_items": feats["n_swot_items"],
            "products": len(products),
        },
        "missing_action_fields": feats["missing_action_fields"],
    }


# ────────────────────────────────────────────────────────────────────────────
# LLM 打分
# ────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(rubric: dict) -> str:
    lines = [
        "你是资深产品竞品分析评审。对一份竞品分析报告按以下维度用 1-5 整数打分。",
        "评分必须基于给出的『确定性指标』和报告正文,不要凭空给高分。",
        "每个维度都要给:score(1-5 整数)、justification(引用报告里具体段落/[SXXXXXXX] chip 作为依据)、",
        "fix_suggestion(若 <5,指出该改 prompts/analyzer_*.md 或 writer 的哪个方向)。",
        "",
        "维度与锚点:",
    ]
    for dim_id, d in (rubric.get("dimensions") or {}).items():
        lines.append(f"- {dim_id}（{d.get('name')}）: {d.get('desc')}")
        for lvl, anchor in (d.get("anchors") or {}).items():
            lines.append(f"    {lvl}分: {anchor}")
    lines += [
        "",
        "只输出 JSON,形如:",
        '{"accuracy":{"score":3,"justification":"...","fix_suggestion":"..."},'
        '"insight":{...},"actionability":{...},"focus":{...}}',
    ]
    return "\n".join(lines)


def score_report(
    report_md: str,
    schema: dict,
    meta: dict,
    rubric: Optional[dict] = None,
) -> dict:
    """对单份报告打分。返回评分卡(含各维 1-5、量化指标、加权 0-100、warning)。

    需要真实 LLM(ARK_API_KEY);Mock 模式会抛错——评分本质需要语义判断。
    """
    from .llm import get_llm, is_mock_mode

    if is_mock_mode():
        raise RuntimeError("judge 需要真实 LLM 语义判断,请取消 ANALYZER_MOCK 并设置 ARK_API_KEY")

    rubric = rubric or load_rubric()
    features = extract_features(schema, meta)

    raw = get_llm().call_json(
        system_prompt=_build_system_prompt(rubric),
        user_payload={
            "analysis_focus": meta.get("analysis_focus"),
            "analysis_purpose": meta.get("analysis_purpose"),
            "确定性指标": features,
            "报告正文": report_md[:12000],  # 截断保护,避免超长
        },
        max_tokens=2048,
        label="judge",
    )

    return _assemble_scorecard(raw, features, meta, rubric)


def _assemble_scorecard(raw: dict, features: dict, meta: dict, rubric: dict) -> dict:
    scale = rubric.get("scale", 5)
    weights = weights_for_purpose(rubric, meta.get("analysis_purpose") or "")
    warn = rubric.get("warn_threshold", 3)
    dims = rubric.get("dimensions") or {}

    scored: dict = {}
    weighted = 0.0
    warnings: list[str] = []
    for dim_id in dims:
        entry = raw.get(dim_id) or {}
        try:
            s = int(entry.get("score", 0))
        except (TypeError, ValueError):
            s = 0
        s = max(0, min(scale, s))
        w = weights.get(dim_id, 0)
        weighted += (s / scale) * 100 * w
        scored[dim_id] = {
            "name": dims[dim_id].get("name"),
            "score": s,
            "scale": scale,
            "weight": w,
            "justification": entry.get("justification", ""),
            "fix_suggestion": entry.get("fix_suggestion", ""),
        }
        if s < warn:
            warnings.append(
                f"[{dims[dim_id].get('name')}] {s}/{scale} · {entry.get('fix_suggestion', '')}"
            )

    return {
        "weighted_score": round(weighted, 1),
        "weights_profile": meta.get("analysis_purpose") or "default",
        "dimensions": scored,
        "deterministic_metrics": features,
        "warnings": warnings,
    }


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────

def _resolve_inputs(args) -> tuple[Path, Path]:
    if args.out_dir:
        d = Path(args.out_dir)
        if not d.is_absolute():
            d = _ROOT / d
        return d / "report.md", d / "schema_draft.json"
    return Path(args.report), Path(args.schema)


def _print_scorecard(card: dict) -> None:
    print("\n" + "=" * 60)
    print(f"报告质量评分(LLM-as-Judge) · 加权得分 {card['weighted_score']}/100"
          f"  [权重档: {card['weights_profile']}]")
    print("=" * 60)
    for dim_id, d in card["dimensions"].items():
        bar = "█" * d["score"] + "·" * (d["scale"] - d["score"])
        print(f"\n{d['name']:<6} {bar} {d['score']}/{d['scale']}  (权重 {d['weight']})")
        print(f"  依据: {d['justification']}")
        if d["fix_suggestion"]:
            print(f"  建议改: {d['fix_suggestion']}")
    m = card["deterministic_metrics"]
    print("\n--- 确定性指标 ---")
    print(f"  证据覆盖率: {m['evidence_coverage_ratio']:.0%} · 洞察密度: {m['insight_density']}"
          f" · 建议含优先级: {m['recs_with_priority_ratio']:.0%}"
          f" · 建议含落地字段: {m['recs_with_action_fields_ratio']:.0%}")
    if m["missing_action_fields"]:
        print(f"  ⚠ 建议普遍缺字段: {', '.join(m['missing_action_fields'])}(实用性受限,见 roadmap §7)")
    if card["warnings"]:
        print("\n--- 待优化方向 ---")
        for w in card["warnings"]:
            print(f"  • {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-as-Judge 报告质量评测")
    ap.add_argument("out_dir", nargs="?", help="out/<domain> 目录(含 report.md + schema_draft.json)")
    ap.add_argument("--report", help="报告 Markdown 路径")
    ap.add_argument("--schema", help="schema_draft.json 路径")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    if not args.out_dir and not (args.report and args.schema):
        ap.error("需要给 out/<domain> 目录,或同时给 --report 和 --schema")

    report_path, schema_path = _resolve_inputs(args)
    if not report_path.exists() or not schema_path.exists():
        print(f"找不到输入: {report_path} / {schema_path}")
        return 1

    report_md = report_path.read_text(encoding="utf-8")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    meta = schema.get("analysis_meta") or {}

    card = score_report(report_md, schema, meta)

    out_path = report_path.parent / "quality_judge.json"
    out_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    else:
        _print_scorecard(card)
        print(f"\n评分卡已写入: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
