# 报告质量改进路线图

> 目标:把当前“能跑通、能溯源”的 Demo 报告,升级成“边界清楚、竞争逻辑明确、建议可落地”的产品研究报告。

---

## 1. 当前诊断

当前项目已经做得比较好的部分:

- **证据链**:报告中的结论带 `[SXXXXXXX]` chip,能回到 `raw_evidence`。
- **结构化输出**:Analyzer 输出 `feature_tree / pricing_model / user_persona / swot / recommendations`。
- **机械质检**:Reviewer 已有 R1-R7,能检查 evidence_id、推理链、公式冲突、时效等。
- **打回闭环**:Reviewer 可以按 `reject_target` 打回 Collector / Analyzer / Writer。

当前主要短板:

- **分析边界没有显式展示**:报告没有说明“代码补全体验”包含什么、不包含什么,导致 F002 “Agent / 端到端任务执行”容易跑偏。
- **竞品范围偏窄**:Demo 只覆盖 Cursor / Windsurf / GitHubCopilot,缺少 Supermaven、JetBrains AI、Tabnine 这类“不同竞争逻辑”的对手。
- **证据类型有 warning**:当前 `quality_report` 有 R2 warning,说明部分质量评分引用了 `feature_existence` 证据。
- **事实和推断没有分层展示**:报告把官方事实、用户反馈、分析推断混在连续段落里,评委追问时解释成本高。
- **竞争逻辑不够突出**:报告偏功能差距,还没充分表达“Cursor 抢 AI 原生开发入口、Copilot 抢分发、Tabnine 抢企业安全采购”。
- **建议缺少落地字段**:recommendation 有优先级,但缺少收益、风险、验收指标、时间线。

---

## 2. 推荐改法总览

| 优先级 | 改动 | 目标 | 主要文件 |
|--------|------|------|----------|
| P0 | 增加“分析边界 / 竞品分层 / 核心结论”报告区块 | 让报告先回答“这次分析什么、为什么这些竞品重要” | `src/writer.py`, `prompts/*`, `data/sample_report.json` |
| P0 | 修复 R2 warning | full 模式不翻车,证据类型更干净 | `data/sample_report.json`, `prompts/analyzer_facts.md` |
| P1 | 增加报告质量 Rubric | Reviewer 不只看格式,也看分析质量 | `src/reviewer.py`, `frontend/app.py` |
| P1 | 扩展 AI coding 竞品池 | 从三产品功能对比升级为竞品分层分析 | `config/products.yaml`, `data/sample_sources.json` |
| P1 | Recommendation 增加落地字段 | 建议从口号变成可执行 action plan | schema / prompts / writer |
| P2 | 引入 Planner 节点 | 先生成假设和信息缺口,再收集证据 | `src/graph.py`, `src/planner.py`, `src/state.py` |
| P2 | 加入反证检查 | 防止报告只为 target 背书 | `src/reviewer.py`, `prompts/analyzer_derivations.md` |

---

## 3. P0:先改报告结构

当前报告从“功能差距”开始,缺少高层 framing。建议 Writer 输出顺序改成:

```text
0. 核心结论
1. 分析边界
2. 竞品分层 / 竞争地图
3. 功能差距
4. 定价对比
5. 用户画像与痛点
6. 竞争逻辑
7. 改进建议
8. SWOT
```

### 3.1 核心结论

示例:

```text
Cursor 的代码补全优势不只是“补下一行代码”,而是 AI 原生编辑流:
通过 Tab、上下文索引、多文件编辑与 Agent 工作流,把补全升级为 predictive editing。
```

### 3.2 分析边界

必须写清楚:

- 本次分析包含:即时补全、Tab 触发、上下文理解、跨文件补全、补全延迟、下一处修改预测、接受/拒绝体验、企业代码库适配。
- 本次分析不包含:完整 Agent 自动执行能力、通用 Chat 能力、非编码场景。

这样可以避免“代码补全体验”被 Agent 能力稀释。

### 3.3 竞品分层

不仅列竞品,还要说明竞争关系:

| 类型 | 产品 | 竞争逻辑 |
|------|------|----------|
| AI 原生编辑器 | Cursor / Windsurf | 抢 AI 原生开发入口 |
| 通用插件型 | GitHub Copilot / Supermaven | 抢现有 IDE 内的 Tab 补全入口 |
| IDE 原生型 | JetBrains AI | 抢专业 IDE 语义与重构场景 |
| 企业安全型 | Tabnine | 抢私有化与合规采购 |

