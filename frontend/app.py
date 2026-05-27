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


def rewrite_chips(text: str, valid_ids: set[str]) -> str:
    """把 [SXXXXXXX] 替换为可点击 anchor 链接;未命中 raw_evidence 的标 miss 样式"""
    def repl(m: re.Match) -> str:
        eid = m.group(1)
        cls = "ca-chip" if eid in valid_ids else "ca-chip miss"
        return f'<a href="#ev-{eid}" class="{cls}">[{eid}]</a>'
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
        cards_html.append(
            f"""
<details class="ca-evidence-card" id="ev-{esc(eid)}" {open_attr}>
  <summary>
    <span class="ca-chip">{esc(eid)}</span>
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
# 页面布局
# ────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="竞品分析 Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_design_system()
topbar()

domains = load_domains()

# ─── 应用 intake「智能填写」结果(同样需在 widget 渲染前注入 session_state)──
_intake_pending = st.session_state.pop("_intake_pending", None)
if _intake_pending:
    if _intake_pending.get("domain_key"):
        load_domains.clear()
        domains = load_domains()
        st.session_state["sb_domain"] = _intake_pending["domain_key"]
    st.session_state["sb_target"] = _intake_pending["target_product"]
    st.session_state["sb_competitors"] = "\n".join(_intake_pending.get("competitors", []))
    st.session_state["sb_focus"] = (_intake_pending.get("analysis_focus") or [""])[0]

# ─── 应用一键预设(session_state 注入需在 widget 渲染前发生)───────
PRESETS = {
    "ai_coding_mock": {
        "label": "💻 AI 编程演示", "domain": "ai_coding",
        "mock": True, "loop": False, "mode": "minimal",
        "desc": "Cursor vs Windsurf vs GitHubCopilot,Mock 模式秒级跑完",
    },
    "pm_mock": {
        "label": "📋 PM 工具演示", "domain": "pm",
        "mock": True, "loop": False, "mode": "minimal",
        "desc": "Notion vs Asana vs Linear,验证零代码切换行业",
    },
    "loop_demo": {
        "label": "🔄 打回闭环演示", "domain": "ai_coding",
        "mock": True, "loop": True, "mode": "minimal",
        "desc": "Analyzer 首轮注入 3 错误 → Reviewer 打回 → 重试通过",
    },
}

_pending = st.session_state.pop("_preset_pending", None)
if _pending:
    p = PRESETS[_pending]
    st.session_state["sb_domain"] = p["domain"]
    st.session_state["sb_mock"] = p["mock"]
    st.session_state["sb_loop"] = p["loop"]
    st.session_state["sb_mode"] = p["mode"]
    st.session_state["_auto_run"] = True
    dom_default = domains.get(p["domain"], {})
    st.session_state["sb_target"] = dom_default.get("target_product", "")
    st.session_state["sb_competitors"] = "\n".join(dom_default.get("competitors", []))
    st.session_state["sb_focus"] = (dom_default.get("analysis_focus") or [""])[0]

with st.sidebar:
    st.markdown("### 行业域")
    domain_key = st.selectbox(
        "选择行业",
        list(domains.keys()),
        format_func=lambda k: f"{domains[k].get('name', '')} · {k}",
        help="domains.yaml 配置;切换 = 改一行 env DOMAIN=xxx,不改代码",
        key="sb_domain",
    )
    dom_cfg = domains.get(domain_key, {})

    st.markdown("### 分析对象")
    if "sb_target" not in st.session_state:
        st.session_state["sb_target"] = dom_cfg.get("target_product", "")
    if "sb_competitors" not in st.session_state:
        st.session_state["sb_competitors"] = "\n".join(dom_cfg.get("competitors", []))
    if "sb_focus" not in st.session_state:
        st.session_state["sb_focus"] = (dom_cfg.get("analysis_focus") or [""])[0]

    target = st.text_input("目标产品", key="sb_target")
    competitors_text = st.text_area("竞品(每行一个)", height=80, key="sb_competitors")
    focus = st.text_input("分析焦点", key="sb_focus")

    st.markdown("### Agent 配置")
    reviewer_mode = st.radio(
        "Reviewer 模式",
        ["minimal", "full"],
        index=0,
        horizontal=True,
        help="minimal: R1/R4/R5 当 error,其余 warning;full: R1-R5 都是 error,R6 终轮启用",
        key="sb_mode",
    )

    use_mock = st.toggle(
        "🧪 Mock 模式(跳过真实 LLM)",
        help="开启后 Analyzer 直接返回 sample_report.json,用于评委演示时省 API 调用",
        key="sb_mock",
    )

    demo_loop = st.toggle(
        "🔄 演示打回闭环",
        help=(
            "开启后(仅 Mock 模式生效)Analyzer 首轮故意输出含 R1+R5+R4 错误的 schema,"
            "Reviewer 检出 → reject_target=analyzer → 重试 → 第二轮干净通过。"
            "用于演示评分维度 1 的「反馈闭环真实可触发,且重做后输出有改善」。"
        ),
        key="sb_loop",
    )

    if not use_mock:
        st.markdown("### ARK API")
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


# ─── 一句话智能填写面板(主区顶部,回填上面的侧栏配置)────────────
intake_panel(domains)


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

auto_run = st.session_state.pop("_auto_run", False)
should_run = run_btn or auto_run

if should_run:
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
    if demo_loop:
        os.environ["DEMO_LOOP"] = "1"
    else:
        os.environ.pop("DEMO_LOOP", None)

    st.session_state.completed = False
    st.session_state.final_state = None
    st.session_state.node_log = []

    competitors_list = [c.strip() for c in competitors_text.splitlines() if c.strip()]

    metric_grid([
        ("目标产品", target, "本轮分析的主产品", ""),
        ("竞品数量", len(competitors_list), ", ".join(competitors_list), ""),
        ("分析焦点", focus, "报告会围绕该维度展开", ""),
        ("运行模式", "Mock" if use_mock else "LLM", reviewer_mode, ""),
    ])
    section("Agent 运行进度", f"{target} vs {', '.join(competitors_list)}")
    progress_box = st.container()
    substep_box = st.empty()  # Analyzer 子步骤实时刷新(消灭 80s 静默)
    log_box = st.empty()

    try:
        from src.graph import run_demo_streaming
        from src.analyzer import set_progress_callback
        from src.llm import set_llm_callback

        # ─── 注册子步骤回调,Analyzer / LLM 调用期间实时刷新 ──────────
        substep_state = {"current": "", "tokens": 0}

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
            label = {"facts": "Step 1/2 事实层(features + pricing + persona)",
                     "derivations": "Step 2/2 推导层(swot + recommendations)"}.get(step, step)
            if phase == "start":
                substep_state["current"] = f"📡 调用中:{label} · 第 {attempt} 次"
                substep_state["cls"] = ""
            elif phase == "done":
                substep_state["current"] = f"✅ 完成:{label} · 第 {attempt} 次"
                substep_state["cls"] = "done"
            elif phase == "repair":
                substep_state["current"] = f"🔧 自修复:{label} 检出 {evt.get('issues', 0)} issue,重新调用 LLM"
                substep_state["cls"] = "repair"
            _render_substep()

        def on_llm(evt):
            if evt.get("phase") == "done":
                substep_state["tokens"] += evt.get("prompt_tokens", 0) + evt.get("completion_tokens", 0)
                substep_state["current"] += f" · {evt.get('duration', 0):.1f}s"
                _render_substep()

        set_progress_callback(on_analyzer)
        set_llm_callback(on_llm)

        # 节点活动指示(带 pass 计数,允许重复出现)
        node_counts = {"collector": 0, "analyzer": 0, "writer": 0, "reviewer": 0}
        with progress_box:
            placeholders = {}
            for node in node_counts:
                placeholders[node] = st.empty()
                placeholders[node].markdown(
                    step_html(node, "wait", "等待调度"),
                    unsafe_allow_html=True,
                )

        t0 = time.time()
        final_state = None
        events_text: list[str] = []
        last_t = t0

        for node_name, state_after in run_demo_streaming(
            target_product=target,
            competitors=competitors_list,
            analysis_focus=[focus],
        ):
            now = time.time()
            step_duration = now - last_t
            last_t = now
            elapsed = now - t0
            icon = _NODE_ICONS.get(node_name, "▶️")

            # 节点 pass 计数 + 状态
            if node_name in node_counts:
                node_counts[node_name] += 1
                pass_n = node_counts[node_name]
                status = state_after.get("status", "?")
                reject = state_after.get("reject_target")

                # Reviewer 打回的特殊提示
                if node_name == "reviewer" and reject and status == "running":
                    qr = state_after.get("quality_report") or {}
                    err_n = len(qr.get("errors") or [])
                    placeholders[node_name].markdown(
                        step_html(
                            node_name,
                            "warn",
                            f"第 {pass_n} 次，检出 {err_n} 个 error，打回 {_NODE_LABELS.get(reject, reject)}，耗时 {step_duration:.1f}s",
                        ),
                        unsafe_allow_html=True,
                    )
                elif node_name == "reviewer" and status == "passed":
                    placeholders[node_name].markdown(
                        step_html(node_name, "ok", f"第 {pass_n} 次，通过质检，耗时 {step_duration:.1f}s"),
                        unsafe_allow_html=True,
                    )
                else:
                    suffix = f"· 第 {pass_n} 次" if pass_n > 1 else ""
                    placeholders[node_name].markdown(
                        step_html(node_name, "ok", f"完成 {suffix}，耗时 {step_duration:.1f}s"),
                        unsafe_allow_html=True,
                    )
            else:
                # 例如 degraded_writer
                with progress_box:
                    st.markdown(
                        step_html(node_name, "warn", f"完成，耗时 {step_duration:.1f}s"),
                        unsafe_allow_html=True,
                    )

            final_state = state_after

            # 完整事件日志
            retry_info = state_after.get("retry_count") or {}
            retry_text = " ".join(f"{k}:{v}" for k, v in retry_info.items() if v)
            qr = state_after.get("quality_report") or {}
            err_n = len(qr.get("errors") or [])
            line = f"[{elapsed:6.1f}s] {icon} {node_name:9s} status={state_after.get('status', '?'):9s}"
            if state_after.get("reject_target"):
                line += f" → reject={state_after['reject_target']}"
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
    eids = extract_evidence_ids(report_md)
    status = fs.get("status", "?")
    status_label, status_cls = _STATUS_STYLE.get(status, (status, ""))

    st.markdown(
        f"""
<div class="ca-strip">
  <span class="ca-pill {esc(status_cls)}">状态：{esc(status_label)}</span>
  <span class="ca-pill">质量分：{esc(qr.get('quality_score', '?'))}/100</span>
  <span class="ca-pill">证据：{len(raw_ev)} 条</span>
  <span class="ca-pill">引用：{len(eids)} 条</span>
  <span class="ca-pill">Reviewer：{esc(qr.get('mode', '?'))}</span>
</div>
        """,
        unsafe_allow_html=True,
    )

    tab_report, tab_quality, tab_schema, tab_raw = st.tabs([
        "报告与证据",
        "质检详情",
        "结构化结果",
        "原始证据",
    ])

    with tab_report:
        left, right = st.columns([3, 2], gap="medium")
        with left:
            section("竞品报告", "带证据 chip 的 Markdown 输出 · 点击 chip 跳右侧证据")
            valid_ids = {e["evidence_id"] for e in raw_ev}
            rewritten = rewrite_chips(report_md, valid_ids)
            st.markdown(f"<div class='ca-report-shell'>\n\n{rewritten}\n\n</div>",
                        unsafe_allow_html=True)
        with right:
            render_evidence_panel(eids, raw_ev)

    with tab_quality:
        render_quality_report(qr)
        section("节点执行日志", "LangGraph 事件序列")
        st.code("\n".join(st.session_state.node_log), language=None)
        section("重试明细", "按 target 分桶")
        st.json(fs.get("retry_count", {}))

    with tab_schema:
        section("schema_draft", "Analyzer 两步式输出")
        st.caption("包含 feature_tree、pricing_model、user_persona、swot、recommendations。")
        st.json(fs.get("schema_draft") or {}, expanded=False)

    with tab_raw:
        section("raw_evidence", f"Collector 抓取并去重后的 {len(raw_ev)} 条证据")
        for ev in raw_ev:
            label = f"`{ev['evidence_id']}` · **{ev['product']}** · {ev['claim_type']} · {ev['source_bias']}"
            with st.expander(label):
                st.json(ev, expanded=False)

elif not should_run:
    # 首屏:一键演示 + 简介
    section("一键演示", "点击下面任意场景,系统会自动填写配置并立刻开始分析")

    preset_cols = st.columns(len(PRESETS))
    for col, (key, cfg) in zip(preset_cols, PRESETS.items()):
        with col:
            st.markdown(
                f"""
<div class="ca-preset">
  <div class="ca-preset-name">{esc(cfg['label'])}</div>
  <div class="ca-preset-desc">{esc(cfg['desc'])}</div>
</div>
                """,
                unsafe_allow_html=True,
            )
            if st.button(f"启动 {cfg['label']}", key=f"preset_btn_{key}", use_container_width=True):
                st.session_state["_preset_pending"] = key
                st.rerun()

    metric_grid([
        ("输入", "一句话", "分析 X 和 Y 在 Z 维度的差距", ""),
        ("流程", "4 Agent", "Collector → Analyzer → Writer → Reviewer", ""),
        ("质量", "R1-R7", "证据、推理链、结构一致性检查", ""),
        ("输出", "可溯源", "点 chip 跳证据,看原文 snippet 与可信度", ""),
    ])

    section("自定义演示路径", "或者从侧栏自己配,启动 Mock 模式无需 API key")
    st.markdown(
        """
<div class="ca-grid">
  <div class="ca-card">
    <div class="ca-card-title">Step 1</div>
    <div class="ca-card-value" style="font-size:20px">选择行业域</div>
    <div class="ca-card-note">左侧会自动填入目标产品、竞品和分析焦点。</div>
  </div>
  <div class="ca-card">
    <div class="ca-card-title">Step 2</div>
    <div class="ca-card-value" style="font-size:20px">开启 Mock</div>
    <div class="ca-card-note">没有 API key 也能完整跑完图编排和报告展示。</div>
  </div>
  <div class="ca-card">
    <div class="ca-card-title">Step 3</div>
    <div class="ca-card-value" style="font-size:20px">开始分析</div>
    <div class="ca-card-note">实时观察每个 Agent 的状态、打回和质检结果。</div>
  </div>
  <div class="ca-card">
    <div class="ca-card-title">Step 4</div>
    <div class="ca-card-value" style="font-size:20px">展开证据</div>
    <div class="ca-card-note">报告中的证据 chip 会映射到右侧原始 snippet。</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    section("打回闭环演示", "评分维度 1 的关键看点")
    st.markdown(
        """
<div class="ca-card">
  <div class="ca-card-value" style="font-size:22px">Analyzer 首轮故意犯错，Reviewer 发现后打回，第二轮修复通过。</div>
  <div class="ca-card-note">
    勾选“演示打回闭环”后，系统会触发 R1 引用不存在、R5 分数公式冲突、R4 推理链断裂。
    最终事件序列为 collector → analyzer → writer → reviewer → analyzer → writer → reviewer，能清楚展示“发现问题、定位目标、重做变好”的闭环。
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
