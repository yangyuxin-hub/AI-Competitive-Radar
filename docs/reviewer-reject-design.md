# Reviewer 打回 + 自主采补 设计（v2.3 提案）

> 目标：把「整体重试」升级为「**各环节自检 → 自主判断缺了什么 → 定向采补**」的自愈闭环。
> 现状基线：`src/reviewer.py`（R0-R8）+ `src/collector.py`（patch_by_requirements）+ `src/graph.py`（按 target 路由）。

---

## 1. 现状与三个核心问题

| # | 问题 | 证据（代码） | 后果 |
|---|------|------|------|
| P1 | 多缺口**塌成单一 target** | `reviewer.py:829` `Counter(reject_target)` 取 max | 一轮只修一类，collector+analyzer 同时缺时要多轮 |
| P2 | 缺口的**产品维度丢失** | issue 只有 `location="pricing_model.Asana"` 字符串，无显式 `product` | 采补无法按产品缩窄 |
| P3 | collector 收到打回**全量重抓** | `collector_node` 对所有 product 跑 URL discovery + `fetch_all`，仅 `patch_by_requirements` 末端按 claim_type 筛 | 只缺「Asana 定价」却重抓 3 产品 × 4 类，撞墙钟超时风险，retry 预算只有 1 次更脆 |

---

## 2. 设计目标（对齐需求）

1. **各环节都能查**：collector / analyzer / writer 三段产出各有体检项，不只查最终 schema。
2. **自主决策缺什么**：把「缺失」表达成结构化 **Gap**，精确到 `(stage, product, claim_type, field)` + **该怎么补**。
3. **定向采补**：只对 Gap 命中的坐标重新检索/重算，不碰无关产品与字段。

---

## 3. 总体架构：自愈闭环

```
       ┌───────────────────────────────────────────────────────────┐
       │                     Reviewer（诊断中枢）                     │
 输出→ │  stage checks → Gap[] → 去重/合并 → 按 stage 分组 → 采补计划   │ →路由
       └───────────────────────────────────────────────────────────┘
          │collector gaps        │analyzer gaps        │writer gaps
          ▼                      ▼                     ▼
   定向补采(只缺口product×        定向重算(只重跑缺口      局部重渲染
   claim_type，定制query)         section，复用已有证据)    (不重抓不重算)
          └──────────────► 回到 Reviewer 复检（只复检该 Gap 是否闭合）
```

关键变化：**Reviewer 从「打回判官」升级为「缺口诊断 + 采补规划中枢」**，产出的不再是单一 `reject_target`，而是一组带「修复策略」的 Gap。

---

## 3.5 前置采集验收门（核心：缺失早发现，早补，不等分析后）

> 设计原则：**采集完立刻自检**——信息缺失在 Collector 出口就拦下、内部补齐，达标才交 Analyzer。
> Reviewer 的 R0/R8 退化为**安全网**（极少触发），不再是发现缺口的主路径。

现状问题（已用代码坐实）：`collector_node` **无内部自愈循环**（`while-loop=False`），它 `coverage`/`official_check` **只测量不行动**，缺口要等 analyzer 跑完、Reviewer R0/R8 才发现 → 长链路、浪费一整轮分析。

### A. 采集指标（可量化验收标准，建议下沉 config）

每产品逐项打分，全过才算「采集达标」：

| 指标 | 阈值（默认，可配） | 不达标含义 |
|------|------|------|
| `claim_type_coverage` | 4 类各 ≥1；关键类(pricing/feature) ≥2 | 某类没证据，分析必出 unknown |
| `official_evidence` | ≥1 条官网/定价页 | 功能/定价无权威源 |
| `total_evidence` | 每产品 ≥8 条 | 样本太薄，结论不稳 |
| `pricing_has_number` | 定价证据含真实价格数值（非全 $0/None） | 抽到页面没抓到价 |
| `freshness_ratio` | ≥60% 证据在近 12 个月 | 信息过时 |
| `relevance_ratio` | ≥70% 证据真提到产品名 | 召回污染（呼应 V2EX 理财 bug） |

### B. 审查 → 采集 Gap

验收门 = 纯函数 `audit_collection(evidence, meta) -> list[Gap]`，对每个未达标指标产出一条采集 Gap（精确到 `product × claim_type × 指标`），带 `fix` 处方（去哪补、用什么词）。**这部分无 LLM、毫秒级**，每轮采集后必跑。

### C. 内部自愈循环（「没达标就继续想办法」）

