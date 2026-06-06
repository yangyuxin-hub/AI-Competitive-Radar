# 竞品分析链路 · 环节 I/O 契约与审核手册

> 用途:把整条链路拆成 8 个**可独立测、可回溯审核**的环节,逐环定义「输入 / 怎么做 / 输出」契约。
> **契约即报告质量天花板**——任何一环的输出不符契约,下游必然连环坏。改代码前后都对着这份文档核口径。
> 字段名均取自源码真实定义(`state.py` / `data/sample_report.json` / `collector.py` / `reviewer.py`),非示意。

---

## 0. 怎么用这份文档

1. **冻结 fixture 原则**:每环跑完把输出冻结成固定文件,作为下一环的输入。这样某环出错 = 一定是这环的锅,不被上游 LLM/网络随机性干扰。
2. **隔离测**:每环都能「喂固定输入 → 出确定输出」单独跑,不必每次等 5 分钟全链路。
3. **审核记录**:每次跑落盘到 `runs/<run_id>/<stage>/`(见 §11),人可逐层下钻核到「这条结论凭哪条证据、证据来自哪个 URL、那次 LLM 喂了什么」。

链路总览:

```
user_input
  ├─ S1 意图澄清   intake.propose          → analysis_meta
  ├─ S2 源规划     source_planner.plan_sources → search_plan[]
  ├─ S3 证据搜集   collector.fetch_all     → raw_evidence[]   ★最关键冻结点
  ├─ S4 证据清洗   _compact_evidence       → compact_evidence[]
  ├─ S5 事实分析   _step1_facts            → facts{feature_tree,pricing_model,user_persona}
  ├─ S6 衍生分析   _step2_derivations      → derivations{swot,recommendations,landscape,positioning}
  ├─ S7 渲染       writer_node             → report_draft (markdown)
  └─ S8 评审       reviewer_node           → quality_report + 打回/降级
```

---

## 1. 贯穿全链的共享契约

### 1.1 `AgentState`(LangGraph 流转的状态,`state.py`)

```jsonc
{
  "user_input": "分析 Cursor 和 Windsurf 在代码补全体验上的差距",
  "analysis_meta": { /* 见 1.2 */ },
  "raw_evidence":  [ /* 见 1.3,S3 产出 */ ],
  "schema_draft":  { /* 见 1.5,S5+S6 产出 */ },
  "report_draft":  "## 一、Executive Summary …",   // S7 产出
  "quality_report":{ /* 见 S8 */ },
  "collection_meta":{ /* S3 的采集统计:coverage/health/adapter_events */ },
  "reject_target": "collector|analyzer|writer|null",  // S8 打回目标
  "reject_requirements": [ /* S8 → S3 的精准补证据需求 */ ],
  "retry_count": {"collector":0,"analyzer":0,"writer":0},
  "max_retries_per_target": {"collector":1,"analyzer":2,"writer":1},
  "status": "running|passed|degraded|failed"
}
```

### 1.2 `analysis_meta`(整条链的"任务说明书",S1 定稿后只读)

```jsonc
{
  "report_id": "CR-20260606-001",
  "schema_version": "2.1",
  "target_product": "Cursor",
  "competitors": ["Windsurf", "GitHubCopilot"],   // 与 products.yaml key 一致,PascalCase 去空格
  "analysis_focus": ["代码补全体验"],
  "analysis_purpose": "学习竞品优点,优化自身产品",
  "analysis_intent": "feature_compare",  // pain_attribution|selection|pricing|market_entry|feature_compare
  "runtime_profile": "fast|balanced|deep",
  "generated_at": "2026-06-06T…Z",
  "data_cutoff": "2026-06-06",
  "agent_trace_id": "trace_20260606…"
}
```

### 1.3 `raw_evidence` 单条(S3 产出,全链最底层的"事实原子")

```jsonc
{
  "evidence_id": "S1A2B3C4",          // "S"+sha1(product|url|claim)[:7].UPPER = 8 字符,确定性,可复现
  "product": "Cursor",
  "claim_type": "pricing",            // feature_existence | performance_quality | pricing | user_pain
  "source_type": "official_page",     // official_page|web_search|simulated_interview|hn|reddit|v2ex|mock
  "source_bias": "vendor_claim",      // vendor_claim|third_party|user_generated|synthetic
  "source_url": "https://cursor.com/pricing",
  "observed_at": "2026-06-06",
  "source_freshness": "current",
  "claim": "Pro plan $20 per month",          // ≤120 字的结论句
  "extracted_snippet": "Pro — $20/mo …",       // 原文片段(分析只能基于它)
  "source_reliability": 0.85,         // 官网 0.85 / 合成访谈 0.40 …
  "claim_relevance": 0.75,
  "evidence_confidence": 0.70,
  "collection_source": "live",        // live|cache|mock|skill:hn|skill:v2ex
  "metadata": { /* 合成访谈才有:persona/question_id/expectation */ }
}
```

