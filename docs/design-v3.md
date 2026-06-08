# design-v3 — 系统级可观测 + 可控改造总图纸

> 项目：AI 驱动的竞品分析 Agent 协作系统（字节跳动 AI 全栈挑战赛 · Topic 3）
> 本文定位：**v2.2 之上的"控制平面"重构总纲**，不改 v2.1 Schema，不破坏报告/API 兼容。
> 关系：v2.2（`docs/design-v2.2.md`）定义了四节点业务流；v3 在其上引入**统一的环节契约与控制环**，让每个环节"可观测、可控、可精准打回"。
> 状态：设计冻结候选 · 增量迁移 · 零拓扑大改

---

## 0. 一句话

> 给每个环节一份**统一的 StageReport 契约**，让"我过了吗 / 哪里坏了 / 怎么修"只有一种说法；再用**一个统一控制环**消费它，把观测（落盘）和控制（路由打回）收敛到同一个对象上。

---

## 1. 问题诊断：能力不缺，缺统一契约

现状每个环节都在回答同三个问题，但**用三种方言**，而且观测分是**事后反推**的：

| 环节 | "我过了吗" | "哪里坏了" | "怎么修" | 代码锚点 |
|------|-----------|-----------|---------|---------|
| Collector | `quality_audit.passed` | `gaps[]`（`gap_type`） | `gap.fix` 处方 ✅ | `quality.annotate_and_audit` |
| Analyzer | `quick_validate`→`list[str]`（用完即弃） | 内联 `sanitize_*`，**外部不可见** | gap-refill 循环，**不可观测** | `analyzer._step1_facts` 765-775 |
| Writer | `report_draft` 非空 | R9/R10 要到 Reviewer 才查 | — | `writer.writer_node` |
| Reviewer | `status` | `errors[]`（`location`） | `reject_requirements` ✅ | `reviewer.make_reviewer_node` 886-915 |
| stage_eval | **再 parse 一遍 state 反推 verdict** | 重算 `hallucination_count` | 不参与 | `stage_eval.py` 37-39 |

**病灶**：`stage_eval` 不读环节自己的判定，而是把 `schema_draft` 重新 JSON 化、正则抠 `evidence_id` 反推幻觉数（`stage_eval.py:37-39`）——观测性是外挂反推，不是环节主动上报。控制同理：打回逻辑只长在 Reviewer 一个闭包里（`reviewer.py:886`），collector 验收门 / analyzer quick_validate 各管各的，没有统一的"上报→路由"通道。

**结论**：子节点要不要拆、打回怎么精准、采集标准怎么定——是同一问题的三个切面。没有统一契约时，每加一道门就要手写一套观测+路由。

---

## 2. 设计目标 / 非目标

**目标**
1. **统一上报**：任意环节/子环节产出同构的 `StageReport`，观测与控制都消费它，杜绝反推。
2. **统一控制**：编排层对每份 StageReport 只做 `advance | repair | degrade` 三选一，打回按 `owner_node × product × claim_type` 精准路由。
3. **统一策略**：哪道门硬/软、哪类 gap 回哪、每个 task 几轮预算——收敛成一张声明式控制表。
4. **全链路时间线**：前端按环节展示状态徽章 → 钻取 StageReport → 钻取结论 chip → 证据。直接服务答辩"协作可信度 35%"。

**非目标（明确不做）**
- 不改 v2.1 Schema、不改最终报告结构、不破坏 `api/main.py` 返回兼容。
- 不把 Analyzer 内部拆成 LangGraph 图节点（见 §7.3，内联实现保留，仅改为"上报"）。
- 不引入新的外部依赖 / 不搞 `GRAPH_GRANULAR` 双图并存。
- 不追求一次性替换；迁移分阶段，每阶段可独立上线、可回滚。

---

## 3. 核心契约：StageReport

### 3.1 Schema（`src/stage_report.py`，新增）

