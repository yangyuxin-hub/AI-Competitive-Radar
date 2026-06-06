# 全链路原始数据 · demo2
> 每个环节的**真实产出**(非总结),供逐环人工核对。


## ① 意图澄清 — LLM 原始输出

```json
{
  "reasoning": "识别为AI编程助手品类，用户明确要求对比Cursor、Windsurf、GitHub Copilot的代码补全体验差距，意图属于功能对比。竞品从2026年最新的公开替代榜单中提取，覆盖主流同体量产品、开源新锐挑战者、大厂生态方案、差异化命令行/云IDE替代，覆盖不同竞争逻辑。焦点维度全部围绕代码补全体验的核心指标展开，没有使用泛化的通用维度，完全贴合用户指定的分析方向。",
  "domain_name": "AI编程助手",
  "analysis_intent": "feature_compare",
  "target_candidates": [
    "Cursor",
    "Windsurf",
    "GitHubCopilot"
  ],
  "competitors_candidates": [
    "Windsurf",
    "GitHubCopilot",
    "Cline",
    "Continue.dev",
    "Aider",
    "Zed",
    "Claude Code",
    "Trae AI",
    "Devin",
    "Replit AI"
  ],
  "competitors_suggested": [
    "Windsurf",
    "GitHubCopilot",
    "Cline"
  ],
  "competitor_hints": {
    "Windsurf": "【主流现有】原Codeium团队推出的AI IDE，主打高性价比补全体验",
    "GitHubCopilot": "【大厂生态】微软+OpenAI联合推出的VS Code原生扩展，生态适配极强",
    "Cline": "【新锐AI】2026年爆火的开源AI编码助手，SWE-bench跑分行业领先",
    "Continue.dev": "【差异化替代】完全开源可自托管的IDE插件，无厂商数据锁定",
    "Aider": "【差异化替代】命令行端的AI编码助手，支持多仓库大文件补全",
    "Zed": "【新锐AI】高性能原生AI IDE，主打低延迟实时补全",
    "Claude Code": "【大厂生态】Anthropic推出的原生编码助手，长上下文补全能力突出",
    "Trae AI": "【大厂生态】字节跳动推出的国产AI IDE，适配国内开发者使用习惯",
    "Devin": "【新锐AI】Cognition推出的端到端AI编程代理，支持全流程自动编码",
    "Replit AI": "【差异化替代】在线云IDE内置的AI编码能力，无需本地环境即可使用"
  },
  "focus_candidates": [
    "实时行内补全准确率",
    "长上下文多文件补全能力",
    "补全触发响应延迟",
    "补全代码可直接运行率",
    "自定义补全规则适配",
    "大模型切换自由度"
  ],
  "focus_hints": {
    "实时行内补全准确率": "日常写单行/小段代码时补全的正确率，直接决定日常编码效率",
    "长上下文多文件补全能力": "跨多个关联文件理解项目上下文后给出补全的能力，是拉开体验差距的核心",
    "补全触发响应延迟": "输入代码后补全弹窗弹出的等待时长，直接影响编码流畅度",
    "补全代码可直接运行率": "生成的补全代码无需修改就能直接运行的比例，减少后续调试成本",
    "自定义补全规则适配": "支持用户自定义项目专属补全规则、内部库提示词的灵活度",
    "大模型切换自由度": "是否支持用户自行切换不同大模型作为补全后端，适配不同场景需求"
  },
  "focus_suggested": "实时行内补全准确率",
  "purpose_candidates": [
    "评估不同AI编程助手的代码补全能力差异",
    "辅助个人/团队选型适配的AI编码工具",
    "找到自身产品补全体验的优化方向",
    "梳理2026年AI编码赛道补全能力的行业基准"
  ],
  "purpose_suggested": "评估不同AI编程助手的代码补全能力差异",
  "domain_key": "ai_coding",
  "_domain_products": [
    "Cursor",
    "Windsurf",
    "GitHubCopilot"
  ],
  "_hit_products": [
    "Cursor",
    "Windsurf",
    "GitHubCopilot"
  ]
}
```

## ② 官网 URL 发现 — 原始