> **evidence_id 是溯源命脉**:S5/S6 所有结论的 `evidence_ids` 必须引用真实存在的 id;`sanitize_schema_evidence_refs` 会删掉任何不存在的引用(抗幻觉硬执行)。

### 1.4 `schema_draft` 顶层(S5+S6 合并产出)

```
{ analysis_meta, feature_tree, pricing_model, user_persona,   // ← S5 facts
  swot, recommendations, competitor_landscape, positioning_map, // ← S6 derivations
  research_method }                                            // ← survey skill(可选)
```

---

## 2. S1 意图澄清 · `intake.propose` / `propose_stream`

| | |
|---|---|
| **输入** | `user_input: str`(一句话意图) |
| **怎么做** | ① `_detect_intent` 关键词判意图类型 → ② `_competitor_web_context` 搜「{target} alternatives 2026」捞新锐/国产 → ③ `_propose_via_llm`(读 `prompts/intake.md`)抽竞品+焦点+目的,无 key 走 `_propose_heuristic` → ④ `_canonicalize_draft` 对齐 products.yaml 命名、去重、剔 target。流式版逐字推 `reasoning` |
| **输出 `draft`** | 见下 |

```jsonc
{
  "analysis_intent": "feature_compare",
  "domain_name": "AI 编程工具",
  "target_candidates": ["Cursor", "Windsurf", "GitHubCopilot"],   // [0] 最可能
  "competitors_candidates": ["Windsurf","GitHubCopilot","Cline","Aider"],
  "competitors_suggested": ["Windsurf","GitHubCopilot"],          // 推荐先选 2-3,覆盖不同竞争逻辑
  "competitor_hints": {"Cline":"【新锐AI】开源 Agent…"},
  "focus_candidates": ["代码补全体验","Agent 能力","定价"],
  "focus_hints": {"代码补全体验":"补全准确率/延迟/跨文件…"},
  "focus_suggested": "代码补全体验",
  "purpose_candidates": ["…"], "purpose_suggested": "…",
  "reasoning": "识别为 AI 编程工具品类,目标 Cursor;竞品覆盖直接(Windsurf)+大厂(Copilot)+新锐(Cline)…"
}
```

**隔离入口**:`intake.propose("…")` / 流式 `intake.propose_stream("…")`(逐 `("status"|"reasoning"|"draft", payload)`)
**审核看点 🚩**:① 竞品有没有漏**主流/新锐/国产/开源**?② intent 分类对不对(痛点归因 ≠ 功能跑分)?③ focus 贴不贴这句话?④ 流式有没有 `reasoning` 事件流出?

---

## 3. S2 源规划 · `source_planner.plan_sources`

| | |
|---|---|
| **输入** | `plan_sources(product, competitors, analysis_focus, missing_claim_types=None, domain=None)` |
| **怎么做** | 按 claim_type 映射权威源(`recommended_for`:定价→官网, 痛点→社区/UGC, 功能→官网/文档),`_build_query` 造**语言一致**的 query + 站点锚定(`site:`) |
| **输出 `search_plan[]`** | 每条:`{ claim_type, query, site, source_type, bias }` |

```jsonc
[ { "claim_type":"pricing", "query":"Cursor pricing plans 2026",
    "site":"cursor.com", "source_type":"official_page", "bias":"vendor_claim" },
  { "claim_type":"user_pain", "query":"Cursor 补全 慢 抱怨",
    "site":"reddit.com", "source_type":"web_search", "bias":"user_generated" } ]
```

**隔离入口**:`source_planner.plan_sources(...)`
**审核看点 🚩**:① query 是否**中英混搭**(应一致)?② 定价是否锚官网、痛点是否锚社区?③ 缺的 claim_type 有没有被规划补?

---

## 4. S3 证据搜集 · `collector.SourceRegistry.fetch_all` ★最关键