```python
from typing import TypedDict, Literal, Optional

Verdict  = Literal["pass", "warn", "fail"]
Status   = Literal["ok", "degraded", "failed"]
Severity = Literal["error", "warning", "info"]

class Check(TypedDict):
    check_id: str          # 唯一稳定 ID：R1 / coverage_short / pricing_no_number / quick_validate_facts ...
    verdict:  Verdict
    severity: Severity     # 由控制表按 mode 重写（hard→error / soft→warning）
    location: str          # schema 路径 / collection_meta 路径 / report_draft
    detail:   str

class Gap(TypedDict):
    owner_node:  str       # 谁来补：fetch_official / fetch_community / backfill / analyzer:<section> / writer
    product:     Optional[str]
    claim_type:  Optional[str]   # feature_existence / performance_quality / pricing / user_pain
    gap_type:    str             # coverage_short / no_official / pricing_no_number / community_missing ...
    fix:         dict            # 处方：{strategy, ladder, query_hint, source_hint, bias}
    fixable:     bool            # False = 客观补不上（如 SPA 定价页），直接 degrade 不空转
    task_key:    str             # 派生：f"{owner_node}:{product}:{claim_type}"，预算与去重的主键

class StageReport(TypedDict):
    stage:    str                # collector / analyzer / writer / reviewer
    node:     str                # 子节点名（拆分后）；未拆时 == stage
    run_id:   str
    attempt:  int                # 该 node 的第几次尝试（从 retry_count 派生）
    status:   Status             # 环节自己的判定 —— 唯一真相，禁止外部反推
    produced: dict               # 关键产物计数：{evidence: N, feature_dims: M, report_chars: X, ...}
    checks:   list[Check]
    gaps:     list[Gap]
    cost:     dict               # {elapsed_sec, tokens}
```

### 3.2 字段语义（契约级约束）

- **`status` 是唯一真相**：环节自己算，落盘与路由都读它，禁止任何下游再 parse `schema_draft` 反推（删除 `stage_eval` 的反推逻辑）。
- **`status` ⇐ `checks` 派生规则**（统一，不再各环节自定义）：
  - 任一 `check.severity == "error"` → `failed`
  - 否则有 `gaps` 但都 `fixable` 且预算未尽 → `degraded`（待修复）
  - 否则 → `ok`
- **`gaps[].task_key`** 是预算与去重的主键，替代 v2.2 的粗粒度 `retry_count{collector/analyzer/writer}`。
- **`gaps[].fixable=False`** 的 gap 不进 repair，直接进 degrade 通道，作为报告里的诚实标注（呼应核心原则 #4 抗幻觉：补不到就标 `unknown`，不硬凑）。

### 3.3 三方言 → StageReport 收编映射（实现不动，仅适配）

| 现有产物 | 来源 | 映射到 |
|---------|------|--------|
| `quality_audit.gaps[]`（含 `fix`） | `quality.annotate_and_audit` | `gaps[]`（补 `owner_node`/`fixable`/`task_key`） |
| `quick_validate_facts/derivations`→`list[str]` | `analyzer` | `checks[]`（check_id=`quick_validate_facts`） |
| `sanitize_* dropped` 计数 | `analyzer` | `checks[]`（info 级，记录自愈动作） |
| Reviewer `errors[]`/`warnings[]` | `reviewer` | `checks[]`（check_id=`R1..R10`） |
| `reject_requirements` | `reviewer` | `gaps[]`（owner 由 `ISSUE_TYPE_TO_TARGET` 升级而来） |
| `collection_meta.quality_audit` 各计数 | `collector` | `produced` |

> **关键约定**：映射是**适配层**，`quality.py` / `analyzer.py` / `reviewer.py` 的检查函数签名与逻辑**不改**，只在节点返回处包一层 `to_stage_report(...)`。这保证 147 测试基线零回归。

---

## 4. 统一控制环

### 4.1 状态机

编排层对每份 StageReport 只做一个三选一决策（把 Reviewer 现有 `Counter+priority+per-target budget` 抽出来、通用化）：

```
        ┌──────────────────┐   StageReport
 node ──┤ 跑门 + to_report  ├───────────────►  control_loop(report, budget)
        └──────────────────┘                        │
                          status==ok ───────────────► advance     进下一段
                          status==degraded:
                            ├ 有 fixable gap & 预算够 ─► repair    只回 gap.owner_node（带 product×claim_type）
                            └ 否则 ────────────────────► degrade   gap 转诚实标注，带下去不阻断
                          status==failed & 预算够 ─────► repair
                          status==failed & 预算尽 ─────► degrade
```

### 4.2 repair 路由（精准打回）

- 路由键 = `gap.owner_node`，不再是粗粒度大节点。`owner_node` 取值：
  - `fetch_official`：`coverage_short(feature/pricing)`、`no_official`、`pricing_no_number`
  - `fetch_community`：`coverage_short(user_pain/performance)`、`bias_all_vendor`、`community_missing/low_quality`
  - `backfill`：`total_too_few`
  - `analyzer:<section>`：R2/R5/R6 等（`feature_fill` / `pricing_extract` / `recommendations` …）
  - `writer`：R9/R10
