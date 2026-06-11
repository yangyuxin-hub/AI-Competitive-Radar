# CLAUDE.md

> 项目：AI 驱动的竞品分析 Agent 协作系统（字节跳动 AI 全栈挑战赛 · Topic 3）
> 设计版本：**v2.2**（pre-mortem 修订）· Schema：v2.1（字段冻结）
> 周期：3 周 Demo（2026-05-20 ~ 2026-06-10），答辩 6-12 ~ 6-19

本文件是给 Claude Code 的项目指南。详细架构见 `docs/design-v2.2.md`（含答辩/合规/质量评测附录 A-D）。变更速览见 v2.2 §十三，v2.1 历史基线见 git 历史。

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
Collector → Analyzer(2步) → Writer → Reviewer → guard_revise → END
```

直线控制流（v3 M4，2026-06-11）：打回路由/retry 配额/degraded_writer 节点已删（54 run 仅 1 次触发打回）。Reviewer 定位清单 → `guard_revise` 确定性修订 → 有变化则 writer 重渲染出货；终态规则：running+有修订→passed / 零修订→degraded（报告外层包分层说明，原 degraded_writer 措辞由 `guard._degraded_annex` 承担）。Analyzer v2.2 拆 facts→derivations 两步，每步自带 quick_validate。状态见 `AgentState`（v2.2 §三）。

v3 新模块（详见 `docs/design-v3-draft.md` §六迁移表）：`evidence_gaps`（缺口判定唯一入口 `find_gaps`→`stage_report.Gap` + 池内回捞）、`guard`（结论强度唯一 owner：G1 强对比对账/G2 basis 对账/幂等 `apply()`）、`evidence_service`（采集执行唯一 owner：`fill()` 回捞优先→定向外搜；Analyst 簇零采集 import，AST 回归测试锁边界）。

## 4. 模块职责（不要越界）

| 模块 | 职责 | 禁止 |
|------|------|------|
| **Collector** | 抓取 raw_evidence；适配器三层降级；按 `reject_requirements` 精准补证据 | 不做语义分析、不生成结论 |
| **Analyzer** | 填充 feature_tree / pricing_model / user_persona / swot / recommendations；按权重计算 priority_score | 不抓数据；不编 evidence_id；不手写 priority |
| **Writer** | 渲染 Markdown 报告 | 不改 schema |
| **Reviewer** | 跑 R0-R10 规则（R9 chip 可溯源 / R10 禁泄分）；输出 `quality_report`；写回 `reject_target` 和 `reject_requirements` | 不修数据，只判定 |

## 5. 关键代码契约

- **evidence_id**：`"S" + sha1(...).hexdigest()[:7].upper()` = 8 字符（v2.2 修订）
- **competitors 命名**：与 `products.yaml` key 一致，去空格 PascalCase（`GitHubCopilot` 而非 `GitHub Copilot`）
- **Analyzer 两步**：Step1 facts(feature/pricing/persona) → Step2 derivations(swot/rec)；每步带 quick_validate
- **retry 配额**：按 target 分桶 `{collector:1, analyzer:2, writer:1}`，用完即降级，不切换 target
- **Writer chip 格式**：每条 claim 句末 `[SXXXXXXX]`，前端识别此模式渲染溯源跳转
- **Reviewer 模式**：`REVIEWER_MODE=minimal`（Demo 默认，hard_gate=R1/R4/R5，R2/R3/R7 仅 warning，R6 关）/ `full`（答辩，R1-R5 全 hard_gate，R6 终轮单次）
- **Writer 在 Reviewer 之前**：Markdown 正文**禁止**含 `quality_score`，前端从 `state.quality_report` 单独渲染徽章
- **Analyzer quick_validate**：`quick_validate_facts(facts, evidence, meta)` 显式接收 meta，target/competitors 从 `analysis_meta` 取
- **source_reliability / TTL / priority / 各权重阈值**：统一在 `config/scoring.yaml`，经 `src/scoring_config.py` 读取（缺失即回退代码默认值，零行为变化）；各采集器/评分器走同一口径，改 yaml 即可跨行业调权
- **dedupe 只在 `registry.fetch_all` 内做一次**，`collector_node` 仅 patch 兜底
- **CacheAdapter 用 merge 写入**（按 evidence_id 去重），不要覆盖整个文件
- **`patch_by_requirements`** 展开所有 `required_claim_types`（不是只取第一个）
- **Reviewer 选打回目标**：Counter + 优先级 `collector > analyzer > writer`
- **R6 通过闭包注入 LLM**，不要把 llm 放进 AgentState
- **timeout**：httpx 读 15s / LLM 调用默认 200s（`LLM_TIMEOUT` 可调；不要用默认 600s）。`max_retries=1`（`LLM_MAX_RETRIES` 可调）

## 6. 目录约定

> 注：源码为**扁平单文件模块**（`src/*.py`），非子包目录。

```
config/
  products.yaml               # 产品别名 + official_pages + pricing_pages
  domains.yaml                # 多行业域配置（DOMAIN env 切换 target/competitors/sample_path）
  sources.yaml                # 采集源配置
  quality_rubric.yaml         # 质量评分维度
  scoring.yaml                # 统一评分配置(权重/阈值/source_reliability/freshness TTL)；缺失即回退代码默认值
src/
  state.py                    # AgentState TypedDict + build_initial_state
  # —— collector 三层 DAG(common 基座 ← adapters ← node 编排;collector.py re-export 全公共名)——
  collector.py                # collector_node + 验收门补采(acceptance_gate_and_heal);re-export common/adapters
  collector_common.py         # 叶子 helper/常量/URL discovery/进度通道单例(三层共享)
  collector_adapters.py       # OfficialPage/Search/Mock/Cache 四适配器 + AdapterRegistry(单向依赖 common)
  # —— analyzer 三层 DAG(common 基座 ← fallback/augment/sanitize ← node;analyzer.py re-export)——
  analyzer.py                 # 两步式(facts/derivations) + quick_validate + analyzer_node + 强约束 Prompt(prompts/)
  analyzer_common.py          # 叶子 helper/预览渲染/证据压缩/load_prompt/进度通道/_FACTS_SECTIONS/_REQUIRED_CT
  analyzer_sanitize.py        # 确定性后处理簇(sanitize_*/soften_overgeneralization;零 LLM/进度依赖)
  analyzer_fallback.py        # 骨架兜底构建器(_fallback_facts/_derivations + _corrupt_* demo 注入)
  analyzer_augment.py         # 证据增强侧流(覆盖缺口定向补采 + 真实UGC不足时合成访谈)
  progress.py                 # 共享进度回调通道 ProgressChannel(每节点独立实例,防 SSE 串台)
  writer.py                   # Markdown 渲染(chip 格式 [SXXXXXXX]) + 数据可得性渲染
  reviewer.py                 # R0-R10 检查函数(R9 chip 可溯源 / R10 禁泄分) + degraded_writer
  graph.py                    # LangGraph 编排 + main 入口
  llm.py                      # LLM 客户端封装(默认 MiMo)
  scoring_config.py           # config/scoring.yaml 加载器(reliability/ttl_days/weights helper，缺失回退默认)
  quality.py                  # 证据级质量分 + 采集覆盖审计(audit_coverage)
  source_ledger.py            # 源质量学习台账(按 category 累积 domain hits/q，跨运行复用高质量源)
  stage_eval.py               # 各环节质量评测聚合(写 logs/stage_quality.jsonl)
  business_value.py           # 业务价值量化指标
  encoding.py                 # Windows GBK 控制台 UTF-8 兜底(__init__ 启动即配置)
  intake.py / source_planner.py / search.py / judge.py  # 意图解析 / 源规划 / 搜索 / 评测
  skill.py / hn_skill.py / v2ex_skill.py / survey_skill.py  # 采集 skill 注册与社区源/合成访谈适配