---

## 4. P0:修复 R2 warning

当前 warning:

- `feature_tree.*.quality_score.evidence_ids` 引用了 `feature_existence`
- `pricing_model.pricing_gap.evidence_ids` 引用了 `user_pain`

推荐处理:

1. **quality_score.evidence_ids** 只放 `performance_quality` / `user_pain`。
2. **support_evidence_ids** 放 `feature_existence`。
3. `pricing_gap.evidence_ids` 如果需要用户抱怨价格,Reviewer 允许 `user_pain` 作为辅助证据,或者拆成:
   - `pricing_gap.evidence_ids`:只放 pricing
   - `pricing_gap.user_reaction_evidence_ids`:放 user_pain

短期最小改动:

- 更新 `collect_all_evidence_refs()` 中 `pricing_gap` 的 allowed claim types,允许 `user_pain`。
- 更新 `sample_report.json`,把 feature quality 里的官方 feature evidence 移到 support evidence。

---

## 5. P1:增加“分析质量 Rubric”

> ✅ 已落地为 **LLM-as-Judge 离线 harness**:`src/judge.py` + `config/quality_rubric.yaml`,
> 设计见 `docs/quality-judge-design.md`。4 维(准确/洞察/实用/聚焦)1-5 锚定打分 +
> 确定性信号压方差 + 按 purpose 加权。当前为离线评测(优化 prompt 用),未接入 graph 打回。
> 下方原 AQ1-AQ7 设想为更早的草案,judge 取其交集并收敛到 4 维。

现在 Reviewer 的分数主要是工程质量分。建议新增一组 soft checks,不要一开始 hard gate。

| 编号 | 维度 | 检查点 | 失败表现 |
------|------|--------|----------|
| AQ1 | 分析边界 | 是否有 analysis_scope 或 report 中明确边界 | 泛泛介绍产品 |
| AQ2 | 竞品范围 | 是否解释竞品类型/竞争关系 | 只列产品名 |
| AQ3 | 维度质量 | feature 是否贴合 analysis_focus | 把 Agent 能力混入代码补全 |
| AQ4 | 证据分层 | 是否区分 vendor_claim / user_generated / third_party | 官网营销当真实体验 |
| AQ5 | 事实推断 | 是否有 inference / confidence / evidence_ids | 过度断言 |
| AQ6 | 竞争逻辑 | 是否输出不同产品的真实竞争点 | 只做功能矩阵 |
| AQ7 | 建议落地 | 是否包含指标、收益、风险、可行性 | “提升体验”式空话 |

建议先在 `quality_report` 中新增:

```json
{
  "analysis_quality": {
    "score": 72,
    "checks": [
      {"id": "AQ1", "status": "warning", "detail": "报告缺少显式分析边界"},
      {"id": "AQ6", "status": "warning", "detail": "缺少竞品竞争逻辑分层"}
    ]
  }
}
```

前端单独渲染成“分析质量”页签,不要和 R1-R7 混在一起。

---

## 6. P1:扩展竞品池

当前 AI coding domain:

```yaml
target: Cursor
competitors:
  - Windsurf
  - GitHubCopilot
```

建议升级为:

```yaml
competitors:
  direct:
    - Windsurf
  plugin:
    - GitHubCopilot
    - Supermaven
  ide_native:
    - JetBrainsAI
  enterprise_secure:
    - Tabnine
```

如果短期不想改 schema,可以先把 `analysis_meta` 增加:

```json
"competitor_map": [
  {"name": "Windsurf", "type": "AI-native editor", "competes_on": ["flow", "tab", "agentic editing"]},
  {"name": "GitHubCopilot", "type": "IDE plugin", "competes_on": ["distribution", "IDE compatibility"]},
  {"name": "Supermaven", "type": "completion plugin", "competes_on": ["latency", "large context"]},
  {"name": "JetBrainsAI", "type": "IDE-native AI", "competes_on": ["project semantics", "refactoring"]},
  {"name": "Tabnine", "type": "enterprise secure completion", "competes_on": ["privacy", "on-prem", "compliance"]}
]
```

---

## 7. P1:让建议更像 action plan

当前 recommendation:

