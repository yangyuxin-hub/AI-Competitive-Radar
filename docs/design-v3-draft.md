# 设计草案 v3 — 按能力重新切分的干净系统

> 状态:**草案**(答辩后启动,不影响 v2.2 演示)
> 依据:2026-06-11 全链路实测(run `trace_20260611053755_a192` vs `trace_20260611052958_4a54`)+ 54 次历史 run 的 `logs/stage_quality.jsonl` 聚合
> 上游文档:`docs/design-v2.2.md`(现行架构)+ `docs/design-v3.md`(控制平面)
> **与 design-v3.md 的关系**:承接其已落地的 M0(StageReport 统一契约 `src/stage_report.py`)与 M1(`/api/timeline` + `checklist.py` 交付验收清单),**取代其 M2-M4**——
> M2 要做的"统一控制环 + task_key 级打回预算"被本文 §四的实测数据判死刑(54 run 打回触发率 1/54,外环名存实亡,删环优于修环);
> M3 的"Collection 真接缝"结论保留,下沉为 EvidenceService 内部结构(见 §三)。

---

## 一、为什么要 v3:现状诊断

v2.2 的防幻觉主线(确定性 evidence_id + chip 溯源 + 证据分级 + unknown 机制)是真资产,**不动**。
要治的是三个结构性根因——系统是被事故驱动演化的,每个补丁都对,加起来没有形状:

### 根因 1:职责按"流水线位置"切,不是按"能力"切

四个 Agent 的名字是位置(采集→分析→写作→审查),但系统真正的能力关切是:
获取证据、判断证据够不够、从证据推结论、控制结论强度、渲染、审计。
两组对不齐,横切能力出问题时被焊到当时出事的节点上:

- "缺口判定"长在两处:collector 验收门(`quality.audit_coverage`)+ analyzer gap refill(`_coverage_gaps`),口径不保证一致
- "结论强度控制"散在三处:`analyzer.soften_overgeneralization`、`analyzer_sanitize.*`、`reviewer.R6`,各管一段互不知情
- analyzer 越界采集(gap refill / rec refill / augment),实测占 analyzer 一半耗时(~80s/157s),违反 CLAUDE.md §4 自己定的"Analyzer 不抓数据"

### 根因 2:每次事故加一层兜底,兜底之间没有总设计

现存 **10 种**自愈/兜底机制:三层降级、验收门自愈、gap refill×2轮、rec refill、
sanitize、soften、骨架 fallback、JSON 截断修复、fill 次数熔断、degraded_writer。
覆盖范围互相重叠,"某条幻觉引用会被哪一层拦住"没有唯一答案。
配置面同步爆炸:20+ 个环境变量开关,每个补丁配一个 kill-switch。

### 根因 3:控制流为几乎不发生的场景付出全部复杂度

打回路由、retry 配额分桶、reject_requirements、conditional edges——
**54 次真实运行只触发过 1 次打回**(还进了 degraded)。
真实修复全在节点内自愈完成;Reviewer 退化成"只打分不驱动":
R6 单次产出 9 条精确到字段的定位(location+detail),状态 passed+warning,无任何下游消费。

### 附:实测数据基线(2026-06-11)

| 指标 | 数值 | 含义 |
|------|------|------|
| 端到端(推荐配置,缓存热) | 242s | intake 0.5 + planner 7 + collector 35 + analyzer 157 + reviewer 42 |
| 端到端(默认配置,缓存冷) | ~690s | thinking 未关:claim_type 103.8s vs 10.8s,fill 25-40s vs 6-10s(3-10 倍差) |
| analyzer 内部 | refill 补采搜索 ~80s/157s | 一半时间在采集,LLM 重填本身只要 ~9s |
| 幻觉 evidence ref 率 | ~6.5%(10/4/3 条/三轮) | sanitize 全部拦截,但说明 prompt 约束可更硬 |
| R6 warning 消费率 | 0%(9/9 条进档案即终点) | 质量反馈流断裂 |
| 打回触发率 | 1/54 run | 外环名存实亡 |
| quality_score | 73/100 | 主要失分:gap/basis 字段从单条证据过度泛化 |

---

## 二、切分原则