```json
{
  "Cursor": {
    "official_pages": [
      "https://cursor.com/features"
    ],
    "pricing_pages": [
      "https://cursor.com/pricing"
    ],
    "aliases": [
      "Cursor AI",
      "Cursor editor"
    ],
    "source": "config"
  },
  "GitHubCopilot": {
    "official_pages": [
      "https://github.com/features/copilot"
    ],
    "pricing_pages": [
      "https://github.com/features/copilot/plans"
    ],
    "aliases": [
      "GitHub Copilot",
      "Copilot"
    ],
    "source": "config"
  },
  "Windsurf": {
    "official_pages": [
      "https://codeium.com/windsurf"
    ],
    "pricing_pages": [
      "https://codeium.com/pricing"
    ],
    "aliases": [
      "Windsurf",
      "Codeium Windsurf"
    ],
    "source": "config"
  }
}
```

## ③ 检索词 + 锚定站点 — 全部 query


### Cursor
| claim_type | query | site | why |
|---|---|---|---|
| feature_existence | Cursor AI coding assistant features | cursor.com | 权威源定向 |
| feature_existence | Cursor AI coding assistant features | (全网) | 全网兜底(相关性门过滤) |
| performance_quality | Cursor AI coding assistant review | reddit.com | 权威源定向 |
| performance_quality | Cursor AI coding assistant review | g2.com | 权威源定向 |
| performance_quality | Cursor AI coding assistant review | (全网) | 全网兜底(相关性门过滤) |
| pricing | Cursor AI coding assistant pricing plans | cursor.com | 权威源定向 |
| pricing | Cursor AI coding assistant pricing plans | (全网) | 全网兜底(相关性门过滤) |
| user_pain | Cursor AI coding assistant complaints problems | reddit.com | 权威源定向 |
| user_pain | Cursor AI coding assistant complaints problems | news.ycombinator.com | 权威源定向 |
| user_pain | Cursor AI coding assistant complaints problems | (全网) | 全网兜底(相关性门过滤) |

### Windsurf
| claim_type | query | site | why |
|---|---|---|---|
| feature_existence | Windsurf AI coding assistant features | codeium.com | 权威源定向 |
| feature_existence | Windsurf AI coding assistant features | (全网) | 全网兜底(相关性门过滤) |
| performance_quality | Windsurf AI coding assistant review | reddit.com | 权威源定向 |
| performance_quality | Windsurf AI coding assistant review | g2.com | 权威源定向 |
| performance_quality | Windsurf AI coding assistant review | (全网) | 全网兜底(相关性门过滤) |
| pricing | Windsurf AI coding assistant pricing plans | codeium.com | 权威源定向 |
| pricing | Windsurf AI coding assistant pricing plans | (全网) | 全网兜底(相关性门过滤) |
| user_pain | Windsurf AI coding assistant complaints problems | reddit.com | 权威源定向 |
| user_pain | Windsurf AI coding assistant complaints problems | news.ycombinator.com | 权威源定向 |
| user_pain | Windsurf AI coding assistant complaints problems | (全网) | 全网兜底(相关性门过滤) |

### GitHubCopilot
| claim_type | query | site | why |
|---|---|---|---|
| feature_existence | GitHubCopilot AI coding assistant features | github.com | 权威源定向 |
| feature_existence | GitHubCopilot AI coding assistant features | (全网) | 全网兜底(相关性门过滤) |
| performance_quality | GitHubCopilot AI coding assistant review | reddit.com | 权威源定向 |
| performance_quality | GitHubCopilot AI coding assistant review | g2.com | 权威源定向 |
| performance_quality | GitHubCopilot AI coding assistant review | (全网) | 全网兜底(相关性门过滤) |
| pricing | GitHubCopilot AI coding assistant pricing plans | github.com | 权威源定向 |
| pricing | GitHubCopilot AI coding assistant pricing plans | (全网) | 全网兜底(相关性门过滤) |
| user_pain | GitHubCopilot AI coding assistant complaints problems | reddit.com | 权威源定向 |
| user_pain | GitHubCopilot AI coding assistant complaints problems | news.ycombinator.com | 权威源定向 |
| user_pain | GitHubCopilot AI coding assistant complaints problems | (全网) | 全网兜底(相关性门过滤) |

## ④ 采集结果 — 全部 96 条原始证据


### Cursor

**feature_existence** (5 条):
- `[S9B45556|official_page]` Cursor: The best coding agent
    - 出处: https://cursor.com/
