"""AgentState 定义 — 见 docs/design-v2.2.md §三"""
from typing import TypedDict, Literal, Optional


class AgentState(TypedDict):
    # 输入
    user_input: str
    analysis_meta: dict

    # 中间产物
    raw_evidence: Optional[list[dict]]
    schema_draft: Optional[dict]
    report_draft: Optional[str]

    # 质检
    quality_report: Optional[dict]
    collection_meta: Optional[dict]

    # 打回信息
    reject_target: Optional[Literal["collector", "analyzer", "writer"]]
    reject_requirements: Optional[list[dict]]

    # 流控(按 target 分桶)
    retry_count: dict[str, int]
    max_retries_per_target: dict[str, int]
    status: Literal["running", "passed", "degraded", "failed"]


def build_initial_state(
    user_input: str,
    target_product: str,
    competitors: list[str],
    analysis_focus: list[str],
    analysis_purpose: str = "学习竞品优点,优化自身产品",
    runtime_profile: str = "deep",
    analysis_intent: str = "feature_compare",
) -> AgentState:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    report_id = f"CR-{now.strftime('%Y%m%d')}-001"

    return {
        "user_input": user_input,
        "analysis_meta": {
            "report_id": report_id,
            "schema_version": "2.1",
            "target_product": target_product,
            "competitors": competitors,
            "analysis_focus": analysis_focus,
            "analysis_purpose": analysis_purpose,
            "analysis_intent": analysis_intent,
            "runtime_profile": runtime_profile,
            "generated_at": now.isoformat(),
            "data_cutoff": now.strftime("%Y-%m-%d"),
            "agent_trace_id": f"trace_{now.strftime('%Y%m%d%H%M%S')}",
        },
        "raw_evidence": None,
        "schema_draft": None,
        "report_draft": None,
        "quality_report": None,
        "collection_meta": None,
        "reject_target": None,
        "reject_requirements": None,
        "retry_count": {"collector": 0, "analyzer": 0, "writer": 0},
        "max_retries_per_target": {"collector": 1, "analyzer": 2, "writer": 1},
        "status": "running",
    }