只有一条:**每个模块的职责能用一句话说清,且有明确禁区。**

推论:

1. 按能力切,不按流水线位置切
2. 兜底必须有唯一 owner——每类失败有且只有一个模块负责
3. 审查产出必须有消费者——没有消费者的检查是装饰
4. 最优配置即默认配置——env 只用来往回退,不用来开启正确行为
   (落地:已 A/B 验证的推荐配置 `LLM_THINKING=disabled` + `LLM_THINKING_DEEP=enabled` + `ANALYZER_PROMPT_SLIM=1` 翻转为代码默认值,这是 env ≤8 目标的主要来源)

---

## 三、六个角色

| 角色 | 一句话职责 | 输入 → 输出 | 禁区 | 失败策略 |
|------|-----------|------------|------|---------|
| **Intake** | 把人话变成分析契约 | 用户输入 → AnalysisContract(target/竞品/焦点/意图/必需证据类型) | 不碰证据 | 启发式兜底,但必须**显式标注降级**(不允许静默) |
| **EvidenceService** | 系统里**唯一**碰外部世界的模块 | 契约或缺口请求 → 证据池 | 不下结论 | 三层降级(live→cache→mock),内部消化 |
| **Analyst** | 证据 → 结构化结论,纯推理 | 证据池 → schema | 不抓数据;缺证据只能**声明缺口**交给 EvidenceService(缺口协议直接复用 `stage_report.Gap`:owner_node × product × claim_type + fix 处方 + fixable + task_key,不另起一套) | 骨架兜底 |
| **Guard** | 保证每条结论强度 ≤ 证据强度;确定性、幂等、零 LLM | schema → 修订后 schema | 不新增内容,只能降级/删除 | 不会失败(纯函数) |
| **Reporter** | schema → Markdown,纯模板 | schema → 报告 | 不改数据 | 不会失败 |
| **Auditor** | 只审不修,每条发现必须有消费者 | schema+报告 → StageReport(`Check[]`/`Gap[]`,M0 已落地的契约,不重新发明) | 不修改任何产物 | 审不了如实标 unavailable |

Auditor 的两个消费者:机器侧 → Guard 修订(见 §四);用户侧 → 已落地的 `checklist.py` 交付验收清单
("该交付的齐没齐 + 谁去补"的决策视角,v3 M1 用户反馈的核心教训,不是遥测时间线)。

两个新角色都是**收编**,不是新建:

### EvidenceService(收编采集与缺口)

= collector 三层 + `analyzer_augment` + 两套缺口判定合并为一套。

- collector 节点 → 对它的**第一次调用**(按契约全量采集)
- analyzer refill → **第二次调用**(按缺口声明定向补)
- 缺口处理顺序:**先回捞池内被 compact/cap 截断的证据(零成本)→ 不足再外搜**。
  治假性缺口:R6 报的"证据不足"有一部分证据明明在 `raw_evidence` 里,
  只是被 top-K(8/5 条)和片段截断(180/140 字符)挡在 prompt 视野外
- 单一缺口口径:合并 `audit_coverage` 与 `_coverage_gaps`,reviewer 侧 intent-aware 口径
  (design-v3.md M3 诊断的 quality.py 与 reviewer.py 口径分裂)一并收口;统一产出 `stage_report.Gap`
- 内部保留 `discover → {fetch_official, fetch_community} → merge_gate` 结构
  (design-v3.md 的真接缝结论:官网/社区失败模式、修复策略、bias 标注各不同;缺社区证据只重跑 fetch_community)

### Guard(收编结论强度控制)

= `analyzer_sanitize.*` + `soften_overgeneralization` + `repair_rec_anchors` + 新增 R6 对账规则。

核心新规则(确定性,治 quality_score 73 的主要失分点):

- **winner/gap 对比结论**:要求 winner 与次优产品各 ≥1 条 quality/pain 证据,
  否则降级为"证据不足,暂不强对比"措辞(F005 现有安全措辞模板)
- **quality_score.basis**:声称"仅确认具备"必须有 ≥1 条 feature_existence 证据支撑,否则改 unknown
- 输入除 schema 外还接受 Auditor 定位清单(见控制流)

---

## 四、控制流:一条直线 + 一次修订