- `[S6CABA2E|official_page]` Cursor — Build Software with AI Agents
    - 出处: https://cursor.com/product
- `[SBE04DD1|web_search]` Meet the new Cursor · Cursor
    - 出处: https://cursor.com/blog/cursor-3
- `[S04B3758|web_search]` Cursor AI April-May 2026 Update: 5 New Features That Actually Matter
    - 出处: https://anycap.ai/page/en-US/blog/cursor-ai-2026-new-features-guide
- `[S88AD1C5|web_search]` Cursor Release Notes - June 2026 Latest Updates - Releasebot
    - 出处: https://releasebot.io/updates/cursor

**performance_quality** (12 条):
- `[S3296F18|official_page]` Cursor - The AI Code Editor
    - 出处: https://cursor.com/en/home
- `[S9B28BF1|official_page]` “ It was night and day from one batch to another, adoption went from single digits to over 80%. It just spread like w...
    - 出处: https://cursor.com/features
- `[SFD8091B|official_page]` “ My favorite enterprise AI service is Cursor. Every one of our engineers, some 40,000, are now assisted by AI and ou...
    - 出处: https://cursor.com/features
- `[S00ABC2D|official_page]` “ The most useful AI tool that I currently pay for, hands down, is Cursor. It's fast, autocompletes when and where yo...
    - 出处: https://cursor.com/features
- `[S235AA3F|reddit]` Cursor is not nearly as good as this sub makes it sound, what ... - Reddit
    - 出处: https://www.reddit.com/r/ChatGPTCoding/comments/1cmxw8a/cursor_is_not_nearly_as_good_as_this_sub_makes_it/
- `[S9A2FCBB|reddit]` cursor - Reddit
    - 出处: https://www.reddit.com/r/cursor/
- `[S433FB37|reddit]` Cursor.sh is Amazing : r/ChatGPT - Reddit
    - 出处: https://www.reddit.com/r/ChatGPT/comments/18jbxar/cursorsh_is_amazing/
- `[S7CBDD7F|web_search]` Cursor Reviews 2026: Details, Pricing, & Features | G2
    - 出处: https://www.g2.com/products/cursor/reviews
- `[S67F9EE1|web_search]` Cursor Pros and Cons | User Likes & Dislikes
    - 出处: https://www.g2.com/products/cursor/reviews?qs=pros-and-cons
- `[S96FFCB5|web_search]` Torn Between Windsurf vs. Cursor? So Was I Until I Went All In
    - 出处: https://learn.g2.com/windsurf-vs-cursor
- `[S23C1F37|web_search]` Cursor Review 2026: Honest Pros & Cons | No Code MBA
    - 出处: https://www.nocode.mba/articles/cursor-review-2026
- `[SA1E71DA|official_page]` “ It's definitely becoming more fun to be a programmer. We are at the 1% of what's possible, and it's in interactive ...
    - 出处: https://cursor.com/features

**pricing** (8 条):
- `[S6B9D3DE|pricing_page]` Cursor · Pricing
    - 出处: https://cursor.com/pricing
- `[SA5E6150|pricing_page]` Pricing and plans | Cursor Docs
    - 出处: https://cursor.com/help/account-and-billing/pricing
- `[S6ACED39|pricing_page]` Models & Pricing | Cursor Docs
    - 出处: https://cursor.com/docs/models-and-pricing
- `[SBC92AAB|web_search]` Cursor · Pricing
    - 出处: https://cursor.com/pricing
- `[SD7CFB03|web_search]` Pricing and plans | Cursor Docs
    - 出处: https://cursor.com/help/account-and-billing/pricing
- `[SAEC9816|web_search]` Cursor Pricing in 2026: Hobby, Pro, Pro+, Ultra, Teams, and Enterprise ...
    - 出处: https://dev.to/rahulxsingh/cursor-pricing-in-2026-hobby-pro-pro-ultra-teams-and-enterprise-plans-explained-4b89
- `[S4734790|official_page]` Hobby · $0 / mo.
    - 出处: https://cursor.com/pricing
- `[SB22F80F|official_page]` Pro · $16 / mo.
    - 出处: https://cursor.com/pricing

