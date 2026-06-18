# 流水线逐环节走读 — 以「Cursor vs Windsurf vs GitHubCopilot · 代码补全体验」为样例

> 目的:用仓库里的黄金样例 [`data/sample_report.json`](../data/sample_report.json)(配套原始证据 `data/sample_sources.json` 的黄金集 `SCABE*/SDEAD*/SFACE*`),把一条 run 从「一句话意图」到「出货报告」每个环节**具体做了什么、输入是什么、产出是什么**讲清楚。
>
> 控制流:`Intake → Collector → Analyzer(Step1 facts → Step2 derivations) → Writer → Reviewer → guard_revise → END`(v3 M4 直线化)。

样例参数(最终 `analysis_meta`):

| 字段 | 值 |
|---|---|
| target_product | `Cursor` |
| competitors | `Windsurf` / `GitHubCopilot` |
| analysis_focus | `代码补全体验` |
| analysis_purpose | `学习竞品优点,优化 Cursor 的产品策略` |
| report_id | `CR-20260524-001` |
| data_cutoff | `2026-05-24` |

---

## 环节 0 · Intake — 一句话意图 → 运行参数

**输入**:用户一句话,如「分析 Cursor 和 Windsurf、Copilot 在代码补全上的差距」。

**做什么**([`src/intake.py`](../src/intake.py)):
1. `_detect_intent()` 粗判意图类型 → 此例命中 `feature_compare`(无"流失/吐槽"等痛点词)。
2. `propose()`:有 LLM 走 `_propose_via_llm`(还会实时搜「Cursor alternatives 2026」补新锐竞品),无 key 走 `_propose_heuristic`(从 `products.yaml`/`domains.yaml` 拼候选)。产出每个字段的**候选 + 推荐 + 一句话 hint**。
3. `build_questions()` 把草稿变成 5 道选择题:`target`(单选)/`competitors`(多选)/`focus`(多选)/`purpose`(多选)/`persist`。**截图里你勾的两道就是第 3、4 题。**
4. `assemble_meta()` 把答案拼成 meta:
   - `analysis_focus` = 勾选的焦点 list(如 `["代码补全体验"]`)
   - `analysis_purpose` = 勾选项用 ` / ` 拼接

**产出**:`{target_product, competitors, analysis_focus, analysis_purpose, user_input}` → 交给 `build_initial_state` 起 run。

> 关键:`analysis_focus` 从这里开始就是分析的脊柱;`analysis_purpose` 进 meta 后主要在「评测权重 + 报告头」起作用(详见环节 2/6 注)。

---

## 环节 1 · Collector — 抓 raw_evidence + 验收门补采

**输入**:`analysis_meta`(target/competitors/focus)。

**做什么**([`src/collector.py`](../src/collector.py) + adapters):
1. **四适配器三层降级**:`OfficialPage`/`Search` → `Cache` → `Mock`,任一层拿到就停。无网/无 key 时落到 `sample_sources.json`。
2. **均衡采集**:按 `REQUIRED_CLAIM_TYPES = {feature_existence, performance_quality, pricing, user_pain}` × 每个产品配额抓(`collector_common.py:283` `per_type = limit // 4`)。
3. **`evidence_id` 确定性生成**:`"S" + sha1(product+url+snippet…).hexdigest()[:7].upper()` = 8 字符(**不用 uuid**,保证可复现)。
4. **验收门 + 自愈补采** `acceptance_gate_and_heal`:若某 claim_type 覆盖为 0 则定向补一轮。

**产出**(节选,真实结构见 `sample_sources.json`):

| evidence_id | product | claim_type | source_bias | snippet 摘要 | confidence |
|---|---|---|---|---|---|
| `SCABE005` | Cursor | performance_quality | user_generated | 跨文件召回约 80% | 0.6 |
| `SCABE00D` | Cursor | user_pain | user_generated | 偶发编造 import ~5% | 0.6 |
| `SFACE008` | GitHubCopilot | performance_quality | user_generated | 约半数忽略其他文件类型 | 0.6 |
| `SCABE008` | Cursor | pricing | vendor_claim | Pro $20/月 | 0.9 |

> Collector **只抓不判**:不做语义分析、不下结论。每条证据带 `source_bias`(vendor_claim/user_generated)、`source_reliability`、`evidence_confidence`,供下游加权。

---

