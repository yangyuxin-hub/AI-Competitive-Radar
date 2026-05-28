# Streamlit 到 React 工作台前端改造方案

> 目标：把当前 Streamlit Demo 前端迁移为 `React + Vite + TypeScript` 工作台，并通过 `FastAPI + SSE` 接入现有 LangGraph Agent。v1 面向比赛 Demo：单用户、本地运行、强调类 ChatGPT Research 的对话式研究体验、实时进度和证据溯源。

---

## 1. 当前现状与迁移原因

当前前端集中在 `frontend/app.py`，已经实现了：

- ChatGPT 风格入口：自然语言输入竞品分析需求。
- Intake 追问：目标产品、竞品、分析焦点、分析目的。
- LangGraph 流式执行：通过 `run_demo_streaming(...)` 展示 Collector / Analyzer / Writer / Reviewer 节点进度。
- 报告工作台：Markdown 报告、证据列表、质检结果、结构化 JSON。
- 基础继续对话：支持下载、查看证据、查看质量等提示，但尚未真正支持“按用户反馈改写报告”。

迁移原因：

- Streamlit 适合快速 Demo，但复杂交互、报告版本管理、证据抽屉、SSE 流式事件、组件状态拆分会逐渐变重。
- React 更适合做类 ChatGPT 的双栏工作台：左侧对话、右侧研究过程与报告文档。
- FastAPI 可以把现有 Python Agent 能力服务化，前端不再直接耦合 Python session state。
- SSE 能直接承接现有 `run_demo_streaming(...)`、Analyzer progress callback、LLM callback。

保留策略：

- `frontend/app.py` 暂时保留为 legacy fallback，不在本次迁移中删除。
- Agent 主逻辑仍在 `src/collector.py`、`src/analyzer.py`、`src/writer.py`、`src/reviewer.py`、`src/graph.py`，前端改造不重写核心 Agent。

---

## 2. 目标体验

v1 目标是做一个类 ChatGPT Research 的竞品分析工作台：

1. 用户在首页输入自然语言需求，例如“分析 Cursor 和 Windsurf、GitHub Copilot 在代码补全体验上的差距”。
2. 系统先理解需求：
   - 信息足够：直接开始研究。
   - 信息不足：在对话流中追问目标产品、竞品、分析焦点、分析目的。
3. 用户确认后启动 Agent：
   - Collector 收集证据。
   - Analyzer 生成 facts 和 derivations。
   - Writer 生成 Markdown。
   - Reviewer 做规则质检，必要时按 target 打回。
4. 前端实时显示节点进度、重试、打回目标、LLM token、质量摘要。
5. 生成报告后，右侧展示：
   - 报告正文。
   - 证据 chip 跳转。
   - 质检面板。
   - 结构化 JSON。
6. 用户继续对话：
   - 可问“这条结论证据在哪”。
   - 可问“为什么质量分不高”。
   - 可要求“把报告改成更适合答辩展示的版本”。
   - 若用户要求新增竞品、改变分析范围、补充新证据，则重新进入 intake 和研究流程。

---

## 3. 技术选型

### 前端

- `React + Vite + TypeScript`
- 状态管理：`zustand`
- Markdown 渲染：`react-markdown + remark-gfm + rehype-sanitize`
- 图标：`lucide-react`
- 样式：普通 CSS modules 或全局 CSS tokens，v1 不引入重型 UI 框架。
- 测试：`vitest`、`@testing-library/react`、`playwright`

不采用 React + Vue 混用。v1 采用单一 React 技术栈，降低比赛 Demo 实现风险。

### 后端

- `FastAPI`
- `uvicorn`
- SSE：原生 `StreamingResponse`，事件格式遵循 `event: xxx\ndata: {...}\n\n`
- 继续复用现有：
  - `src.intake.intake_questions`
  - `src.intake.assemble_meta`
  - `src.graph.run_demo_streaming`
  - `src.analyzer.set_progress_callback`
  - `src.llm.set_llm_callback`
  - `src.collector.reset_registry`

新增依赖：

```text
fastapi>=0.115
uvicorn[standard]>=0.30
```

---

## 4. 后端 API 设计

新增文件建议：

```text
src/web_api.py
```

### 4.1 Health

`GET /api/health`

返回：

```json
{
  "ok": true,
  "service": "competitive-radar-api",
  "domains": ["ai_coding", "pm"],
  "default_domain": "ai_coding"
}
```

### 4.2 Domains

`GET /api/domains`

返回 `config/domains.yaml` 和必要产品配置：