```python
def collect_with_gate(state, max_inner_rounds=2):
    evidence = initial_collect(state)            # 冷启动全量
    for round in range(max_inner_rounds):
        gaps = audit_collection(evidence, meta)  # 毫秒级自检
        if not gaps: break                       # 达标 → 出门
        patch = targeted_refill(gaps, strategy_ladder[round])  # 只补缺口
        evidence = merge_dedupe(evidence, patch)
    # 仍未闭合的 gap → 记入 collection_meta.unfilled_gaps（诚实标注,不让分析编)
    return evidence, remaining_gaps
```

### D. 策略升级阶梯（自主「想办法」的灵魂）

同一缺口补不到，**换策略而非重复同一个动作**（呼应 PUA「换根本性方法」）：

| 阶梯 | 动作 | 适用 |
|------|------|------|
| L0 | 站内定向检索（reddit/g2/官网 site 锚定 + 定向词） | 首选 |
| L1 | 全网检索（去掉 site 限制，放宽相关性门到 0.5） | L0 空 |
| L2 | 产品别名/中英互译再搜（可灵↔Kling、Copilot↔GitHub Copilot） | 召回 0 |
| L3 | 官网 SPA 渲染（Playwright）补定价/功能 | pricing/feature 缺且有官网 URL |
| L4 | 放弃该 gap → `unfilled_gaps` 标注「该数据不可得」 | 阶梯耗尽 |

> 颗粒度对齐：阶梯是**逐 gap 独立推进**的——Asana 定价缺走 L3 渲染官网，Cursor 痛点缺走 L0/L1 搜 Reddit，互不干扰、并行。

### E. 与 Reviewer 的分工（拉通）

| | 旧（被动） | 新（主动前置） |
|---|---|---|
| 谁先发现缺口 | Reviewer R0/R8（分析后） | **采集验收门（分析前）** |
| 补采时机 | 打回 → 重走 collector | 采集环内部即时补 |
| Reviewer 角色 | 缺口主探测器 | **安全网 + 分析/渲染层质检**（R1-R6/R9/R10） |
| 触发全量重分析 | 经常 | 几乎不（采集已达标） |

---

## 3.6 质量指标与证据预处理（「好不好」，不只「有没有」）

> §3.5 是**数量门（有没有）**；本节是**质量门（好不好）**。一条证据可能「有」但是营销空话、没数字、过时、离题——数量达标质量却废。
> 现状：证据已带 `source_reliability / claim_relevance / evidence_confidence / source_bias / source_freshness` 字段，但**只用于排序截顶，从不当门**。实测数据 57% 是 `vendor_claim` 厂商自夸 → 痛点类质量堪忧。

### A. 单条证据质量打分（预处理，主要靠确定性信号，毫秒级）

`score_quality(e) -> e["quality_score"] ∈ [0,1]`，加权融合：

| 信号 | 怎么算（无 LLM） | 抓什么坏 |
|------|------|------|
| 信息量/具体性 | 有数字(`\d`)、版本、场景词、对比词；长度健康区间 | 「很好用」式空话 |
| 去营销味 | 命中 fluff 词典(革命性/seamless/极致/领先/world-class)且无事实 → 扣分 | 官网 slogan 堆砌 |
| 片段完整性 | `extracted_snippet` 非空、非导航/页脚样板(无 Cookie/©/Skip to content)、≥N 字 | 抓到壳没抓到肉 |
| 相关性 | 复用 `claim_relevance` + 产品/focus 命中 | 离题(理财帖那种) |
| 来源权威 | 复用 `source_reliability`(官网>测评>UGC>合成) | 道听途说 |
| 时效 | 复用 `source_freshness` | 过期信息 |

可选 **LLM 质量评审**（仅 deep 档 + 仅边界样本）：批量判「这条是否具体、可信、真支撑对 <product> <focus> 的判断」。贵，只在确定性分模糊(0.4-0.6)的小集上跑，不全量。

### B. 质量感知的验收门（和数量门并联）

每个 `(product × claim_type)` 要同时过：

| 门 | 判据 |
|------|------|
| 数量（有没有） | 证据数 ≥ N（§3.5） |
| **质量（好不好）** | ≥1 条 `quality_score ≥ 0.65`，且该桶**均质量 ≥ 0.45** |
| **偏置平衡** | 痛点/性能类**不能 100% vendor_claim** → 需 ≥1 条 user_generated/third_party |
| **定价含金量** | 定价桶 ≥1 条有**真实价格数值**（不是「联系销售」「敬请期待」） |