| | |
|---|---|
| **输入** | `product: str, focus: str`(逐产品调用) |
| **怎么做** | **三层降级**:① live(`OfficialPageAdapter` httpx→Playwright 渲染抓官网+定价页;`SearchAdapter` Brave→Tavily→DDG;skills HN/V2EX/reddit 并行)→ ② cache(按缺失 claim_type 补)→ ③ mock(兜底保 4 类覆盖)。`infer_claim_type` 加权分类;`dedupe_evidence` 按 id 去重;算 coverage/health |
| **输出** | `(raw_evidence[], collection_meta)` |

```jsonc
// collection_meta
{ "product":"Cursor", "coverage":{"feature_existence":9,"performance_quality":8,"pricing":7,"user_pain":8},
  "source_summary":{"official_page":12,"web_search":11,"reddit":9},
  "health":"ok|partial|empty", "missing_claim_types":[],
  "adapter_events":[{"adapter":"OfficialPageAdapter","status":"success","count":12}, …] }
```

**隔离入口**:
- 单 adapter:`OfficialPageAdapter().fetch("Cursor","代码补全体验")`(单测官网渲染/定价提取/分类)
- 整合:`registry.fetch_all("Cursor","代码补全体验")`

**审核看点 🚩**:① **4 类 claim_type 是否全覆盖**?② **定价条目有没有真实档位价**(不是功能 bullet 被误标 pricing)?③ 官网 SPA 渲染兜底有没有触发?④ 分类有没有串台?⑤ evidence_id 是否确定性可复现?

---

## 5. S4 证据清洗 · `_compact_evidence`

| | |
|---|---|
| **输入** | `raw_evidence[]`(全量) |
| **怎么做** | 按 `(claim_type × product)` 分桶 → 各取 top-8(按 `evidence_confidence`)→ 近似去重(token Jaccard ≥ 0.82)→ 片段截 180 字。**全量证据仍保留作 id 校验**,只精简喂 LLM 的部分 |
| **输出** | `compact_evidence[]`:`{evidence_id, product, claim_type, source_bias, claim, extracted_snippet}` |

**隔离入口**:`_compact_evidence(raw_fixture)`
**审核看点 🚩**:① 同义重复有没有被合并(8 槽位装 8 个不同点)?② 低可信产品有没有被全局挤光(分桶应防住)?

---

## 6. S5 事实分析 · `_step1_facts(evidence, meta)`

| | |
|---|---|
| **输入** | `raw_evidence[]` + `analysis_meta` |
| **怎么做** | `_feature_spine` 出维度骨架(喂证据,排除计划/配额维度)→ `_feature_fill` 逐产品并行填(support_status 看官网/feature_existence,quality_score 看 UGC,二者解耦)→ 组装 + `_compute_gap` + **剪枝全`—`行(floor=2)** → `pricing_model`/`user_persona` 并行 → `quick_validate_facts`+确定性 `sanitize` → **gap-refill 多轮**(`_coverage_gaps` 找缺口 → `_gap_targeted_recollect` 定向补→局部重出;per-product 定价熔断 + section 闸门防空转) |
| **输出 `facts`** | 见下三块 |

```jsonc
// feature_tree
{ "category":"代码补全",
  "features":[ {
    "feature_id":"F001", "name":"多行 / 跨文件补全",
    "products":{ "Cursor":{
        "support_status":"supported|not_supported|unknown",
        "support_evidence_ids":["SCABE001"],
        "quality_score":{ "score":4,"scale":5,"basis":"…",
          "aggregation":{"aggregation_type":"sampled_evidence","positive_mentions":3,…},
          "evidence_ids":["SCABE005"] } } },
    "gap":{ "winner":"Cursor","gap_type":"accuracy|performance|feature_completeness|parity_unrated|unknown",
            "reason":"…","evidence_ids":[…],"confidence":0.82 } } ] }

// pricing_model
{ "products":[ { "name":"Cursor", "tiers":[ {
      "tier_name":"Pro","billing_cycle":"monthly",
      "price":{"amount":20,"currency":"USD","normalized_usd_month":20},
      "limits":[{"limit_name":"completions","limit_value":"unlimited","unit":"requests_per_month"}],
      "display_limits":"无限补全 + 500 次快速请求",
      "observed_at":"2026-05-22","source_freshness":"current","evidence_ids":["S…"] } ] } ],
  "pricing_gap":{ "target_position":"more_expensive|cheaper|parity","summary":"…","evidence_ids":[…],"confidence":0.88 } }

// user_persona  ← 注意是 user_segments,不是 personas
{ "user_segments":[ {"segment_id":"U001","name":"独立开发者","description":"…","evidence_ids":[…],"confidence":0.78} ],
  "pain_points":[ {"pain_id":"P001","description":"…",
      "frequency":{"level":"high","count":"4 条中 4 条提及","sample_size":4,"evidence_ids":[…]},
      "affected_products":[…],"affected_segments":[…],"user_expectation":"…","confidence":0.85} ],
  "praise_points":[ {"praise_id":"PR001","description":"…","frequency":{…},"affected_products":[…],"confidence":0.8} ] }
```