**user_pain** (7 条):
- `[S531F883|reddit]` Customer Support : r/cursor
    - 出处: https://www.reddit.com/r/cursor/comments/1jxgegx/customer_support/
- `[S16FD350|hn]` Cursor IDE support hallucinates lockout policy, causes user ...
    - 出处: https://news.ycombinator.com/item?id=43683012
- `[S325255D|hn]` Cursor seems to have the ultimate “free” users waste the most ...
    - 出处: https://news.ycombinator.com/item?id=43375658
- `[SD2F1426|hn]` Cursor 3 | Hacker News
    - 出处: https://news.ycombinator.com/item?id=47618084
- `[SF00B1E8|web_search]` Fix Annoying Double Cursor on Windows 10 - YouTube
    - 出处: https://www.youtube.com/watch?v=i61ubm-Ohqw
- `[S0486109|web_search]` Agent Persona multiple personalities - Feedback - Cursor | Forum
    - 出处: https://forum.cursor.com/t/agent-persona-multiple-personalities/161153
- `[S17AAF37|web_search]` Cursor Read Customer Complaints and Reviews - Xolvie
    - 出处: https://www.sikayetvar.com/en/cursor-us

### GitHubCopilot

**feature_existence** (6 条):
- `[SA2BA026|official_page]` GitHub Copilot · Your AI pair programmer · GitHub
    - 出处: https://github.com/features/copilot
- `[S4F1BE05|official_page]` GitHub Copilot features - GitHub Docs
    - 出处: https://docs.github.com/en/copilot/about-github-copilot/github-copilot-features
- `[S68683F7|web_search]` GitHub Copilot features
    - 出处: https://docs.github.com/en/copilot/get-started/features
- `[SBA78E21|web_search]` GitHub for Beginners: Essential features of GitHub Copilot
    - 出处: https://github.blog/ai-and-ml/github-copilot/github-for-beginners-essential-features-of-github-copilot/
- `[S208F4F5|web_search]` Top 10 GitHub Copilot Features Every Developer Should Know
    - 出处: https://www.c-sharpcorner.com/article/top-10-github-copilot-features-every-developer-should-know/
- `[SCD709A9|official_page]` GitHub Copilot is powered by generative AI models developed by GitHub, OpenAI, and Microsoft. It has been trained on ...
    - 出处: https://github.com/features/copilot

**performance_quality** (8 条):
- `[S8047A45|official_page]` GitHub Copilot enables developers to focus more energy on problem solving and collaboration and spend less effort on ...
    - 出处: https://github.com/features/copilot
- `[S52F168E|official_page]` GitHub Copilot enables developers to focus more energy on problem solving and collaboration and spend less effort on ...
    - 出处: https://github.com/features/copilot/plans
- `[SF259F5C|reddit]` Why does GitHub Copilot pull request reviews give such poor ... - Reddit
    - 出处: https://www.reddit.com/r/ExperiencedDevs/comments/1ly13n0/why_does_github_copilot_pull_request_reviews_give/
- `[SBF0C947|reddit]` Is Github Copilot worth it? : r/ExperiencedDevs - Reddit
    - 出处: https://www.reddit.com/r/ExperiencedDevs/comments/1kwnmnn/is_github_copilot_worth_it/
- `[S859DA7B|reddit]` GitHub copilot for code reviewer - Reddit
    - 出处: https://www.reddit.com/r/github/comments/1rqj6sq/github_copilot_for_code_reviewer/
- `[SFC14712|web_search]` Top 10 Gemini Alternatives & Competitors in 2026 | G2
    - 出处: https://www.g2.com/products/google-gemini/competitors/alternatives
- `[SBBD71EA|web_search]` 8 Best AI Coding Assistants I Recommend for 2026
    - 出处: https://learn.g2.com/best-ai-coding-assistants
- `[S30618A0|web_search]` I Tried GitHub Copilot vs. ChatGPT for Coding: What I Learned
    - 出处: https://learn.g2.com/github-copilot-vs-chatgpt

**pricing** (10 条):
- `[S6D56447|official_page]` GitHub Copilot · Plans & pricing · GitHub
    - 出处: https://github.com/features/copilot/plans
- `[S74E8FAF|official_page]` Usage from your existing licensed users continues to draw from their included monthly allowance as it does today. Beg...
    - 出处: https://github.com/features/copilot
