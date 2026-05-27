# 答辩 Q&A 谈话要点

> 评委高概率追问的 12 个问题 + 精炼回答模板。
> 原则:**承认局限,展示思考**。所有回答控制在 30-60 秒。

---

## Q1 · "为什么不用 CrewAI / AutoGen,要自己搭 LangGraph?"

**核心点**:LangGraph 给我们"显式 DAG + 状态机 + 条件路由"的精细控制力,这是 Reviewer 打回闭环所需要的。

> CrewAI 偏 role-play(经理-员工模式),AutoGen 偏多 Agent 自由对话。我们需要的是**确定性的 DAG 路由**,尤其是 Reviewer 检出 error 后要按 `reject_target` 精准回到 collector / analyzer / writer,**还要按 target 分桶限制重试次数**(避免无限循环)。LangGraph 的 `conditional_edges` + `AgentState` 自然支持这个,自己用 CrewAI 实现这套机制反而要写更多胶水。

---

## Q2 · "Reviewer 怎么避免幻觉?LLM 自我审 LLM 不就互相骗?"

**核心点**:R6 LLM judge 是辅助,**R1-R5 全是确定性规则**,不依赖 LLM。

> 你看到的 Reviewer 默认 minimal 模式 hard gate 只有 R1/R4/R5 三条 — R1 是 set 比较,R4 是引用图遍历,R5 是公式校验和 winner 在不在 products 列表里 — **全是 Python 代码**,不调 LLM,所以不存在"互相骗"。
>
> R6 LLM 语义校验是兜底,Demo 默认关掉,只在 full 模式且 R1-R5 全过之后跑一次。即使 R6 出错,最差也是放行一个能通过结构校验的 schema,不会被 LLM 误判打回。

---

## Q3 · "豆包 token 成本怎么算?跑一次多少钱?"

**核心点**:数字给出来,承认贵但有控制。

> 单次端到端真豆包跑通,两次 LLM 调用:**facts ~10K input + 7K output**,**derivations ~14K input + 4K output**,总计约 **35K token / 次**。
>
> 按豆包 Lite 当前定价 0.0003 元/千 token 算,一次完整跑约 **0.01 元人民币**。一天演示 100 次也就 1 块钱。
>
> 真实生产场景下,Cache 命中后只跑 Analyzer 两步,成本会更低。

---

## Q4 · "Mock 模式不就是假数据,跑通了能说明什么?"

**核心点**:**Mock 是把真实 sample_report 走完整 graph,不是绕过校验**。

> Mock 模式只是让 Analyzer 跳过 LLM 调用,直接返回我们预先准备好的 `sample_report.json`。但是这份 JSON 之后要走的路径 — Writer 渲染、Reviewer 7 条规则校验、按 target 分桶 retry、降级流程 — **全部都是真实 Python 代码在跑**,没有任何短路。
>
> 而且我刚才演示打回闭环时,**Reviewer 真的检出了我注入的 3 个 error 并真的打回了** — 这就证明规则代码在工作。

---

## Q5 · "你这 30 条 evidence 是从哪来的?自己编的?"

**核心点**:**坦诚,Demo 数据为主,但来源真实**。

> 这 30 条是为 demo 准备的 evidence,**信息来源都是真实可访问的**:Cursor/Notion 等官方页面、Reddit subreddit 公开帖、Hacker News 评论、PCMag 等第三方评测。我把 URL 和发布日期都标在每条 evidence 里,这些链接现在都可以打开。
>
> 引用的内容是我从这些来源**摘录**的,不是无中生有。比如 SBABE006 那条 Reddit 帖说 Notion 加载 200 行 database 要 3-5 秒,这种用户实测我们没法替他们说,所以引用真实文本而非编造。
>
> 阶段 D 我们做了 OfficialPageAdapter 框架,能用 httpx 真实抓取 — 但 demo 当场抓 cursor.com 这种海外站有网络风险,所以演示用 Mock。

---

## Q6 · "如果用户输入一个完全没见过的行业,系统能扛吗?"

**核心点**:架构上能扛,实测要 2 步喂数据。

