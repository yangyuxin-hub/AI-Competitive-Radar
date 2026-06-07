# 打分规则说明

> **所有权重/阈值集中在 [`config/scoring.yaml`](../config/scoring.yaml)**（v2.3 下沉 config，跨行业可调，由 `src/scoring_config.py` 加载，缺失回退代码默认）。
> 评分体系**完整全景 + 互补不重复的设计依据**见 [`docs/reviewer-reject-design.md` §8.8](reviewer-reject-design.md) 与 [评分体系架构图](scoring-architecture.md)。
> 本文聚焦三套**进报告/可打回**的核心打分；另有证据级 `quality_score`、采集验收门、completeness、stage_eval 等层，见架构图。

| 打分 | 衡量什么 | 性质 | 范围 | 配置 section | 接入实时流程 |
|------|---------|------|------|---------|----------------|
| **报告质量分** | 报告内部自洽 + 6 维可信度 | 确定性、可打回 | 0-100 | `reviewer_quality` | ✅ 是 |
| **建议优先级分** | 哪条改进建议先做 | 加权公式 | 1-5 → P0-P3 | `recommendation_priority` | ✅ 是 |
| **内容质量分** | 报得好不好(准确/洞察/实用/聚焦) | 主观、连续分 | 4 维 × 1-5 | `quality_rubric.yaml` | ❌ 离线 harness |
| 证据级质量分 | 单条证据好不好 | 确定性 | 0-1 | `evidence_quality` | ✅(采集门/截顶) |
| 采集验收门 | 采够没/采好没 | pass/fail | — | `collection_gate` | ✅(自愈) |

---

## 一、报告质量分 — Reviewer R0-R7(确定性门禁)

代码 `src/reviewer.py`。报告页那个「质检 85/100 · passed」就是这一层。

### 1.1 七条规则

| 规则 | 名称 | 检查点 |
|------|------|--------|
| **R0** | evidence_coverage_gate | 4 类必需诉求(功能/性能/定价/痛点)是否都有证据覆盖 |
| **R1** | evidence_reference_integrity | 每个 `evidence_id` 是否真实存在于 raw_evidence(防编造引用) |
| **R2** | claim_type_compatibility | 结论引用的证据类型是否匹配(如质量分不能只引 feature_existence) |
| **R3** | aggregation_integrity | 聚合统计(positive/negative 计数、sample_size)是否自洽 |
| **R4** | reasoning_chain_integrity | 推理链完整性:建议是否挂了 source_feature_ids / source_pain_ids |
| **R5** | structured_contradiction | 结构冲突,如 `priority_score.final_score` 与公式计算值不符(误差 > 0.01) |
| **R6** | semantic_grounding | (LLM 复核)结论语义是否真被证据 snippet 支撑 |
| **R7** | freshness_and_confidence | 证据时效与置信度 |
| **R8** | content_coverage | 内容整缺硬门:某产品定价/功能整列塌 → 回 collector 采补 |
| **R9** | report_chip_traceability | (writer 自检)正文 chip `[SXXXXXXX]` 都能在 raw_evidence 找到 |
| **R10** | report_no_score_leak | (writer 自检)正文禁泄 `quality_score`(徽章前端单独渲染) |

### 1.2 分数公式

```
rule_score        = max(0, 100 − error × penalty_per_error − warning × penalty_per_warning)
dimensional_score = Σ(6 维 score × 权重)        # 见下,权重在 scoring.yaml: reviewer_quality
quality_score     = min(rule_score, dimensional_score)   # 规则违规会一票压低
```
penalty 默认 error×10 / warning×3（`scoring.yaml: reviewer_quality`）。**6 个可信度维度**(加权)：
证据覆盖 .22 · 可追溯 .22 · 来源可信 .18 · 报告完整 .18 · 时效 .10 · 冲突处理 .10。
- 输出在 `state.quality_report`:`{quality_score, quality_dimensions, passed_rules, failed_rules, warning_rules, errors[], warnings[]}`。

### 1.3 两种模式(`REVIEWER_MODE`)

| 模式 | 硬门禁(error,会打回) | 软规则(warning,仅扣分) | R6 LLM |
|------|----------------------|------------------------|--------|
| **minimal**(Demo 默认) | R1 / R4 / R5 / **R9** + R8 内容门 | R0 / R2 / R3 / R7 / R10 | 终轮单次(R6_FINAL) |
| **full**(答辩) | R0 / R1 / R2 / R3 / R4 / R5 / R9 / R10 | R7 | 开(结构通过后单次) |

### 1.4 打回闭环

硬门禁出现 error → Reviewer 按 issue 类型路由到对应节点重跑(`ISSUE_TYPE_TO_TARGET`):

