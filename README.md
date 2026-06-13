# 🛰️ AI Competitive Radar · 竞品分析 Agent 协作系统

> 字节跳动 AI 全栈挑战赛 · Topic 3
> 多 Agent 协作 · LangGraph 编排 · 结论级证据溯源 · 单一职责控制环（设计 v3 M4）

[![Demo](https://img.shields.io/badge/Demo-Live-green)](https://ai-competitive-radar-web.onrender.com/)
[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**输入一句话「分析 X 和 Y 在 Z 维度的差距」→ 自动产出结构化竞品报告**：功能对比矩阵 / 用户痛点 / 定价策略 / SWOT / 优先级建议——**每一条结论都可一键溯源到原始 evidence**。

🔗 **在线体验**：https://ai-competitive-radar-web.onrender.com/

<p align="center">
  <img src="docs/assets/screenshot-home.png" alt="竞品情报工作台首页" width="85%">
</p>

---

## ✨ 核心特性

| 能力 | 说明 |
|------|------|
| 🤝 **多 Agent 流水线协作** | 规划 → 采集 → 分析（两步式）→ 撰写 → 质检 → 终门修订，LangGraph 编排为**直线有向图**，单一 `AgentState` 全局共享 |
| 🧬 **结构化 Schema 抽取** | 字段冻结的知识 Schema（功能树 / 定价模型 / 用户画像 / SWOT / 建议），跨产品跨行业输出一致 |
| 🛡️ **质检 + 确定性终门** | Reviewer 跑 R0–R10 **只审不修**，输出定位清单；`guard_revise` 据此做一次确定性修订定终态（有修订→passed / 零修订→degraded 分层降级），无打回回流、工程上杜绝死循环 |
| 🔗 **结论级证据溯源** | 每条结论带确定性 hash `evidence_id`，正文以 `[SXXXXXXX]` chip 标注，前端一键跳转原文 |
| 🪂 **三层采集降级** | 抓取 live → cache → mock；搜索 Brave → Tavily → DuckDuckGo；断网 / 无 key 也能跑通闭环 |
| 🧱 **单一职责控制环** | 缺口判定（`evidence_gaps`）/ 采集执行（`evidence_service`）/ 结论强度（`guard`）三大唯一 owner，AST 测试锁边界——Analyzer 零采集 import |
| 🧩 **配置驱动跨行业** | 换行业只改 `config/*.yaml` + `prompts/*.md`，**代码零改动**，已内置 9 个行业域 |
| 🔍 **全链路可观测** | LangSmith + `logs/{agent_trace,llm_calls,stage_quality}.jsonl`，每个节点决策可追溯 |

---

## 📸 界面预览

| 报告页 · 质检徽章 + 业务价值量化 | 证据溯源 · 点 chip 看原文 |
|:---:|:---:|
| ![报告页](docs/assets/screenshot-report.png) | ![证据溯源](docs/assets/screenshot-evidence.png) |
| 综合评级 + 「vs 传统人工」对比表（约快 75×、103 条可溯源证据） | 抽屉展示原文片段、来源 URL、可信度评级 |

| SWOT + 用户痛点 + 调研方法 |
|:---:|
| ![分析深度](docs/assets/screenshot-analysis.png) |
| SWOT 四象限每条带来源 chip；用户痛点附「问卷 + 模拟访谈」调研方法与受访画像 |

---

## 🏗️ 系统架构

### 数据流（直线控制流 · v3 M4）

> 实测 54 run 仅 1 次触发打回——真实修复全在节点内自愈。v3 M4 删掉打回路由 / retry 配额 / degraded_writer 节点，改为**一条直线 + 一次确定性终门修订**：Reviewer 只产出定位清单，`guard_revise` 据此修订定终态。

```mermaid
flowchart TD
    U([用户输入<br/>目标产品 / 竞品 / 维度]) --> S[AgentState 初始化]
    S --> P[🗺️ EvidencePlanner<br/>缺口规划 + 源规划]
    P --> C[📥 Collector<br/>三层降级 live→cache→mock<br/>验收门 + 自愈补采]
    C -->|raw_evidence| A[🧠 Analyzer 两步式<br/>Step1 facts · Step2 derivations<br/>每步 quick_validate · 缺口交 evidence_service]
    A -->|schema_draft| W[✍️ Writer<br/>Markdown + SXXXXXXX 溯源 chip]
    W -->|report_draft| R[🧪 Reviewer<br/>R0–R10 只审不修<br/>输出 reject_target 定位清单]
    R -->|quality_report| G[🛡️ guard_revise<br/>确定性终门<br/>幻觉清理 / G1 强对比对账 / G2 basis 对账]
    G -->|有修订| E([✅ passed · 输出报告 + 质检报告])
    G -->|零修订| Ed([⚠️ degraded · 报告外层包分层说明]) --> E
```

### 分层调用关系

```mermaid
flowchart LR
    subgraph FE[前端 · Next.js]
        P1[输入页] --> P2[Agent 状态页 · SSE] --> P3[报告溯源页]
    end
    subgraph BE[后端 · FastAPI + sse-starlette]
        API["/api/intake · /api/run(SSE) · /api/reports"]
    end
    subgraph ORC[编排层 · LangGraph]
        G[StateGraph 直线图<br/>evidence_planner→collector→analyzer<br/>→writer→reviewer→guard_revise]
    end
    subgraph EXT[外部服务 / 数据]
        LLM[LLM: 豆包 Doubao]
        SE[搜索: Brave→Tavily→DDG]
        FS[(文件存储<br/>cache / logs / reports)]
    end
    FE -->|HTTP/SSE| BE --> ORC --> LLM & SE & FS
```

详细设计见 [`docs/design-v3.md`](docs/design-v3.md)（控制平面重构总纲）+ [`docs/design-v3-draft.md`](docs/design-v3-draft.md)（六角色单一 owner 重切，§六迁移表）+ [`docs/design-v2.2.md`](docs/design-v2.2.md)（四节点业务流基线，含答辩 / 合规 / 质量评测附录）。

---

## 🚀 快速开始

### 方式 1：在线体验（推荐）

直接访问 👉 https://ai-competitive-radar-web.onrender.com/

> ⚠️ 免费实例首次加载可能需要 30-60 秒冷启动

### 方式 2：本地运行

```powershell
# 1. 克隆项目
git clone https://github.com/yangyuxin-hub/AI-Competitive-Radar.git
cd AI-Competitive-Radar

# 2. 创建虚拟环境（Python 3.10+）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装依赖
pip install -r requirements.txt
playwright install chromium  # 采集用

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API key（见下方说明）

# 5a. CLI 模式（Mock，无需 API key）
$env:ANALYZER_MOCK="1"
python -m src.graph
# 产物：out/ai_coding/{report.md, schema_draft.json, quality_report.json}

# 5b. CLI 模式（真实 LLM）
$env:ARK_API_KEY="你的火山 key"
$env:ARK_EP="ep-xxx"
python -m src.graph

# 5c. Web 工作台模式（推荐演示）
# 终端 1：后端 API
.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
# 终端 2：前端
cd web ; npm install ; npm run dev
# 浏览器打开 http://localhost:3000
```

---

## 🔀 切换行业（零代码）

`config/domains.yaml` 已内置 **9 个行业域**（`ai_coding` / `pm` / `ai_assistant` / `design` / `baas` / `ai_image` / `ai_video` / `ai_music` / `ai_search`），改一个环境变量即可切换：

```powershell
$env:DOMAIN="ai_coding"   # Cursor vs Windsurf vs GitHubCopilot
python -m src.graph

$env:DOMAIN="pm"          # Notion vs Asana vs Linear
python -m src.graph
```

新增行业 = 在 `config/domains.yaml` 加一个 entry + 写一份 `data/sample_sources_<domain>.json`，**代码 0 改动**。

---

## 🔌 后端 API 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/intake/propose` | POST | 意图解析 → 分析配置草稿（`fast=true` 跳 LLM 秒回） |
| `/api/intake/questions` | POST | 生成澄清追问 |
| `/api/intake/stream` | POST | SSE 流式意图解析 |
| `/api/run` | POST | **主流程**，SSE 流式推送四节点进度与最终报告 |
| `/api/reports` · `/api/reports/{id}` | GET | 报告列表 / 详情 |
| `/api/stage_quality` | GET | 各环节质量评测聚合 |
| `/api/timeline` | GET | 节点时间线（耗时 / token / StageReport）|
| `/api/checklist` | GET | Reviewer 定位清单（issue → reject_target 归因）|

---

## 🧠 大模型 / AI 能力

- **主模型**：火山豆包 Doubao（`doubao-seed-2-0-lite`，OpenAI 兼容协议）。
- **调用封装**：`src/llm.py` —— `trust_env=False` 关系统代理、timeout 200s、`max_retries=1`、JSON fence 兜底、Mock 模式。
- **Prompt 工程**：强约束模板放 `prompts/*.md`，与代码解耦（`analyzer_facts.md` / `analyzer_derivations.md` / `intake.md` / `url_discovery.md` / `source_discovery.md`）。
- **AI 在系统中的位置**：Collector（URL 发现 / 意图解析）、Analyzer（两步式事实抽取 + 推导）、Reviewer（R6 语义审查，闭包注入 LLM，不入全局状态）。
- **证据链 vs RAG**：当前用确定性 hash 证据链 + Schema 抽取实现溯源（比向量召回更可复现、零幻觉编号）；向量召回为 roadmap 候选，设计见 [`docs/rag-recall-design.md`](docs/rag-recall-design.md)。

---

## 🌐 部署到服务器

后端是标准 FastAPI ASGI 应用，可直接部署：

```bash
# 生产启动（绑 0.0.0.0，去掉 --reload）
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**部署要点（务必注意）**：

1. **不要上 Serverless**。主流程 `/api/run` 是 SSE 长连接、整轮约 3 分钟（analyzer ~130s），需长驻进程环境（云 VM / Render / Railway / Fly / Docker）。
2. **反向代理关 SSE 缓冲**。Nginx 需 `proxy_buffering off;` + `proxy_read_timeout 300s;`，否则进度流被攒着不下发。
3. **挂可写持久卷**。报告 / 缓存 / 日志存本地文件（`out/`、`data/cache/`、`logs/`），容器部署需持久卷，否则重启丢数据。
4. **公网前加鉴权**。当前无内置鉴权，公开前建议加 API Key / 网关。
5. **CORS**：生产设 `API_CORS_ORIGINS=https://你的前端域名`（不设则放行 localhost）。

---

## ⚙️ 环境变量

| 变量 | 用途 | 默认 |
|------|------|------|
| `ANALYZER_MOCK` | =1 跳过真实 LLM，返回 sample_report.json | unset |
| `ARK_API_KEY` | 火山豆包 API key | unset（非 Mock 必填） |
| `ARK_EP` | 模型 endpoint id（如 `ep-xxx`） | unset（非 Mock 必填） |
| `LLM_API_KEY` | LLM API key（优先级更高） | unset |
| `LLM_MODEL` | 模型 id | `doubao-seed-2-0-lite-250428` |
| `LLM_BASE_URL` | API 地址 | `https://ark.cn-beijing.volces.com/api/v3` |
| `DOMAIN` | 选行业域 | `ai_coding` |
| `REVIEWER_MODE` | `minimal` / `full` | `minimal` |
| `API_CORS_ORIGINS` | 生产前端域名（逗号分隔） | unset（放行 localhost） |
| `BRAVE_API_KEY` / `TAVILY_API_KEY` | 搜索 key（不给走 DDG 免费兜底） | unset |
| `ENABLE_LIVE_FETCH` | =1 启用 OfficialPageAdapter 真实抓取 | unset |

---

## 📂 目录结构

```
AI-Competitive-Radar/
├── config/
│   ├── products.yaml          # 产品别名 + official_pages + pricing_pages
│   ├── domains.yaml           # 行业域映射（DOMAIN env → 默认参数 + sample 路径）
│   ├── scoring.yaml           # 统一评分配置（权重 / 阈值 / 可靠度 / TTL）
│   └── sources.yaml / quality_rubric.yaml / business_value.yaml
├── src/
│   ├── state.py                # AgentState + per-target retry buckets
│   ├── llm.py                  # Doubao 客户端 + JSON fence 兜底 + Mock
│   ├── collector.py            # collector_node + 验收门补采
│   ├── collector_common.py     # 叶子 helper / 常量 / URL discovery
│   ├── collector_adapters.py   # OfficialPage/Search/Mock/Cache 四适配器
│   ├── analyzer.py             # 两步式 pipeline + quick_validate（零采集 import，AST 锁边界）
│   ├── analyzer_common.py / _sanitize.py / _fallback.py / _augment.py
│   ├── evidence_plan.py        # 🆕 evidence_planner_node — 缺口规划 + 源规划（入口节点）
│   ├── evidence_gaps.py        # 🆕 缺口判定唯一 owner — find_gaps → Gap + 池内回捞
│   ├── evidence_service.py     # 🆕 采集执行唯一 owner — fill() 回捞优先 → 定向外搜
│   ├── guard.py                # 🆕 结论强度唯一 owner — guard_revise 终门 + G1/G2 对账
│   ├── writer.py               # Markdown 渲染 + [SXXXXXXX] chip
│   ├── reviewer.py             # R0–R10 规则（只审不修，输出 reject_target 定位清单）
│   ├── graph.py                # LangGraph 直线编排 + main 入口
│   ├── search.py               # 多供应商搜索降级 + 磁盘缓存
│   └── progress.py             # 每节点独立 ProgressChannel
├── api/
│   └── main.py                 # FastAPI: intake / run(SSE) / reports 端点
├── web/                        # Next.js 前端工作台
│   ├── app/ components/ lib/   # 意图澄清 + Agent 状态 + 报告溯源
├── data/
│   ├── sample_sources.json     # AI 编程域 evidence（Mock 兜底）
│   ├── sample_sources_pm.json  # PM 工具域 evidence
│   └── sample_report.json      # Analyzer baseline
├── prompts/                    # Analyzer / intake / discovery 强约束 Prompt
├── tests/                      # 36 个测试文件，259 用例
├── docs/
│   ├── design-v3.md            # 控制平面重构总纲（v3）
│   ├── design-v3-draft.md      # 六角色单一 owner 重切（§六迁移表）
│   ├── design-v2.2.md          # 四节点业务流基线
│   └── assets/                 # README 截图素材
├── requirements.txt            # Python 依赖
├── .env.example                # 环境变量示例
├── SETUP.md                    # 详细配置指南
└── DEPLOY.md                   # 部署指南
```

---

## 🎛️ 关键设计选择

1. **Analyzer 拆两步**：单次调用易超 token，facts（事实层）→ derivations（推导层）分发，防 LLM「为结论倒推事实」。
2. **直线控制流 + 确定性终门**：实测 54 run 仅 1 次触发打回，v3 M4 删掉打回路由 / retry 配额 / degraded_writer 节点；Reviewer 只产出定位清单，`guard_revise` 做一次确定性修订定终态（有修订→passed / 零修订→degraded），无回流、零死循环。
3. **单一职责 owner**：缺口判定（`evidence_gaps`）/ 采集执行（`evidence_service`）/ 结论强度（`guard`）各有唯一 owner，AST 回归测试锁死边界——Analyzer 零采集 import，Reviewer 只审不修。
4. **Reviewer minimal/full 双模式**：Demo 默认 minimal（R1/R4/R5 hard gate），答辩可切 full；R6 LLM 校验仅在结构通过后跑一次。
5. **Writer 在 Reviewer 之前**：Markdown 正文**禁止**含 `quality_score`，前端从 `state.quality_report` 单独渲染徽章（R10 自检）。
6. **`[SXXXXXXX]` chip + 确定性 evidence_id**：`"S"+sha1(...)[:7].upper()`（不用 uuid），同输入可复现；R9 自检 chip 是否引用真实 ID。

---

## 🧪 测试

```powershell
pytest -q          # 36 个测试文件 / 259 用例，覆盖 collector / analyzer / reviewer / guard / evidence_service / scoring / search / writer
```

---

## 📊 当前完成度

```
[██████████] 数据 + Prompt（Schema v2.1 冻结 + 多行业 evidence）
[██████████] 多 Agent 骨架 + 真实 LLM 跑通
[██████████] 跨行业演示（9 个行业域，配置零代码切换）
[██████████] 前端工作台（Next.js：意图澄清 + Agent 状态 + 报告溯源）
[██████████] 三层采集降级 + 缓存兜底 + 多供应商搜索
[██████████] v3 直线控制流 + guard 确定性终门 + 分层降级
[██████████] 单一 owner 重切（evidence_gaps / evidence_service / guard，AST 锁边界）
[██████████] scoring 配置化 + 源质量台账 + 阶段质量评测
[██████████] 答辩材料 + 合规说明
```

**完成度定位**：可用 Demo（端到端闭环跑通，259 项测试全过）。

---

## 📋 答辩与演示材料

- 📋 [`presentation/demo_script.md`](presentation/demo_script.md) — 现场演示脚本（含台词）
- 💬 [`presentation/talking_points.md`](presentation/talking_points.md) — 评委 Q&A 应答模板
- 🧭 [`docs/project-overview-diagrams.md`](docs/project-overview-diagrams.md) — 项目介绍与图设计（答辩 / README / 路演页复用）
- 🏗️ [`docs/design-v2.2.md`](docs/design-v2.2.md) — 完整架构设计（v2.2.1 frozen）
- 📖 [`docs/competitive-analysis-playbook.md`](docs/competitive-analysis-playbook.md) — 竞品分析方法论

---

## 🛡️ 合规声明

- 信息采集遵守目标站点 robots.txt 与服务条款，数据来源均为公开信息，并标注 source_bias。
- 用户访谈 / 问卷数据已脱敏（含合成访谈兜底，不含真实个人敏感信息）。
- LLM API key 等密钥运行时注入、不 commit；工具与资源使用符合挑战赛规范。
- 未使用任何受版权保护的非授权内容。

---

## 👤 作者

**杨雨欣**（GitHub [@yangyuxin-hub](https://github.com/yangyuxin-hub)）— 独立完成全栈开发（架构设计 / 多 Agent 编排 / 采集与分析 Agent / 质检规则 / Next.js 前端 / FastAPI 后端 / Prompt 工程）。

🔗 GitHub: https://github.com/yangyuxin-hub/AI-Competitive-Radar