### C. 预处理流水线（插在采集后、交分析前）

```
raw_evidence
  → score_quality(e)            # 每条打质量分
  → 丢垃圾(quality < 0.25)        # 空片段/导航壳/纯 fluff,直接删,减 token 防超时
  → audit 门(数量+质量+偏置+定价)  # 产出 Gap(含"质量 Gap")
  → 仍缺 → 定向补(见 D)
  → cap_evidence(改为质量加权)    # 截顶时高质量优先,而非只看 confidence
```

### D. 质量 Gap 驱动**不同**的补法（关键：让「想办法」更聪明）

| Gap 类型 | 症状 | 补法（策略阶梯指向不同） |
|------|------|------|
| 数量 Gap | 某类 0 证据 | L0/L1 检索该类 |
| **质量 Gap·定价无数值** | 有定价证据但全是「联系销售」 | 直接跳 **L3 官网 SPA 渲染**(真价格在 JS 渲染的档位表里) |
| **质量 Gap·全厂商自夸** | 痛点类只有 vendor_claim | 定向搜 **Reddit/G2 真实吐槽**(user_generated) |
| **质量 Gap·全是空话** | 有证据但无数字/场景 | 换**测评/对比类**信源(g2/benchmark) |

> 底层逻辑：数量门答「缺不缺」，质量门答「补来的能不能用」。没有质量门，自愈循环会被「营销空话」骗过——数量达标就出门，结果分析全是厂商话术。质量 Gap 让采补**带着目的去找对的东西**，而不是凑数。

---

## 4. 各环节检查矩阵（复用 R0-R8 + 补缺）

| 环节 | 检查 | 现有规则 | 缺什么 → 补什么 |
|------|------|---------|----------------|
| **Collector** | 4 类 claim_type 是否齐（每产品） | R0 | 某产品某类=0 → 定向补该 (product×claim_type) |
| | 官网证据是否抓到 | R8(官网check) | 官网 0 证据 → 重试官网/SPA 渲染，换 pricing_page |
| | 证据时效 | R7 freshness | 过期 → 限定近 N 月重搜 |
| **Analyzer** | evidence_id 是否真实存在（抑制幻觉） | R1 | 引用不存在 → 重算该字段（不补采，是分析错） |
| | claim_type 与字段是否兼容 | R2 | 用错类证据 → 重算该 section |
| | 聚合/均分是否自洽 | R3 | 加总对不上 → 重算 scoring |
| | 推理链是否完整（结论有支撑） | R4 | 断链 → 重算该 derivation |
| | 结构是否自相矛盾 | R5 | 矛盾 → 重算冲突字段 |
| | 内容是否整缺（定价/功能整列塌） | R8 | 整缺 → **回 collector** 补采，不是 analyzer 重算 |
| | 语义是否扎根证据（LLM 判） | R6 | 编造/夸大 → 重算该结论 |
| **Writer** | 正文 chip `[SXXXXXXX]` 是否都可溯源 | （新增 R9） | 正文引用不在 schema → 重渲染 |
| | 是否泄漏 quality_score 进正文 | （新增 R10） | 命中 → 重渲染（禁词） |

> 红线区分（关键决策）：**「内容整缺」走 collector 采补，「分析做错」走 analyzer 重算，「渲染问题」走 writer 重渲**。同一症状要判断根因落在哪一环——这是「自主决策」的核心分诊逻辑。

---

## 5. Gap 数据模型（自主决策缺什么）

把现有扁平 issue 升级为带「修复处方」的 Gap：

```python
Gap = {
  "gap_id": "sha1(stage|product|claim_type|field)[:8]",
  "stage":  "collector" | "analyzer" | "writer",   # 根因落点（分诊结论）
  "rule":   "R0",                                   # 触发规则，可溯源
  # —— 精确坐标（替代 location 字符串反解）——
  "product": "Asana",          # 显式产品（None=全局）
  "claim_type": "pricing",     # 缺哪类证据（analyzer/writer gap 可空）
  "field": "pricing_model.Asana.tiers",  # 缺哪个 schema 字段
  "reason": "Asana 未产出任何定价档位",
  # —— 修复处方（采补/重算怎么做）——
  "fix": {
    "strategy": "recollect" | "reanalyze" | "rerender",
    "query_hint": "Asana pricing plans per seat",   # 给检索的定向词
    "source_hint": ["pricing_page", "g2.com"],       # 优先去哪找
    "scope": "product"        # product / field / global
  },
  "severity": "error" | "warning",   # 由 MODE_CONFIG 改写
}
```