- `[S4D1D5D7|pricing_page]` GitHub Copilot · Plans & pricing
    - 出处: https://github.com/features/copilot/plans
- `[S3B6FA48|pricing_page]` Plans for GitHub Copilot
    - 出处: https://docs.github.com/en/copilot/get-started/plans
- `[SDE2D015|pricing_page]` About individual GitHub Copilot plans and benefits
    - 出处: https://docs.github.com/en/copilot/concepts/billing/individual-plans
- `[SF81FA60|web_search]` GitHub Copilot Pricing (2026): Plans, Costs & Is It Worth It?
    - 出处: https://devtoolsreview.com/pricing/copilot-pricing/
- `[S2C964C8|web_search]` GitHub Copilot Pricing 2026: Free to $39/user/month — All 5 Plans
    - 出处: https://costbench.com/software/ai-coding-assistants/github-copilot/
- `[SFFC36D1|web_search]` GitHub Copilot Pricing — Business & Enterprise Plans | Copilot
    - 出处: https://www.githubcopilot.dev/pricing
- `[S47400E3|official_page]` GitHub AI Credits are how you pay for AI usage in GitHub Copilot. Every plan includes a monthly allowance: 1 AI credi...
    - 出处: https://github.com/features/copilot
- `[S4B6D36B|official_page]` GitHub AI Credits are how you pay for AI usage in GitHub Copilot. Every plan includes a monthly allowance: 1 AI credi...
    - 出处: https://github.com/features/copilot

**user_pain** (8 条):
- `[SFA50E79|reddit]` GitHub issues are getting filled with low quality Copilot reports - Reddit
    - 出处: https://www.reddit.com/r/GithubCopilot/comments/1qudvjo/github_issues_are_getting_filled_with_low_quality/
- `[S89AE4F4|reddit]` I get the frustration, but is the whole point of this sub to complain? - Reddit
    - 出处: https://www.reddit.com/r/GithubCopilot/comments/1smwvo6/i_get_the_frustration_but_is_the_whole_point_of/
- `[SDAF5489|reddit]` Copilot is rubbish, and I'm tired of pretending it isn't. : r/github - Reddit
    - 出处: https://www.reddit.com/r/github/comments/15kua54/copilot_is_rubbish_and_im_tired_of_pretending_it/
- `[S37D435F|hn]` We've filed a lawsuit against GitHub Copilot | Hacker News
    - 出处: https://news.ycombinator.com/item?id=33457063
- `[SA00BA35|hn]` GitHub's Copilot lies about its documentation. Why would I trust it with my code | Hacker News
    - 出处: https://news.ycombinator.com/item?id=41719610
- `[S2D8B676|hn]` I find GitHub Copilot close to useless for production code. The worst, most obsc... | Hacker News
    - 出处: https://news.ycombinator.com/item?id=40341181
- `[SEC9E5B7|web_search]` Common GitHub Copilot Complaints | CrowdMind Atlas
    - 出处: https://crowdmindatlas.com/complaints/github-copilot/
- `[SB5D150C|web_search]` Is GitHub Copilot Getting Worse in 2026? What Changed... | NxCode
    - 出处: https://www.nxcode.io/resources/news/github-copilot-getting-worse-2026-developers-switching

### Windsurf

**feature_existence** (13 条):
- `[S74DE73A|official_page]` Unlimited access to SWE-1.6, the fastest coding model in the world.
    - 出处: https://codeium.com/windsurf
- `[SB00FAB4|official_page]` mcp-registry.codeium.com
    - 出处: https://mcp-registry.codeium.com/
- `[SFBE6636|official_page]` windsurf-stable.codeium.com
    - 出处: https://windsurf-stable.codeium.com/api/update/win32-x64-user/stable/latest
- `[S4F567D2|web_search]` Windsurf - The best AI for Coding
    - 出处: https://windsurf.com/
- `[SD785395|web_search]` Windsurf Tutorial for Beginners (AI Code Editor) - Better... - YouTube
    - 出处: https://www.youtube.com/watch?v=8TcWGk1DJVs
- `[S249F056|web_search]` Windsurf AI Review 2026: The Best Coding IDE for... | NxCode
    - 出处: https://www.nxcode.io/resources/news/windsurf-ai-review-2026-best-ide-for-beginners
