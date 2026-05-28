# 5 分钟演示脚本

> 时间预算:5 分钟正讲 + 10 分钟答辩问答
> 演示前 checklist:虚拟环境激活、后端 `uvicorn api.main:app --port 8000` 已起、前端 `cd web && npm run dev` 已起、浏览器开在 http://localhost:3000、网络畅通(若要演示真豆包)

---

## 00:00 - 00:30 · 问题陈述(30 秒)

**台词**:

> 企业产品团队做竞品分析,通常要 4 小时以上:搜资料、做表格、对比功能、写痛点、出建议。流程重复性高,信息源分散,而且**结论难追溯到原始证据** — 答辩时被质疑"你这个数据从哪儿来的",经常找不回去。
>
> 我们做了一个多 Agent 协作系统:输入「分析 X 和 Y 在 Z 维度的差距」,15 分钟内输出结构化报告,**每条结论后面都跟着可点开看原文 snippet 的证据 chip**。同一份代码可以零修改换行业 — 今天会演示 AI 编程工具和项目协作工具两个域。

---

## 00:30 - 01:30 · 架构概览(60 秒)

**台词 + 切到架构图(README 或 PPT 一页)**:

> 4 个专职 Agent,LangGraph 编排:
>
> - **Collector** 抓证据,三层兜底(实时网页 → 本地缓存 → Mock)
> - **Analyzer** 拆两步:Step1 出事实(功能树/定价/用户画像),Step2 基于事实推导(SWOT/改进建议)。**拆两步是为了防 LLM 为了结论倒推事实**,这是设计文档 v2.2 里 pre-mortem 之后改的
> - **Writer** 渲染 Markdown,每条 claim 末尾追加 `[SXXXXXXX]` chip
> - **Reviewer** 跑 R1-R7 七条规则,有打回机制 — 按 target 分桶配额:`{collector:1, analyzer:2, writer:1}`,用完即降级输出

> **关键设计取舍**:Reviewer 分 minimal / full 两个模式 — minimal 模式只把 R1 引用完整、R4 推理链、R5 结构冲突当 error,其余只给 warning。**不是想到啥规则都当 hard gate**,这样既给 LLM 留自主空间,又保住核心可信度。

---

## 01:30 - 03:30 · 现场演示(120 秒)

### 演示 A:Mock 模式 + 打回闭环(60 秒)

**操作**:
1. 侧栏勾选 **🧪 Mock 模式** + **🔄 演示打回闭环**
2. 点 **🚀 开始分析**

**指着屏幕讲**:
> 主区现在实时显示 4 个 Agent 的进度。注意看 Reviewer 这一行:

> 第一轮 Analyzer 故意输出了 3 个错误 — 一个不存在的 evidence_id、一个 priority_score 公式不一致、一个 recommendation 漏了 source 引用。

> Reviewer 检出后,**自动打回到 Analyzer**(显示 🔁 第 1 次,3 个 error,reject=analyzer),retry_count.analyzer 加 1。

> 第二轮 Analyzer 重跑,输出干净版,Reviewer 通过。**errors 从 3 → 0,重做后输出有改善** — 这就是评分维度 1 要求的"反馈闭环真实可触发"。

> 你能看到完整 7 步事件序列在下面的日志区。

### 演示 B:跨行业切换(60 秒)

**操作**:
1. 侧栏切 `domain` 从 `ai_coding` → `pm`
2. 默认参数自动变成 Notion vs Asana / Linear
3. 关掉 DEMO_LOOP,保留 Mock,点 🚀

**指着屏幕讲**:
> 同一份代码,同一个 graph,同一组 Prompt,**只改了一个环境变量 DOMAIN=pm**。
>
> Tab2 报告里能看到 Notion 的功能差距、定价对比、5 个用户痛点、4 条改进建议 — 域语言完全切换到「数据库性能、Sprint 工作流、Permissions」,**不是把 Cursor 域的话术换皮**。
>
> 右侧证据库,点 chip 展开可以看到原始 Reddit / HN 原文 snippet 和可信度评分。**每条结论都能溯源**。

