"""LangGraph 编排 + main 入口 — 见 docs/design-v2.2.md §九

用法:
    # 无 API key,跑 Mock 闭环(推荐先跑这个)
    set ANALYZER_MOCK=1 & python -m src.graph

    # 真实 LLM(需 ARK_API_KEY)
    set ARK_API_KEY=... & python -m src.graph
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env(若存在),允许通过 .env 注入 ARK_API_KEY / ARK_EP / ANALYZER_MOCK
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from .analyzer import analyzer_node  # noqa: E402
from .collector import collector_node  # noqa: E402
from .reviewer import degraded_writer_node, make_reviewer_node  # noqa: E402
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

    reviewer_node = make_reviewer_node(llm=llm, mode=reviewer_mode)

    graph = StateGraph(AgentState)
    graph.add_node("collector", collector_node)
    graph.add_node("analyzer", analyzer_node)
    graph.add_node("writer", writer_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("degraded_writer", degraded_writer_node)

    graph.set_entry_point("collector")
    graph.add_edge("collector", "analyzer")
    graph.add_edge("analyzer", "writer")
    graph.add_edge("writer", "reviewer")

    def route_after_review(state: AgentState) -> str:
        if state.get("status") == "passed":
            return "end"
        if state.get("status") == "degraded":
            return "degraded_writer"
        return state.get("reject_target") or "analyzer"

    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "collector": "collector",
            "analyzer": "analyzer",
            "writer": "writer",
            "degraded_writer": "degraded_writer",
            "end": END,
        },
    )
    graph.add_edge("degraded_writer", END)

    return graph.compile()


# ────────────────────────────────────────────────────────────────────────────
# Main runner
# ────────────────────────────────────────────────────────────────────────────

def run_demo(
    target_product: str = "Cursor",
    competitors: Optional[list[str]] = None,
    analysis_focus: Optional[list[str]] = None,
    user_input: Optional[str] = None,
) -> AgentState:
    competitors = competitors or ["Windsurf", "GitHubCopilot"]
    analysis_focus = analysis_focus or ["代码补全体验"]
    user_input = user_input or f"分析 {target_product} 与 {', '.join(competitors)} 在 {analysis_focus[0]} 上的差距"

    app = build_app()
    initial = build_initial_state(
        user_input=user_input,
        target_product=target_product,
        competitors=competitors,
        analysis_focus=analysis_focus,
    )
    # LangGraph 0.2 默认 recursion_limit=25,我们最多 collector1+analyzer2+writer1 = 4 轮重试,
    # 每轮 5 节点,理论上限 ~25。给个 50 留余量。
    final = app.invoke(initial, config={"recursion_limit": 50})
    return final


def main() -> int:
    print("=" * 60)
    print("竞品分析 Agent 系统 — Demo (v2.2.1 骨架)")
    print("=" * 60)

    final = run_demo()

    # 写报告
    out_dir = _ROOT / "out"
    out_dir.mkdir(exist_ok=True)
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

    if final.get("status") == "passed":
        return 0
    if final.get("status") == "degraded":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
