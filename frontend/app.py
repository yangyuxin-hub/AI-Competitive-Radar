"""Streamlit 前端 — 竞品分析 Agent 协作系统

启动:
    streamlit run frontend/app.py

环境变量(可选,也可在侧栏填):
    ARK_API_KEY  豆包 API key
    ARK_EP       endpoint id
    DOMAIN       ai_coding | pm
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import streamlit as st
import yaml

# 让 frontend/ 能直接 import src/
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


# ────────────────────────────────────────────────────────────────────────────
# 工具
# ────────────────────────────────────────────────────────────────────────────

_CHIP_RE = re.compile(r"\[(S[0-9A-F]{7})\]")
_NODE_ICONS = {
    "collector": "📥",
    "analyzer": "🧠",
    "writer": "✍️",
    "reviewer": "🧪",
    "degraded_writer": "⚠️",
}


@st.cache_data
def load_domains() -> dict:
    with (_ROOT / "config" / "domains.yaml").open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("domains", {})


def extract_evidence_ids(text: str) -> list[str]:
    """从 Markdown 报告里按出现顺序抽取所有 chip 中的 evidence_id(去重保序)"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _CHIP_RE.finditer(text or ""):
        eid = m.group(1)
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def render_evidence_panel(evidence_ids: list[str], evidence_pool: list[dict]) -> None:
    by_id = {e["evidence_id"]: e for e in evidence_pool}
    st.markdown(f"#### 证据库({len(evidence_ids)} 条被引用)")
    st.caption("按报告中首次出现顺序排列,点击展开看原文 snippet")
    for eid in evidence_ids:
        ev = by_id.get(eid)
        if not ev:
            st.warning(f"`{eid}` 未在 raw_evidence 中找到")
            continue
        label = f"`{eid}` · **{ev['product']}** · {ev['claim_type']} · {ev['source_bias']}"
        with st.expander(label):
            st.markdown(f"**Claim**:{ev.get('claim', '')}")
            st.markdown(f"**Snippet**:")
            st.code(ev.get("extracted_snippet", ""), language=None)
            cols = st.columns(4)
            cols[0].metric("可信度", f"{ev.get('source_reliability', 0):.2f}")
            cols[1].metric("相关性", f"{ev.get('claim_relevance', 0):.2f}")
            cols[2].metric("综合", f"{ev.get('evidence_confidence', 0):.2f}")
            cols[3].metric("时效", ev.get("source_freshness", "?"))
            st.caption(f"📅 {ev.get('observed_at', '?')} · 🔗 [{ev.get('source_url', '')}]({ev.get('source_url', '')})")


def render_quality_report(qr: dict) -> None:
    if not qr:
        st.info("尚无质检报告")
        return
    cols = st.columns(4)
    cols[0].metric("总分", f"{qr.get('quality_score', '?')}/100")
    cols[1].metric("模式", qr.get("mode", "?"))
    cols[2].metric("错误数", len(qr.get("errors", [])))
    cols[3].metric("警告数", len(qr.get("warnings", [])))

    st.markdown("#### 规则状态")
    pass_set = set(qr.get("passed_rules", []))
    warn_set = set(qr.get("warning_rules", []))
    fail_set = set(qr.get("failed_rules", []))
    all_rules = {
        "R1": "evidence_reference_integrity",
        "R2": "claim_type_compatibility",
        "R3": "aggregation_integrity",
        "R4": "reasoning_chain_integrity",
        "R5": "structured_contradiction",
        "R6": "semantic_grounding",
        "R7": "freshness_and_confidence",
    }
    rcols = st.columns(7)
    for col, (rid, name) in zip(rcols, all_rules.items()):
        if rid in fail_set:
            col.markdown(f"❌ **{rid}**\n\n{name}")
        elif rid in warn_set:
            col.markdown(f"⚠️ **{rid}**\n\n{name}")
        elif rid in pass_set:
            col.markdown(f"✅ **{rid}**\n\n{name}")
        else:
            col.markdown(f"⬜ {rid}\n\n{name}")

    st.markdown("#### 模块状态")
    ms = qr.get("module_status", {})
    mcols = st.columns(len(ms) or 1)
    for col, (mod, status) in zip(mcols, ms.items()):
        icon = {"passed": "✅", "warning": "⚠️", "failed": "❌"}.get(status, "❓")
        col.markdown(f"{icon} **{mod}**\n\n{status}")

    if qr.get("warnings"):
        with st.expander(f"⚠️ {len(qr['warnings'])} 条 warnings"):
            for w in qr["warnings"]:
                st.markdown(f"- **[{w['rule']}]** `{w['location']}` · {w.get('detail', '')}")
    if qr.get("errors"):
        with st.expander(f"❌ {len(qr['errors'])} 条 errors"):
            for e in qr["errors"]:
                st.markdown(f"- **[{e['rule']}]** `{e['location']}` · {e.get('detail', '')}")


