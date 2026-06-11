"""AgentState 定义 — 见 docs/design-v2.2.md §三"""
from typing import TypedDict, Literal, Optional


class AgentState(TypedDict):
    # 输入
    user_input: str
    analysis_meta: dict

    # 中间产物
    evidence_plan: Optional[dict]
    raw_evidence: Optional[list[dict]]
    schema_draft: Optional[dict]
    report_draft: Optional[str]

    # 质检
    quality_report: Optional[dict]
    collection_meta: Optional[dict]

    # 可观测:graph._instrument 每个节点末尾挂的本节点 StageReport(计时/token/checks)。
    # 必须声明为 channel,否则 LangGraph 会丢弃节点返回的未声明 key,
    # 导致 api 读不到 → 无 stage_report SSE 事件(前端交付清单不推进、token 计数为 0)。
    _stage_report: Optional[dict]

    # v3 M4:Guard 一次修订的产出(修订计数/消费的 Auditor 发现数),guard_revise 节点写入
    guard_revision: Optional[dict]

    # 打回信息(M4 后图内无消费者——打回循环已删;reviewer 仍写入,
    # 供 checklist/stage_report 的缺口归因消费。字段删除留 M4b 收尾)
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
    import secrets
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
            # 加 4 位随机后缀:同秒内并发起的多个 run(API 高并发)不会撞同一 trace_id,
            # 否则 stage_quality.jsonl / llm_calls.jsonl 按 run_id 聚合会串运行。
            "agent_trace_id": f"trace_{now.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}",
        },
        "evidence_plan": None,
        "raw_evidence": None,
        "schema_draft": None,
        "report_draft": None,
        "quality_report": None,
        "collection_meta": None,
        "_stage_report": None,
        "guard_revision": None,
        "reject_target": None,
        "reject_requirements": None,
        "retry_count": {"collector": 0, "analyzer": 0, "writer": 0},
        "max_retries_per_target": {"collector": 1, "analyzer": 2, "writer": 1},
        "status": "running",
    }