- `[SD43D838|official_page]` You decide what to build, then your agents write the code, chase the edge cases, and test every detail.
    - 出处: https://codeium.com/windsurf
- `[SC297D8B|official_page]` To build a dashboard for real-time store sales data, we will stream events from Kafka over websockets and render them...
    - 出处: https://codeium.com/windsurf
- `[S218E76F|official_page]` Manage fleets of local and cloud agents from one surface. Plan, delegate, review, and ship without leaving your editor.
    - 出处: https://codeium.com/windsurf
- `[SE7022FC|official_page]` Devin Desktop includes a full IDE with syntax highlighting, autocomplete, and debugging tools built in for you to sta...
    - 出处: https://codeium.com/windsurf
- `[SA264AA2|official_page]` List, create, update, and query issues, projects, initiatives, cycles, and comments.
    - 出处: https://codeium.com/windsurf
- `[S86197A6|official_page]` Access Jira and Confluence — manage issues and create enterprise documentation.
    - 出处: https://codeium.com/windsurf
- `[S724BFD2|official_page]` Read, trace, and debug every change your agents ship.
    - 出处: https://codeium.com/windsurf

**performance_quality** (7 条):
- `[S8645718|official_page]` Fast Context finds the exact files and lines your agent needs—in milliseconds.
    - 出处: https://codeium.com/windsurf
- `[S21E9C9F|reddit]` Is Windsurf really that good or just hype ? : r/ChatGPTCoding - Reddit
    - 出处: https://www.reddit.com/r/ChatGPTCoding/comments/1gwnpqs/is_windsurf_really_that_good_or_just_hype/
- `[S66C709B|reddit]` Windsurf is actually great. - Reddit
    - 出处: https://www.reddit.com/r/windsurf/comments/1q3cq8x/windsurf_is_actually_great/
- `[SBC8356E|reddit]` Any real dev have legit review of Windsurf? : r/cursor - Reddit
    - 出处: https://www.reddit.com/r/cursor/comments/1jn8pfg/any_real_dev_have_legit_review_of_windsurf/
- `[S3780DA1|web_search]` Windsurf Reviews 2026: Details, Pricing, & Features | G2
    - 出处: https://www.g2.com/products/exafunction-windsurf/reviews
- `[S15EDE59|web_search]` Windsurf Pros and Cons | User Likes & Dislikes - G2
    - 出处: https://www.g2.com/products/exafunction-windsurf/reviews?qs=pros-and-cons
- `[SCD0C36D|web_search]` Windsurf Reviews 2025: Details, Pricing, & Features - G2
    - 出处: https://www.g2.com/products/exafunction-windsurf/reviews?page=3

**pricing** (8 条):
- `[S53C45A6|pricing_page]` Pricing - Windsurf
    - 出处: https://codeium.com/redirect/windsurf/learn-pricing
- `[S175E9ED|pricing_page]` Blog | Windsurf
    - 出处: https://codeium.com/blog
- `[SE957AC8|pricing_page]` Windsurf for Enterprise
    - 出处: https://codeium.com/enterprise
- `[S1EA5110|web_search]` Introducing our new Windsurf pricing plans | Devin
    - 出处: https://devin.ai/blog/windsurf-pricing-plans
- `[S0D76DF4|web_search]` Plans and Usage - Devin Docs
    - 出处: https://docs.windsurf.com/windsurf/accounts/usage
- `[S5D9E9B8|web_search]` Windsurf Pricing 2026: Plans, Quotas & What Changed - Verdent Guides
    - 出处: https://www.verdent.ai/guides/windsurf-pricing-2026
- `[S26EAE1A|official_page]` Frequently Asked Questions · Free $0 Download Light quota to code with agents Limited model availability Unlimited in...
    - 出处: https://codeium.com/pricing
- `[S2223916|official_page]` Frequently Asked Questions · +$40/month per full user Each full user includes their own generous quota and access to ...
    - 出处: https://codeium.com/pricing

**user_pain** (4 条):
- `[S4B2B734|reddit]` Windsurf Issues : r/Codeium - Reddit
    - 出处: https://www.reddit.com/r/Codeium/comments/1i4jk42/windsurf_issues/
- `[SF7718BE|reddit]` Report from a Windsurf user : r/Codeium - Reddit
    - 出处: https://www.reddit.com/r/Codeium/comments/1hsn1xw/report_from_a_windsurf_user/