# ────────────────────────────────────────────────────────────────────────────
# 页面布局
# ────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="竞品分析 Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🔍 竞品分析 Agent 协作系统")
st.caption(
    "多 Agent · LangGraph · 豆包 Lite · 4 个专职 Agent (Collector / Analyzer / Writer / Reviewer) "
    "+ 自动打回闭环 · 全链路证据可溯源 — 设计 v2.2.1"
)

domains = load_domains()

with st.sidebar:
    st.markdown("### 🌐 行业域")
    domain_key = st.selectbox(
        "选择行业",
        list(domains.keys()),
        format_func=lambda k: f"{k} — {domains[k].get('name', '')}",
        help="domains.yaml 配置;切换 = 改一行 env DOMAIN=xxx,不改代码",
    )
    dom_cfg = domains.get(domain_key, {})

    st.markdown("### 🎯 分析对象")
    target = st.text_input("目标产品", value=dom_cfg.get("target_product", ""))
    competitors_text = st.text_area(
        "竞品(每行一个)",
        value="\n".join(dom_cfg.get("competitors", [])),
        height=80,
    )
    focus = st.text_input(
        "分析焦点",
        value=(dom_cfg.get("analysis_focus") or [""])[0],
    )

    st.markdown("### ⚙️ Agent 配置")
    reviewer_mode = st.radio(
        "Reviewer 模式",
        ["minimal", "full"],
        index=0,
        horizontal=True,
        help="minimal: R1/R4/R5 当 error,其余 warning;full: R1-R5 都是 error,R6 终轮启用",
    )

    use_mock = st.toggle(
        "🧪 Mock 模式(跳过真实 LLM)",
        value=False,
        help="开启后 Analyzer 直接返回 sample_report.json,用于评委演示时省 API 调用",
    )

    if not use_mock:
        st.markdown("### 🔑 ARK API")
        api_key = st.text_input(
            "ARK_API_KEY",
            value=os.environ.get("ARK_API_KEY", ""),
            type="password",
        )
        ep = st.text_input(
            "ARK_EP",
            value=os.environ.get("ARK_EP", "ep-20260514111325-xjmj7"),
        )
    else:
        api_key, ep = "", ""

    st.divider()
    run_btn = st.button(
        "🚀 开始分析",
        type="primary",
        use_container_width=True,
        disabled=not target or not focus,
    )


# ────────────────────────────────────────────────────────────────────────────
# 运行逻辑
# ────────────────────────────────────────────────────────────────────────────

# session_state 初始化
if "completed" not in st.session_state:
    st.session_state.completed = False
if "final_state" not in st.session_state:
    st.session_state.final_state = None
if "node_log" not in st.session_state:
    st.session_state.node_log = []

if run_btn:
    # 注入环境变量
    os.environ["DOMAIN"] = domain_key
    os.environ["REVIEWER_MODE"] = reviewer_mode
    if use_mock:
        os.environ["ANALYZER_MOCK"] = "1"
    else:
        os.environ.pop("ANALYZER_MOCK", None)
        if api_key:
            os.environ["ARK_API_KEY"] = api_key
        if ep:
            os.environ["ARK_EP"] = ep

    st.session_state.completed = False
    st.session_state.final_state = None
    st.session_state.node_log = []

    competitors_list = [c.strip() for c in competitors_text.splitlines() if c.strip()]

    st.markdown(f"#### ⏱️ 正在运行:**{target}** vs {', '.join(competitors_list)} — {focus}")
    progress_box = st.container()
    log_box = st.empty()

    try:
        from src.graph import run_demo_streaming

        # 4 个节点的固定状态占位
        with progress_box:
            placeholders = {}
            for node in ("collector", "analyzer", "writer", "reviewer"):
                icon = _NODE_ICONS[node]
                placeholders[node] = st.empty()
                placeholders[node].markdown(f"⬜ {icon} **{node}** — 等待中")

        t0 = time.time()
        final_state = None
        seen_nodes = set()
        events_text: list[str] = []

        for node_name, state_after in run_demo_streaming(
            target_product=target,
            competitors=competitors_list,
            analysis_focus=[focus],
        ):
            elapsed = time.time() - t0
            icon = _NODE_ICONS.get(node_name, "▶️")
            # 主节点状态行
            ph = placeholders.get(node_name)
            if ph is not None:
                ph.markdown(f"✅ {icon} **{node_name}** — 完成 ({elapsed:.1f}s)")
            else:
                # 例如 degraded_writer
                with progress_box:
                    st.markdown(f"⚠️ {icon} **{node_name}** — 完成 ({elapsed:.1f}s)")

            seen_nodes.add(node_name)
            final_state = state_after
            retry_info = state_after.get("retry_count") or {}
            retry_text = " · ".join(f"{k}:{v}" for k, v in retry_info.items() if v)
            line = f"[{elapsed:6.1f}s] {icon} {node_name} → status={state_after.get('status', '?')}"
            if retry_text:
                line += f"  retry={{{retry_text}}}"
            events_text.append(line)
            log_box.code("\n".join(events_text[-12:]), language=None)

        st.session_state.final_state = final_state
        st.session_state.completed = True
        st.session_state.node_log = events_text

        status = final_state.get("status") if final_state else "?"
        if status == "passed":
            st.success(f"🎉 完成 · status={status} · 用时 {time.time()-t0:.1f}s")
        elif status == "degraded":
            st.warning(f"⚠️ 降级输出 · status={status} · 用时 {time.time()-t0:.1f}s")
        else:
            st.info(f"status={status}")

    except Exception as e:
        st.error(f"运行失败: {type(e).__name__}: {e}")
        st.exception(e)