```json
{
  "domains": {
    "ai_coding": {
      "name": "AI 编程工具",
      "target_product": "Cursor",
      "competitors": ["Windsurf", "GitHubCopilot"],
      "analysis_focus": ["代码补全体验"],
      "analysis_purpose": "学习竞品优点,优化 Cursor 的产品策略"
    }
  },
  "products": ["Cursor", "Windsurf", "GitHubCopilot", "Notion", "Asana", "Linear"]
}
```

### 4.3 Intake

`POST /api/intake`

请求：

```json
{
  "message": "分析 Cursor 和 Windsurf、GitHub Copilot 在代码补全体验上的差距",
  "domain_key": "ai_coding"
}
```

返回：

```json
{
  "mode": "direct",
  "meta": {
    "target_product": "Cursor",
    "competitors": ["Windsurf", "GitHubCopilot"],
    "analysis_focus": ["代码补全体验"],
    "analysis_purpose": "学习竞品优点,优化 Cursor 的产品策略",
    "user_input": "分析 Cursor 和 Windsurf、GitHub Copilot 在代码补全体验上的差距"
  },
  "questions": []
}
```

信息不足时：

```json
{
  "mode": "clarify",
  "meta": null,
  "questions": [
    {
      "key": "target",
      "question": "要分析的目标产品是哪一个?",
      "options": ["Cursor", "Windsurf", "GitHubCopilot"],
      "multi": false,
      "suggested": ["Cursor"],
      "allow_custom": true
    }
  ]
}
```

### 4.4 Create Run

`POST /api/runs`

请求：

```json
{
  "meta": {
    "target_product": "Cursor",
    "competitors": ["Windsurf", "GitHubCopilot"],
    "analysis_focus": ["代码补全体验"],
    "analysis_purpose": "学习竞品优点,优化 Cursor 的产品策略",
    "user_input": "分析 Cursor 和 Windsurf、GitHub Copilot 在代码补全体验上的差距"
  },
  "settings": {
    "domain_key": "ai_coding",
    "reviewer_mode": "minimal",
    "use_mock": true,
    "enable_live": false,
    "demo_loop": false
  }
}
```

返回：

```json
{
  "run_id": "run_20260528_120001",
  "status": "queued"
}
```

v1 是单用户本地 Demo，可以用内存字典保存 run 状态。为避免全局环境变量冲突，同一时间只允许一个 active run；如果已有 run 正在执行，返回 `409 Conflict`。

### 4.5 Run Events

`GET /api/runs/{run_id}/events`

SSE 事件：

```text
event: run_started
data: {"run_id":"run_20260528_120001","meta":{...}}

event: analyzer_step
data: {"step":"facts","phase":"start","attempt":1}

event: llm_usage
data: {"label":"analyzer_facts","phase":"done","duration":8.2,"prompt_tokens":1200,"completion_tokens":900}

event: node_completed
data: {"node":"collector","status":"running","duration":2.4,"retry_count":{"collector":0,"analyzer":0,"writer":0},"summary":{"evidence_count":34}}

event: final
data: {"run_id":"run_20260528_120001","status":"passed","quality_score":92}
```

最终产物不建议完整塞进 SSE `final`。`final` 只发摘要，前端随后调用 `GET /api/runs/{run_id}` 获取完整状态。

### 4.6 Get Run

`GET /api/runs/{run_id}`

返回：

```json
{
  "run_id": "run_20260528_120001",
  "status": "passed",
  "meta": {},
  "final_state": {
    "raw_evidence": [],
    "schema_draft": {},
    "report_draft": "...",
    "quality_report": {},
    "collection_meta": {},
    "retry_count": {"collector":0,"analyzer":0,"writer":0}
  },
  "events": []
}
```

### 4.7 Follow-up

`POST /api/runs/{run_id}/followups`

请求：

```json
{
  "message": "把这份报告改成更适合答辩展示的版本，压缩到 5 分钟讲稿结构"
}
```

返回类型：

```json
{
  "action": "answer",
  "message": "右侧证据页可以查看所有 chip 对应的原始片段。"
}
```

```json
{
  "action": "revision",
  "message": "已生成答辩版报告。",
  "version": 2,
  "report_md": "..."
}
```

```json
{
  "action": "new_intake",
  "message": "这个请求改变了分析范围，需要重新确认研究需求。",
  "intake_seed": "..."
}
```

---

## 5. React 页面与组件拆分

建议新增目录：