**自主决策 = 规则函数直接产出带 `fix` 处方的 Gap**，而不是只报「哪里错」。比如 R8 定价整缺 → 产出 `strategy=recollect, query_hint="<product> pricing", source_hint=[pricing_page]`。

---

## 6. 采补规划器（定向，不全量）

新增 `collector.targeted_recollect(state, gaps)`：

```python
def targeted_recollect(state, gaps):
    # 1) 只取 collector gaps，按 product 聚合需要的 claim_types
    need = {}                       # {product: {claim_types}, ...}
    for g in gaps:
        if g["stage"] != "collector": continue
        need.setdefault(g["product"], set()).add(g["claim_type"])
    # 2) 只对缺口 product 跑（不动其它产品），URL discovery 仅当缺 pricing/feature 才跑
    # 3) source_planner 只规划缺口 claim_type 的 query，注入 gap.fix.query_hint / source_hint
    # 4) feature_targeted_evidence / web_search 定向抓 → patch 进 raw_evidence（按 id 去重）
    # 5) 跳过整轮全量 fetch_all
```

**改动点**：`collector_node` 开头加分支——`if state.get("reject_requirements"): return targeted_recollect(...)`，与冷启动全量采集分流。

收益（以「Asana 缺定价」为例）：
- 现状：3 产品 × URL discovery LLM + 3 × 全 4 类检索 → 末端筛掉一堆；
- 改后：1 产品（Asana）× 1 类（pricing）× 定向 query → 省 ~80% 调用，且避开墙钟超时。

---

## 7. 路由与预算（并行多目标）

| 维度 | 现状 | 改后 |
|------|------|------|
| 目标数 | 单一 target（max count） | 按 stage 分组，**collector 采补与 analyzer 重算可同轮并行** |
| 预算 | `{collector:1, analyzer:2, writer:1}` 按 target | 保留，但按 **Gap 是否闭合**判定，而非「重试过就算用掉」 |
| 复检 | 全量 R0-R8 重跑 | 优先**只复检本轮 Gap 对应规则**是否闭合（快） |
| 兜底 | 预算耗尽 → degraded_writer | 不变；degraded 时按未闭合 Gap 分层标注「哪块数据不可得」 |

> 颗粒度对齐：预算的语义从「这个 target 重试过几次」改成「这个 Gap 补了几次还没闭合」——避免一个产品的缺口耗光整个 collector 预算，连累其它产品。

---

## 8. 落地拆解（分阶段，可增量上线）

| 阶段 | 改动 | 文件 | 风险 |
|------|------|------|------|
| S1 | issue 加显式 `product` 字段（规则函数填，不再反解 location） | `reviewer.py` 各 `_mk_issue` 调用 | 低 |
| S2 | `targeted_recollect` + collector_node 分支（只补缺口 product×claim_type） | `collector.py` | 中（核心收益） |
| S3 | Gap 模型 + `fix` 处方（query_hint/source_hint） | `reviewer.py` | 中 |
| S4 | 新增 R9/R10（writer 自检：chip 可溯源 / 禁泄 score） | `reviewer.py` | 低 |
| S5 | 并行多目标路由 + 按 Gap 闭合判预算 | `graph.py` `reviewer.py` | 高（最后做） |

**建议先做 S1+S2**：90% 的浪费在「全量重抓」，S2 单独上线就能止血；S3-S5 是体验与泛化增强。

---

## 8.5 各环节节点：审查 / 测试 / 补采标准（细化 spec）

> 每个节点都按统一三件套定义：**审查标准**（出口必须满足什么）、**测试标准**（怎么验证它工作）、**补采/修复方案**（不达标怎么办、回谁）。

### ① Intake（意图澄清）
| 项 | 标准 |
|---|---|
| 审查 | 解析出 target + ≥2 竞品 + ≥1 focus + intent(枚举内)；竞品非幻觉（在已知品类或可解析）；focus 与输入语义相关（非泛化跑分） |
| 测试 | 单测：中/英/混输入→字段非空、竞品去空格 PascalCase、intent∈枚举；边界：模糊输入("分析下AI工具")→触发澄清问而非瞎猜；mock：无 key 走启发式仍出候选 |
| 补采/修复 | 字段缺→二次澄清提问 / domains.yaml 默认补全 / 降级 heuristic 候选。**不回退采集**（这是入口） |