# ────────────────────────────────────────────────────────────────────────────
# 结果展示(完成后)
# ────────────────────────────────────────────────────────────────────────────

if st.session_state.completed and st.session_state.final_state:
    fs = st.session_state.final_state
    report_md = fs.get("report_draft") or ""
    raw_ev = fs.get("raw_evidence") or []
    qr = fs.get("quality_report") or {}

    tab_report, tab_quality, tab_schema, tab_raw = st.tabs([
        "📄 报告与证据",
        "🧪 质检详情",
        "🗂️ schema_draft",
        "📚 原始 evidence",
    ])

    with tab_report:
        left, right = st.columns([3, 2], gap="medium")
        with left:
            st.markdown(report_md, unsafe_allow_html=False)
        with right:
            eids = extract_evidence_ids(report_md)
            render_evidence_panel(eids, raw_ev)

    with tab_quality:
        render_quality_report(qr)
        st.markdown("#### 节点执行日志")
        st.code("\n".join(st.session_state.node_log), language=None)
        st.markdown("#### 重试明细")
        st.json(fs.get("retry_count", {}))

    with tab_schema:
        st.caption("Analyzer 两步式输出的完整 schema_draft(feature_tree + pricing_model + user_persona + swot + recommendations)")
        st.json(fs.get("schema_draft") or {}, expanded=False)

    with tab_raw:
        st.caption(f"Collector 抓取并去重后的 raw_evidence — {len(raw_ev)} 条")
        for ev in raw_ev:
            label = f"`{ev['evidence_id']}` · **{ev['product']}** · {ev['claim_type']} · {ev['source_bias']}"
            with st.expander(label):
                st.json(ev, expanded=False)

elif not run_btn:
    # 首屏:简介
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
### 🔧 这套系统能做什么

- **多 Agent 协作**:Collector 抓证据 → Analyzer 两步出结构化 schema → Writer 渲染报告 → Reviewer 7 条规则质检
- **失败可降级**:Reviewer 打回 Collector/Analyzer/Writer,按 target 分桶配额(`{collector:1, analyzer:2, writer:1}`),配额耗尽走降级报告
- **证据全溯源**:每条结论后缀 `[SXXXXXXX]` chip,可在右侧证据库展开看原文 snippet
- **零代码切行业**:`config/domains.yaml` 加一个 entry 即可切换(本 demo 已含 AI 编程 + 项目协作工具两个域)
        """)
    with col2:
        st.markdown("""
### 📋 怎么用

1. **左侧侧栏** 选行业域,默认参数会自动填好
2. 可改 target / competitors / focus 自定义场景
3. **Reviewer 模式**:`minimal` 适合 demo(R1/R4/R5 当 error),`full` 答辩展示(R1-R5 全 error)
4. **Mock 模式**:无 API key 时跳过真实 LLM,直接拿 `sample_report.json` 走完 graph
5. 配好后点 **🚀 开始分析**,实时看 Agent 进度
        """)
        st.info("提示:豆包真实调用约 2-5 分钟(Analyzer 两步各 60-90s);demo 时建议先开 Mock 跑一次熟悉界面")