```json
{
  "action": "...",
  "rationale": "...",
  "priority_score": {...}
}
```

建议新增字段:

```json
{
  "expected_impact": "提升大仓库用户留存,巩固 predictive editing 心智",
  "success_metric": "100k+ LOC 首次索引 < 10 分钟;跨文件召回率 > 90%",
  "risk": "索引资源成本上升;后台任务影响本地性能",
  "time_horizon": "2 周 POC + 4 周灰度",
  "owner_hint": "Editor Infra / Indexing"
}
```

Writer 展示为:

```text
建议: ...
为什么值得做: ...
验收指标: ...
风险: ...
周期: ...
```

这样报告会从“建议”升级为“产品行动计划”。

---

## 8. P2:新增 Planner 节点

长期最值得加的是 Planner。当前流程是:

```text
Collector -> Analyzer -> Writer -> Reviewer
```

建议变成:

```text
Planner -> Collector -> Analyzer -> Writer -> Reviewer
```

Planner 输出:

```json
{
  "analysis_scope": {
    "include": ["即时补全", "Tab 触发", "上下文理解", "下一步修改预测"],
    "exclude": ["完整 Agent 自动执行", "通用 Chat"]
  },
  "hypotheses": [
    "Cursor 的优势不在单点补全,而在 predictive editing",
    "Copilot 的主要优势来自分发和生态",
    "Tabnine 的竞争力主要在企业安全采购"
  ],
  "competitor_map": [...],
  "evidence_plan": [
    {"question": "Copilot 支持哪些 IDE?", "preferred_source": "official_doc"},
    {"question": "Supermaven 是否主打大上下文和低延迟?", "preferred_source": "official_page"},
    {"question": "用户是否抱怨 Cursor 大仓库索引?", "preferred_source": "user_generated"}
  ]
}
```

Planner 的价值:

- Collector 不再盲抓,而是按问题补证据。
- Analyzer 不再乱扩展分析范围。
- Reviewer 可以检查最终报告是否回答了初始假设。

### 8.1 已落地:intake 意图问询层(Planner 雏形)

`src/intake.py` 实现了 Planner 的「问询补全」部分(尚未接入 graph,只负责生成运行参数):

- 用户给一句话意图 → agent 把决策点(target / 竞品 / 焦点 / 目的 / 是否存盘)做成**选择题**(含推荐),用户点选即可,不用手填字段。
- 有 LLM(非 mock 且有 ARK_API_KEY)→ 智能抽取意图 + 推荐同类竞品;无 key / mock → 启发式从 `products.yaml` / `domains.yaml` 出候选。
- 选「保存为新行业」→ `persist_domain()` 写回 `domains.yaml` + `products.yaml`,之后 `DOMAIN=xxx` 复用。
- 入口:前端侧栏上方「🧭 一句话智能填写」面板(回填 sb_*);CLI `python -m src.intake` 向导。

后续接入 graph 时,intake 的 `analysis_scope` / `hypotheses` / `evidence_plan` 再补上(本节上方的完整 Planner 设计)。

---

## 9. 推荐实施顺序

### 第 1 天:报告结构升级

- Writer 增加“核心结论 / 分析边界 / 竞品分层 / 竞争逻辑”区块。
- Prompt 要求 Analyzer 输出对应字段。
- Demo 报告先用 sample_report 补齐这些字段。

### 第 2 天:质量评价升级

- Reviewer 增加 `analysis_quality` soft checks。
- 前端增加“分析质量”卡片。
- 修复 R2 warning,确保 full 模式能过。

### 第 3 天:竞品池升级

- `products.yaml` 增加 Supermaven / JetBrainsAI / Tabnine。
- `sample_sources.json` 补官方事实与少量用户反馈。
- 报告从 3 产品功能对比变成 6 产品竞争地图。

### 第 4 天以后:Planner 节点

- 新增 `src/planner.py`。
- `AgentState` 增加 `analysis_plan`。
- Graph entry 从 collector 改成 planner。

---

## 10. 最小可交付版本

如果时间很紧,只做这 4 件:

1. 报告开头加“分析边界”。
2. 报告加“竞品分层 / 竞争逻辑”。
3. 修 R2 warning。
4. Recommendation 加“验收指标 / 风险 / 周期”。

这四件能立刻让报告从“能跑通的 Demo”变成“像产品研究报告”。