- **同段多 gap**：按 `task_key` 去重后批量下发给各自 owner，一次 repair 可并行补多个缺口（复用 `_targeted_refill` 已有的多 gap 处理）。
- **owner 推导是确定性的**：新增 `quality.gap_owner(gap)`，从 `(claim_type, gap_type)` 查 `GAP_OWNER` 表得出，**不加 state 字段**。

### 4.3 预算：从大节点降到 task_key

```python
# v2.2：粗
retry_count = {"collector": 0, "analyzer": 0, "writer": 0}
max_retries_per_target = {"collector": 1, "analyzer": 2, "writer": 1}

# v3：细（task_key 级；大节点预算作为兜底上限保留）
repair_budget = {
    # task_key -> {attempts, max}
    "fetch_community:Linear:user_pain": {"attempts": 0, "max": 2},
    ...
}
node_budget_cap = {"collector": 3, "analyzer": 2, "writer": 1}  # 防止单段无限循环的硬上限
```

- 某个精准任务多轮失败 → 只降级**它自己**（该 gap `fixable=False` 转诚实标注），不连累整段。
- `node_budget_cap` 作为防呆硬上限，对应 LangGraph `recursion_limit`，避免环路爆栈。

### 4.4 与 LangGraph 的接法（不拆 Analyzer）

- Collector 拆 `discover_urls → {fetch_official, fetch_community} → merge_gate` 三子节点（见 design-v3 §7.2 与上一轮 Collection 拆分结论）。
- Analyzer / Writer / Reviewer **维持单节点**，但返回值附带 StageReport。
- `control_loop` 实现为 `route_after_*` 条件边的统一版本，替换 `graph.py:88 route_after_review` 的专用逻辑。

---

## 5. 控制表（声明式策略，单一事实源）

把"哪道门硬/软、哪类 gap 回哪、每个 task 几轮"收敛成一张表（扩展 `config/scoring.yaml` + `reviewer.MODE_CONFIG` + `reviewer.ISSUE_TYPE_TO_TARGET`，而非新增三处分支）。

```yaml
# config/control_plane.yaml（新增）
modes:
  minimal:                         # Demo 默认
    hard_gate: [R1, R4, R5, R9, coverage_short, no_official]
    soft:      [R0, R2, R3, R7, R10, pricing_no_number, bias_all_vendor]
    r6: final_warn                 # 终轮仅 warning
  full:                            # 答辩
    hard_gate: [R0, R1, R2, R3, R4, R5, R9, R10, coverage_short, no_official, pricing_no_number]
    soft:      [R7]
    r6: hard

gap_owner:                         # (claim_type, gap_type) -> owner_node
  pricing/coverage_short:        fetch_official
  feature_existence/coverage_short: fetch_official
  "*/no_official":               fetch_official
  pricing/pricing_no_number:     fetch_official
  user_pain/coverage_short:      fetch_community
  performance_quality/coverage_short: fetch_community
  "*/bias_all_vendor":           fetch_community
  "*/community_missing":         fetch_community
  "*/community_low_quality":     fetch_community
  "*/total_too_few":             backfill

budget:
  default_max_attempts: 2
  node_cap: {collector: 3, analyzer: 2, writer: 1}

intent_required_claim_types:       # §8 修正：采集门跟评分门对齐
  feature_compare:   [feature_existence, performance_quality, pricing, user_pain]
  pain_attribution:  [user_pain, performance_quality]
  pricing_compare:   [pricing, feature_existence]
```

> 调策略 = 改这张表。换行业、调答辩强度、改打回激进度，都不动代码——这是"系统级可控"的抓手。

---

## 6. 时间线观测 UX

### 6.1 数据来源

- 每个节点返回的 StageReport append 到 `logs/stage_quality.jsonl`（升级现有格式，stage_eval 改为**写入上报的 report，不再反推**）。
- `api/main.py` 新增 `/api/timeline?run_id=` 聚合该 run 的所有 StageReport，按 `ts` 排序。
- SSE 实时推送：节点完成即推一条 StageReport 摘要（复用 `ProgressChannel`）。

### 6.2 前端时间线（`web/`）