> 架构层面 100% 可以 — Schema / Reviewer / Writer / LangGraph 全部域无关。要换行业,**只改两个文件**:
> 1. `config/domains.yaml` 加一个 entry(target/competitors/focus)
> 2. `data/sample_sources_<domain>.json` 提供初始证据(或者接入真实 Adapter)
>
> 代码 0 改动。今天演示的 PM 域就是这么加的 — 我们没有为 PM 改任何 Python 代码,**就改了 1 个 YAML + 写了 30 条 evidence**。
>
> 至于"完全没见过的行业",前提是该行业有足够公开评论数据(Reddit / 第三方评测)。如果是 B2B 极度小众的领域可能要主动做用户访谈补 evidence,但 Schema 本身能容纳。

---

## Q7 · "比传统人工竞品分析快多少?"

**核心点**:三个量化维度。

> 真实测过的 PM 域报告:
>
> - **时间**:人工 3-4 小时 vs 系统 5 分 30 秒,约 **38 倍**
> - **证据覆盖**:人工凭印象通常 15-20 条,系统 **30 条**(可配置)
> - **可重现性**:人工同一题不同人结论不同,系统**同样输入 100% 一致**
>
> 后两项是质变,不只是速度。具体在 `docs/comparison.md` 里。

---

## Q8 · "Doubao-Lite 这种小模型靠谱吗?为什么不用 GPT-4?"

**核心点**:挑战赛规则用豆包 Lite,但**实测够用**。

> 课题指定了 Doubao-Seed-2.0-lite,这是约束条件。但我们实测下来在这个任务上够用:
>
> - 输入 25K token、输出 7K token 完全 hold 得住
> - 4 个 feature gap 都识别对了,priority_score 公式没算错过
> - **0 次 evidence_id 编造**(Reviewer R1 实测 0 error)
>
> 我们做了一些工程兜底来抵消小模型的不稳定:Analyzer 拆两步降低单次复杂度、quick_validate 本地一次自修复、Reviewer 打回机制做二次保障。
>
> 换 GPT-4 / Claude 4.6 应该更稳,LLMClient 那一层是抽象的,改 base_url 就能切。

---

## Q9 · "Reviewer 规则是预先设计的,会不会过拟合 Demo?"

**核心点**:**承认,所以才有 minimal 模式**。

> 这个问题问得好。规则确实是预先设计的,在实际使用前没法验证哪条是必须的。所以我们做了两件事:
>
> 1. **Reviewer minimal 模式默认只把 3 条规则当 error**(R1 引用完整、R4 推理链、R5 结构冲突 — 这三条是机械不变量,无论行业怎么变都是对的)。其余 4 条只给 warning,不打回。
> 2. **scoring 矩阵 / TTL / priority 阈值预留 config 化路径**,设计文档 v2.3 计划下沉到 `config/scoring/<industry>.yaml`,跨行业可以重新调权重。
>
> 我们的态度是:**承认规则可能过拟合,所以默认保守(minimal),用户可以按需扩到 full**。

---

## Q10 · "如果 Analyzer 真编 evidence_id 怎么办?"

**核心点**:演示给评委看 — **打回闭环就在演示这个**。

> 这正是反馈闭环要解决的。刚才 demo 里我用 DEMO_LOOP 模拟了这个场景:
>
> - Analyzer 第一轮塞了一个伪造的 `SDEMOFAK`
> - Reviewer R1 检出 → reject=analyzer → 重试
> - 第二轮干净通过
>
> 实际跑真豆包测试 34 条 evidence 时,豆包**没有编造任何 evidence_id**(R1 0 error)。我们觉得 Prompt 里的硬约束 + 第二步 quick_validate 自修复已经足够压制这类幻觉。如果实际场景出现,Reviewer 打回机制会兜底。

---

## Q11 · "合规问题 — 爬 Reddit 这些内容有版权风险吗?"

**核心点**:**只摘要 + 标 URL,符合公共发表合理引用**。