---

## 03:30 - 04:15 · 量化收益(45 秒)

**台词 + 切到 docs/comparison.md 那张表**:

> 我们在 PM 域做了个对照实验,同样的 Notion vs Asana vs Linear 这个题目:

| 维度 | 人工分析师 | 本系统 |
|------|-----------|--------|
| 耗时 | 3-4 小时 | **5 分 30 秒**(端到端真豆包) |
| 证据量 | 通常 15-20 条 | **30 条**(可配置) |
| 结构一致性 | 自由 Word/PPT | **100% 符合 Schema** |
| 可重现性 | 同一题不同人结论不同 | **同样输入 → 100% 一致** |
| 可溯源性 | 段落引用,经常丢 | **每条结论带 chip,点开看原文** |

> 把人工 3 小时变 5 分钟,**38 倍效率提升**。更关键的是后两项 — 一致性和可溯源性是人工流程根本做不到的。

---

## 04:15 - 05:00 · 设计取舍亮点(45 秒)

**台词**:

> 我想强调三个非显然的设计选择:

> **第一,Analyzer 拆两步**。第一版我们想一次性出全部 schema,豆包 Lite 实测在 max_tokens=4096 时 JSON 截断在 line 390 — 全是踩出来的坑。拆成 facts → derivations 之后,每步 max_tokens=8192 都跑得过,而且第二步以第一步事实为输入,**降低了为了结论倒推事实的幻觉风险**。

> **第二,Reviewer 不是越严越好**。我们一开始设计了 13 个 issue_type、5×4 的 source_reliability 矩阵、固定的 FRESHNESS_TTL。后来意识到**规则太密集反而损害泛化和 LLM 自主性**,所以拆了 minimal/full 双模式,Demo 默认 minimal 只把 3 条规则当 hard gate,其余只 warning。

> **第三,可换行业不是嘴上说的**。从一开始就把 products / domains / scoring 都拆成 yaml,**代码里没有任何"Cursor"或"代码补全"硬编码**。今天演示的 PM 域跑通,就是这套抽象的硬证据。

> 完整设计 + 6 轮 commit 历史都在 GitHub:`yangyuxin-hub/AI-Competitive-Radar`。谢谢。

---

## 应急预案

| 状况 | 应对 |
|------|------|
| 真豆包卡了 / 超时 | 立即切 Mock 模式,5 秒出结果。**Mock 不是假数据,是把 sample_report.json 走完整个 graph,所有 Reviewer 规则真实校验** |
| 浏览器渲染 chip 不好看 | 用 Tab3 schema_draft 看 JSON,强调"结构化输出符合预定义 Schema" |
| 网卡了根本起不来 | 切到 README + design-v2.2.md + comparison.md 三个 markdown,讲设计思路 5 分钟 |
| 评委问"和某某框架比有啥优势" | 见 `talking_points.md` Q7 |

---

## Demo 当天前 10 分钟必做

```powershell
cd D:\claude code\multi-agent-langchain-product-competitive-anaysis
.\.venv\Scripts\Activate.ps1
# 预热:先跑一次 ai_coding 真豆包,把缓存预热到 out/ai_coding/
$env:DOMAIN="ai_coding"; $env:ARK_API_KEY="..."; python -m src.graph
# 再跑一次 pm,把 pm 缓存预热
$env:DOMAIN="pm"; python -m src.graph
# 启动前端工作台(两个终端)
./.venv/Scripts/python.exe -m uvicorn api.main:app --port 8000
cd web && npm run dev   # → http://localhost:3000
```

预热的目的:演示当场切到真豆包模式时,即使现场网络抖动,至少 `out/<domain>/` 已经有真实产物,可以直接展示。