```
┌─ Run trace ─────────────────────────────────────────────────────┐
│ ● discover_urls   ok     3 products · 6 urls            1.2s     │
│ ● fetch_official  ok     12 evidence · 门 2/2 pass      8.4s     │
│ ⚠ fetch_community degraded  Linear/user_pain 缺社区     11.1s    │
│      └─ gap: community_missing → repair fetch_community(Linear)  │
│ ● fetch_community(retry) ok  +4 evidence · 门 pass      6.3s     │
│ ● merge_gate      ok     32 evidence · coverage 4/4     0.3s     │
│ ● analyzer        ok     7 feature dims · halluc 0      42s      │
│      └─ checks: quick_validate_facts pass · sanitize dropped 2   │
│ ● writer          ok     报告 4.1k 字 · 18 chips        3s       │
│ ⚠ reviewer        warn   R6 1 warning · score 86/100    9s       │
└──────────────────────────────────────────────────────────────────┘
点 stage → 展开 StageReport（checks/gaps/produced/cost）
点结论 chip [SXXXXXXX] → 跳证据卡（已有溯源能力）
```

**答辩价值**：评委一眼看到"每段都过了门、缺社区被自动定向补回、零幻觉、分数怎么来的"——协作可信度（35%）的最强叙事，且每条都可下钻到证据。

---

## 7. 现状 → 目标 收编映射

| 能力 | 现状 | v3 动作 | 改动量 |
|------|------|---------|--------|
| 进度可视化 | `ProgressChannel` 每节点独立实例 | 复用，emit StageReport 摘要 | 小 |
| 阶段质量日志 | `stage_eval` 反推 4 套 metrics | 改读环节上报的 StageReport，删反推 | 中 |
| 门结果 | audit/quick_validate/R0-R10 三方言 | 统一映射进 `checks[]`，检查函数不动 | 中 |
| 缺口+修复 | `gap`/`reject_requirements` 两方言 | 统一进 `gaps[]` + 补 `owner_node`/`fixable` | 中 |
| 打回路由+预算 | 仅 Reviewer，按大 target | 抽成通用 control_loop，按 task_key | 中 |
| 控制策略 | 散在 3 文件 | 收敛 `control_plane.yaml` | 中 |
| Collector 重抓 | 每次 `fetch_all` 全量重抓后 patch | 拆 fetch_official/community，按 owner 精准回 | 中 |
| 采集门 intent | `audit_coverage` 对所有 intent 一刀切 | 读 `intent_required_claim_types`，与评分门对齐 | 小 |

### 7.3 为什么 Analyzer 不拆图节点（决策记录）

判据：子步提升为图节点需同时满足 ①独立失败 ②修复目标与外层不同 ③数据真解耦。
- facts↔derivations：步间审查门已内联存在（`quick_validate_facts`→`sanitize`，`analyzer.py:765`），derivations 错误的修复目标仍是 analyzer 自己（不会回采集），且 facts 三 section 是 `ThreadPoolExecutor` 并行 + gap-refill 是共享 `evidence/spine/facts` 的迭代循环——②③不满足。
- 结论：**Analyzer 内部维持内联实现，仅增加 StageReport 上报**。"实现内联"与"系统可观测"不冲突：实现可内联，上报必须统一。

---

## 8. 顺带修正的口径分裂（随迁移一起做）

- **采集门 vs 评分门 intent 不一致**：`audit_coverage`（`quality.py:254`）对所有 intent 都要 `feature≥2/pricing≥1`，但 Reviewer `_quality_dimensions`（`reviewer.py:670`）对 `pain_attribution` 已踢掉 feature/pricing。后果：痛点分析时采集门为补不到的 feature/pricing 空转。v3 让两者同读 `intent_required_claim_types`。
- **gap 缺 owner_node**：阻碍精准打回，由 `GAP_OWNER` 表 + `gap_owner()` 补齐。
- **不可解缺口未在采集门侧标记**：`fixable=False` 字段统一承载（analyzer 的 `exhausted_pricing` 思路上移到契约层）。

---

## 9. 迁移图（增量、每阶段可独立上线/回滚）

