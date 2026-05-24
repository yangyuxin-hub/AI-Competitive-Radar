# CLAUDE.md

> 项目：AI 驱动的竞品分析 Agent 协作系统（字节跳动 AI 全栈挑战赛 · Topic 3）
> 设计版本：**v2.2**（pre-mortem 修订）· Schema：v2.1（字段冻结）
> 周期：3 周 Demo（2026-05-20 ~ 2026-06-10），答辩 6-12 ~ 6-19

本文件是给 Claude Code 的项目指南。详细架构见 `docs/design-v2.2.md`（最新）+ `docs/design-v2.1.md`（基线历史）。变更速览见 v2.2 §十三。

---

## 1. 项目定位

- **用户**：企业 PM / 数据分析师
- **目标**：输入「分析 X 和 Y 在 Z 维度的差距」→ 输出结构化竞品报告（功能对比 + 痛点 + 定价 + SWOT + 优先级建议），每条结论可溯源
- **Demo 场景**：AI 编程工具（Cursor / Windsurf / GitHub Copilot），分析焦点「代码补全体验」
- **扩展方式**：换行业 = 改 `config/products.yaml`，不改代码

## 2. 核心原则（必须遵守）

1. **证据覆盖率可控** —— Collector 必须保证 `REQUIRED_CLAIM_TYPES = {feature_existence, performance_quality, pricing, user_pain}` 都有证据，三层降级（live → cache → mock）
2. **失败可降级** —— Reviewer 最多 2 轮打回，超限走 `degraded_writer_node` 分层输出
3. **证据链可复现** —— 所有结论字段必须含 `evidence_ids`，引用 `raw_evidence` 中真实存在的 ID；`evidence_id` 用确定性 hash 生成（不要 uuid）
4. **抑制幻觉** —— Analyzer 禁止编造 evidence_id；事实结论只能基于 `extracted_snippet`；证据不足输出 `unknown`

## 3. 架构速览

```
Collector → Analyzer(2步) → Writer → Reviewer ─┬─ passed → END
                                                ├─ running → 按 target 配额回到对应节点
                                                │            (collector:1, analyzer:2, writer:1)
                                                └─ degraded → degraded_writer → END
```

四个 Agent 节点 + 一个降级节点，LangGraph 编排。Analyzer v2.2 拆 facts→derivations 两步，每步自带 quick_validate。状态见 `AgentState`（v2.2 §三）。

## 4. 模块职责（不要越界）

| 模块 | 职责 | 禁止 |
|------|------|------|
| **Collector** | 抓取 raw_evidence；适配器三层降级；按 `reject_requirements` 精准补证据 | 不做语义分析、不生成结论 |
| **Analyzer** | 填充 feature_tree / pricing_model / user_persona / swot / recommendations；按权重计算 priority_score | 不抓数据；不编 evidence_id；不手写 priority |
| **Writer** | 渲染 Markdown 报告 | 不改 schema |
| **Reviewer** | 跑 R1-R7 规则；输出 `quality_report`；写回 `reject_target` 和 `reject_requirements` | 不修数据，只判定 |

## 5. 关键代码契约