**隔离入口**:`_step1_facts(raw_fixture, meta)`;子步骤可单跑 `_feature_spine(...)` / `_feature_fill(...)`
**审核看点 🚩**:① 维度是**能力级**(实时协同/组件系统)还是跑偏成**计划/配额**(团队成员上限/免费版权益)?② 全`—`行剪没剪?③ 定价档位**结构化**了没(有 `normalized_usd_month`)?④ **每个 evidence_id 都真实存在**(零幻觉)?⑤ gap-refill 是否收敛(没空转烧 pricing_model)?

---

## 7. S6 衍生分析 · `_step2_derivations(facts, evidence, meta)`

| | |
|---|---|
| **输入** | `facts` + `raw_evidence[]` + `analysis_meta` |
| **怎么做** | 4 个 section 并行 LLM(swot / recommendations / competitor_landscape / positioning_map);`recommendations` 按权重算 `priority_score.final_score`;`sanitize_derivations` 删无效 evidence/feature/pain 引用 + 重算 priority 保 R5 自洽 |
| **输出 `derivations`** | 见下 |

```jsonc
{ "swot":{ "target":"Cursor","note":"…",
    "strengths":[{"point":"…","evidence_ids":[…],"confidence":0.8}], "weaknesses":[…],"opportunities":[…],"threats":[…] },
  "recommendations":[ {
    "rec_id":"R001","action":"推中端价位档…","rationale":"…",
    "source_feature_ids":["F002"],"source_pain_ids":["P003"],"evidence_ids":[…],
    "priority_score":{"pain_frequency":4,"business_impact":5,"implementation_feasibility":4,
                      "evidence_confidence":3,"weights":{…},"final_score":4.15,"priority":"P1"} } ],
  "competitor_landscape":{ "direct":[{"name":"Windsurf","relation":"direct","reason":"…","evidence_ids":[…]}],
                           "indirect":[…],"alternative":[…],"selection_rationale":"纳入标准…" },
  "positioning_map":{ "products":[{"name":"Cursor","target_user":"…","core_scenario":"…",
                       "value_proposition":"…","positioning_label":"AI IDE","evidence_ids":[…]}] } }
```

**隔离入口**:`_step2_derivations(facts_fixture, evidence_fixture, meta)`
**审核看点 🚩**:① swot/建议每条挂**真实 evidence_id**?② `priority_score.final_score` 与权重自洽?③ competitor_landscape **不编竞品**(name 来自真实证据/meta)?④ source_feature_ids/source_pain_ids 指向 facts 里真实存在的 id?

---

## 8. S7 渲染 · `writer_node(state)`

| | |
|---|---|
| **输入** | 含 `schema_draft` 的 state |
| **怎么做** | 按固定顺序渲染 8 模块 → 每条 claim 句末打 chip `[SXXXXXXX]` → `_renumber_sections` 统一编号 |
| **输出** | `report_draft: str`(markdown);模块序:header→exec→竞品格局→定位→评分总览→功能差距→定价→用户之声→SWOT→建议→证据覆盖→不确定性 |

**隔离入口**:`writer.writer_node({"schema_draft": schema_fixture, …})`
**审核看点 🚩**:① 8 模块齐?② chip 格式 `[SXXXXXXX]` 对(前端靠它跳溯源)?③ **正文禁含 quality_score**(徽章前端单独渲染)?④ 分节编号连续?

---

## 9. S8 评审 · `reviewer_node(state)`

| | |
|---|---|
| **输入** | `schema_draft` + `raw_evidence` + `collection_meta` + `analysis_meta` |
| **怎么做** | 跑 R0-R7(`minimal` 默认:hard_gate=R1/R4/R5,R2/R3/R7 仅 warning,R6 关;`full`:R1-R5 全 hard_gate + R6 终轮)。有 error → Counter+优先级(collector>analyzer>writer)选打回 target;超配额走 degraded |
| **输出 `quality_report`** | 见下 |

