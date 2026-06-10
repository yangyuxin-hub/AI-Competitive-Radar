# 🛰️ AI Competitive Radar · 竞品分析 Agent 协作系统

> 字节跳动 AI 全栈挑战赛 · Topic 3
> 多 Agent 协作 · LangGraph 编排 · 结论级证据溯源

**输入一句话「分析 X 和 Y 在 Z 维度的差距」→ 自动产出结构化竞品报告**：功能对比矩阵 / 用户痛点 / 定价策略 / SWOT / 优先级建议——**每一条结论都可一键溯源到原始 evidence**。

---

## ✨ 核心特性

| 能力 | 说明 |
|------|------|
| 🤝 **四 Agent 流水线** | 采集 → 分析（两步式）→ 撰写 → 质检，LangGraph 编排 |
| 🔗 **结论级证据溯源** | 每条结论带 `[SXXXXXXX]` chip，前端一键跳转原文 |
| 🔁 **质检反馈闭环** | Reviewer 跑 R0–R10 规则，不合格打回，最多 2 轮 |
| 🪂 **三层采集降级** | live → cache → mock；Brave → Tavily → DuckDuckGo |
| 🧩 **配置驱动跨行业** | 换行业只改 `config/*.yaml`，代码零改动 |

---

## 🏗️ 系统架构

```mermaid
flowchart TD
    U([用户输入]) --> S[AgentState]
    S --> C[📥 Collector]
    C --> A[🧠 Analyzer 两步式]
    A --> W[✍️ Writer]
    W --> R{🧪 Reviewer}
    R -->|passed| E([✅ 输出报告])
    R -->|degraded| D[⚠️ 降级输出] --> E
    R -.->|打回| C & A & W
```

---

## 📖 详细配置指南

👉 **[SETUP.md](SETUP.md)** — 完整的环境配置步骤，让项目能直接跑起来

---

## 🚀 快速开始

```powershell
# 1. 创建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3a. Mock 模式（无需 API key）
$env:ANALYZER_MOCK="1"
python -m src.graph

# 3b. 真实 LLM（豆包 Doubao）
$env:LLM_API_KEY="你的 key"
$env:LLM_MODEL="ep-xxx"
$env:LLM_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
python -m src.graph

# 3c. 启动前端工作台
# 终端 1：后端
.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
# 终端 2：前端
cd web ; npm install ; npm run dev
# 浏览器打开 http://localhost:3000
```

---

## 🌐 部署

```bash
# 生产启动
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**要点**：
1. **不要上 Serverless** — SSE 长连接需长驻进程（Render / Railway / Docker）
2. **反向代理关 SSE 缓冲** — Nginx 需 `proxy_buffering off;`
3. **CORS** — 设 `API_CORS_ORIGINS=https://你的前端域名`

---

## ⚙️ 环境变量

| 变量 | 用途 | 默认 |
|------|------|------|
| `LLM_API_KEY` | LLM API key | unset（非 Mock 必填） |
| `LLM_MODEL` | 模型 id | `ep-xxx`（豆包 Doubao） |
| `LLM_BASE_URL` | API 地址 | `https://ark.cn-beijing.volces.com/api/v3` |
| `DOMAIN` | 选行业域 | `ai_coding` |
| `API_CORS_ORIGINS` | 生产前端域名 | unset |

---

## 📂 目录结构

```
src/           # 四 Agent + 编排 + 搜索 + 质检
api/           # FastAPI 后端
web/           # Next.js 前端
config/        # 产品/行业/评分配置
prompts/       # Analyzer 强约束 Prompt
data/          # Mock 数据 + 缓存
tests/         # 201 个测试用例
```

---

## 👤 作者

**杨雨欣** — GitHub [@yangyuxin-hub](https://github.com/yangyuxin-hub)

🔗 https://github.com/yangyuxin-hub/AI-Competitive-Radar