```
Intake → EvidenceService → Analyst ⇄(缺口声明/补证据) → Guard → Reporter
                                                                  ↓
                出货 ← Reporter 重渲染 ← Guard 修订 ←(定位清单) Auditor
```

1. **打回循环删除**。Auditor 发现走前向回灌:定位清单 → Guard 一次确定性修订 →
   Reporter 重渲染 → 出货。最多一次修订,无环、无 retry 配额、无路由函数。
   (54 run 数据支持:外环本来就没在工作)
2. **degraded_writer 删除**。"降级"不再是特殊节点,而是 Guard 的一种输出:
   某板块证据太弱就整段降级措辞或标注不可得,Reporter 照常渲染。
   降级从异常路径变成正常路径的一种程度。
3. **兜底 10 种归并为 3 类**,各归其主:

| 失败类型 | 唯一 owner | 策略 |
|----------|-----------|------|
| 获取失败 | EvidenceService | live→cache→mock 内部降级 |
| 推理失败 | Analyst | 骨架兜底 |
| 结论过强 | Guard | 降级措辞 / 删除 |

"这条幻觉会被谁拦住"有唯一答案:引用不存在的 ID → Guard;内容过度声称 → Guard;
证据本身缺 → EvidenceService。

---

## 五、旧代码 → 新结构映射(搬家,不是重写)

| 现有 | 去向 |
|------|------|
| `intake.py` + `evidence_plan.py` | **Intake**(evidence_planner 是纯规则查表,不配做图节点,并入 intake 尾部) |
| `collector*.py` + `analyzer_augment.py` + `quality.audit_coverage` + `analyzer._coverage_gaps` | **EvidenceService** |
| `analyzer.py` 核心 pipeline(spine/fill/persona/pricing/derivations) | **Analyst** |
| `analyzer_sanitize.py` + `soften_overgeneralization` + `repair_rec_anchors` + R6 对账(新) | **Guard** |
| `writer.py` | **Reporter**(不动,已经干净) |
| `reviewer.py` 只读化(R 规则 + R6) | **Auditor** |

### 删除清单

- `degraded_writer_node`、打回路由 `route_after_review`、`retry_count`/`max_retries_per_target` 分桶
- `reject_target`/`reject_requirements` state 字段(缺口请求改为 EvidenceService 内部协议)
- `evidence_plan` 双源(state channel 删,契约只在 meta/contract)
- 大部分 kill-switch env(保留:profile、mock、LLM 凭据;删:各 refill/heal/claim 开关,
  行为由模块内默认策略决定)

### 顺带修复(并发与归因,v2.2 已知问题)——✅ 已全部完成(2026-06-11,Phase 0,先于 M1-M4 落地)

- [x] `source_planner._discovered_domains` 模块级全局 → ContextVar(跨线程由 `CtxThreadPoolExecutor` 快照传播,双线程隔离冒烟通过)
- [x] `_collector_run_count` 删除(run 标签改用 `agent_trace_id`)、`_FILL_ATTEMPTS` → ContextVar + 锁(每 run 换新 dict)
- [x] `.env` 加载前移到 `src/__init__.py`(包导入即生效,graph.py 冗余加载已删)
- [x] intake LLM 调用补归因:api 两个 intake 入口设 `intake_<ts>` run 标签,LLM worker 走 `CtxThreadPoolExecutor`

---

## 六、迁移路径(四步渐进,每步独立可验证)

前提:**答辩期间(6-12 ~ 6-19)不动结构**。v2.2 继续演示。