```text
web/
  package.json
  vite.config.ts
  index.html
  src/
    main.tsx
    App.tsx
    types.ts
    api/
      client.ts
      sse.ts
    store/
      workspaceStore.ts
    components/
      layout/
        Sidebar.tsx
        AppHeader.tsx
      chat/
        ChatPanel.tsx
        PromptBox.tsx
        ClarificationForm.tsx
        MessageList.tsx
      research/
        AgentTimeline.tsx
        RunStatus.tsx
        NodeCard.tsx
      report/
        ReportWorkspace.tsx
        ReportViewer.tsx
        EvidenceDrawer.tsx
        QualityPanel.tsx
        SchemaPanel.tsx
        DownloadBar.tsx
        ReportVersionTabs.tsx
    styles/
      tokens.css
      app.css
```

核心状态：

```ts
type WorkspaceMode = "home" | "clarifying" | "running" | "ready" | "error";

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
};

type RunEvent = {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  timestamp: string;
};

type ReportVersion = {
  version: number;
  title: string;
  reportMd: string;
  createdAt: string;
};
```

组件职责：

- `Sidebar`：新建对话、最近报告占位、默认行业、运行模式、Reviewer 模式。
- `ChatPanel`：消息流、追问表单、输入框、继续对话。
- `ClarificationForm`：根据 `/api/intake` 返回的 `questions` 渲染单选、多选、自定义输入。
- `AgentTimeline`：渲染 Collector / Analyzer / Writer / Reviewer 节点状态。
- `ReportWorkspace`：右侧主区域，根据 mode 切换空态、运行态、报告态。
- `ReportViewer`：Markdown 渲染和 evidence chip 解析。
- `EvidenceDrawer`：点击 `[SXXXXXXX]` 后显示 evidence 原文、source_url、claim_type、confidence。
- `QualityPanel`：渲染 `quality_report`，包括 R1-R7、warnings、errors、module_status。
- `SchemaPanel`：只读 JSON viewer。
- `ReportVersionTabs`：显示原始报告和用户反馈改写版本。

---

## 6. SSE 事件模型

后端将现有回调统一转换为前端事件：

| 来源 | 后端来源 | SSE event | 前端用途 |
|------|----------|-----------|----------|
| 运行开始 | `POST /api/runs` 后启动 | `run_started` | 设置 mode=running |
| Analyzer | `set_progress_callback` | `analyzer_step` | 显示 facts/derivations 进度 |
| LLM | `set_llm_callback` | `llm_usage` | 显示 token 和耗时 |
| LangGraph 节点 | `run_demo_streaming` | `node_completed` | 更新节点卡片、重试、打回 |
| 运行完成 | graph stream 结束 | `final` | 拉取完整 final_state |
| 异常 | try/except | `error` | 展示错误并允许重新运行 |

前端处理规则：

- SSE 只追加事件和更新摘要状态，不直接承担完整产物传输。
- `final` 后调用 `GET /api/runs/{run_id}` 获取 `final_state`。
- 断线时展示错误，v1 不做自动断点续传。

---

## 7. 报告、证据、质检与继续修改

### 7.1 报告渲染

Markdown 渲染必须支持 GitHub 表格。`[SXXXXXXX]` chip 需要被替换为可点击组件。

规则：

- chip 正则：`\[(S[0-9A-F]{7})\]`
- chip 只负责定位 evidence，不改写原 Markdown 数据。
- 不存在于 `raw_evidence` 的 chip 标红，提示 Reviewer R1 风险。

### 7.2 证据抽屉

点击 chip 打开右侧 evidence drawer，展示：

- `evidence_id`
- `product`
- `claim_type`
- `claim`
- `extracted_snippet`
- `source_url`
- `source_bias`
- `collection_source`
- `source_reliability`
- `claim_relevance`
- `evidence_confidence`
- `observed_at`

### 7.3 质检面板

`quality_report` 不写入 Markdown 正文，由前端单独渲染：

- 质量分。
- Reviewer 模式。
- passed / warning / failed rules。
- errors / warnings 列表。
- module_status。
- retry_count。

### 7.4 继续修改报告

新增 `prompts/report_revision.md`，用于报告改写。约束：

- 只能基于当前 `report_draft`、`schema_draft`、`raw_evidence`、`quality_report`。
- 禁止新增 evidence_id。
- 禁止把未知事实写成确定结论。
- 改写后后端必须校验所有 chip 都存在于 `raw_evidence`。
- 用户要求新增竞品、改变分析维度、补充新资料时，不执行 revision，返回 `new_intake`。

v1 可先支持三类意图：

