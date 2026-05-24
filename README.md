# 竞品分析 Agent 协作系统

> 字节跳动 AI 全栈挑战赛 · Topic 3
> 多 Agent · LangGraph · 豆包 Lite · 设计 v2.2.1

输入「分析 X 和 Y 在 Z 维度的差距」→ 自动产出结构化竞品报告:功能对比 / 用户痛点 / 定价 / SWOT / 优先级建议,**每条结论可溯源到原始 evidence**。

---

## 快速开始

```powershell
# 1. 创建虚拟环境(用任意 Python 3.10+)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3a. CLI 跑一次(Mock 模式,无需 API key)
$env:ANALYZER_MOCK="1"
python -m src.graph
# 产物: out/ai_coding/{report.md, schema_draft.json, quality_report.json}

# 3b. CLI 跑真豆包
$env:ANALYZER_MOCK=""
$env:ARK_API_KEY="ark-xxx"
$env:ARK_EP="ep-xxx"
python -m src.graph

# 3c. 启 Streamlit 前端(推荐演示用)
streamlit run frontend/app.py
# 浏览器打开 http://localhost:8501
```

---

## 切换行业

零代码,改一个环境变量即可。`config/domains.yaml` 已配两个域:

```powershell
$env:DOMAIN="ai_coding"   # Cursor vs Windsurf vs GitHubCopilot
python -m src.graph

$env:DOMAIN="pm"          # Notion vs Asana vs Linear
python -m src.graph
```

新增行业 = 在 `config/domains.yaml` 加 entry + 写一份 `data/sample_sources_<domain>.json`,代码 0 改动。

---

## 架构

```
用户输入 → AgentState 初始化
    ↓
Collector (3 层兜底: live → cache → mock)
    ↓ raw_evidence
Analyzer (两步式)
  ├ Step 1 facts:        feature_tree + pricing_model + user_persona
  └ Step 2 derivations:  swot + recommendations
    ↓ schema_draft
Writer (Markdown + [SXXXXXXX] 溯源 chip)
    ↓ report_draft
Reviewer (R1-R7 规则; minimal 模式默认 hard_gate=R1/R4/R5)
    ↓
  passed   → 输出报告
  running  → 按 reject_target 配额回到 Collector/Analyzer/Writer
             (collector:1, analyzer:2, writer:1; 用完即降级)
  degraded → degraded_writer 分层输出
```

详细设计见 [`docs/design-v2.2.md`](docs/design-v2.2.md)(v2.2.1 已冻结)。

---

## 主要文件

```
config/
  products.yaml          # 产品入口 + URL
  domains.yaml           # 行业域映射 (DOMAIN env → 默认参数 + sample 路径)
data/
  sample_sources.json       # AI 编程域 34 条 evidence
  sample_sources_pm.json    # PM 工具域 30 条 evidence
  sample_report.json        # Analyzer 期望输出 baseline (Mock 模式与单测共用)
prompts/
  analyzer_facts.md         # Step 1 system prompt
  analyzer_derivations.md   # Step 2 system prompt
src/
  state.py        AgentState + per-target retry buckets
  llm.py          ARK/Doubao 客户端 + JSON fence 兜底 + Mock 模式
  collector.py    SourceAdapter + MockAdapter + Registry + 并发 node
  analyzer.py     两步式 LLM 调用 + quick_validate 自修复
  writer.py       Markdown 渲染 + [SXXXXXXX] chip
  reviewer.py     R1-R7 规则 + minimal/full 模式 + degraded_writer
  graph.py        LangGraph 编排 + main 入口 + 流式生成器
frontend/
  app.py          Streamlit 前端 (sidebar 配置 + 3 tabs)
docs/
  design-v2.1.md  设计基线
  design-v2.2.md  当前设计 (v2.2.1 frozen)
```

---

## 关键设计选择