```
M0  契约就位（不改行为）
    ├─ 新增 src/stage_report.py（schema + to_stage_report 适配器 + gap_owner）
    ├─ 4 节点返回处包 to_stage_report（收编现有判定，零新逻辑）
    └─ stage_eval 改为写入上报的 report；删反推
    ✅ 验收：147 测试全过 + stage_quality.jsonl 出现统一 schema

M1  观测上线（看得见）
    ├─ /api/timeline 聚合 + SSE 推 StageReport 摘要
    └─ web/ 时间线视图
    ✅ 验收：跑一次 demo，前端时间线逐段可下钻

M2  控制环统一（可控）
    ├─ 抽 control_loop（替换 route_after_review 专用逻辑）
    ├─ 预算降到 task_key + node_cap 防呆
    └─ config/control_plane.yaml 接入
    ✅ 验收：注入错误，打回按 owner_node 精准回，degrade 正确

M3  Collector 精准化（少重抓）
    ├─ 拆 discover_urls → {fetch_official, fetch_community} → merge_gate
    ├─ gap.owner_node 路由：缺社区只回 fetch_community
    └─ intent-aware 采集门
    ✅ 验收：缺 Linear/user_pain 只触发社区补采，不重抓 Cursor/Windsurf（集成测试）

M4  收尾
    ├─ fixable=False 不可解缺口诚实标注闭环
    └─ 文档同步 CLAUDE.md §3/§5，design-v3 冻结
```

**关键性质**：M0/M1 纯增量，**不改任何控制行为**，可先上线拿观测收益；M2 起才动控制流，且每阶段独立可回滚。整个迁移**不引入双图、不动 v2.1 Schema、不破坏 API**。

---

## 10. 测试矩阵

| 层 | 用例 | 现状 |
|----|------|------|
| 契约单测 | `to_stage_report` 三方言映射正确；`status` 派生规则；`task_key` 派生 | 新增 |
| owner 路由单测 | `gap_owner((user_pain, community_missing)) == fetch_community` 等全表 | 新增 |
| intent 门单测 | `pain_attribution` 不为 feature/pricing 报 `coverage_short` | 新增（配合 §8） |
| 控制环单测 | ok→advance / degraded+预算→repair / 预算尽→degrade / fixable=False→degrade | 新增 |
| **闭环集成**（最关键） | stub `search`：缺 Linear/user_pain → 只 `fetch_community(Linear)`，不碰其它产品 | 新增 |
| 回归 | 147 现有测试全过；Cursor/Windsurf demo `quality_score` ≥ 基线 | 守住 |
| gap 生成单测 | `test_quality_gate.py` 11 例 | 已绿，保留 |

---

## 11. 兼容性与风险

- **Schema 兼容**：StageReport 是**旁路观测/控制对象**，不进 `schema_draft`，不进报告正文，不改 v2.1 字段。前端报告渲染、`api/main.py` 报告返回零变化。
- **回退**：M0–M1 可随时关闭时间线视图回到 v2.2 行为；M2 的 control_loop 保留 `node_cap` 兜底，行为可由 `control_plane.yaml` 调回 v2.2 等价策略。
- **风险：控制环死循环** → `node_cap` 硬上限 + `fixable` 短路双保险。
- **风险：StageReport 与实际状态漂移** → 契约规定 `status` 为唯一真相、禁止下游反推，从源头消除二义。
- **风险：迁移期两套并存** → M0 适配器模式，旧字段（`reject_requirements` 等）保留，新对象叠加，不删旧路径直到 M2 验收通过。

---

## 附录 A：与 v2.2 的字段关系

| v2.2 | v3 | 处置 |
|------|-----|------|
| `state.reject_target` | `gap.owner_node` | M2 后由 gaps 派生，保留字段做兜底 |
| `state.reject_requirements` | `gaps[]` | 升级为结构化 gap，owner 显式 |
| `retry_count{collector/analyzer/writer}` | `repair_budget{task_key}` + `node_cap` | 细化，大节点上限保留 |
| `reviewer.ISSUE_TYPE_TO_TARGET` | `control_plane.gap_owner` | 迁入 yaml |
| `reviewer.MODE_CONFIG` | `control_plane.modes` | 迁入 yaml |
| `stage_quality.jsonl`（反推） | `stage_quality.jsonl`（上报） | 同文件，schema 升级 |

## 附录 B：不做清单（防 scope 蔓延）

- ❌ Analyzer 拆 LangGraph 子节点（§7.3）
- ❌ `GRAPH_GRANULAR` 双图并存
- ❌ 新增 `StageIssue/RepairTask/node_status/active_repair_scope` 等 state 字段（用 StageReport 旁路对象承载）
- ❌ 改 v2.1 Schema / 报告结构 / API 返回