- `answer`：解释证据、质量、下载、运行状态。
- `revision`：改变表达、结构、篇幅、面向答辩/老板汇报。
- `new_intake`：改变研究对象或研究范围。

---

## 8. 分阶段实施路线

### Phase 1：API 壳与 React 壳

- 新增 FastAPI `src/web_api.py`。
- 新增 React Vite 项目 `web/`。
- 打通 `GET /api/health`、`GET /api/domains`。
- 实现左侧 sidebar、首页输入框、基础视觉框架。

验收：

- 后端可启动。
- 前端可启动。
- 首页可读取 domain 配置。

### Phase 2：Intake 与追问

- 实现 `POST /api/intake`。
- 迁移 Streamlit 中 `direct_meta_from_prompt` 逻辑到后端 API。
- React 渲染 clarification form。
- 用户确认后创建 run。

验收：

- 清晰需求直接进入运行。
- 模糊需求触发追问。
- 自定义目标产品/竞品可组装为 meta。

### Phase 3：SSE 运行进度

- 实现 `POST /api/runs`。
- 实现 `GET /api/runs/{run_id}/events`。
- 后端桥接 Analyzer callback、LLM callback、LangGraph stream。
- React `AgentTimeline` 实时更新节点状态。

验收：

- Mock 模式可以跑完整流程。
- 前端可看到 Collector / Analyzer / Writer / Reviewer 进度。
- Reviewer 打回时能显示 reject target 和 retry_count。

### Phase 4：报告工作台

- 实现 `GET /api/runs/{run_id}`。
- React 渲染报告、证据、质检、结构化 JSON。
- 实现 chip 点击打开 evidence drawer。
- 实现下载 `report.md`、`quality_report.json`、`schema_draft.json`。

验收：

- 报告 Markdown 表格正常。
- `[SXXXXXXX]` chip 可点击。
- 质检分数来自 `quality_report`，不依赖 Markdown 正文。

### Phase 5：继续对话与报告改写

- 实现 `POST /api/runs/{run_id}/followups`。
- 新增 `prompts/report_revision.md`。
- React 支持报告版本 tabs。
- 对修改后的报告执行 chip 校验。

验收：

- “更简洁”“更适合答辩”“突出建议”生成新版本。
- “新增一个竞品”“换成 PM 工具”进入新 intake。
- 改写版本不引入不存在的 evidence_id。

---

## 9. 测试计划

### 后端测试

- `test_api_intake_direct`：清晰需求返回 `mode=direct`。
- `test_api_intake_clarify`：模糊需求返回 `mode=clarify` 和 questions。
- `test_api_run_mock_sse`：Mock run 输出 `node_completed` 和 `final`。
- `test_api_single_active_run_lock`：已有 active run 时新 run 返回 409。
- `test_followup_revision_chip_validation`：改写报告不能新增不存在的 evidence_id。

### 前端测试

- chip parser：能识别、去重、定位 `[SXXXXXXX]`。
- SSE reducer：能按事件更新节点状态。
- clarification form：单选、多选、自定义输入组装正确。
- report version reducer：原始版本和改写版本不会互相覆盖。

### E2E 测试

- 首页输入 Cursor 代码补全需求。
- Mock 模式完整跑完。
- 报告页出现标题、表格、chip。
- 点击 chip 打开 evidence drawer。
- 质检页展示 quality score、warnings、errors。
- 继续输入“改成 5 分钟答辩版”，生成 version 2。

---

## 10. 验收标准

功能验收：

- 用户无需接触 YAML 或环境变量即可完成一次竞品分析。
- 需求不明确时，系统会先追问再研究。
- Agent 运行过程可视化，能看到节点、耗时、重试、打回。
- 报告每条 chip 都能跳到真实 evidence。
- 质量分、规则状态、warnings/errors 单独展示。
- 用户可继续对话修改报告表达，或发起新研究。

工程验收：

- Streamlit legacy 前端未被破坏。
- React 前端和 FastAPI 后端可分别启动。
- API key 不进入前端代码、localStorage 或请求 body。
- Mock 模式无 API key 可端到端跑通。
- 关键 API 和前端 reducer 有测试覆盖。

---

## 11. 明确假设

- v1 是单用户本地比赛 Demo，不做登录、数据库、多租户、权限和任务队列。
- v1 不混用 Vue，采用单一 React 技术栈。
- API key 只由后端环境变量读取，前端不提供密钥输入框。
- SSE 断线后 v1 不做自动恢复，只提示用户重新运行。
- 报告改写只改表达和结构，不改变事实来源；改变研究范围必须重新运行 Agent。