### ② Collect（采集）— 已实现，标准固化
| 项 | 标准 |
|---|---|
| 审查 | **数量门**：每产品 4 类覆盖(关键≥2)、官网≥1、总量≥6；**质量门**：定价含金量、偏置平衡、avg_quality≥0.5；相关性≥70% 真提产品 |
| 测试 | 单测(已 11 个)：构造缺口必触发、达标不误杀；集成：Cursor(有兜底)/可灵(无兜底)验收门判定正确；自愈：构造 Gap→闭合可闭合、honest 留不可得 |
| 补采 | 策略阶梯 **L0 站内定向→L1 全网放宽→L2 别名/中英互译→L3 官网 SPA 渲染→L4 标注不可得**；逐 gap 独立并行 |

### ③ Analyze（分析）
| 项 | 标准 |
|---|---|
| 审查 | evidence_id 全真实(R1, 0 幻觉)；每事实结论 ≥1 证据支撑(R4)；claim_type 与字段兼容(R2)；聚合/均分自洽(R3)；无结构矛盾(R5)；证据不足→输出 unknown(不编) |
| 测试 | 单测：注入幻觉 id→R1 必抓、断链→R4 必抓；quick_validate 各 section 校验有单测；mock 走 sample_report 闭环。**注：R6 现有 4 个预存失败待修** |
| 补采/修复 | 幻觉/断链/矛盾→**analyzer 重算该 section**(分析错,不补采)；内容整缺(定价/功能整列塌)→**回 collector 采补**(R8)；unfilled_gaps→该字段标「数据不可得」 |

### ④ Write（渲染）
| 项 | 标准 |
|---|---|
| 审查 | 每条 claim 句末 chip `[SXXXXXXX]` 且 id 在 schema 存在(新 R9)；正文**不含** quality_score(新 R10)；11 模块结构完整无空段 |
| 测试 | 单测：chip 正则匹配、禁词扫描、模块齐全；渲染快照：字数/模块数在合理区间 |
| 补采/修复 | chip 不可溯源/泄分/缺段→**writer 重渲**(不重抓不重算) |

### ⑤ Review（质检安全网）
| 项 | 标准 |
|---|---|
| 审查 | R0-R10 跑全，hard_gate 按 mode(minimal/full) |
| 测试 | 每条规则有正例(过)/反例(打回)单测；路由：多 Gap 分诊到正确 target；预算耗尽→degraded |
| 补采/修复 | 按 reject_target 路由 + 预算{collector:1,analyzer:2,writer:1}；耗尽→degraded_writer 分层标注 |

---

## 8.6 高质量源台账（Source Ledger）

> 把「每次重新发现官网/社区」变成「**学习过的源复用**」。持久化 `data/source_ledger.json`。

**结构**（按 品类×桶×domain 累计 EWMA 质量 + hits + recency）：
```json
{
  "by_category": {
    "ai_video": {
      "official":  [{"domain":"klingai.com","q":0.82,"hits":12,"last":"2026-06-07"}],
      "community": [{"domain":"reddit.com","q":0.90,"hits":8}],
      "review":    [{"domain":"g2.com","q":0.71,"hits":5}]
    }
  },
  "by_product": {"可灵Kling": {"official":["klingai.com"], "community":["reddit.com"]}}
}
```
**写入点**：采集验收门后，把本轮 `quality_score≥0.65` 的证据按 `(category, source_type→桶, domain)` 累计（EWMA 更新质量、hits+1、last 刷新）。
**读取点**：① URL discovery 先查 ledger 同产品/品类 official domain → **跳过 LLM 发现**；② source_planner 规划时把 ledger 高分 community/review domain 作 site 锚定**优先**。
**容量/衰减**：每桶 top-K，按 `q × log(1+hits) × recency` 排序，旧的自然沉底。

## 8.7 各环节质量评估（Stage Eval）

> 落 `logs/stage_quality.jsonl`，每 run 每段一行，看**哪段是瓶颈**。

```json
{"ts","run_id","stage":"collect","verdict":"pass|warn|fail","elapsed_sec":42,"llm_calls":3,
 "metrics":{"coverage_ratio":0.9,"avg_quality":0.70,"official_ratio":0.5,"relevance_ratio":0.8,"gaps_open":1}}
```
各段 metrics：
- **intake**: fields_filled_ratio, competitors_resolved, elapsed
- **collect**: coverage_ratio, avg_quality, official_ratio, relevance_ratio, gaps_open, elapsed, llm_calls
- **analyze**: hallucination_count(应 0), unknown_ratio, reasoning_complete_ratio, quick_validate_fixes, elapsed, llm_calls
- **write**: chip_traceable_ratio, banned_word_hits, module_complete, elapsed
- **review**: rules_failed, rounds, quality_score, elapsed