## 环节 2 · Analyzer Step1(facts)— 把证据整理成事实

**输入**:`analysis_meta` + `raw_evidence`。Prompt:[`prompts/analyzer_facts.md`](../prompts/analyzer_facts.md)(硬约束第 1 条:「严格围绕 `analysis_focus`,不要泛化」)。

**做什么**(三个 section 并行,每步带 `quick_validate`):

### 2a 功能树(`focus` 在这里直接决定对比哪些功能)
[`analyzer.py:409-418`](../src/analyzer.py:409) 拿 `focus="代码补全体验"` 喂 Prompt:「列出该维度下 4-6 个**适合跨产品横向对比**的功能点」→ 得到 `F001 多行/跨文件补全`、`F002 Agent 端到端`、`F003 代码库索引`。

每个 feature 给每个产品打 `quality_score`(1-5),并算 `gap.winner`。**F001 完整产出**:

| 产品 | score | basis(摘要) | evidence_ids |
|---|---|---|---|
| Cursor | 4/5 | 跨文件召回~80%(SCABE005),重构略优(SCABE007),~5% 编造 import(SCABE00D) | SCABE005/007/00D |
| Windsurf | 3/5 | Supercomplete 单文件相当,多文件略差(SDEAD005) | SDEAD005 |
| GitHubCopilot | 2/5 | 基础补全广,跨文件约半数忽略(SFACE008),TS 泛型常过不了类型检查(SFACE009) | SFACE005/008/009 |

→ `gap = {winner: Cursor, gap_type: accuracy, confidence: 0.82}`。注意每个 `quality_score` 带 `aggregation`(positive/negative_mentions + sample_size),把"4/5"锚定到真实样本计数,防止凭空打分。

### 2b 定价模型
抽各档价格归一化成 `normalized_usd_month`:Cursor Pro $20 / Windsurf $15 / Copilot $10 → `pricing_gap = {target_position: more_expensive, confidence: 0.88}`(引用 `SCABE00A` 用户明确抱怨贵)。

### 2c 用户画像
聚合出 3 个 segment(U001 重度开发者 / U002 中小团队采购 / U003 大仓库工程师)+ 5 个 pain(P001~P005)+ 2 个 praise。每个 pain 带 `frequency.level` 和 `evidence_ids`,例:`P003 价格阻力 medium → SCABE00A`。

> **缺证据怎么办**:Analyzer **不自己去抓**,只声明 `Gap` 交给 evidence_service(见环节 3)。零采集 import,有 AST 测试锁这条边界。

---

## 环节 3 · evidence_gaps + evidence_service — 缺口回捞/补采

**做什么**:
- [`evidence_gaps.find_gaps()`](../src/evidence_gaps.py:70):缺口判定唯一入口。扫四类 claim_type 覆盖 + 功能格子空白,产出 `Gap` 列表。
- `evidence_service.fill(gaps)`:**回捞优先**(`ANALYZER_POOL_RECALL`,先把被 top-K/截断挡在池里的证据捞回)→ 不够再**定向外搜** → UGC 不足时**合成访谈**(survey_skill)。

本样例证据已均衡,这一环多为空过;它的价值在真实 run 里——比如 `user_pain` 只有 vendor 证据时,定向补 Reddit/HN 真实吐槽。

---

## 环节 4 · Analyzer Step2(derivations)— 从事实推导 SWOT + 建议

**输入**:`analysis_meta` + `raw_evidence` + **Step1 的 facts**。Prompt:[`prompts/analyzer_derivations.md`](../prompts/analyzer_derivations.md)。

**做什么**:不新增 feature/不改 pricing,只在事实链上推导。

### 4a SWOT(每象限 1-3 条,每条 ≥1 evidence_id)
- Strengths 必须对应 `gap.winner==Cursor` 的 feature → 「跨文件准确性领先(SCABE005/007)」
- Threats ≥1 条来自竞品真实优势 → 「Windsurf 自研推理栈速度感知更快(SDEAD004)」

### 4b recommendations(可执行 + 可排序)— priority 按公式算,**不许手填**

以 `R001` 为例,展示打分闭环:

```
action: 推出中端价位档($12-15/月)或团队折扣,降低全员采购门槛
绑定:  source_pain_ids=[P003]  evidence=[SCABE00A, SCABE008, SDEAD006, SFACE006]
打分:  pain_frequency=4  business_impact=5  feasibility=4  evidence_confidence=3
公式:  0.35×4 + 0.30×5 + 0.20×4 + 0.15×3 = 1.40+1.50+0.80+0.45 = 4.15
映射:  4.15 ≥ 3.4 且 <4.2 → P1
```

四条建议 R001(P1)/R002 索引性能(P1)/R003 import 校验(P2)/R004 Windows 稳定性(P2),按 `final_score` 降序。每条是轻量 PRD:含 `expected_impact`/`success_metric`/`risk`/`time_horizon`/`validation_method`。

> **analysis_purpose 在这一步**:它在 payload 的 `analysis_meta` 里(`analyzer_common.py:244` 白名单),derivations agent 看得到。但当前 Prompt 正文**没有显式指令**按 purpose 调建议取向——所以"影响建议取向"目前是隐式的(上一轮已标注的待改点)。

---

## 环节 5 · Writer — 渲染决策四层 Markdown

**输入**:`schema_draft`(facts+derivations 合并)+ `raw_evidence`。

**做什么**([`writer_node` writer.py:1197](../src/writer.py:1197)):按固定**物理顺序**拼 section,再 `_renumber_sections` 统一编中文序号:

1. **证据与生成概览** — header / 数据可得性 / 证据覆盖地图 / **决策摘要**
2. **产品定位对比** — 定位地图 / 竞品格局
3. **功能矩阵与评分** — 多维度评分总览 / 功能覆盖与差距(`F001~F003`)
4. **定价对比** — 归一化单位成本 / 性价比场景判断
5. **用户之声** — 画像 / 正向反馈 / 痛点
6. **建议 + SWOT + 附录(不确定性/技术能力/口径锁定表)**

两条硬契约:
- **chip 格式**:每条 claim 句末挂 `[SCABE005]` 这样的 8 字符 id,前端正则识别 → 渲染溯源跳转。
- **正文禁含 `quality_score`**:质量分由前端从 `state.quality_report` 单独渲染徽章(防泄分,Reviewer R10 盯)。
- 报告**标题**直接吃 `focus`:`# Cursor vs Windsurf vs GitHubCopilot — 代码补全体验 竞品报告`;头部一行吃 `purpose`:`目的: 学习竞品优点…`。

---

## 环节 6 · Reviewer → guard_revise — 自检 + 确定性修订定终态

**做什么**:
1. **Reviewer**([`reviewer.py`](../src/reviewer.py))跑 R0-R10 规则,**只审不修**,产出 `quality_report`(errors 带 `reject_target` 归因)。关键规则:
   - R1/R4/R5 hard_gate(Demo 默认 `minimal` 模式);R9 chip 可溯源自检;R10 禁泄分。
   - 例:R9 会校验报告里每个 `[SXXXXXXX]` 是否真存在于 `raw_evidence`;R4 校验每条 rec 有 feature/pain/pricing 推理链。
2. **guard_revise**([`guard.py:178`](../src/guard.py:178)):消费 Reviewer 发现做**一次确定性修订**——清理幻觉引用 / G1 强对比对账 / G2 basis 对账 / 泛化收敛。只降级/删除,**不新增内容**。
3. **终态规则**:有修订 → `passed`(writer 重渲染出货);零修订 → `degraded`(报告外层包一层分层说明)。54 run 仅 1 次触发实质修订。

**最终产出**:`report_draft`(Markdown,带溯源 chip)+ `quality_report`(前端徽章)→ END。

---

## 一图总览:focus 与 purpose 的"渗透深度"

| 环节 | analysis_focus | analysis_purpose |
|---|---|---|
| Intake | 出选择题、进 meta | 出选择题、进 meta |
| Analyzer facts | **决定功能树对比哪些功能** + 取证关键词 + 选权重 | 进 payload(弱) |
| Analyzer derivations | 进 payload,约束"不泛化" | 进 payload,但 Prompt 未显式调用(隐式) |
| judge 评测 | — | **按 purpose 切换评分权重档** |
| Writer | **进报告标题** | 报告头一行 |
| 前端 UI | **「分析对象」展示** | 不展示 |

> 结论:`focus` 是贯穿采集→分析→渲染的主心骨;`purpose` 真正落地的是「评测权重 + markdown 头」,它对"建议取向"的影响目前还是隐式的——是可加固的点。
