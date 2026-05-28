# 执行流程审计 · 优化点 · 缺漏

> 目的:把当前系统从「一句话输入」到「出报告」的完整执行过程记录下来,标出可优化处与处理缺漏。
> 截至:2026-05-28 整合 Tavily + HN/V2EX skills 后。配套:`design-v2.2.md`(架构)、`scoring-rules.md`(打分)。

---

## 一、执行全流程(实测口径)

```
一句话输入
  └─ POST /api/intake/questions ──► intake.propose(LLM,40s 超时)──► reasoning + 智能竞品
        └─ build_questions ──► 选择题(target 单选 / competitors·focus·purpose 多选 + 其他自填)
              └─ 前端澄清页:「🧭 Agent 的判断」+ 可点选调整
  └─ POST /api/run (SSE) ──► graph: collector → analyzer → writer → reviewer ─┬─ passed → END
                                                                              ├─ running → 按 reject_target 回退重试
                                                                              └─ degraded → degraded_writer → END
        └─ 持久化 out/reports/<id>.json + index.json
```

### 1. Intake(意图理解)
- `intake.propose`:LLM 抽取品类/目标/竞品/焦点/目的 + **reasoning(判断依据)**;40s 超时,超时回退 `_propose_heuristic`(products.yaml/domains.yaml 启发式,无 reasoning)。
- 实测耗时 **20-30s**(LLM)。前端「理解意图中…」期间等待。
- 输出:选择题 Choice 列表;focus/purpose 已多选;每题支持「其他」自填。

### 2. Collector(采集)
- **URL Discovery**:`discover_urls` 先查 products.yaml,无配置则 LLM 找官网/定价页;`discover_all_urls` 并发。
- **AdapterRegistry.fetch_all**(每产品,collector_node 内 ThreadPool 并发):
  - **live 层**:`OfficialPageAdapter`(httpx+BS4 抓官网,默认开,`DISABLE_LIVE_FETCH=1` 关)、`SearchAdapter`(Tavily,有 `TAVILY_API_KEY` 即开)
  - **skills 层**:`hn_skill`(HN 官方 Algolia API)、`v2ex_skill`(V2EX);`create_skill_registry`,`DISABLE_SKILLS` 可关
  - **cache 层**:`CacheAdapter`(data/cache,TTL 去重 merge)
  - **mock 层**:`MockAdapter`(sample_sources.json 兜底,保 4 类覆盖)
- **source_planner**:决定 Tavily「去哪搜什么」;默认 config 启发式,`SOURCE_PLANNER_LLM=1` 启用 LLM 规划(实时输出"决定去 X 搜 Y"思考流)。
- **cap_evidence_per_product**:按 claim_type 均衡截顶 40/产品(防高置信官网/HN 挤掉低置信 UGC)。
- 实测来源均衡:search(Tavily)/ skill:hn / live(官网)/ skill:v2ex 共存。耗时 **40-66s**(LLM 规划 + 检索)。

### 3. Analyzer(两步)
- **Step1 facts**:LLM 出 feature_tree/pricing_model/user_persona → `quick_validate_facts` → issue>6 跳过 LLM 重跑直接 `sanitize`,否则 LLM 修一次。
- **Step2 derivations**:LLM 出 swot/recommendations(priority_score 按公式)→ `quick_validate_derivations`。
- 每步完成 emit 真实摘要(功能维度/痛点/SWOT/首要建议)。实测 **130-180s**(最大头,LLM-bound)。

### 4. Writer
- 渲染 Markdown,每条 claim 挂 `[SXXXXXXX]` chip;正文禁含 quality_score。<1s。

### 5. Reviewer
- R0-R7,`minimal`(默认,R0/R1/R4/R5 hard gate)/ `full`。
- 不过 → 按 target 回退重试(配额 collector:1/analyzer:2/writer:1)→ 超限走 `degraded_writer`。

**端到端**:首轮约 **3-4 分钟**;触发重试再 +130-180s。

---

## 二、可优化点(按性价比)

| 优先级 | 优化 | 现状 | 建议 |
|--------|------|------|------|
| 🔴 高 | **Analyzer 延迟** | 130-180s,占总时长 ~60% | 拆并行(facts 的 feature/pricing/persona 可分别并发出)、或减小 prompt(证据再精简)、或换更快模型 |
| 🔴 高 | **degraded 频发** | 真实证据杂 → evidence_id 误引 → R1/R0 打回 → 常 degraded | 加强 facts prompt 的引用约束、sanitize 后补一轮轻校验、或 reviewer 对 UGC 证据放宽 |
| 🟠 中 | **Intake 30s 等待** | LLM propose 阻塞 | 先秒回启发式、后台异步升级 LLM 结果;或缓存同输入 |
| 🟠 中 | **source_planner 默认启发式** | LLM 规划默认关(`SOURCE_PLANNER_LLM`),查询不够聪明 | demo 视网络情况开启;或启发式查询模板再调优 |
| 🟠 中 | **并发隔离** | 模块级全局回调 + 单例 registry → 同后端一次只能跑一个分析 | 改 per-request 上下文(回调/registry 不用全局) |
| 🟡 低 | **cap=40 仍可能丢有用证据** | 均衡截顶按置信度 | 按 claim_relevance 而非仅 confidence;或动态额度 |
| 🟡 低 | **Tavily 成本/命中** | 每查询 1 次 API,Reddit 命中一般 | 缓存查询结果;知乎/小红书命中差的源降级 |

---

## 三、缺漏 / 风险

### 处理缺漏
- **新模块零测试**:`search.py` / `source_planner.py` / `SearchAdapter` / `hn_skill` / `v2ex_skill` 都没单测(现有测试仅 analyzer/reviewer)。重构易回归。
- **`data/evidence_debug_*.json` 未 gitignore**:`.gitignore` 有 `out/` 但没盖 `data/` 下的 debug 产物 → 会误提交。**建议加 `data/evidence_debug_*.json`**。
- **真·token 流式做不到**:Doubao EP 不给增量 chunk(内容末尾一次性到),所以"逐字思考"无法实现;只能稳定计时 + 转场摘要。
- **UGC 源覆盖窄**:实测仅 HN(Algolia)+ V2EX skill + Tavily 泛搜;Reddit 靠 Tavily(质量波动),小红书/知乎/AppStore 基本抓不到。
- **degraded 时报告质量**:走 degraded_writer 分层降级,内容明显弱于 passed,但前端未明显区分提示用户。

### 架构/产品缺漏(对照 usage-scenarios.md)
- **只有"按需"触发**:事件驱动(场景4)、持续监控(场景3)未实现。
- **只有一种报告模板**:无 1页brief / 时间线 / 原话引用集(场景4/5/6 需要)。
- **无档案 diff 的深用**:`/report/[id]` 有同 target diff,但未做跨时间窗聚合。
- **LLM-as-Judge 未接入**:内容质量分仍离线,报告页只显示 Reviewer 工程质量分。

### 协作风险
- **collector.py / api/main.py 反复双写撞车**(本会话已 3 次架构级冲突)。**建议团队约定模块归属 + 用分支/PR**,别都直推 main。

---

## 四、最高优先级三件(建议下一步)
1. **降 degraded 率**:真实证据下报告质量的命门——加强引用约束 + sanitize 后校验。
2. **Analyzer 提速**:并行化 facts 子项或精简 prompt,把 3-4 分钟压到 2 分钟内。
3. **补 .gitignore + 新模块冒烟测试**:防误提交 debug 产物 + 防整合回归。