prompts/                      # Analyzer 强约束 Prompt（analyzer_facts.md 等）
data/
  cache/<product>.json        # CacheAdapter 持久化
  sample_sources.json         # Mock 兜底数据（sample_sources_pm.json 为 PM 域）
  sample_report.json          # Mock 报告（拆 facts/derivations）
  source_ledger.json          # 源质量台账运行产物（gitignore）
  debug/                      # evidence_debug 落盘（gitignore）
api/main.py                   # FastAPI + SSE 后端(+ /api/stage_quality 阶段质量聚合)
web/                          # Next.js 前端（输入 / Agent 状态 / 报告溯源）
logs/stage_quality.jsonl       # 各环节 StageReport(status/checks/gaps/produced/cost,按 run_id/node 聚合)
logs/llm_calls.jsonl           # LLM 全量调用日志(system_prompt 去重存 llm_prompts/,按大小轮转)
docs/design-v2.2.md           # 设计文档(v2.2) + 附录 A-D(对比/合规/judge/roadmap)
docs/usage-scenarios.md       # 需求场景叙事 + MVP user journey
docs/competitive-analysis-playbook.md  # 分析方法论
docs/task-requirements.md     # 赛题需求
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

- **LLM**：默认豆包 Doubao（`doubao-seed-2-0-lite`）。运行时环境变量注入，**不要 commit**：
  - `ARK_API_KEY` + `ARK_EP`（或 `LLM_API_KEY` + `LLM_MODEL` + `LLM_BASE_URL`）
  - `ANALYZER_MOCK=1`：无 API key 跑骨架，走 `sample_report.json`
  - `ANALYZER_MOCK=1`：无 API key 跑骨架，走 `sample_report.json`
  - `LLM_THINKING=disabled`：关掉 Doubao Seed 思考模型的隐藏思维链（`llm_calls.jsonl` 实测 43%-91% 的 completion_tokens 是不可见 reasoning，~70 tok/s 下即每次 20-40s 纯思考；机械分类实测 24.8s→1.4s）。未设置=不传参数零行为变化
  - `LLM_THINKING_DEEP`：深度推理调用（swot/recommendations/reviewer_r6/judge/intake 流式）的独立档位，未设回退全局。注意：**Seed-2.0-lite 只支持 enabled/disabled，传 auto 报 400**；llm.py 对 thinking 被拒会自动去参重试一次，不会把 run 打成 degraded
  - `ANALYZER_PROMPT_SLIM=1`：Analyzer payload 瘦身——meta 白名单（剔除 evidence_plan 等采集内务）+ derivations 按 section 过滤证据类型（不再四份相同 45k 快照）+ deriv 专用紧口径（`ANALYZER_DERIV_MAX_PER_TYPE=5`/`ANALYZER_DERIV_SNIPPET_LEN=140`）。未设=旧口径零变化
  - **推荐运行配置（已 A/B 验证）**：`LLM_THINKING=disabled` + `LLM_THINKING_DEEP=enabled` + `ANALYZER_PROMPT_SLIM=1`——derivations prompt −23~38%、recommendations 74s→40s、judge 盲评零掉分、chip 不减、R 规则零新增 failed
  - 代理实测给 LLM 调用平添 ~10s，客户端已 `trust_env=False` 关掉系统代理
