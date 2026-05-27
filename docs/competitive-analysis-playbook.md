# 竞品分析方法论 Playbook

> 来源:基于 Cursor 代码补全体验竞品分析样例与 Agent 竞品分析流程复盘沉淀。
> 用途:指导 Planner / Analyzer / Reviewer / Reporter 的分析口径,避免报告退化成搜索摘要。

---

## 1. 先定边界,再做分析

竞品分析必须先回答“这次不分析什么”。例如“Cursor 代码补全体验”不等于完整 Agent 能力,边界应限定为:

- 补全触发是否自然
- 建议是否准确
- 是否理解当前文件、相关文件、项目结构
- 补全延迟
- 是否能预测下一处修改
- 是否干扰原生 IDE
- 是否容易接受、拒绝、局部采纳
- 是否适合企业代码库

边界不清时,Analyzer 容易把 Agent、Chat、IDE、企业治理混成一锅,报告会变成产品介绍。

---

## 2. 竞品不是列表,而是分层

Cursor 的代码补全竞品至少可分为四类:

| 类型 | 代表产品 | 核心定位 | 与 Cursor 的竞争关系 |
|------|----------|----------|----------------------|
| AI 原生编辑器 | Windsurf、Kiro | 从 IDE 形态重做 AI 开发工作流 | 最直接竞争 |
| 通用插件型补全 | GitHub Copilot、Supermaven、Tabnine | 嵌入 VS Code / JetBrains / Vim 等已有 IDE | 抢 Tab 补全场景 |
| IDE 厂商内置 AI | JetBrains AI Assistant、Visual Studio Copilot | 原生 IDE 内的 AI 补全与辅助 | 抢专业开发者场景 |
| 企业安全型补全 | Tabnine、Amazon Q Developer | 私有化、合规、安全、组织级部署 | 抢大公司采购场景 |

报告里必须区分“表面功能相似”和“真实竞争点不同”。例如:

| 产品 | 表面功能 | 真实竞争点 |
|------|----------|------------|
| Cursor | 代码补全、Chat、Agent | AI 原生开发入口 |
| Copilot | 代码补全、Chat | GitHub 生态分发 |
| Windsurf | Tab、Flow、Agent | 类 Cursor 的 AI 编辑器体验 |
| Supermaven | 快速补全 | 低延迟和大上下文 |
| JetBrains AI | IDE 内补全 | 专业 IDE 原生集成 |
| Tabnine | 补全和企业部署 | 安全合规采购 |

---

## 3. Plan 是分析质量上限

Agent 不能一上来就搜索关键词。Planner 先形成分析假设:

```text
初始判断:
Cursor 的补全体验优势不在单点代码生成,而在上下文感知和连续编辑流。

待验证问题:
1. Cursor 是否强调代码库级上下文?
2. Windsurf 是否也在做 Tab 级下一步预测?
3. Copilot 的主要优势是否来自生态分发?
4. Supermaven 是否以低延迟和大上下文形成差异化?
5. Tabnine 是否主要打企业私有化和安全?
```

好的 Plan 至少包含:

- 分析对象
- 分析边界
- 竞品范围
- 维度框架
- 待验证假设
- 信息缺口
- 信息来源优先级

---

## 4. 信息源必须分层

| 层级 | 来源 | 适合证明 | 使用方式 |
|------|------|----------|----------|
| L1 官方来源 | 官网、文档、价格页、更新日志、博客、Help Center | 功能、定价、支持 IDE、部署方式、官方定位 | 事实基准 |
| L2 用户评价 | Reddit、HN、X、YouTube 评论、论坛、GitHub issue | 延迟、误补、烦人程度、迁移阻力、付费意愿 | 用户感知证据 |
| L3 第三方评测 | 媒体、测评博客、Product Hunt | 竞品补充、市场热度、心智参考 | 谨慎使用 |
| L4 实测数据 | 同仓库同任务横向测试 | 命中率、延迟、人工修改次数、跨文件理解 | 最高价值 |

事实和推断必须分开:

- 事实:Supermaven 官方宣称支持 100 万 token context。
- 推断:因此 Supermaven 可能在大代码库补全场景形成差异化。

没有证据的内容只能标为 hypothesis,不能进入 final conclusion。

---

## 5. 从资料到判断的链路

系统核心不是生成文章,而是生成:

```text
Evidence -> Fact -> Claim -> Insight -> Recommendation
```

推荐结构:

```json
{
  "claim": "Copilot 的核心优势是 IDE 分发和 GitHub 生态,而不是单点补全体验。",
  "dimension": "distribution",
  "confidence": "high",
  "evidence_ids": ["SXXXXXXX", "SYYYYYYY"],
  "reasoning": "Copilot 支持多 IDE inline suggestion,且与 GitHub 企业账号、代码托管和组织采购链路绑定。"
}
```

Reviewer 要重点拦截:

- 无 evidence_id 的结论
- 把用户反馈当官方事实
- 把官方营销当实测数据
- 没有反证的绝对化结论
- 只做功能矩阵,没有竞争逻辑

---

## 6. Cursor 代码补全的核心判断

Cursor 不应把代码补全定义为 autocomplete,而应定义为 predictive editing。

三层能力:

1. **代码补全**:当前光标处补一行、一块、一个函数。
2. **下一步补全**:预测接下来要改哪里,如 schema 改动后提示 API、类型、测试、前端字段同步修改。
3. **任务级补全**:用户给出任务,系统自动修改多个文件,用户审查 diff。

Cursor 真正该强化的是第二层和第三层之间的过渡:让 Tab 从“接受代码”变成“接受下一步”。

---

## 7. 报告呈现建议

页面组织:

1. 顶部一句结论:Cursor 的代码补全优势不是补全本身,而是 AI 原生编辑流。
2. 中间放竞品矩阵:Cursor、Copilot、Windsurf、Supermaven、JetBrains AI、Tabnine。
3. 右侧放 evidence panel:每条结论对应来源 snippet。
4. 最后放战略建议:Cursor 应从 autocomplete 叙事转向 predictive editing 叙事。

最终结论模板:

```text
Cursor 的代码补全体验处于第一梯队,但护城河不在单点补全模型,
而在“补全 + 项目上下文 + 多文件编辑 + Agent 工作流”的组合体验。
GitHub Copilot 拥有最强分发和企业生态;Supermaven 在速度和大上下文上形成尖刀;
Windsurf 在 Tab 交互和 AI 原生 IDE 方向上高度贴近 Cursor;
JetBrains AI 依托专业 IDE 语义能力占据强工程场景;
Tabnine 在企业私有化和合规场景中具备优势。
```