1. **Analyzer 拆两步**:单次调用超 token,facts(事实层)→ derivations(推导层)分别发,防 LLM"为了结论倒推事实"
2. **按 target 分桶 retry**:`{collector:1, analyzer:2, writer:1}`,Collector 重试不补数据所以只给 1 次
3. **Reviewer minimal/full 模式**:Demo 默认 minimal(R1/R4/R5 hard gate),答辩可切 full;R6 LLM 校验仅在结构通过后跑一次
4. **Writer 在 Reviewer 之前**:Markdown 正文**禁止**含 `quality_score`,前端从 `state.quality_report` 单独渲染
5. **`[SXXXXXXX]` chip 格式**:每条 claim 句末追加 evidence_id 标记,前端识别此模式触发溯源跳转(本 demo 用静态 expander 展示)

---

## 环境变量

| 变量 | 用途 | 默认 |
|------|------|------|
| `ANALYZER_MOCK` | =1 跳过真实 LLM,直接返回 sample_report.json | unset |
| `ARK_API_KEY` | 豆包 / ARK API key | unset(非 Mock 必填) |
| `ARK_EP` | 模型 endpoint id | `doubao-seed-2-0-lite` |
| `DOMAIN` | 选行业域 | `ai_coding` |
| `REVIEWER_MODE` | `minimal` / `full` | `minimal` |
| `SAMPLE_SOURCES_PATH` | 直接指定 evidence 文件路径(覆盖 DOMAIN) | unset |
| `ENABLE_LIVE_FETCH` | =1 启用 OfficialPageAdapter 真实抓取 | unset(默认关) |
| `DEMO_LOOP` | =1 配合 Mock 模式,演示 Reviewer 打回闭环 | unset |

---

## 答辩与演示材料

- 📋 [`presentation/demo_script.md`](presentation/demo_script.md) — 5 分钟现场演示脚本(含台词)
- 💬 [`presentation/talking_points.md`](presentation/talking_points.md) — 评委 12 个 Q&A 应答模板
- 📊 [`docs/comparison.md`](docs/comparison.md) — 人工 vs 系统量化对比(38× 效率 + 质变)
- 🛡️ [`docs/compliance.md`](docs/compliance.md) — 数据采集合规 / robots / 隐私
- 🏗️ [`docs/design-v2.2.md`](docs/design-v2.2.md) — 完整架构设计(v2.2.1 frozen)

---

## TRAE 协作开发引导

本项目设计上**预留了多个适合用 TRAE 协作开发**的扩展点。如果你要继续推进:

| 模块 | 推荐用 TRAE 干什么 | 标注方式 |
|------|------------------|---------|
| `src/analyzer.py` | 让 TRAE 帮你迭代 prompt 结构、调整 quick_validate 规则边界 | commit message 加 `[TRAE]` 前缀,如 `[TRAE] tighten facts prompt for pricing extraction` |
| `prompts/analyzer_*.md` | TRAE 配合实测样本调 prompt few-shot | 同上;同时在 prompt 文件头加 `> 最后调整:via TRAE 2026-05-XX` |
| `src/reviewer.py` 规则函数 | TRAE 帮你为新规则写实现 + 单元测试 | 单测放在 `tests/test_reviewer.py`,文件头注释标 `# TRAE-assisted` |
| `frontend/app.py` | TRAE 改 Streamlit 布局、加 chart 可视化 | commit `[TRAE-UI]` 前缀 |
| `data/sample_sources_*.json` | TRAE 帮造新行业 evidence | data 文件 `_meta.generated_by: "TRAE-assisted"` |

**为什么这样做**:评分维度 4 明确要求"TRAE 等 AI 编程工具的使用痕迹清晰",commit message + 文件头注释能让评委 5 秒看到协作证据,不需要现场演示 TRAE 操作。

---

## 当前完成度

```
[██████████] 阶段 A 数据 + Prompt
[██████████] 阶段 B 骨架代码 + 真豆包跑通
[██████████] 跨行业演示(PM 域 30 条 evidence)
[██████████] 阶段 C 前端 Demo(Streamlit + 4 tabs)
[██████████] 阶段 D CacheAdapter + OfficialPageAdapter(fixture 验证)
[██████████] 阶段 E 打回闭环演示(DEMO_LOOP=1)
[██████████] 阶段 F 答辩材料(脚本 + Q&A + 量化对比)
[██████████] 合规说明(robots / UA / 隐私)
```

GitHub: https://github.com/yangyuxin-hub/AI-Competitive-Radar