> 评论里我们只引用**公开论坛上用户主动发表的内容**,每条都标了原始 URL 和发布日期。**没有用 API 大批量抓取,没有重新发布原文,只在分析报告里以"用户反馈"形式摘要 1-2 句**。
>
> 这符合 Reddit 服务条款里 "fair use for commentary and analysis" 的条款。
>
> 更严肃地说,我们 `docs/compliance.md` 列了所有用到的数据源、robots.txt 状态、UA 标识。生产环境部署时建议:
> 1. 接入 Reddit 官方 API(我们 Code 已留了 RedditAdapter 接口)
> 2. 用户访谈类数据脱敏处理
> 3. 输出报告标注数据采集时间,不暗示"实时市场态势"

---

## Q12 · "为什么选 Streamlit 不用 React?会不会显得不够 production-ready?"

**核心点**:**ROI**。

> 3 周 demo,我做了取舍。Streamlit 的优势:
>
> - Python 原生,**直接 `from src.graph import run_demo_streaming`**,不用 REST API 层
> - LangGraph 的 `app.stream()` 配 `st.empty()` 占位,几行代码就能做实时进度展示 — 评委今天看到的 4 节点动画就是这么实现的
> - 300 行代码,一晚做完 v1
>
> React + FastAPI 颜值更高但成本至少 3 倍。**评委要看的是"多 Agent 真在协作"和"证据可溯源",不是看 CSS 调多漂亮**。如果项目落地,前端确实应该重写成 React,代码层我已经按 "graph 是 service,UI 是 view" 拆开,迁移成本不大。

---

## Q13 · "你这个报告和普通搜索摘要有什么区别?"

**核心点**:我们不是按关键词堆资料,而是走 `Plan -> Evidence -> Fact -> Claim -> Insight -> Recommendation`。

> 普通搜索摘要通常是"Cursor 有补全、Copilot 有补全、Windsurf 有补全",最后变成功能清单。我们的目标是找出**竞争逻辑**。
>
> 以 Cursor 代码补全为例,我们先限定边界:不泛泛分析完整 Agent,只分析即时补全、Tab 触发、上下文理解、延迟、下一处修改预测、企业代码库适配。然后按来源分层:官网和文档只证明官方事实,Reddit/HN 只证明用户感知,第三方评测只是补充,不会混用。
>
> 最后不是问"谁有 autocomplete",而是问"谁在抢什么入口":Cursor 抢 AI 原生开发入口,Copilot 抢 GitHub/IDE 分发,Windsurf 抢 AI 编辑流,Supermaven 抢低延迟和大上下文,JetBrains 抢专业 IDE 语义,Tabnine 抢企业安全采购。
>
> 所以我们的报告核心不是文章,而是一条可审计链路:**Evidence -> Fact -> Claim -> Insight -> Recommendation**。每条重要结论都要有 evidence_id,没证据的只能标 hypothesis,不能进入最终 conclusion。

---

## Q14 · "如果 Cursor 补全体验领先,为什么还要看 Supermaven、JetBrains、Tabnine?"

**核心点**:竞品不是"功能一样"才算竞品,而是会在某个场景抢走同一类用户决策。

> Cursor 的代码补全优势不只是"下一行补得准",而是往 predictive editing 走:预测开发者下一步要改哪里、改什么。Windsurf 是最像 Cursor 的直接对手,因为它也把 Tab 做成多动作入口。
>
> 但用户决策不只看这个。Copilot 的优势是不用换 IDE,GitHub 生态和企业采购强;Supermaven 是速度和大上下文尖刀,会抢"只要补全不要 Agent"的用户;JetBrains AI 依托专业 IDE 静态分析和重构能力,会守住 Java/Kotlin/后端用户;Tabnine 不一定体验最酷,但私有化、合规、air-gapped 部署更容易过大企业安全审查。
>
> 所以我们的结论是:Cursor 不该把补全叙事停留在 autocomplete,而要升级成 predictive editing,让 Tab 从"接受代码"变成"接受下一步开发动作"。

---

## 应对"我没听懂"型问题(Bonus)

如果评委提问含混,**先反问澄清**而不是猜:

> "您是问 Reviewer 打回的具体规则,还是问打回之后 Analyzer 怎么知道改哪里?"
>
> "您说的'幻觉'是指 evidence_id 编造,还是说事实描述偏离原文?"

这样既不会答错方向,又显得严谨。
