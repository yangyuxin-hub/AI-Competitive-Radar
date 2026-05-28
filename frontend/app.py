"""Streamlit 前端 — 竞品分析 Agent 协作系统

启动:
    streamlit run frontend/app.py

环境变量(可选,也可在侧栏填):
    ARK_API_KEY  豆包 API key
    ARK_EP       endpoint id
    DOMAIN       ai_coding | pm
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

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

from src import intake  # noqa: E402  意图问询复用层


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

_NODE_LABELS = {
    "collector": "收集证据",
    "analyzer": "分析结论",
    "writer": "生成报告",
    "reviewer": "规则质检",
    "degraded_writer": "降级输出",
}

_RULE_NAMES = {
    "R1": "引用完整",
    "R2": "证据类型",
    "R3": "聚合一致",
    "R4": "推理链",
    "R5": "结构冲突",
    "R6": "语义落地",
    "R7": "时效置信",
}

_STATUS_STYLE = {
    "passed": ("通过", "ok"),
    "warning": ("预警", "warn"),
    "failed": ("失败", "bad"),
    "running": ("运行中", "run"),
    "degraded": ("降级", "warn"),
}

_SOURCE_STYLE = {
    "live": ("实时抓取", "ok", "🌐"),
    "cache": ("本地缓存", "warn", "💾"),
    "mock": ("Mock 数据", "bad", "🧪"),
    "unknown": ("未知", "", "❓"),
}


@st.cache_data
def load_domains() -> dict:
    with (_ROOT / "config" / "domains.yaml").open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("domains", {})


def inject_design_system() -> None:
    st.markdown(
        """
<style>
:root {
  --ca-bg: #f6f7f4;
  --ca-panel: #ffffff;
  --ca-panel-soft: #f0f4ef;
  --ca-ink: #151914;
  --ca-muted: #667064;
  --ca-line: #d9dfd6;
  --ca-green: #1f7a4d;
  --ca-blue: #245d8f;
  --ca-amber: #a76112;
  --ca-red: #b33c35;
  --ca-shadow: 0 12px 34px rgba(24, 31, 23, .08);
}

.stApp {
  background:
    linear-gradient(180deg, rgba(246, 247, 244, .95), rgba(246, 247, 244, .98)),
    repeating-linear-gradient(90deg, rgba(21, 25, 20, .025) 0 1px, transparent 1px 36px);
  color: var(--ca-ink);
}

.block-container {
  max-width: 1480px;
  padding-top: 1.4rem;
  padding-bottom: 3rem;
}

[data-testid="stSidebar"] {
  background: #eef2ec;
  border-right: 1px solid var(--ca-line);
}

[data-testid="stSidebar"] * {
  letter-spacing: 0;
}

h1, h2, h3, h4 {
  color: var(--ca-ink);
  letter-spacing: 0;
}

div[data-testid="stMarkdownContainer"] h1 {
  font-size: 34px;
  line-height: 1.18;
  letter-spacing: 0;
}

div[data-testid="stMarkdownContainer"] h2 {
  font-size: 24px;
  line-height: 1.25;
  margin-top: 1.8rem;
  padding-top: .7rem;
  border-top: 2px solid #273128;
}

div[data-testid="stMarkdownContainer"] h3 {
  font-size: 18px;
}

div[data-testid="stMarkdownContainer"] blockquote {
  border-left: 4px solid var(--ca-green);
  background: #f3f7f2;
  padding: 12px 14px;
  color: #263027;
}

div[data-testid="stMarkdownContainer"] table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

div[data-testid="stMarkdownContainer"] th {
  background: #eef2ec;
  color: #263027;
  font-weight: 800;
}

div[data-testid="stMarkdownContainer"] th,
div[data-testid="stMarkdownContainer"] td {
  border: 1px solid var(--ca-line);
  padding: 8px 10px;
}

.ca-topbar {
  border: 1px solid var(--ca-line);
  background: rgba(255, 255, 255, .82);
  box-shadow: var(--ca-shadow);
  padding: 22px 24px;
  margin-bottom: 18px;
}

.ca-kicker {
  color: var(--ca-green);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .08em;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.ca-title {
  font-size: clamp(30px, 4vw, 52px);
  line-height: 1;
  font-weight: 850;
  letter-spacing: 0;
  margin: 0 0 10px 0;
}

.ca-subtitle {
  max-width: 920px;
  color: var(--ca-muted);
  font-size: 15px;
  line-height: 1.65;
}

.ca-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

.ca-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 4px 10px;
  border: 1px solid var(--ca-line);
  background: #fff;
  color: var(--ca-ink);
  font-size: 12px;
  font-weight: 720;
}