- `[SB25AF39|reddit]` Anyone else having issues with Windsurf editing files? - Reddit
    - 出处: https://www.reddit.com/r/Codeium/comments/1j9eott/anyone_else_having_issues_with_windsurf_editing/
- `[S65D18BD|hn]` Ask HN: Cursor or Windsurf? - Hacker News
    - 出处: https://news.ycombinator.com/item?id=43959710

## ⑤ 清洗后 — 喂 LLM 的 83 条(分桶 top-K + 近似去重)


### Cursor

**feature_existence** (5 条):
- `[S9B45556]` Cursor: The best coding agent
- `[S6CABA2E]` Cursor — Build Software with AI Agents
- `[SBE04DD1]` Meet the new Cursor · Cursor
- `[S04B3758]` Cursor AI April-May 2026 Update: 5 New Features That Actually Matter
- `[S88AD1C5]` Cursor Release Notes - June 2026 Latest Updates - Releasebot

**performance_quality** (8 条):
- `[S3296F18]` Cursor - The AI Code Editor
- `[S9B28BF1]` “ It was night and day from one batch to another, adoption went from single digits to over 80%. It just spread like w...
- `[SFD8091B]` “ My favorite enterprise AI service is Cursor. Every one of our engineers, some 40,000, are now assisted by AI and ou...
- `[SA1E71DA]` “ It's definitely becoming more fun to be a programmer. We are at the 1% of what's possible, and it's in interactive ...
- `[S00ABC2D]` “ The most useful AI tool that I currently pay for, hands down, is Cursor. It's fast, autocompletes when and where yo...
- `[S235AA3F]` Cursor is not nearly as good as this sub makes it sound, what ... - Reddit
- `[S9A2FCBB]` cursor - Reddit
- `[S433FB37]` Cursor.sh is Amazing : r/ChatGPT - Reddit

**pricing** (8 条):
- `[S6B9D3DE]` Cursor · Pricing
- `[SA5E6150]` Pricing and plans | Cursor Docs
- `[S6ACED39]` Models & Pricing | Cursor Docs
- `[SBC92AAB]` Cursor · Pricing
- `[SD7CFB03]` Pricing and plans | Cursor Docs
- `[SAEC9816]` Cursor Pricing in 2026: Hobby, Pro, Pro+, Ultra, Teams, and Enterprise ...
- `[S4734790]` Hobby · $0 / mo.
- `[SB22F80F]` Pro · $16 / mo.

**user_pain** (7 条):
- `[S531F883]` Customer Support : r/cursor
- `[S16FD350]` Cursor IDE support hallucinates lockout policy, causes user ...
- `[S325255D]` Cursor seems to have the ultimate “free” users waste the most ...
- `[SD2F1426]` Cursor 3 | Hacker News
- `[SF00B1E8]` Fix Annoying Double Cursor on Windows 10 - YouTube
- `[S0486109]` Agent Persona multiple personalities - Feedback - Cursor | Forum
- `[S17AAF37]` Cursor Read Customer Complaints and Reviews - Xolvie

### GitHubCopilot

**feature_existence** (6 条):
- `[SA2BA026]` GitHub Copilot · Your AI pair programmer · GitHub
- `[S4F1BE05]` GitHub Copilot features - GitHub Docs
- `[S68683F7]` GitHub Copilot features
- `[SBA78E21]` GitHub for Beginners: Essential features of GitHub Copilot
- `[S208F4F5]` Top 10 GitHub Copilot Features Every Developer Should Know
- `[SCD709A9]` GitHub Copilot is powered by generative AI models developed by GitHub, OpenAI, and Microsoft. It has been trained on ...

**performance_quality** (7 条):
- `[S8047A45]` GitHub Copilot enables developers to focus more energy on problem solving and collaboration and spend less effort on ...
- `[SF259F5C]` Why does GitHub Copilot pull request reviews give such poor ... - Reddit
- `[SBF0C947]` Is Github Copilot worth it? : r/ExperiencedDevs - Reddit
- `[S859DA7B]` GitHub copilot for code reviewer - Reddit
- `[SFC14712]` Top 10 Gemini Alternatives & Competitors in 2026 | G2
- `[SBBD71EA]` 8 Best AI Coding Assistants I Recommend for 2026
- `[S30618A0]` I Tried GitHub Copilot vs. ChatGPT for Coding: What I Learned

