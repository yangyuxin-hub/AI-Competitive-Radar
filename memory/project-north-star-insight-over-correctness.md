---
name: project-north-star-insight-over-correctness
description: 项目北极星——报告要有洞察/有用，而非"看上去正确但无价值"
metadata:
  type: project
---

用户明确的项目目标（2026-05-25 确认）：要做出**有洞察力、有用**的竞品分析报告，而不是"结构上正确、证据齐全但缺少判断价值"的报告。

**Why:** 现有 Reviewer R1–R7 全是结构完整性检查（防造假地板），不评洞察。CLAUDE.md 强调的是证据链/schema 正确性，但用户真正在意的是"判断更准、证据更硬、建议更能落地"。

**How to apply:**
- 评价体分双轨：A 轨=确定性硬约束(进 retry 闭环，防造假)；B 轨=7 层洞察 rubric(0–100，软评分，不轻易触发打回)。
- **洞察是 Analyzer + schema 上游生成的，Reviewer 只能挡不能造**。要提升洞察，优先级是 schema 字段 → analyzer prompt → reviewer rubric，Reviewer 排最后。
- 7 层评价框架见 [[competitive-report-7-layer-rubric]]。能落 schema 字段的层（边界/竞品分层/事实推断分离/建议五件套）就用确定性规则，别依赖 LLM 主观打分；只有维度洞察(第3层)/战略判断(第6层)交给 LLM(R6)。