```jsonc
{ "mode":"minimal|full", "quality_score":82, "quality_dimensions":{…},
  "passed_rules":["R1",…], "failed_rules":["R4"], "warning_rules":["R2"], "skipped_rules":["R6"],
  "module_status":{"raw_evidence":"passed","feature_tree":"warning","pricing_model":"passed",…},
  "errors":[ {"rule":"R1","issue_type":"…","location":"feature_tree.F001…","detail":"…",
              "reject_target":"collector|analyzer|writer","required_claim_types":["pricing"]} ],
  "warnings":[…] }
// 打回时还写回 state.reject_target + state.reject_requirements[{rule,issue_type,location,required_claim_types,reject_target}]
```

**隔离入口**:`reviewer.make_reviewer_node(llm)(state)`;单规则 `reviewer.check_*(schema, evidence)`
**审核看点 🚩**:① R1 引用完整性(有没有放过悬空 id)?② 打回 target 选得对不对?③ reject_requirements 的 required_claim_types 准不准?④ degraded 路径通不通?

---

## 10. 环节间的冻结点(fixture)

| 冻结文件 | 由谁产出 | 喂给谁 |
|---|---|---|
| `fixtures/meta.json` | S1 | S2–S8(全程只读任务说明书) |
| `fixtures/search_plan.json` | S2 | S3 |
| `fixtures/raw_evidence.json` ★ | S3 | S4/S5/S6/S8 |
| `fixtures/facts.json` | S5 | S6/S7/S8 |
| `fixtures/schema_draft.json` | S5+S6 | S7/S8 |
| `fixtures/report_draft.md` | S7 | (人工读) |
| `fixtures/quality_report.json` | S8 | (人工读) |

`--use-fixture` 模式下,某环只读上游冻结文件 → **失败 100% 归本环**。

---

## 11. 审核记录格式(可回溯)

每跑一环写一个目录,人可逐层下钻:

```
runs/<run_id>/<stage>/
  input.json     # 这一环吃进去的完整输入
  output.json    # 这一环吐出来的完整输出
  steps.jsonl    # 子步骤逐条留痕(S3:每 adapter 抓几条;S5:spine→fill→定价→gap 每轮)
  review.md      # 人工审核页(见下样例)
  meta.json      # 耗时 / LLM 调用数 / 关键环境变量
```

`review.md` 样例(S3):

```md
## S3 搜集 · Cursor   [✓ 通过]
输入: product=Cursor focus=代码补全体验
输出: 32 条证据 | 覆盖 {feature:9, perf:8, pricing:7, pain:8}  ✓四类全
定价: 3 档 $0/$20/$40  ✓有真价     分类串台: 0  🚩无
🚩 待核: GitHubCopilot 付费档官网 SPA 没渲染出 → 只抓到 Free
来源: official_page×12  web_search×11  reddit×9
↓ 下钻 output.json 看每条原文 + evidence_id + source_url
```

### 与现有日志衔接(已有,直接接上)

| 已有留痕 | 能核什么 |
|---|---|
| `logs/llm_calls.jsonl` | 每次 LLM 调用的**完整 prompt + 回复 + tokens + 耗时**(label 区分是哪环哪步) |
| `logs/agent_trace.jsonl` | 节点级流转事件 |
| `data/debug/` | evidence 落盘快照 |

`review.md` 里给出对应 `logs/llm_calls.jsonl` 行号 → 要核「这条结论凭哪条证据、证据来自哪个 URL、那次 LLM 喂了什么 prompt」可一路点进去。

---

## 12. 断言总表(每环"算通过"的硬标准)

| 环 | 硬断言(不满足即这环 FAIL) |
|---|---|
| S1 | 竞品≥3 且含非同质玩家;intent∈5 类且与关键词一致;focus 非空 |
| S2 | 每个缺失 claim_type 都有 query;query 无中英混搭;定价/痛点锚源正确 |
| S3 | 4 类 claim_type 覆盖>0;pricing 条目≥1 条含价格 token;无 claim_type 串台;evidence_id 唯一且确定性 |
| S4 | 输出条数 ≤ 输入;每桶 ≤8;近似重复对被去重 |
| S5 | 矩阵无全`—`行;无计划/配额类维度名;pricing tier 有 `normalized_usd_month`;**所有 evidence_ids ⊆ raw_evidence ids**;gap-refill 轮数 ≤ 上限 |
| S6 | 每条 swot/rec 有≥1 真实 evidence_id;`final_score` = 加权和;landscape.name 不在 raw_evidence/meta 外 |
| S7 | 8 模块全在;chip 正则 `\[S[A-Z0-9]{7}\]` 命中;正文无 "quality_score" 字样 |
| S8 | quality_report 字段齐;errors 的 reject_target∈3 类;打回不超配额 |
