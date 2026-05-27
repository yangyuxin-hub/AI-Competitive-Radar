# 报告质量评测设计 — LLM-as-Judge

> 目的:给竞品报告的「分析质量」打一个客观、可量化、可复现的分,用来**优化报告**
> ——即指导迭代 `prompts/analyzer_*.md`(skill)。
> 实现:`src/judge.py` + `config/quality_rubric.yaml`。离线 harness,Pointwise 打分。

## 1. 定位:和 R1-R7 的分工

| | R1-R7(`src/reviewer.py`) | LLM-as-Judge(`src/judge.py`) |
|---|---|---|
| 问题 | 报告**内部自洽吗**(引用/推理链/冲突/时效) | 报告**是不是好分析**(准确/洞察/实用/聚焦) |
| 性质 | 确定性、二值、可复现 | 主观、连续分、有方差 |
| 产出 | `quality_report`(机械门,可打回) | `analysis_quality` 评分卡(离线,不打回) |

judge **不替代** R1-R7,是新增的一层。R1-R7 保证「可信地报」,judge 衡量「报得好不好」。

## 2. 评分维度(4 维,1-5 锚定)

| 维度 | 看什么 | 确定性锚信号(代码算) |
|------|--------|------------------------|
| **准确性** accuracy | 结论是否真被 snippet 支持,无过度断言 | `evidence_coverage_ratio` |
| **洞察力** insight | 超越功能对照,有竞争逻辑/根因 | `insight_density` = (swot+建议)/功能行 |
| **实用性** actionability | 建议能否直接执行(收益/指标/风险/周期) | `recs_with_action_fields_ratio` + `missing_action_fields` |
| **聚焦度** focus | 紧扣 analysis_focus,不被无关能力稀释 | feature 名 vs analysis_focus |

锚点(1/3/5 分行为描述)见 `config/quality_rubric.yaml`。

## 3. 为什么「确定性信号 + LLM」混合

纯 LLM 打分会「凭感觉给 4 分」、不可复现。做法:先用代码算出客观比例(如「4 条建议 0 条带验收指标 = 0%」),把事实塞进 judge 的 prompt,LLM 再据此给 1-5 + 理由。分数既量化又稳定。

判分要求(prompt 强制):每维输出 `score / justification(引用具体段落或 [SXXXXXXX] chip) / fix_suggestion`,`temperature=0`。

## 4. 加权与权重档

各维 1-5 → 归一到 0-100 → 按权重加权。权重随 `analysis_purpose` 浮动(子串匹配):

- default: 准确 .30 / 洞察 .30 / 实用 .25 / 聚焦 .15
- 「定价」类目的: 准确 .40(事实更重)
- 「差异化」类目的: 洞察 .40

`warn_threshold=3`:某维低于此分,评分卡列入「待优化方向」(只提示,不打回)。

## 5. 优化报告的闭环(离线)

```
跑分 → 读低分维度 + fix_suggestion → 改 prompts/analyzer_*.md(skill) → 重生成 → 再跑分对比
```

harness 是「尺子」,prompts 是「螺丝」。评测本身不改报告,它告诉你该往哪个方向拧 skill。

```bash
# 1. 生成报告(已有 out/<domain>/report.md + schema_draft.json)
DOMAIN=ai_coding python -m src.graph
# 2. 评测(需 ARK_API_KEY,judge 必须真调 LLM)
python -m src.judge out/ai_coding
# → 打印评分卡 + 写 out/ai_coding/quality_judge.json
```

## 6. 后续(未做)

- 接进 graph 当 judge 节点(full 模式按阈值打回,配额 1 次)——本期只做离线 harness。
- Pairwise A/B:两版报告让 judge 选更优,方差更小,用于验证「改 prompt 后真的变好」。
- 人工 gold 校准集(好/中/差各一),验证 judge 排序与人工一致。