- **Web 搜索**：多供应商自动降级（`src/search.py`），统一返回 `{title,url,content,score}`：
  - 主力 **Brave**（`BRAVE_API_KEY`，免费额度大）→ **Tavily**（`TAVILY_API_KEY`，额度小易触 432）→ **DuckDuckGo**（`ddgs` 包，无 key 免费兜底）
  - `SEARCH_PROVIDER=brave,ddg` 显式指定顺序；留空=auto；结果磁盘缓存于 `data/cache/tavily`（TTL 72h）
  - `search_available()`/`tavily_available()` 同义（向后兼容）；只要 ddgs 装了就恒为 True
- **可观测**：LangSmith（`LANGCHAIN_PROJECT=competitive-analysis-agent`）+ `logs/stage_quality.jsonl`（各环节 StageReport）+ `logs/llm_calls.jsonl`（LLM 调用全量，按 run_id/stage 归因）
- **合规**：遵守 robots.txt；source_bias 标注；用户访谈数据脱敏

## 9. 待办（v2.2 §十三）

**v2.2 核心骨架已全部实现：**
- [x] `state.py`：AgentState + per-target retry 字段
- [x] Analyzer 两步节点（facts / derivations）+ quick_validate（facts 三 section 并行 + 确定性 sanitize 兜底）
- [x] Writer 渲染器（chip 格式 `[SXXXXXXX]`）
- [x] Reviewer 节点（R0-R7 + R6 闭包注入）+ degraded_writer
- [x] Collector + 三层降级 + URL discovery + ThreadPoolExecutor
- [x] sample_sources.json / products.yaml / domains.yaml
- [x] 前端三页面（`web/`，Next.js）+ FastAPI/SSE 后端（`api/main.py`）

**进行中 / 已超出 v2.2 设计：**
- [x] runtime profiles 提速 + 阶段耗时/ETA + 档案化 JSON 持久化
- [x] LLM 默认切 MiMo + 全量调用日志 `logs/llm_calls.jsonl`
- [x] scoring/TTL/priority/source_reliability 阈值 config 化（`config/scoring.yaml` + `src/scoring_config.py`，各采集器/评分器读同一口径）
- [x] Reviewer 增 R9（chip 可溯源自检）/ R10（禁泄 quality_score）；源质量学习台账（`source_ledger.py`）；各环节质量评测（`stage_eval.py` + `/api/stage_quality`）
- [ ] 证据过多时仍偶发 LLM 超时（已靠 _compact_evidence + 并行 section 缓解，待持续观察）

**v2.3 候选：**
- [ ] 规则瘦身（R2/R3/R5 合并入 R6）
- [ ] `ISSUE_TYPE_TO_TARGET` 13 项收敛到 3 类
- [ ] 业务价值量化指标（评分维度 3，答辩必备；`business_value.py` 已起步）
- [x] 单文件瘦身（#4/#5 完成，基座模式解循环依赖，全程 re-export 保 back-compat）：
  - [x] 进度回调样板抽 `progress.py`（ProgressChannel，每节点独立实例）
  - [x] collector 拆三层 DAG：`collector.py` 1494→327 + `collector_common`(518) + `collector_adapters`(730)
  - [x] analyzer 拆三层 DAG：`analyzer.py` 1950→974 + `_common`/`_sanitize`/`_fallback`/`_augment`
  - 验证基线：pyflakes 零未定义名 + 147 测试全过；callsite/测试/api/graph 接线零变化
  - 注：analyzer.py 余 974 行为核心分析 pipeline(feature-tree/step1/step2/node),紧耦合,保留不再拆