**pricing** (8 条):
- `[S6D56447]` GitHub Copilot · Plans & pricing · GitHub
- `[S4D1D5D7]` GitHub Copilot · Plans & pricing
- `[S3B6FA48]` Plans for GitHub Copilot
- `[SDE2D015]` About individual GitHub Copilot plans and benefits
- `[SF81FA60]` GitHub Copilot Pricing (2026): Plans, Costs & Is It Worth It?
- `[S2C964C8]` GitHub Copilot Pricing 2026: Free to $39/user/month — All 5 Plans
- `[SFFC36D1]` GitHub Copilot Pricing — Business & Enterprise Plans | Copilot
- `[S74E8FAF]` Usage from your existing licensed users continues to draw from their included monthly allowance as it does today. Beg...

**user_pain** (8 条):
- `[SFA50E79]` GitHub issues are getting filled with low quality Copilot reports - Reddit
- `[S89AE4F4]` I get the frustration, but is the whole point of this sub to complain? - Reddit
- `[SDAF5489]` Copilot is rubbish, and I'm tired of pretending it isn't. : r/github - Reddit
- `[S37D435F]` We've filed a lawsuit against GitHub Copilot | Hacker News
- `[SA00BA35]` GitHub's Copilot lies about its documentation. Why would I trust it with my code | Hacker News
- `[S2D8B676]` I find GitHub Copilot close to useless for production code. The worst, most obsc... | Hacker News
- `[SEC9E5B7]` Common GitHub Copilot Complaints | CrowdMind Atlas
- `[SB5D150C]` Is GitHub Copilot Getting Worse in 2026? What Changed... | NxCode

### Windsurf

**feature_existence** (8 条):
- `[SB00FAB4]` mcp-registry.codeium.com
- `[SFBE6636]` windsurf-stable.codeium.com
- `[S4F567D2]` Windsurf - The best AI for Coding
- `[SD785395]` Windsurf Tutorial for Beginners (AI Code Editor) - Better... - YouTube
- `[S249F056]` Windsurf AI Review 2026: The Best Coding IDE for... | NxCode
- `[S74DE73A]` Unlimited access to SWE-1.6, the fastest coding model in the world.
- `[SD43D838]` You decide what to build, then your agents write the code, chase the edge cases, and test every detail.
- `[SC297D8B]` To build a dashboard for real-time store sales data, we will stream events from Kafka over websockets and render them...

**performance_quality** (6 条):
- `[S8645718]` Fast Context finds the exact files and lines your agent needs—in milliseconds.
- `[S21E9C9F]` Is Windsurf really that good or just hype ? : r/ChatGPTCoding - Reddit
- `[S66C709B]` Windsurf is actually great. - Reddit
- `[SBC8356E]` Any real dev have legit review of Windsurf? : r/cursor - Reddit
- `[S3780DA1]` Windsurf Reviews 2026: Details, Pricing, & Features | G2
- `[S15EDE59]` Windsurf Pros and Cons | User Likes & Dislikes - G2

**pricing** (8 条):
- `[S53C45A6]` Pricing - Windsurf
- `[S175E9ED]` Blog | Windsurf
- `[SE957AC8]` Windsurf for Enterprise
- `[S1EA5110]` Introducing our new Windsurf pricing plans | Devin
- `[S0D76DF4]` Plans and Usage - Devin Docs
- `[S5D9E9B8]` Windsurf Pricing 2026: Plans, Quotas & What Changed - Verdent Guides
- `[S26EAE1A]` Frequently Asked Questions · Free $0 Download Light quota to code with agents Limited model availability Unlimited in...
- `[S2223916]` Frequently Asked Questions · +$40/month per full user Each full user includes their own generous quota and access to ...

**user_pain** (4 条):
- `[S4B2B734]` Windsurf Issues : r/Codeium - Reddit
- `[SF7718BE]` Report from a Windsurf user : r/Codeium - Reddit
- `[SB25AF39]` Anyone else having issues with Windsurf editing files? - Reddit
- `[S65D18BD]` Ask HN: Cursor or Windsurf? - Hacker News