.ca-pill.ok { border-color: rgba(31, 122, 77, .26); background: #e8f4ee; color: var(--ca-green); }
.ca-pill.warn { border-color: rgba(167, 97, 18, .28); background: #fff4df; color: var(--ca-amber); }
.ca-pill.bad { border-color: rgba(179, 60, 53, .25); background: #fff0ee; color: var(--ca-red); }
.ca-pill.run { border-color: rgba(36, 93, 143, .26); background: #e9f2fb; color: var(--ca-blue); }

.ca-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
  margin: 14px 0 18px;
}

.ca-card {
  border: 1px solid var(--ca-line);
  background: rgba(255, 255, 255, .88);
  box-shadow: 0 8px 24px rgba(24, 31, 23, .055);
  padding: 16px;
}

.ca-card-title {
  color: var(--ca-muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.ca-card-value {
  color: var(--ca-ink);
  font-size: 28px;
  font-weight: 850;
  line-height: 1.05;
}

.ca-card-note {
  color: var(--ca-muted);
  font-size: 12px;
  line-height: 1.45;
  margin-top: 8px;
}

.ca-section {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--ca-line);
  padding-bottom: 8px;
  margin: 18px 0 12px;
}

.ca-section h3 {
  font-size: 18px;
  margin: 0;
}

.ca-section span {
  color: var(--ca-muted);
  font-size: 12px;
}

.ca-timeline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin: 14px 0;
}

.ca-step {
  border: 1px solid var(--ca-line);
  background: #fff;
  padding: 12px;
  min-height: 84px;
}

.ca-step.ok { border-left: 4px solid var(--ca-green); }
.ca-step.warn { border-left: 4px solid var(--ca-amber); }
.ca-step.run { border-left: 4px solid var(--ca-blue); }
.ca-step.wait { border-left: 4px solid #a9b2a5; }

.ca-step-name {
  font-weight: 800;
  font-size: 14px;
}

.ca-step-meta {
  color: var(--ca-muted);
  font-size: 12px;
  line-height: 1.45;
  margin-top: 8px;
}

.ca-report-shell {
  border: 1px solid var(--ca-line);
  background: #fff;
  padding: 24px 28px;
  box-shadow: var(--ca-shadow);
}

.ca-report-shell h1 {
  font-size: 30px;
  line-height: 1.14;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ca-line);
}

.ca-report-shell h2 {
  margin-top: 34px;
  padding-top: 10px;
  border-top: 2px solid #273128;
  font-size: 22px;
}

.ca-report-shell h3 {
  margin-top: 22px;
  font-size: 17px;
}

.ca-report-shell blockquote {
  border-left: 4px solid var(--ca-green);
  background: #f3f7f2;
  padding: 12px 14px;
  margin: 14px 0;
  color: #263027;
}

.ca-report-shell table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.ca-report-shell th {
  background: #eef2ec;
  color: #263027;
  font-weight: 800;
}

.ca-report-shell th, .ca-report-shell td {
  border: 1px solid var(--ca-line);
  padding: 8px 10px;
}

.ca-evidence-head {
  border: 1px solid var(--ca-line);
  background: #ffffff;
  padding: 14px;
  margin-bottom: 10px;
}

.ca-muted {
  color: var(--ca-muted);
}

.ca-empty {
  border: 1px dashed #b7c0b2;
  background: rgba(255, 255, 255, .65);
  padding: 28px;
  color: var(--ca-muted);
}

div[data-testid="stMetric"] {
  background: #fff;
  border: 1px solid var(--ca-line);
  padding: 12px 14px;
  box-shadow: 0 8px 18px rgba(24, 31, 23, .045);
}

div[data-testid="stTabs"] button {
  font-weight: 750;
}

.stButton > button {
  border-radius: 4px;
  min-height: 44px;
  font-weight: 800;
  background: var(--ca-green);
  color: #fff;
  border: 1px solid var(--ca-green);
}

.stButton > button:hover {
  background: #185f3c;
  border-color: #185f3c;
  color: #fff;
}

/* ─── 证据 chip 与跳转目标 ─────────────────────────── */
html { scroll-behavior: smooth; }

.ca-chip {
  display: inline-block;
  font-family: ui-monospace, "JetBrains Mono", Menlo, monospace;
  font-size: 11px;
  padding: 0 6px;
  margin: 0 2px;
  border: 1px solid rgba(36, 93, 143, .28);
  background: #e9f2fb;
  color: var(--ca-blue);
  text-decoration: none;
  border-radius: 3px;
  vertical-align: baseline;
  transition: background .15s, color .15s;
}
.ca-chip:hover {
  background: var(--ca-blue);
  color: #fff;
  border-color: var(--ca-blue);
}
.ca-chip.miss {
  background: #fff0ee;
  border-color: rgba(179, 60, 53, .35);
  color: var(--ca-red);
}

.ca-evidence-card {
  border: 1px solid var(--ca-line);
  background: #fff;
  padding: 0;
  margin-bottom: 10px;
  scroll-margin-top: 90px;
  transition: box-shadow .2s, border-color .2s;
}
.ca-evidence-card[open] {
  border-color: rgba(36, 93, 143, .35);
  box-shadow: 0 4px 14px rgba(24, 31, 23, .07);
}
.ca-evidence-card:target {
  border-color: var(--ca-green);
  animation: ca-flash 1.4s ease;
}
@keyframes ca-flash {
  0%   { background: #fff4df; }
  60%  { background: #fffaef; }
  100% { background: #fff; }
}
.ca-evidence-card summary {
  cursor: pointer;
  list-style: none;
  padding: 12px 14px;
  font-weight: 720;
  font-size: 13px;
  color: var(--ca-ink);
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.ca-evidence-card summary::-webkit-details-marker { display: none; }
.ca-evidence-card summary::after {
  content: "▾";
  margin-left: auto;
  color: var(--ca-muted);
  font-size: 11px;
}
.ca-evidence-card[open] summary::after { content: "▴"; }
.ca-evidence-card .ca-ev-body {
  padding: 0 14px 14px;
  font-size: 13px;
  color: var(--ca-ink);
  line-height: 1.55;
}
.ca-evidence-card .ca-ev-snippet {
  background: #f8faf7;
  border: 1px solid var(--ca-line);
  padding: 10px 12px;
  margin: 8px 0;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.ca-evidence-card .ca-ev-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--ca-muted);
}
.ca-evidence-card .ca-ev-meta b {
  color: var(--ca-ink);
  font-weight: 720;
  margin-right: 3px;
}

/* ─── 一键演示预设按钮 ─────────────────────────── */
.ca-preset-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin: 14px 0 24px;
}
.ca-preset {
  border: 1px solid var(--ca-line);
  background: #fff;
  padding: 16px 18px;
  box-shadow: 0 6px 16px rgba(24, 31, 23, .04);
  transition: border-color .15s, box-shadow .2s;
}
.ca-preset:hover { border-color: var(--ca-green); box-shadow: 0 10px 28px rgba(24, 31, 23, .08); }
.ca-preset-name {
  font-size: 16px;
  font-weight: 800;
  color: var(--ca-ink);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.ca-preset-desc {
  font-size: 12px;
  color: var(--ca-muted);
  line-height: 1.5;
  min-height: 36px;
}

/* ─── 子步骤进度 ─────────────────────────── */
.ca-substep {
  border: 1px solid var(--ca-line);
  border-left: 4px solid var(--ca-blue);
  background: rgba(36, 93, 143, .04);
  padding: 10px 14px;
  font-size: 13px;
  color: var(--ca-ink);
  margin-top: 6px;
  font-family: ui-monospace, monospace;
}
.ca-substep.done {
  border-left-color: var(--ca-green);
  background: rgba(31, 122, 77, .05);
}
.ca-substep.repair {
  border-left-color: var(--ca-amber);
  background: rgba(167, 97, 18, .05);
}
</style>
        """,
        unsafe_allow_html=True,
    )


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def section(title: str, note: str = "") -> None:
    st.markdown(
        f"""
<div class="ca-section">
  <h3>{esc(title)}</h3>
  <span>{esc(note)}</span>
</div>
        """,
        unsafe_allow_html=True,
    )


def metric_grid(items: list[tuple[str, object, str, str]]) -> None:
    cards = []
    for label, value, note, style in items:
        cards.append(
            f"""
<div class="ca-card">
  <div class="ca-card-title">{esc(label)}</div>
  <div class="ca-card-value">{esc(value)}</div>
  <div class="ca-card-note">{esc(note)}</div>
</div>
            """
        )
    st.markdown(f"<div class='ca-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)


def topbar() -> None:
    st.markdown(
        """
<div class="ca-topbar">
  <div class="ca-kicker">Competitive Intelligence Agent</div>
  <div class="ca-title">竞品分析工作台</div>
  <div class="ca-subtitle">
    面向 PM 与分析师的多 Agent 协作系统。自动收集证据、生成结构化竞品报告，并用 Reviewer 检查引用完整性、推理链和结论可信度。
  </div>
  <div class="ca-strip">
    <span class="ca-pill">Collector</span>
    <span class="ca-pill">Analyzer</span>
    <span class="ca-pill">Writer</span>
    <span class="ca-pill">Reviewer</span>
    <span class="ca-pill ok">Evidence Traceable</span>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def intake_panel(domains: dict) -> None:
    """「一句话智能填写」:agent 主动把决策点做成选择题,用户点选后回填侧栏配置。

    交互分两步(都靠 session_state + rerun,保证回填发生在侧栏 widget 渲染前):
      1. 输入一句话意图 → 生成选择题(LLM 优先,启发式兜底)
      2. 点选答案 → 「应用」→ 写 _intake_pending,下一轮注入 sb_*
    """
    with st.expander("🧭 一句话智能填写(让 Agent 帮你对齐分析背景)", expanded=False):
        st.caption(
            "不确定 target / 竞品 / 焦点怎么填?用一句话描述需求,Agent 会把要决策的点"
            "做成选择题(含推荐),你点选即可,无需手填字段。"
        )
        seed = st.text_input(
            "用一句话描述你想分析什么",
            placeholder="例:想看看 Notion 和同类项目协作工具在任务管理上的差距",
            key="intake_seed",
        )
        col_gen, col_clear = st.columns([1, 1])
        with col_gen:
            gen = st.button("🤖 生成问题", use_container_width=True)
        with col_clear:
            if st.button("清空", use_container_width=True):
                st.session_state.pop("intake_qs", None)
                st.rerun()

        if gen:
            # 用侧栏当前选中的行业作为推荐提示,让候选更贴合
            domain_hint = st.session_state.get("sb_domain")
            with st.spinner("Agent 正在拆解意图、推荐候选…"):
                qs = [c.to_dict() for c in intake.intake_questions(seed or "", domain_hint)]
            st.session_state["intake_qs"] = qs
            st.rerun()

        qs = st.session_state.get("intake_qs")
        if not qs:
            return

        st.divider()
        answers: dict = {}
        for q in qs:
            opts = list(q["options"])
            sug = q.get("suggested") or []
            label = q["question"]
            if q["multi"]:
                answers[q["key"]] = st.multiselect(
                    label, opts, default=[s for s in sug if s in opts],
                    key=f"intake_{q['key']}",
                )
            else:
                idx = opts.index(sug[0]) if (sug and sug[0] in opts) else 0
                answers[q["key"]] = st.selectbox(
                    label, opts, index=idx if opts else 0,
                    key=f"intake_{q['key']}",
                ) if opts else ""
            if q.get("allow_custom"):
                custom = st.text_input(
                    f"↳ 或自定义「{label}」(留空则用上面所选)",
                    key=f"intake_custom_{q['key']}",
                )
                if custom.strip():
                    answers[q["key"]] = (
                        [c.strip() for c in custom.split(",") if c.strip()]
                        if q["multi"] else custom.strip()
                    )

        if st.button("✅ 应用到分析配置", type="primary", use_container_width=True):
            meta = intake.assemble_meta(answers, user_input=seed or None)
            pending = {
                "target_product": meta["target_product"],
                "competitors": meta["competitors"],
                "analysis_focus": meta["analysis_focus"],
            }
            if intake.wants_persist(answers):
                key = intake.persist_domain(meta)
                pending["domain_key"] = key
                st.success(
                    f"已保存为新行业 `{key}`。记得补 data/sample_sources_{key}.json,"
                    "或开启实时抓取(ENABLE_LIVE_FETCH=1)。"
                )
            st.session_state["_intake_pending"] = pending
            st.session_state.pop("intake_qs", None)
            st.rerun()


def step_html(node: str, status_class: str, meta: str) -> str:
    return f"""
<div class="ca-step {status_class}">
  <div class="ca-step-name">{_NODE_ICONS.get(node, "▶️")} {esc(_NODE_LABELS.get(node, node))}</div>
  <div class="ca-step-meta">{esc(meta)}</div>
</div>
    """


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


def rewrite_chips(text: str, valid_ids: set[str], evidence_pool: Optional[list[dict]] = None) -> str:
    """把 [SXXXXXXX] 替换为可点击 anchor 链接;未命中 raw_evidence 的标 miss 样式。
    如果提供 evidence_pool，chip 后会加来源图标(🌐/💾/🧪)。"""
    by_id = {}
    if evidence_pool:
        by_id = {e["evidence_id"]: e for e in evidence_pool}

    def repl(m: re.Match) -> str:
        eid = m.group(1)
        cls = "ca-chip" if eid in valid_ids else "ca-chip miss"
        icon = ""
        if eid in by_id:
            src = by_id[eid].get("collection_source", "")
            icon = {"live": "🌐", "cache": "💾", "mock": "🧪"}.get(src, "")
        return f'<a href="#ev-{eid}" class="{cls}">[{eid}]</a>{icon}'
    return _CHIP_RE.sub(repl, text or "")


def render_evidence_panel(evidence_ids: list[str], evidence_pool: list[dict]) -> None:
    """证据列表用原生 HTML <details>,带 id=ev-XXX 锚点供 chip 跳转;
    第一项默认展开,其余折叠 — 但 chip 点击会触发 :target 闪光 + scroll-into-view。"""
    by_id = {e["evidence_id"]: e for e in evidence_pool}
    st.markdown(
        f"""
<div class="ca-evidence-head">
  <div class="ca-card-title">Evidence Library</div>
  <div class="ca-card-value">{len(evidence_ids)} 条</div>
  <div class="ca-card-note">点击左侧报告里的 <span class="ca-chip">[SXXXXXXX]</span> chip,会自动滚动到对应证据并闪烁一下。</div>
</div>
        """,
        unsafe_allow_html=True,
    )

    cards_html: list[str] = []
    for idx, eid in enumerate(evidence_ids):
        ev = by_id.get(eid)
        if not ev:
            cards_html.append(
                f"""
<details class="ca-evidence-card" id="ev-{esc(eid)}" open>
  <summary><span class="ca-chip miss">{esc(eid)}</span> 未在 raw_evidence 中找到</summary>
  <div class="ca-ev-body">
    <p class="ca-muted">Reviewer R1 会把这条标记为 evidence_id_not_found 错误。</p>
  </div>
</details>
                """
            )
            continue
        open_attr = "open" if idx == 0 else ""
        url = esc(ev.get("source_url", ""))
        src = ev.get("collection_source", "unknown")
        src_label, src_cls, src_icon = _SOURCE_STYLE.get(src, ("未知", "", "❓"))
        cards_html.append(
            f"""
<details class="ca-evidence-card" id="ev-{esc(eid)}" {open_attr}>
  <summary>
    <span class="ca-chip">{esc(eid)}</span>
    <span class="ca-pill {esc(src_cls)}">{src_icon} {esc(src_label)}</span>
    <span><b>{esc(ev.get('product', ''))}</b> · {esc(ev.get('claim_type', ''))} · {esc(ev.get('source_bias', ''))}</span>
  </summary>
  <div class="ca-ev-body">
    <div><b>Claim</b>:{esc(ev.get('claim', ''))}</div>
    <div class="ca-ev-snippet">{esc(ev.get('extracted_snippet', ''))}</div>
    <div class="ca-ev-meta">
      <span><b>可信度</b>{esc(f"{ev.get('source_reliability', 0):.2f}")}</span>
      <span><b>相关性</b>{esc(f"{ev.get('claim_relevance', 0):.2f}")}</span>
      <span><b>综合</b>{esc(f"{ev.get('evidence_confidence', 0):.2f}")}</span>
      <span><b>时效</b>{esc(ev.get('source_freshness', '?'))}</span>
      <span><b>观测</b>{esc(ev.get('observed_at', '?'))}</span>
      <span><b>来源</b><a href="{url}" target="_blank" rel="noopener">{url}</a></span>
    </div>
  </div>
</details>
            """
        )
    st.markdown("".join(cards_html), unsafe_allow_html=True)


def render_quality_report(qr: dict) -> None:
    if not qr:
        st.info("尚无质检报告")
        return
    errors = qr.get("errors", [])
    warnings = qr.get("warnings", [])
    score = qr.get("quality_score", "?")
    metric_grid([
        ("质量分", f"{score}/100", "Reviewer 规则综合评分", "ok"),
        ("模式", qr.get("mode", "?"), "minimal 适合演示，full 适合答辩", "run"),
        ("错误", len(errors), "会触发打回或降级", "bad"),
        ("警告", len(warnings), "不阻断当前输出", "warn"),
    ])

    section("规则状态", "R1-R7")
    pass_set = set(qr.get("passed_rules", []))
    warn_set = set(qr.get("warning_rules", []))
    fail_set = set(qr.get("failed_rules", []))
    rule_cards = []
    for rid, name in _RULE_NAMES.items():
        if rid in fail_set:
            cls, label = "bad", "失败"
        elif rid in warn_set:
            cls, label = "warn", "预警"
        elif rid in pass_set:
            cls, label = "ok", "通过"
        else:
            cls, label = "", "未执行"
        rule_cards.append(
            f"""
<div class="ca-card">
  <div class="ca-card-title">{rid}</div>
  <div class="ca-card-value" style="font-size:18px">{esc(name)}</div>
  <div class="ca-card-note"><span class="ca-pill {cls}">{esc(label)}</span></div>
</div>
            """
        )
    st.markdown(f"<div class='ca-grid'>{''.join(rule_cards)}</div>", unsafe_allow_html=True)

    section("模块状态", "每个产物分区的健康度")
    ms = qr.get("module_status", {})
    mod_cards = []
    for mod, status in ms.items():
        label, cls = _STATUS_STYLE.get(status, (status, ""))
        mod_cards.append(
            f"""
<div class="ca-card">
  <div class="ca-card-title">{esc(mod)}</div>
  <div class="ca-card-note"><span class="ca-pill {esc(cls)}">{esc(label)}</span></div>
</div>
            """
        )
    st.markdown(f"<div class='ca-grid'>{''.join(mod_cards)}</div>", unsafe_allow_html=True)

    if warnings:
        with st.expander(f"⚠️ {len(warnings)} 条 warnings"):
            for w in warnings:
                st.markdown(f"- **[{w['rule']}]** `{w['location']}` · {w.get('detail', '')}")
    if errors:
        with st.expander(f"❌ {len(errors)} 条 errors"):
            for e in errors:
                st.markdown(f"- **[{e['rule']}]** `{e['location']}` · {e.get('detail', '')}")


# ────────────────────────────────────────────────────────────────────────────
# ChatGPT 风格工作台(v2.3 UI)
# ────────────────────────────────────────────────────────────────────────────

DOCUMENT_MODES = {"empty", "clarifying", "running", "ready", "error"}


def inject_chat_styles() -> None:
    st.markdown(
        """
<style>
.stApp {
  background: #ffffff;
  color: #0d0d0d;
}
.block-container {
  max-width: none;
  padding: 0 42px 28px 42px;
}
header[data-testid="stHeader"],
div[data-testid="stToolbar"],
div[data-testid="stDecoration"],
div[data-testid="stStatusWidget"],
#MainMenu {
  display: none;
}
[data-testid="stSidebar"] {
  background: #f7f7f7;
  border-right: 1px solid #e5e5e5;
}
[data-testid="stSidebar"] > div:first-child {
  padding: 12px 12px 18px;
}
[data-testid="stSidebar"] hr {
  margin: 12px 0;
}
.ca-app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 44px;
  gap: 16px;
  padding: 8px 0 4px;
  margin-bottom: 0;
}
.ca-app-title {
  font-size: 20px;
  line-height: 1.2;
  font-weight: 760;
  letter-spacing: 0;
  margin: 0;
}
.ca-app-subtitle {
  color: var(--ca-muted);
  font-size: 13px;
  line-height: 1.55;
  margin-top: 6px;
  max-width: 760px;
}
.ca-chat-caption {
  color: var(--ca-muted);
  font-size: 12px;
  margin: 0 0 12px;
}
.ca-home-spacer {
  height: clamp(18px, 8vh, 70px);
}
.ca-home-title {
  font-size: 29px;
  line-height: 1.2;
  font-weight: 520;
  letter-spacing: 0;
  margin: 0 0 18px;
  text-align: center;
}
div[data-testid="stForm"] {
  border: 0;
  padding: 0;
}
div[data-testid="stForm"] div[data-testid="stTextInput"] input {
  min-height: 56px;
  border-radius: 28px;
  border: 1px solid #d4d4d4;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .07);
  padding-left: 20px;
  padding-right: 20px;
  font-size: 15px;
  background: #ffffff;
}
div[data-testid="stForm"] .stButton > button {
  min-height: 56px;
  border-radius: 28px;
}
.ca-suggestion-row {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}
.ca-suggestion {
  border: 1px solid #dedede;
  background: #fff;
  color: #4f4f4f;
  border-radius: 999px;
  padding: 8px 14px;
  font-size: 14px;
}
.ca-home-send-note {
  color: #8a8a8a;
  text-align: center;
  font-size: 12px;
  margin-top: 4px;
}
.ca-sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 760;
  margin: 4px 4px 16px;
}
.ca-sidebar-logo {
  width: 34px;
  height: 34px;
  border: 1px solid #dddddd;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: #ffffff;
  font-weight: 850;
}
.ca-side-section {
  margin: 16px 4px 6px;
  color: #5f5f5f;
  font-size: 12px;
  font-weight: 700;
}
.ca-side-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 34px;
  padding: 7px 10px;
  color: #171717;
  border-radius: 10px;
  font-size: 14px;
}
.ca-side-item.active {
  background: #ececec;
}
.ca-recent {
  padding: 7px 10px;
  color: #242424;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ca-side-foot {
  border-top: 1px solid #e5e5e5;
  margin-top: 18px;
  padding: 12px 6px 2px;
  color: #555;
  font-size: 13px;
}
.ca-doc-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  border-bottom: 1px solid var(--ca-line);
  padding-bottom: 10px;
  margin-bottom: 12px;
}
.ca-doc-title {
  font-size: 18px;
  font-weight: 830;
  line-height: 1.25;
}
.ca-doc-meta {
  color: var(--ca-muted);
  font-size: 12px;
  line-height: 1.5;
  margin-top: 4px;
}
.ca-action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0 12px;
}
.ca-running-note {
  border: 1px solid rgba(36, 93, 143, .24);
  background: #eef6ff;
  color: #1c4f7c;
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 12px;
}
.ca-empty strong {
  color: var(--ca-ink);
}
div[data-testid="stChatInput"] {
  max-width: 820px;
  margin: 8px auto 0;
}
div[data-testid="stChatMessage"] {
  max-width: 820px;
  margin-left: auto;
  margin-right: auto;
}
.stButton > button,
.stDownloadButton > button {
  border-radius: 12px;
  min-height: 40px;
  background: #0d0d0d;
  color: #ffffff;
  border: 1px solid #0d0d0d;
  font-weight: 650;
}
.stButton > button:hover,
.stDownloadButton > button:hover {
  background: #2b2b2b;
  color: #ffffff;
  border-color: #2b2b2b;
}
[data-testid="stSidebar"] .stButton > button {
  justify-content: flex-start;
  background: #ececec;
  color: #171717;
  border: 1px solid transparent;
  box-shadow: none;
}
[data-testid="stSidebar"] .stButton > button:hover {
  background: #e3e3e3;
  color: #171717;
  border-color: transparent;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def init_chat_state() -> None:
    defaults = {
        "chat_messages": [],
        "pending_questions": None,
        "pending_prompt": None,
        "run_request": None,
        "active_run": False,
        "completed": False,
        "final_state": None,
        "node_log": [],
        "document_mode": "empty",
        "last_run_meta": None,
        "last_error": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def append_chat(role: str, content: str) -> None:
    st.session_state.chat_messages.append({"role": role, "content": content})


def reset_workspace() -> None:
    st.session_state.chat_messages = []
    st.session_state.pending_questions = None
    st.session_state.pending_prompt = None
    st.session_state.run_request = None
    st.session_state.active_run = False
    st.session_state.completed = False
    st.session_state.final_state = None
    st.session_state.node_log = []
    st.session_state.document_mode = "empty"
    st.session_state.last_run_meta = None
    st.session_state.last_error = ""


def product_hits(user_input: str) -> list[str]:
    products = intake.load_products()
    text = (user_input or "").lower()
    hits: list[str] = []
    for name, cfg in products.items():
        candidates = [name] + list(cfg.get("aliases") or [])
        if any(c and c.lower() in text for c in candidates):
            hits.append(name)
    return hits


def infer_focus(user_input: str) -> Optional[str]:
    text = user_input or ""
    rules = [
        (("代码补全", "补全体验", "autocomplete", "completion"), "代码补全体验"),
        (("定价", "价格", "pricing", "price"), "定价策略"),
        (("任务管理", "项目管理", "协作"), "团队任务管理体验"),
        (("功能", "feature", "能力"), "核心功能完整度"),
        (("上手", "易用", "体验", "ux"), "用户体验与上手成本"),
        (("集成", "生态", "插件"), "集成与生态"),
        (("痛点", "差评", "用户反馈"), "用户痛点"),
    ]
    lowered = text.lower()
    for keywords, focus in rules:
        if any(k.lower() in lowered for k in keywords):
            return focus
    return None


def default_purpose(dom_cfg: dict) -> str:
    return dom_cfg.get("analysis_purpose") or "学习竞品优点，优化自身产品"


def question_default(questions: list[dict], key: str) -> object:
    for q in questions:
        if q.get("key") == key:
            suggested = q.get("suggested") or []
            if q.get("multi"):
                return suggested
            return suggested[0] if suggested else ""
    return []


def direct_meta_from_prompt(user_input: str, questions: list[dict], dom_cfg: dict) -> Optional[dict]:
    hits = product_hits(user_input)
    focus = infer_focus(user_input)
    if len(hits) < 2 or not focus:
        return None

    answers = {
        "target": hits[0],
        "competitors": hits[1:],
        "focus": focus,
        "purpose": question_default(questions, "purpose") or default_purpose(dom_cfg),
        "persist": "仅本次运行",
    }
    return intake.assemble_meta(answers, user_input=user_input)


def domain_meta(domain_key: str, domains: dict) -> dict:
    dom_cfg = domains.get(domain_key, {})
    target = dom_cfg.get("target_product") or "Cursor"
    competitors = list(dom_cfg.get("competitors") or ["Windsurf", "GitHubCopilot"])
    focus = list(dom_cfg.get("analysis_focus") or ["代码补全体验"])
    return {
        "target_product": target,
        "competitors": competitors,
        "analysis_focus": focus,
        "analysis_purpose": default_purpose(dom_cfg),
        "user_input": f"分析 {target} 和 {', '.join(competitors)} 在 {focus[0]} 上的差距",
    }


def queue_run(meta: dict, note: Optional[str] = None) -> None:
    st.session_state.run_request = meta
    st.session_state.last_run_meta = meta
    st.session_state.active_run = True
    st.session_state.completed = False
    st.session_state.final_state = None
    st.session_state.node_log = []
    st.session_state.document_mode = "running"
    if note:
        append_chat("assistant", note)


def start_intake_or_run(user_input: str, domain_key: str, domains: dict) -> None:
    append_chat("user", user_input)

    # 已有报告后的轻量继续对话：新分析走 intake，其余提示当前能力边界。
    if st.session_state.get("final_state") and not looks_like_new_analysis(user_input):
        lowered = user_input.lower()
        if "下载" in user_input or "download" in lowered:
            append_chat("assistant", "右侧文档顶部可以下载 `report.md`，质检和结构化结果也可以分别下载 JSON。")
        elif "证据" in user_input or "引用" in user_input:
            append_chat("assistant", "右侧切到“证据”标签页，可以按 `[SXXXXXXX]` 查看每条结论对应的原始片段。")
        elif "质量" in user_input or "评分" in user_input:
            append_chat("assistant", "右侧“质检”标签页展示 Reviewer 的规则结果、质量分、warning 和 error。")
        else:
            append_chat(
                "assistant",
                "当前版本支持查看、下载、重新生成报告，也可以直接输入新的竞品分析需求开始下一轮。自由报告问答还没有接入。",
            )
        return

    dom_cfg = domains.get(domain_key, {})
    with st.spinner("正在理解你的分析需求..."):
        questions = [c.to_dict() for c in intake.intake_questions(user_input, domain_key)]

    meta = direct_meta_from_prompt(user_input, questions, dom_cfg)
    if meta:
        queue_run(
            meta,
            "信息足够清楚。我会先让 Collector 收集证据，然后生成右侧报告文档。",
        )
        return

    st.session_state.pending_questions = [
        q for q in questions if q.get("key") in {"target", "competitors", "focus", "purpose"}
    ]
    st.session_state.pending_prompt = user_input
    st.session_state.document_mode = "clarifying"
    append_chat("assistant", "Collector 开始收集前还需要确认几个点，避免把产品或分析维度理解错。")


def looks_like_new_analysis(user_input: str) -> bool:
    text = (user_input or "").lower()
    if product_hits(user_input):
        return True
    keywords = ("分析", "对比", "比较", "竞品", "差距", "vs", "versus", "重新生成")
    return any(k in text for k in keywords)


def render_clarification_form() -> None:
    questions = st.session_state.get("pending_questions") or []
    if not questions:
        return

    with st.chat_message("assistant"):
        st.markdown("请确认下面的信息，确认后我会开始生成报告。")
        with st.form("clarification_form"):
            answers: dict = {}
            for q in questions:
                key = q["key"]
                opts = list(q.get("options") or [])
                suggested = list(q.get("suggested") or [])
                label = q.get("question") or key
                widget_key = f"chat_clarify_{key}"

                if q.get("multi"):
                    default = [s for s in suggested if s in opts]
                    answers[key] = st.multiselect(label, opts, default=default, key=widget_key)
                elif opts:
                    default_idx = opts.index(suggested[0]) if suggested and suggested[0] in opts else 0
                    answers[key] = st.selectbox(label, opts, index=default_idx, key=widget_key)
                else:
                    answers[key] = st.text_input(label, key=widget_key)

                if q.get("allow_custom"):
                    custom = st.text_input(
                        f"自定义{label}（可留空）",
                        key=f"chat_clarify_custom_{key}",
                    )
                    if custom.strip():
                        answers[key] = (
                            [c.strip() for c in custom.split(",") if c.strip()]
                            if q.get("multi")
                            else custom.strip()
                        )

            submitted = st.form_submit_button("开始生成报告", type="primary", use_container_width=True)

    if not submitted:
        return

    meta = intake.assemble_meta(answers, user_input=st.session_state.get("pending_prompt"))
    if not meta.get("target_product") or not meta.get("competitors") or not meta.get("analysis_focus"):
        st.warning("目标产品、竞品和分析焦点都需要确认后才能开始。")
        return

    append_chat(
        "assistant",
        f"收到。我会分析 {meta['target_product']} 和 {', '.join(meta['competitors'])} 在 {meta['analysis_focus'][0]} 上的差距。",
    )
    st.session_state.pending_questions = None
    st.session_state.pending_prompt = None
    queue_run(meta)
    st.rerun()


def configure_runtime(settings: dict) -> None:
    os.environ["DOMAIN"] = settings["domain_key"]
    os.environ["REVIEWER_MODE"] = settings["reviewer_mode"]
    if settings["use_mock"]:
        os.environ["ANALYZER_MOCK"] = "1"
    else:
        os.environ.pop("ANALYZER_MOCK", None)
        if settings.get("api_key"):
            os.environ["ARK_API_KEY"] = settings["api_key"]
        if settings.get("ep"):
            os.environ["ARK_EP"] = settings["ep"]
    if settings.get("demo_loop"):
        os.environ["DEMO_LOOP"] = "1"
    else:
        os.environ.pop("DEMO_LOOP", None)
    if settings.get("enable_live"):
        os.environ["ENABLE_LIVE_FETCH"] = "1"
    else:
        os.environ.pop("ENABLE_LIVE_FETCH", None)


def execute_analysis(meta: dict, settings: dict) -> None:
    configure_runtime(settings)
    st.session_state.active_run = True
    st.session_state.document_mode = "running"

    competitors = list(meta.get("competitors") or [])
    focus = list(meta.get("analysis_focus") or [""])

    st.markdown(
        f"""
<div class="ca-running-note">
  正在生成 <b>{esc(meta.get('target_product'))}</b> vs <b>{esc(', '.join(competitors))}</b>
  的竞品报告。生成完成后会自动切换为可下载文档。
</div>
        """,
        unsafe_allow_html=True,
    )
    metric_grid([
        ("目标产品", meta.get("target_product", ""), "本轮分析的主产品", ""),
        ("竞品数量", len(competitors), ", ".join(competitors), ""),
        ("分析焦点", focus[0] if focus else "", "报告会围绕该维度展开", ""),
        ("运行模式", "Mock" if settings["use_mock"] else "LLM", settings["reviewer_mode"], ""),
    ])

    progress_box = st.container()
    substep_box = st.empty()
    log_box = st.empty()

    try:
        from src.analyzer import set_progress_callback
        from src.collector import reset_registry
        from src.graph import run_demo_streaming
        from src.llm import set_llm_callback

        reset_registry()

        substep_state = {"current": "等待 Analyzer 调度", "tokens": 0, "cls": ""}

        def _render_substep():
            line = substep_state["current"]
            tok = substep_state["tokens"]
            tok_text = f" · 累计 {tok:,} token" if tok else ""
            cls = substep_state.get("cls", "")
            substep_box.markdown(
                f"<div class='ca-substep {cls}'>{esc(line)}{esc(tok_text)}</div>",
                unsafe_allow_html=True,
            )

        def on_analyzer(evt):
            step = evt.get("step", "?")
            phase = evt.get("phase", "?")
            attempt = evt.get("attempt", 1)
            label = {
                "facts": "Step 1/2 事实层(features + pricing + persona)",
                "derivations": "Step 2/2 推导层(swot + recommendations)",
            }.get(step, step)
            if phase == "start":
                substep_state["current"] = f"调用中:{label} · 第 {attempt} 次"
                substep_state["cls"] = ""
            elif phase == "done":
                substep_state["current"] = f"完成:{label} · 第 {attempt} 次"
                substep_state["cls"] = "done"
            elif phase == "repair":
                substep_state["current"] = f"自修复:{label} 检出 {evt.get('issues', 0)} issue，重新调用 LLM"
                substep_state["cls"] = "repair"
            _render_substep()

        def on_llm(evt):
            if evt.get("phase") == "done":
                substep_state["tokens"] += evt.get("prompt_tokens", 0) + evt.get("completion_tokens", 0)
                substep_state["current"] += f" · {evt.get('duration', 0):.1f}s"
                _render_substep()

        set_progress_callback(on_analyzer)
        set_llm_callback(on_llm)

        node_counts = {"collector": 0, "analyzer": 0, "writer": 0, "reviewer": 0}
        placeholders = {}
        with progress_box:
            for node in node_counts:
                placeholders[node] = st.empty()
                placeholders[node].markdown(step_html(node, "wait", "等待调度"), unsafe_allow_html=True)

        t0 = time.time()
        last_t = t0
        final_state = None
        events_text: list[str] = []

        for node_name, state_after in run_demo_streaming(
            target_product=meta.get("target_product"),
            competitors=competitors,
            analysis_focus=focus,
            analysis_purpose=meta.get("analysis_purpose"),
            user_input=meta.get("user_input"),
        ):
            now = time.time()
            step_duration = now - last_t
            last_t = now
            elapsed = now - t0
            icon = _NODE_ICONS.get(node_name, "▶️")

            if node_name in node_counts:
                node_counts[node_name] += 1
                pass_n = node_counts[node_name]
                status = state_after.get("status", "?")
                reject = state_after.get("reject_target")
                if node_name == "reviewer" and reject and status == "running":
                    qr = state_after.get("quality_report") or {}
                    err_n = len(qr.get("errors") or [])
                    meta_text = (
                        f"第 {pass_n} 次，检出 {err_n} 个 error，"
                        f"打回 {_NODE_LABELS.get(reject, reject)}，耗时 {step_duration:.1f}s"
                    )
                    placeholders[node_name].markdown(step_html(node_name, "warn", meta_text), unsafe_allow_html=True)
                elif node_name == "reviewer" and status == "passed":
                    placeholders[node_name].markdown(
                        step_html(node_name, "ok", f"第 {pass_n} 次，通过质检，耗时 {step_duration:.1f}s"),
                        unsafe_allow_html=True,
                    )
                else:
                    suffix = f" · 第 {pass_n} 次" if pass_n > 1 else ""
                    placeholders[node_name].markdown(
                        step_html(node_name, "ok", f"完成{suffix}，耗时 {step_duration:.1f}s"),
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(step_html(node_name, "warn", f"完成，耗时 {step_duration:.1f}s"), unsafe_allow_html=True)

            final_state = state_after
            retry_info = state_after.get("retry_count") or {}
            retry_text = " ".join(f"{k}:{v}" for k, v in retry_info.items() if v)
            qr = state_after.get("quality_report") or {}
            err_n = len(qr.get("errors") or [])
            line = f"[{elapsed:6.1f}s] {icon} {node_name:9s} status={state_after.get('status', '?'):9s}"
            if state_after.get("reject_target"):
                line += f" -> reject={state_after['reject_target']}"
            if err_n:
                line += f" errors={err_n}"
            if retry_text:
                line += f" retry={{{retry_text}}}"
            events_text.append(line)
            with log_box.container():
                with st.expander("查看节点日志", expanded=False):
                    st.code("\n".join(events_text[-16:]), language=None)

        st.session_state.final_state = final_state
        st.session_state.completed = True
        st.session_state.node_log = events_text
        st.session_state.run_request = None
        st.session_state.active_run = False
        st.session_state.document_mode = "ready"

        status = final_state.get("status") if final_state else "unknown"
        append_chat("assistant", f"报告已生成，状态 `{status}`。你可以在右侧查看、下载，或继续输入新的分析需求。")
        st.rerun()

    except Exception as e:
        st.session_state.last_error = f"{type(e).__name__}: {e}"
        st.session_state.run_request = None
        st.session_state.active_run = False
        st.session_state.document_mode = "error"
        append_chat("assistant", f"运行失败：{type(e).__name__}: {e}")
        st.rerun()


def source_summary(raw_ev: list[dict]) -> str:
    source_counts = {}
    for ev in raw_ev:
        src = ev.get("collection_source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
    parts = []
    for src, icon in [("live", "🌐"), ("cache", "💾"), ("mock", "🧪"), ("unknown", "❓")]:
        if src in source_counts:
            parts.append(f"{icon} {src}: {source_counts[src]}")
    return " · ".join(parts) if parts else "无"


def render_ready_document(fs: dict) -> None:
    report_md = fs.get("report_draft") or ""
    raw_ev = fs.get("raw_evidence") or []
    qr = fs.get("quality_report") or {}
    eids = extract_evidence_ids(report_md)
    status = fs.get("status", "?")
    status_label, status_cls = _STATUS_STYLE.get(status, (status, ""))

    st.markdown(
        f"""
<div class="ca-doc-header">
  <div>
    <div class="ca-doc-title">竞品分析报告</div>
    <div class="ca-doc-meta">状态 {esc(status_label)} · 质量分 {esc(qr.get('quality_score', '?'))}/100 · 证据 {len(raw_ev)} 条 · 引用 {len(eids)} 条</div>
  </div>
  <span class="ca-pill {esc(status_cls)}">{esc(status)}</span>
</div>
        """,
        unsafe_allow_html=True,
    )

    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button(
            "下载 report.md",
            data=report_md,
            file_name="report.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "下载 quality_report.json",
            data=json.dumps(qr, ensure_ascii=False, indent=2),
            file_name="quality_report.json",
            mime="application/json",
            use_container_width=True,
        )
    with d3:
        st.download_button(
            "下载 schema_draft.json",
            data=json.dumps(fs.get("schema_draft") or {}, ensure_ascii=False, indent=2),
            file_name="schema_draft.json",
            mime="application/json",
            use_container_width=True,
        )

    st.markdown(
        f"""
<div class="ca-strip">
  <span class="ca-pill">Reviewer：{esc(qr.get('mode', '?'))}</span>
  <span class="ca-pill">来源分布：{esc(source_summary(raw_ev))}</span>
  <span class="ca-pill">重试：{esc(fs.get('retry_count', {}))}</span>
</div>
        """,
        unsafe_allow_html=True,
    )

    tab_report, tab_evidence, tab_quality, tab_schema = st.tabs(["报告", "证据", "质检", "结构化"])
    with tab_report:
        valid_ids = {e["evidence_id"] for e in raw_ev}
        rewritten = rewrite_chips(report_md, valid_ids, raw_ev)
        st.markdown(f"<div class='ca-report-shell'>\n\n{rewritten}\n\n</div>", unsafe_allow_html=True)
    with tab_evidence:
        render_evidence_panel(eids, raw_ev)
    with tab_quality:
        render_quality_report(qr)
        section("节点执行日志", "LangGraph 事件序列")
        st.code("\n".join(st.session_state.node_log), language=None)
    with tab_schema:
        st.json(fs.get("schema_draft") or {}, expanded=False)


def render_document_workspace(settings: dict) -> None:
    run_request = st.session_state.get("run_request")
    if run_request:
        execute_analysis(run_request, settings)
        return

    mode = st.session_state.get("document_mode", "empty")
    if mode not in DOCUMENT_MODES:
        mode = "empty"

    if mode == "ready" and st.session_state.get("final_state"):
        render_ready_document(st.session_state.final_state)
    elif mode == "clarifying":
        st.markdown(
            """
<div class="ca-empty">
  <strong>等待补充信息</strong><br>
  左侧确认目标产品、竞品和分析焦点后，Collector 才会开始收集证据。
</div>
            """,
            unsafe_allow_html=True,
        )
    elif mode == "error":
        st.error(st.session_state.get("last_error") or "运行失败")
    else:
        st.markdown(
            """
<div class="ca-empty">
  <strong>右侧会生成报告文档</strong><br>
  在左侧输入类似“分析 Cursor 和 Windsurf、GitHub Copilot 在代码补全体验上的差距”，
  生成后这里会显示可下载的 Markdown 报告、证据和质检结果。
</div>
            """,
            unsafe_allow_html=True,
        )


def render_chat_controls(domains: dict) -> None:
    fs = st.session_state.get("final_state")
    if fs:
        b1, b2 = st.columns(2)
        with b1:
            if st.button("重新生成", use_container_width=True):
                meta = st.session_state.get("last_run_meta")
                if meta:
                    queue_run(meta, "我会基于上一轮参数重新生成报告。")
                    st.rerun()
        with b2:
            if st.button("开始新分析", use_container_width=True):
                st.session_state.pending_questions = None
                st.session_state.pending_prompt = None
                append_chat("assistant", "直接输入新的分析需求即可，我会重新判断是否需要追问。")
                st.rerun()
        report_md = fs.get("report_draft") or ""
        st.download_button(
            "下载当前报告",
            data=report_md,
            file_name="report.md",
            mime="text/markdown",
            use_container_width=True,
        )


def render_chat_panel(domain_key: str, domains: dict) -> None:
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    render_clarification_form()
    render_chat_controls(domains)

    if st.session_state.get("active_run"):
        st.info("Agent 正在右侧生成报告，完成后可以继续对话。")

    prompt = st.chat_input("输入竞品分析需求，或继续询问当前报告")
    if prompt:
        start_intake_or_run(prompt, domain_key, domains)
        st.rerun()


def render_home_page(domain_key: str, domains: dict) -> None:
    st.markdown(
        """
<div class="ca-home-spacer"></div>
<div class="ca-home-title">有什么可以帮忙的？</div>
        """,
        unsafe_allow_html=True,
    )
    _, input_col, _ = st.columns([1.1, 2.5, 1.1])
    with input_col:
        with st.form("home_prompt_form", clear_on_submit=True, border=False):
            text_col, send_col = st.columns([8, 0.85], gap="small")
            with text_col:
                prompt = st.text_input(
                    "输入竞品分析需求",
                    placeholder="分析 Cursor 和 Windsurf、GitHub Copilot 在代码补全体验上的差距",
                    label_visibility="collapsed",
                )
            with send_col:
                submitted = st.form_submit_button("↑", use_container_width=True)
        st.markdown("<div class='ca-home-send-note'>输入需求后点击发送，Agent 会先判断是否需要追问。</div>", unsafe_allow_html=True)
    st.markdown(
        """
<div class="ca-suggestion-row">
  <span class="ca-suggestion">生成竞品报告</span>
  <span class="ca-suggestion">查找证据</span>
  <span class="ca-suggestion">对比定价</span>
</div>
        """,
        unsafe_allow_html=True,
    )
    if submitted and prompt.strip():
        start_intake_or_run(prompt.strip(), domain_key, domains)
        st.rerun()


def render_app_header(settings: dict) -> None:
    st.markdown(
        f"""
<div class="ca-app-header">
  <div>
    <div class="ca-app-title">Competitive Radar <span style="color:#777;font-size:14px">⌄</span></div>
  </div>
  <div class="ca-strip">
    <span class="ca-pill {'bad' if settings['use_mock'] else 'ok'}">{'Mock' if settings['use_mock'] else 'LLM'}</span>
    <span class="ca-pill">Reviewer: {esc(settings['reviewer_mode'])}</span>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(
    page_title="竞品分析 Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_design_system()
inject_chat_styles()
init_chat_state()

domains = load_domains()
if "chat_domain" not in st.session_state:
    st.session_state.chat_domain = os.environ.get("DOMAIN", "ai_coding") if os.environ.get("DOMAIN") in domains else "ai_coding"
if "chat_mock" not in st.session_state:
    st.session_state.chat_mock = True
if "chat_mode" not in st.session_state:
    st.session_state.chat_mode = "minimal"
if "chat_loop" not in st.session_state:
    st.session_state.chat_loop = False
if "chat_live" not in st.session_state:
    st.session_state.chat_live = False

with st.sidebar:
    st.markdown(
        """
<div class="ca-sidebar-brand">
  <div class="ca-sidebar-logo">CR</div>
  <div>Competitive Radar</div>
</div>
<div class="ca-side-item active">＋ 新分析</div>
<div class="ca-side-item">⌕ 搜索报告</div>
<div class="ca-side-item">▣ 项目</div>
<div class="ca-side-item">◇ Agent</div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("新建对话", use_container_width=True):
        reset_workspace()
        st.rerun()

    st.markdown(
        """
<div class="ca-side-section">最近</div>
<div class="ca-recent">Cursor 代码补全差距</div>
<div class="ca-recent">Notion 任务管理对比</div>
<div class="ca-recent">Reviewer 打回闭环演示</div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("运行 AI 编程演示", use_container_width=True):
        reset_workspace()
        st.session_state.chat_domain = "ai_coding"
        st.session_state.chat_mock = True
        st.session_state.chat_loop = False
        meta = domain_meta("ai_coding", domains)
        append_chat("user", meta["user_input"])
        queue_run(meta, "我会使用 AI 编程工具预设开始生成报告。")
        st.rerun()

    with st.expander("运行设置", expanded=False):
        domain_options = list(domains.keys()) or ["ai_coding"]
        domain_key = st.selectbox(
            "默认行业",
            domain_options,
            format_func=lambda k: f"{domains.get(k, {}).get('name', '')} · {k}",
            key="chat_domain",
        )
        reviewer_mode = st.radio(
            "Reviewer 模式",
            ["minimal", "full"],
            horizontal=True,
            key="chat_mode",
        )
        use_mock = st.toggle("Mock 模式", key="chat_mock")
        enable_live = False
        if not use_mock:
            enable_live = st.toggle("实时抓取官网", key="chat_live")
        demo_loop = st.toggle("演示打回闭环", key="chat_loop")

        if not use_mock:
            api_key = st.text_input("ARK_API_KEY", value=os.environ.get("ARK_API_KEY", ""), type="password")
            ep = st.text_input("ARK_EP", value=os.environ.get("ARK_EP", "ep-20260514111325-xjmj7"))
        else:
            api_key, ep = "", ""

    st.markdown(
        """
<div class="ca-side-foot">
  <div>本地演示版</div>
  <div style="color:#888;font-size:12px;margin-top:2px">Evidence traceable</div>
</div>
        """,
        unsafe_allow_html=True,
    )

settings = {
    "domain_key": domain_key,
    "reviewer_mode": reviewer_mode,
    "use_mock": use_mock,
    "enable_live": enable_live,
    "demo_loop": demo_loop,
    "api_key": api_key,
    "ep": ep,
}
configure_runtime(settings)

render_app_header(settings)

mode = st.session_state.get("document_mode", "empty")
is_empty_home = (
    mode == "empty"
    and not st.session_state.get("chat_messages")
    and not st.session_state.get("pending_questions")
    and not st.session_state.get("run_request")
    and not st.session_state.get("final_state")
)

if is_empty_home:
    render_home_page(domain_key, domains)
elif mode in {"running", "ready"} or st.session_state.get("run_request") or st.session_state.get("final_state"):
    chat_col, doc_col = st.columns([0.72, 1.28], gap="large")
    with chat_col:
        render_chat_panel(domain_key, domains)
    with doc_col:
        render_document_workspace(settings)
else:
    left_pad, chat_col, right_pad = st.columns([0.18, 0.64, 0.18])
    with chat_col:
        render_chat_panel(domain_key, domains)
    if mode == "error":
        st.error(st.session_state.get("last_error") or "运行失败")

st.stop()