| 步骤 | 内容 | 验证 |
|------|------|------|
| M0 ✅ | (design-v3.md 已完成)StageReport 契约 `src/stage_report.py` + timeline/checklist 观测面 | 已合入,本草案直接复用 Gap/Check,不重做 |
| M1 ✅ | (2026-06-11 代码完成)`src/evidence_gaps.py`:`find_gaps()` 统一两套口径产出 `stage_report.Gap`(并集+task_key 去重);`recall_from_pool()` 外搜前先回捞池内被 top-K/截断挡住的证据(`_recalled` 标记 → `_compact_evidence` 顶进视野+放宽截断至 400 字符);analyzer refill 循环回捞优先,collector 验收门挂 `gaps_unified`。回退开关 `ANALYZER_POOL_RECALL=0` | 对账测试 7 例通过(并集一致/去重/回捞收敛/视野提升);238 测试全过;refill 外搜轮次下降待答辩后 live trace 复核。注:阈值表仍在 quality/augment 原位,M3 随 EvidenceService 迁入吸收 |
| M2 ✅ | (2026-06-11 代码完成)`src/guard.py`:结论强度唯一 owner——`apply(schema, evidence, findings=None)` 终门 = 幻觉引用清理 + **G1 强对比对账**(winner 与次优各需 ≥1 条 quality/pain 证据,否则降级"暂不强对比"安全措辞) + **G2 basis 对账**(声称"仅确认具备"需 ≥1 条 feature_existence 证据,否则改 unknown) + 过度泛化收敛;sanitize 簇经 Guard re-export(单一入口);analyzer 终门已切换,`_guard_report` 挂 schema 供观测;findings 参数预留 M4 Auditor 回灌 | 幂等测试过(二次套用零变化);G1/G2 规则 8 例;246 测试全过;R6 warning 9→≤3 与 quality_score 73→80 待答辩后 live run 复核 |
| M3 ✅ | (2026-06-11 代码完成)`src/evidence_service.py`:采集执行唯一 owner——`fill(evidence, meta, gaps, focus)` 统一入口(回捞优先→定向外搜→id+文本双重去重,mode=pool/search/none);`_gap_targeted_recollect`/`_recollect_pricing_official`/`_run_survey` 系自 analyzer_augment 原样迁入;analyzer_augment 只剩缺口判定纯函数+re-export;`smart_truncate` 拆 `textutil.py` 切断 Analyst→采集层最后一条 import 边;collect(contract) 全量采集留 M4 收编 | Analyst 簇 5 文件零采集 import(AST 检查锁进回归测试);fill 三模式+back-compat re-export 链 5 例;251 测试全过;端到端耗时不升待 live trace 复核 |
| M4 ✅(主体) | (2026-06-11 代码完成)控制流改直线+一次修订:图拓扑 `… → writer → reviewer → guard_revise → END`,无条件边/无回边/无 degraded_writer 节点;`guard.guard_revise_node` 消费 Auditor 定位清单 → `guard.apply` 确定性修订 → 有变化则 Reporter 重渲染;终态规则诚实(running+有修订→passed / running+零修订→degraded / degraded 包分层说明——原 degraded_writer 渲染收编为 Guard 降级措辞 `_degraded_annex`);api/stage_report 节点映射同步 | 拓扑测试(无回边/guard_revise 终点)+ 终态规则 4 例;256 测试全过。**M4b 已完成(2026-06-11)**:state 级打回字段(reject_target/reject_requirements/retry_count/max_retries_per_target)全删,issue 级 reject_target 归因保留在 quality_report.errors;reviewer 只审不修(error→running,终态归 guard_revise);degraded_writer_node 函数删除;推荐 env 翻转为代码默认(thinking=disabled/deep=enabled/prompt_slim=1/pool_recall=1,回退口:passthrough/inherit/=0)。**仅剩 live trace 对表**(端到端≤240s/quality_score≥80/R6 消费率) |

**教训保留原则**:10 层兜底是 54 次真实运行换来的,删机制不能删教训——
每个兜底对应的事故场景沉淀为回归测试(大部分已在现有测试集中,迁移时逐一核对归属新 owner)。

---

## 七、目标指标(v3 验收)

| 指标 | v2.2 现状 | v3 目标 |
|------|----------|---------|
| 端到端(默认配置) | ~690s | ≤240s(最优即默认) |
| 端到端(缓存热) | 242s | ≤180s(refill 回捞 + R6 瘦身) |
| quality_score | 73 | ≥80 |
| R6 发现消费率 | 0% | 100%(回灌修订或显式弃置) |
| 缺口判定口径 | 2 套 | 1 套 |
| 自愈机制 | 10 种无 owner | 3 类各一 owner |
| 并发 run 隔离 | 全局变量污染 | 零共享可变状态 |
| env 开关 | 20+ | ≤8 |