- **evidence_id**：`"S" + sha1(...).hexdigest()[:7].upper()` = 8 字符（v2.2 修订）
- **competitors 命名**：与 `products.yaml` key 一致，去空格 PascalCase（`GitHubCopilot` 而非 `GitHub Copilot`）
- **Analyzer 两步**：Step1 facts(feature/pricing/persona) → Step2 derivations(swot/rec)；每步带 quick_validate
- **retry 配额**：按 target 分桶 `{collector:1, analyzer:2, writer:1}`，用完即降级，不切换 target
- **Writer chip 格式**：每条 claim 句末 `[SXXXXXXX]`，前端识别此模式渲染溯源跳转
- **Reviewer 模式**：`REVIEWER_MODE=minimal`（Demo 默认，hard_gate=R1/R4/R5，R2/R3/R7 仅 warning，R6 关）/ `full`（答辩，R1-R5 全 hard_gate，R6 终轮单次）
- **Writer 在 Reviewer 之前**：Markdown 正文**禁止**含 `quality_score`，前端从 `state.quality_report` 单独渲染徽章
- **Analyzer quick_validate**：`quick_validate_facts(facts, evidence, meta)` 显式接收 meta，target/competitors 从 `analysis_meta` 取
- **source_reliability / TTL / priority 阈值**：当前硬编码，v2.3 计划下沉 config（跨行业泛化）
- **dedupe 只在 `registry.fetch_all` 内做一次**，`collector_node` 仅 patch 兜底
- **CacheAdapter 用 merge 写入**（按 evidence_id 去重），不要覆盖整个文件
- **`patch_by_requirements`** 展开所有 `required_claim_types`（不是只取第一个）
- **Reviewer 选打回目标**：Counter + 优先级 `collector > analyzer > writer`
- **R6 通过闭包注入 LLM**，不要把 llm 放进 AgentState
- **timeout**：httpx 读 15s / ARK LLM 90s（不要用默认 600s）

## 6. 目录约定

```
config/products.yaml          # 产品别名 + official_pages + pricing_pages
src/
  state.py                    # AgentState TypedDict
  collector/                  # AdapterRegistry + 各 SourceAdapter
  analyzer/                   # 含强约束 Prompt
  writer/
  reviewer/                   # R1-R7 检查函数
  graph.py                    # LangGraph 编排
data/
  cache/<product>.json        # CacheAdapter 持久化
  mock/sample_sources.json    # Mock 兜底数据
logs/agent_trace.jsonl        # 可观测性日志
docs/design-v2.2.md           # 最新设计文档(v2.2)
docs/design-v2.1.md           # 历史基线(v2.1-frozen)
```

## 7. 开发节奏

| 阶段 | 交付 |
|------|------|
| Day 1-2 | Schema v2.1 + `sample_sources.json` + `sample_report.json` 锁定 |
| Day 3-4 | LangGraph 四节点跑通 **Mock 数据闭环**（先不接真实抓取） |
| Day 5-7 | 前端：输入页 / Agent 状态页 / 报告溯源页 |
| Week 2 | 接实时采集 + 缓存兜底 |
| Week 3 | Reviewer 打回演示 + LangSmith + 答辩脚本 |

## 8. 资源

- **LLM**：Doubao-Seed-2.0-lite，EP 与 APIKEY 见赛事内部文档；运行时通过环境变量 `ARK_API_KEY` / `ARK_EP` 注入，**不要 commit**
- **可观测**：LangSmith（`LANGCHAIN_PROJECT=competitive-analysis-agent`）+ `logs/agent_trace.jsonl`
- **合规**：遵守 robots.txt；source_bias 标注；用户访谈数据脱敏

## 9. 待办（v2.2 §十三）

**已纳入 v2.2 设计、待实现：**
- [ ] `state.py`：AgentState + per-target retry 字段
- [ ] Analyzer 两步节点（facts / derivations）+ quick_validate
- [ ] Writer 渲染器（chip 格式 `[SXXXXXXX]` + 6 个 render_* 函数）
- [ ] Reviewer 节点（R1-R5 + R7 + R6 闭包注入）+ degraded_writer
- [ ] Collector + 三层 Adapter + ThreadPoolExecutor
- [ ] sample_sources.json（三产品 × 四 claim_type，Day1-2 锁数据）
- [ ] products.yaml
- [ ] 前端三页面（输入 / Agent 状态 / 报告溯源 chip）

**v2.3 候选（Week 3 评估）：**
- [ ] 规则瘦身（R2/R3/R5 合并入 R6；scoring/TTL/priority 阈值 config 化）
- [ ] `ISSUE_TYPE_TO_TARGET` 13 项收敛到 3 类
- [ ] TRAE 协作痕迹标注（评分维度 4）
- [ ] 业务价值量化指标（评分维度 3，答辩必备）
