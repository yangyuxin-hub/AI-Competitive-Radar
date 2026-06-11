"""LangGraph 编排 + main 入口 — 见 docs/design-v2.2.md §九

用法:
    # 无 API key,跑 Mock 闭环(推荐先跑这个)
    set ANALYZER_MOCK=1 & python -m src.graph

    # 真实 LLM(需 ARK_API_KEY)
    set ARK_API_KEY=... & python -m src.graph
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional


_ROOT = Path(__file__).resolve().parent.parent

# .env 加载已前移到 src/__init__.py(包导入即生效,所有入口统一),此处不再重复。

from .analyzer import analyzer_node  # noqa: E402
from .collector import collector_node  # noqa: E402
from .evidence_plan import evidence_planner_node  # noqa: E402
from .guard import guard_revise_node  # noqa: E402
from .reviewer import make_reviewer_node  # noqa: E402
from .state import AgentState, build_initial_state  # noqa: E402
from .writer import writer_node  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Graph wiring
# ────────────────────────────────────────────────────────────────────────────

def build_app(llm: Optional[object] = None, reviewer_mode: Optional[str] = None):
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as e:
        raise RuntimeError(
            "langgraph 未安装。pip install -r requirements.txt"
        ) from e

    mode = reviewer_mode or os.environ.get("REVIEWER_MODE", "minimal")
    # full 模式 R6 是硬门;minimal 终轮 R6(REVIEWER_R6_FINAL,默认开)也需注入 llm
    r6_final = os.environ.get("REVIEWER_R6_FINAL", "1").strip() not in ("0", "false", "False")
    if llm is None and (mode == "full" or r6_final):
        try:
            from .llm import get_llm, is_mock_mode
            if not is_mock_mode() and (os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY")):
                llm = get_llm()
        except Exception as e:
            print(f"[graph] WARN: R6 LLM 注入失败,Reviewer 将记录 warning: {e}")

    reviewer_node = make_reviewer_node(llm=llm, mode=mode)

    # 各环节质量评估埋点(§8.7):包一层计时+落 logs/stage_quality.jsonl。invoke/stream 两条路径都覆盖。
    # 同时设 run/stage 归因 + 开本节点 token 累加器(llm.py),让节点内所有 LLM 调用的 token
    # 归到本节点 → StageReport.cost;并把本节点 StageReport 挂回 state(单一计时口径,api 直接读)。
    def _instrument(name, fn):
        import time as _t
        from .llm import begin_token_accumulator, set_run_stage
        from .stage_eval import log_stage_quality

        def _wrapped(state):
            rid = (state.get("analysis_meta") or {}).get("agent_trace_id")
            set_run_stage(rid, name)
            tokens = begin_token_accumulator()
            _t0 = _t.time()
            out = fn(state)
            elapsed = _t.time() - _t0
            try:
                rid = (out.get("analysis_meta") or {}).get("agent_trace_id") or rid
                rec = log_stage_quality(name, out, elapsed_sec=elapsed, run_id=rid, tokens=tokens)
                if rec is not None:
                    # 把本节点 StageReport 挂到 state,api 直接读(单一计时口径,
                    # 不在 SSE 循环里用队列到达间隔重估一遍耗时)。
                    out = {**out, "_stage_report": rec}
            except Exception:  # noqa: BLE001
                pass
            return out
        return _wrapped

    graph = StateGraph(AgentState)
    graph.add_node("evidence_planner", _instrument("evidence_planner", evidence_planner_node))
    graph.add_node("collector", _instrument("collector", collector_node))
    graph.add_node("analyzer", _instrument("analyzer", analyzer_node))
    graph.add_node("writer", _instrument("writer", writer_node))
    graph.add_node("reviewer", _instrument("reviewer", reviewer_node))
    graph.add_node("guard_revise", _instrument("guard_revise", guard_revise_node))

    # v3 M4:一条直线 + 一次修订。打回路由/retry 配额/degraded_writer 已删
    # (54 run 仅 1 次触发打回,真实修复全在节点内自愈;Auditor 发现走前向回灌:
    # reviewer 定位清单 → guard_revise 确定性修订 → Reporter 重渲染 → 出货)。
    graph.set_entry_point("evidence_planner")
    graph.add_edge("evidence_planner", "collector")
    graph.add_edge("collector", "analyzer")
    graph.add_edge("analyzer", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_edge("reviewer", "guard_revise")
    graph.add_edge("guard_revise", END)

    return graph.compile()


# ────────────────────────────────────────────────────────────────────────────
# Main runner
# ────────────────────────────────────────────────────────────────────────────

def _load_domain_config() -> Optional[dict]:
    """读取 config/domains.yaml,按 DOMAIN env 选行业。返回 None 表示走 ai_coding 默认"""
    domain = os.environ.get("DOMAIN", "").strip()
    if not domain:
        return None
    try:
        import yaml
        with (_ROOT / "config" / "domains.yaml").open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("domains") or {}).get(domain)
    except Exception as e:
        print(f"[graph] WARN: DOMAIN={domain} 读取失败,回退默认: {e}")
        return None


def _resolve_run_args(
    target_product: Optional[str],
    competitors: Optional[list[str]],
    analysis_focus: Optional[list[str]],
    analysis_purpose: Optional[str],
    user_input: Optional[str],
    analysis_intent: Optional[str] = None,
) -> tuple[str, list[str], list[str], str, str, str]:
    dom = _load_domain_config()
    if dom:
        target_product = target_product or dom.get("target_product")
        competitors = competitors or dom.get("competitors")
        analysis_focus = analysis_focus or dom.get("analysis_focus")
        analysis_purpose = analysis_purpose or dom.get("analysis_purpose")
        print(f"[graph] 使用行业域: {dom.get('name', '?')} (target={target_product})")

    target_product = target_product or "Cursor"
    competitors = competitors or ["Windsurf", "GitHubCopilot"]
    try:
        from .collector import canonical_product_name
        target_product = canonical_product_name(target_product)
        competitors = [canonical_product_name(c) for c in competitors]
    except Exception:
        pass
    analysis_focus = analysis_focus or ["代码补全体验"]
    analysis_purpose = analysis_purpose or "学习竞品优点,优化自身产品"
    user_input = user_input or f"分析 {target_product} 与 {', '.join(competitors)} 在 {analysis_focus[0]} 上的差距"
    # 意图未显式给出时,从这句话推断(痛点/选型/定价/入场/功能对比)
    if not analysis_intent:
        try:
            from .intake import _detect_intent
            analysis_intent = _detect_intent(user_input)
        except Exception:  # noqa: BLE001
            analysis_intent = "feature_compare"
    return target_product, competitors, analysis_focus, analysis_purpose, user_input, analysis_intent


def run_demo_streaming(
    target_product: Optional[str] = None,
    competitors: Optional[list[str]] = None,
    analysis_focus: Optional[list[str]] = None,
    analysis_purpose: Optional[str] = None,
    user_input: Optional[str] = None,
    runtime_profile: str = "deep",
    analysis_intent: Optional[str] = None,
):
    """生成器: 每完成一个节点 yield (node_name, state_after_node)。
    供 Streamlit / 任何 stream UI 实时展示节点进度用。"""
    from .collector import reset_debug_file, reset_registry
    reset_debug_file()
    reset_registry()
    tp, comp, focus, purpose, ui, intent = _resolve_run_args(
        target_product, competitors, analysis_focus, analysis_purpose, user_input, analysis_intent
    )
    app = build_app()
    initial = build_initial_state(
        user_input=ui,
        target_product=tp,
        competitors=comp,
        analysis_focus=focus,
        analysis_purpose=purpose,
        runtime_profile=runtime_profile,
        analysis_intent=intent,
    )
    for event in app.stream(initial, config={"recursion_limit": 50}):
        # event 形如 {node_name: state_after_node}
        for node_name, state_after in event.items():
            yield node_name, state_after


def run_demo(
    target_product: Optional[str] = None,
    competitors: Optional[list[str]] = None,
    analysis_focus: Optional[list[str]] = None,
    analysis_purpose: Optional[str] = None,
    user_input: Optional[str] = None,
    runtime_profile: str = "deep",
    analysis_intent: Optional[str] = None,
) -> AgentState:
    from .collector import reset_debug_file, reset_registry
    reset_debug_file()
    reset_registry()
    tp, comp, focus, purpose, ui, intent = _resolve_run_args(
        target_product, competitors, analysis_focus, analysis_purpose, user_input, analysis_intent
    )

    app = build_app()
    initial = build_initial_state(
        user_input=ui,
        target_product=tp,
        competitors=comp,
        analysis_focus=focus,
        analysis_purpose=purpose,
        runtime_profile=runtime_profile,
        analysis_intent=intent,
    )
    # LangGraph 0.2 默认 recursion_limit=25,我们最多 collector1+analyzer2+writer1 = 4 轮重试,
    # 每轮 5 节点,理论上限 ~25。给个 50 留余量。
    final = app.invoke(initial, config={"recursion_limit": 50})
    return final


def discover_only(
    target_product: Optional[str] = None,
    competitors: Optional[list[str]] = None,
) -> int:
    """仅运行 URL Discovery，不跑完整 pipeline。用于测试。

    用法:
        python -m src.graph --discover-only                          # 交互式输入
        python -m src.graph --discover-only --target Notion --competitors Asana,Linear
    """
    from .collector import discover_all_urls

    # 解析 CLI 参数
    args = sys.argv[1:]
    if "--target" in args:
        idx = args.index("--target")
        target_product = args[idx + 1] if idx + 1 < len(args) else None
    if "--competitors" in args:
        idx = args.index("--competitors")
        raw = args[idx + 1] if idx + 1 < len(args) else None
        if raw:
            competitors = [c.strip() for c in raw.split(",")]

    # 没有通过参数指定，交互式输入
    if not target_product:
        target_product = input("输入目标产品名 (如 Notion, Figma, Slack): ").strip()
    if not target_product:
        print("错误: 必须指定目标产品")
        return 1

    if not competitors:
        raw = input("输入竞品名 (逗号分隔, 如 Asana,Linear): ").strip()
        competitors = [c.strip() for c in raw.split(",") if c.strip()]
    if not competitors:
        print("错误: 必须指定至少一个竞品")
        return 1

    products = [target_product] + competitors

    print("=" * 60)
    print(f"URL Discovery 测试 — 产品: {products}")
    print("=" * 60)

    results = discover_all_urls(products)

    for product, info in results.items():
        source = info.get("source", "?")
        official = info.get("official_pages", [])
        pricing = info.get("pricing_pages", [])
        reasoning = info.get("reasoning", "")
        error = info.get("error", "")

        print(f"\n{'─' * 40}")
        print(f"产品: {product}")
        print(f"来源: {source}")
        if reasoning:
            print(f"推理: {reasoning}")
        if error:
            print(f"错误: {error}")

        print(f"\n官方功能页 ({len(official)}):")
        for url in official:
            print(f"  → {url}")
        if not official:
            print("  (无)")

        print(f"\n官方定价页 ({len(pricing)}):")
        for url in pricing:
            print(f"  → {url}")
        if not pricing:
            print("  (无)")

    print(f"\n{'=' * 60}")
    return 0


def main() -> int:
    # 支持 --discover-only 参数
    if "--discover-only" in sys.argv:
        return discover_only()

    print("=" * 60)
    print("竞品分析 Agent 系统 — Demo (v2.2.1 骨架)")
    print("=" * 60)

    final = run_demo()

    # 写报告 — 按 DOMAIN 分子目录,避免覆盖
    domain_tag = os.environ.get("DOMAIN", "").strip() or "ai_coding"
    out_dir = _ROOT / "out" / domain_tag
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"
    qr_path = out_dir / "quality_report.json"
    schema_path = out_dir / "schema_draft.json"

    report_path.write_text(final.get("report_draft") or "", encoding="utf-8")
    qr_path.write_text(
        json.dumps(final.get("quality_report") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    schema_path.write_text(
        json.dumps(final.get("schema_draft") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    qr = final.get("quality_report") or {}
    print(f"\nStatus:          {final.get('status')}")
    print(f"Quality score:   {qr.get('quality_score', '?')}/100")
    print(f"Mode:            {qr.get('mode', '?')}")
    print(f"Passed rules:    {qr.get('passed_rules')}")
    print(f"Failed rules:    {qr.get('failed_rules')}")
    print(f"Warning rules:   {qr.get('warning_rules')}")
    print(f"Retry count:     {final.get('retry_count')}")
    print(f"\nReport written:  {report_path}")
    print(f"Schema written:  {schema_path}")
    print(f"Quality report:  {qr_path}")

    # 业务价值 headline:CLI 路径用 stage_timings_from_run 从本次 run 的 stage_quality.jsonl
    # 取逐节点实测耗时,与 API 路径(state["_stage_report"])同源,喂给 business_value_metrics。
    try:
        from .business_value import business_value_metrics
        from .stage_report import stage_timings_from_run

        rid = (final.get("analysis_meta") or {}).get("agent_trace_id")
        bv = business_value_metrics(
            final.get("schema_draft") or {},
            final.get("analysis_meta") or {},
            stage_timings_from_run(rid),
            final.get("raw_evidence") or [],
        )
        print(f"\nBusiness value:  {bv.get('headline')}")
    except Exception:  # noqa: BLE001
        pass

    if final.get("status") == "passed":
        return 0
    if final.get("status") == "degraded":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