实现：helper `log_stage_quality(stage, metrics, elapsed, run_id)` append jsonl；各 node 末尾调用。用途：聚合看板暴露「analyze 耗时占 70% / collect 相关性最低」这类瓶颈，驱动定点改进。

---

## 8.8 评分准则优化 + 分数驱动补救矩阵（v2.3）

### A. 评分准则:集中 + 去重(已落地 config/scoring.yaml)

原问题:7 套评分散落、覆盖率被算 4 遍、阈值硬编码。优化后**单一口径**:

| 层 | 评分 | 口径(权重/阈值集中在 `config/scoring.yaml`) |
|---|---|---|
| 证据 | `quality_score` [0,1] | specificity .28/integrity .16/relevance .22/authority .22/freshness .12 |
| 采集门 | 数量门 + 质量门(pass/fail) | coverage_min/total_min/official_min + q_high .65/q_mid .45 |
| 分析 | `priority_score` 1-5 | pain .35/impact .30/feasibility .20/evidence .15 |
| 质检 | `quality_report` 0-100 | 6 维加权 + `min(rule_score, dimensional)` |
| 评测 | judge(LLM 4维) + completeness(确定性) | quality_rubric.yaml |

**去重原则**:`report_completeness`(质检) 统一调 `completeness_metrics`(评测)，不再各算一份；`traceability`/`evidence_coverage` 共用 schema 引用覆盖率函数。**单条证据质量统一 `quality_score`**，`evidence_confidence` 降级为仅 collector 内部排序兜底(待退役)。

### B. 分数驱动补救矩阵(统一三动词:采补 / 重算 / 重渲)

> 底层逻辑:**哪个分低 → 触发哪个补救 → 升级阶梯 → 耗尽则诚实降级**。同一症状只走一条链,不重复打回。

| 环节 | 触发分/门 | 阈值 | 补救动作 | 升级阶梯 | 耗尽后(诚实降级) |
|---|---|---|---|---|---|
| Collect | 数量门 coverage_short | <coverage_min | **采补**该 product×claim_type | L0 站内→L1 全网→L2 别名 | 标 `unfilled_gaps` |
| Collect | 质量门 pricing_no_number | 无价格数值 | **采补**定价 | L3 官网 SPA 渲染 | 标「定价不可得(如积分制)」 |
| Collect | 质量门 bias_all_vendor | 全 vendor | **采补** UGC | L0 Reddit/G2 定向 | 标「仅厂商口径,审慎」 |
| Collect | 官网门 no_official | <official_min | **采补**官网 | 台账命中→L3 渲染 | 标「无权威源」 |
| Analyze | R1 幻觉 / R4 断链 | >0 | **重算**该 section | 收紧 prompt 约束 | 该字段输出 `unknown` |
| Analyze | R8 内容整缺 | 定价/功能整列塌 | **回 Collect 采补** | 同上采补链 | degraded 分层 |
| Write | R9 chip 不可溯源 | >0 | **重渲** | — | 删该 chip + 标注 |
| Write | R10 泄 quality_score | 命中 | **重渲** | — | — |
| Review | quality_score 低 | <阈值(warn) | 按 reject_target 路由 | 预算 {c:1,a:2,w:1} | degraded_writer |

**核心改进**:
1. **诚实降级是一等公民**——每条补救链末端都有「标注不可得」出口,绝不靠编数掩盖(对齐核心原则#4)；`unfilled_gaps` 落 `collection_meta`,analyzer/writer 读它把对应字段写成「数据不可得」而非 `unknown` 占位。
2. **分数→补救是确定性映射**——不再"出问题再让 Reviewer 猜打回谁",而是分数低于阈值即知补救动作与升级路径。
3. **阈值可调**——所有触发线在 `scoring.yaml`,换行业/换严格度零改码。

---

## 9. 与核心原则对齐（CLAUDE.md §2）

- **证据覆盖率可控**：Gap 显式表达 (product×claim_type) 覆盖缺口，采补定向闭合。
- **失败可降级**：预算耗尽仍走 degraded_writer，按未闭合 Gap 分层。
- **证据链可复现**：采补只 patch（按 evidence_id 去重），不覆盖，溯源链不断。
- **抑制幻觉**：analyzer 类 Gap 走重算不走采补——分析错不该用补数据掩盖。