| issue 类型 | 打回目标 |
|-----------|---------|
| evidence_id_not_found / freshness_stale / missing_product_evidence / missing_claim_type_coverage / missing_pricing_content / missing_feature_content | **collector** |
| missing_evidence_ids / claim_type_mismatch / aggregation_* / broken_reasoning_chain / structured_contradiction / semantic_grounding_* | **analyzer** |
| report_chip_not_found / report_score_leak (R9/R10) | **writer** |

- 多个 error 时按 `Counter` + 优先级 `collector > analyzer > writer` 选一个 target。
- retry 配额按 target 分桶 `{collector:1, analyzer:2, writer:1}`,用完仍不过 → 走 `degraded_writer` 分层降级输出(`status=degraded`)。

---

## 二、内容质量分 — LLM-as-Judge(主观质量)

代码 `src/judge.py` + 量表 `config/quality_rubric.yaml`。**离线 harness**,衡量"报得好不好",不打回、暂未接入网页。

> 与 R0-R7 的分工:R0-R7 保证「可信地报」(确定性/二值),judge 衡量「报得好不好」(主观/连续分)。

### 2.1 四个维度(1-5 锚定)

| 维度 | 看什么 | 1 分 | 3 分 | 5 分 |
|------|--------|------|------|------|
| **准确性** accuracy | 结论是否真被 snippet 支撑、无幻觉、区分 vendor/用户 | 多处无证据或矛盾,把营销当事实 | 多数有支撑,个别过度断言 | 每条可溯源、明确区分 vendor_claim 与用户实测 |
| **洞察力** insight | 是否超越功能对照,点出竞争逻辑/根因 | 仅功能罗列 | 有 SWOT 但偏表面 | 清晰指出各竞品竞争逻辑与 target 真实位置 |
| **实用性** actionability | 建议能否直接执行(动作/收益/指标/风险/周期) | "提升体验"式空话 | 有动作和优先级,缺收益/指标/风险/周期 | 五要素齐全可直接立项 |
| **聚焦度** focus | 是否紧扣 analysis_focus | 大幅跑题 | 基本贴合,混入少量焦点外 | 紧扣各子维度,无稀释 |

### 2.2 确定性信号压方差

纯 LLM 打分会"凭感觉给 4 分"。做法:先用代码算客观比例(如「4 条建议 0 条带验收指标 = 0%」)塞进 judge 的 prompt,LLM 再据此给 1-5 + 理由 + 修改建议,`temperature=0`。

### 2.3 加权(随分析目的浮动)

各维 1-5 → 归一到 0-100 → 按权重加权。权重按 `analysis_purpose` 子串匹配:

| purpose | accuracy | insight | actionability | focus |
|---------|:--------:|:-------:|:-------------:|:-----:|
| **default** | 0.30 | 0.30 | 0.25 | 0.15 |
| 含「**定价**」 | 0.40 | 0.20 | 0.25 | 0.15 |
| 含「**差异化**」 | 0.20 | 0.40 | 0.25 | 0.15 |

`warn_threshold = 3`:某维低于此分,评分卡列入「待优化方向」(只提示,不打回)。

### 2.4 用法(离线)

```bash
python -m src.judge out/<domain>
# → 打印 4 维评分卡 + 写 out/<domain>/quality_judge.json
```
闭环:跑分 → 读低分维度 + fix_suggestion → 改 `prompts/analyzer_*.md` → 重生成 → 再跑分对比。

---

## 三、建议优先级分 — priority_score

代码约束见 `prompts/analyzer_derivations.md`,Reviewer R5 校验公式一致性。报告「改进建议」每条的 `P0/P1` 徽章来自这里。

### 3.1 公式

```
final_score = 0.35 × pain_frequency      ┐
            + 0.30 × business_impact      │ 权重在 scoring.yaml: recommendation_priority
            + 0.20 × implementation_feasibility  │ (可跨行业调,缺失回退此默认)
            + 0.15 × evidence_confidence  ┘
```

- 4 个评分项均为 **1-5 整数**;weights 原样输出;final_score 保留两位小数。
- Analyzer 与 Reviewer 都会重算校验,误差 > 0.01 触发 **R5 结构冲突**打回。

### 3.2 优先级阈值

| priority | 条件 |
|----------|------|
| **P0** | final_score ≥ 4.2 |
| **P1** | 3.4 ≤ final_score < 4.2 |
| **P2** | 2.6 ≤ final_score < 3.4 |
| **P3** | final_score < 2.6 |

---

## 四、三者关系一句话

- **报告质量分(R0-R7)**:能不能发出去——不达标就打回重做。
- **内容质量分(Judge)**:发出去的东西好不好——用来迭代 prompt(离线)。
- **建议优先级分**:报告内部，告诉 PM 先做哪条改进。

> 当前网页只展示第 1 和第 3 层。若要把第 2 层(Judge 4 维)也展示到报告页,需在分析完成后追加一次 judge 调用并渲染「内容质量卡」。
