# 逐环产出 · demo2

**输入**: 分析 Cursor 与 Windsurf, GitHubCopilot 在 代码补全体验 上的差距


## ① 意图澄清 — 相关竞品 + 分析维度/目标

- **品类**: AI编程助手 | **意图**: feature_compare
- **发现的竞品候选**: ['Windsurf', 'GitHubCopilot', 'Cline', 'Continue.dev', 'Aider', 'Zed', 'Claude Code', 'Trae AI', 'Devin', 'Replit AI']
- **推荐先选**: ['Windsurf', 'GitHubCopilot', 'Cline']
- **竞品类型标注**:
    - Windsurf: 【主流现有】原Codeium团队推出的AI IDE，主打高性价比补全体验
    - GitHubCopilot: 【大厂生态】微软+OpenAI联合推出的VS Code原生扩展，生态适配极强
    - Cline: 【新锐AI】2026年爆火的开源AI编码助手，SWE-bench跑分行业领先
    - Continue.dev: 【差异化替代】完全开源可自托管的IDE插件，无厂商数据锁定
    - Aider: 【差异化替代】命令行端的AI编码助手，支持多仓库大文件补全
    - Zed: 【新锐AI】高性能原生AI IDE，主打低延迟实时补全
    - Claude Code: 【大厂生态】Anthropic推出的原生编码助手，长上下文补全能力突出
    - Trae AI: 【大厂生态】字节跳动推出的国产AI IDE，适配国内开发者使用习惯
    - Devin: 【新锐AI】Cognition推出的端到端AI编程代理，支持全流程自动编码
    - Replit AI: 【差异化替代】在线云IDE内置的AI编码能力，无需本地环境即可使用
- **分析维度候选**: ['实时行内补全准确率', '长上下文多文件补全能力', '补全触发响应延迟', '补全代码可直接运行率', '自定义补全规则适配', '大模型切换自由度']
- **推荐维度**: 实时行内补全准确率
- **分析目的**: 评估不同AI编程助手的代码补全能力差异
- **Agent 判断**: 识别为AI编程助手品类，用户明确要求对比Cursor、Windsurf、GitHub Copilot的代码补全体验差距，意图属于功能对比。竞品从2026年最新的公开替代榜单中提取，覆盖主流同体量产品、开源新锐挑战者、大厂生态方案、差异化命令行/云IDE替代，覆盖不同竞争逻辑。焦点维度全部围绕代码补全体验的核心指标展开，没有使用泛化的通用维度，完全贴合用户指定的分析方向。

> 实际选定(本次): target=Cursor, competitors=['Windsurf', 'GitHubCopilot'], focus=代码补全体验

## ② 竞品官网 URL 发现(LLM 自主)

| 产品 | 来源 | official_pages | pricing_pages |
|---|---|---|---|
| Cursor | config | https://cursor.com/features | https://cursor.com/pricing |
| Windsurf | config | https://codeium.com/windsurf | https://codeium.com/pricing |
| GitHubCopilot | config | https://github.com/features/copilot | https://github.com/features/copilot/plans |

## ③ 检索词设计 + 锚定站点(逐产品 × claim_type)


**Cursor**:
| claim_type | 检索词 query | 锚定站点 site |
|---|---|---|
| feature_existence | Cursor AI coding assistant features | cursor.com |
| feature_existence | Cursor AI coding assistant features | (全网) |
| performance_quality | Cursor AI coding assistant review | reddit.com |
| performance_quality | Cursor AI coding assistant review | g2.com |
| performance_quality | Cursor AI coding assistant review | (全网) |
| pricing | Cursor AI coding assistant pricing plans | cursor.com |
| pricing | Cursor AI coding assistant pricing plans | (全网) |
| user_pain | Cursor AI coding assistant complaints problems | reddit.com |
| user_pain | Cursor AI coding assistant complaints problems | news.ycombinator.com |
| user_pain | Cursor AI coding assistant complaints problems | (全网) |

**Windsurf**:
| claim_type | 检索词 query | 锚定站点 site |
|---|---|---|
| feature_existence | Windsurf AI coding assistant features | codeium.com |
| feature_existence | Windsurf AI coding assistant features | (全网) |
| performance_quality | Windsurf AI coding assistant review | reddit.com |
| performance_quality | Windsurf AI coding assistant review | g2.com |
| performance_quality | Windsurf AI coding assistant review | (全网) |
| pricing | Windsurf AI coding assistant pricing plans | codeium.com |
| pricing | Windsurf AI coding assistant pricing plans | (全网) |
| user_pain | Windsurf AI coding assistant complaints problems | reddit.com |
| user_pain | Windsurf AI coding assistant complaints problems | news.ycombinator.com |
| user_pain | Windsurf AI coding assistant complaints problems | (全网) |

**GitHubCopilot**:
| claim_type | 检索词 query | 锚定站点 site |
|---|---|---|
| feature_existence | GitHubCopilot AI coding assistant features | github.com |
| feature_existence | GitHubCopilot AI coding assistant features | (全网) |
| performance_quality | GitHubCopilot AI coding assistant review | reddit.com |
| performance_quality | GitHubCopilot AI coding assistant review | g2.com |
| performance_quality | GitHubCopilot AI coding assistant review | (全网) |
| pricing | GitHubCopilot AI coding assistant pricing plans | github.com |
| pricing | GitHubCopilot AI coding assistant pricing plans | (全网) |
| user_pain | GitHubCopilot AI coding assistant complaints problems | reddit.com |
| user_pain | GitHubCopilot AI coding assistant complaints problems | news.ycombinator.com |
| user_pain | GitHubCopilot AI coding assistant complaints problems | (全网) |

## ④ 检索/采集结果

- **总证据**: 96 条 | **按来源**: {'official_page': 31, 'reddit': 16, 'web_search': 33, 'hn': 7, 'pricing_page': 9} | **按类型**: {'feature_existence': 24, 'performance_quality': 27, 'user_pain': 19, 'pricing': 26}
- **官网 check**: {"Cursor": {"official_evidence": 9, "had_urls": true, "url_source": "config", "ok": true}, "Windsurf": {"official_evidence": 13, "had_urls": true, "url_source": "config", "ok": true}, "GitHubCopilot": {"official_evidence": 9, "had_urls": true, "url_source": "config", "ok": true}}

各来源样本(每类 2 条):
  - `[feature_existence|official_page|Windsurf]` Unlimited access to SWE-1.6, the fastest coding model in the world.  ⟨https://codeium.com/windsurf⟩
  - `[performance_quality|official_page|Windsurf]` Fast Context finds the exact files and lines your agent needs—in milliseconds.  ⟨https://codeium.com/windsurf⟩
  - `[performance_quality|reddit|Windsurf]` Is Windsurf really that good or just hype ? : r/ChatGPTCoding - Reddit  ⟨https://www.reddit.com/r/ChatGPTCoding/comments/1gwnpqs/is_w⟩
  - `[performance_quality|reddit|Windsurf]` Windsurf is actually great. - Reddit  ⟨https://www.reddit.com/r/windsurf/comments/1q3cq8x/windsurf_⟩
  - `[performance_quality|web_search|Windsurf]` Windsurf Reviews 2026: Details, Pricing, & Features | G2  ⟨https://www.g2.com/products/exafunction-windsurf/reviews⟩
  - `[performance_quality|web_search|Windsurf]` Windsurf Pros and Cons | User Likes & Dislikes - G2  ⟨https://www.g2.com/products/exafunction-windsurf/reviews?qs=⟩
  - `[user_pain|hn|Windsurf]` Ask HN: Cursor or Windsurf? - Hacker News  ⟨https://news.ycombinator.com/item?id=43959710⟩
  - `[pricing|pricing_page|Windsurf]` Pricing - Windsurf  ⟨https://codeium.com/redirect/windsurf/learn-pricing⟩
  - `[pricing|pricing_page|Windsurf]` Blog | Windsurf  ⟨https://codeium.com/blog⟩
  - `[user_pain|hn|GitHubCopilot]` We've filed a lawsuit against GitHub Copilot | Hacker News  ⟨https://news.ycombinator.com/item?id=33457063⟩

## ⑤ 清洗处理结果(分桶 top-K + 近似去重,喂 LLM 的版本)

- 全量 96 条 → 清洗后 83 条
- 分桶(产品×类型)条数: {'Cursor/feature_existence': 5, 'Cursor/performance_quality': 8, 'Cursor/pricing': 8, 'Cursor/user_pain': 7, 'GitHubCopilot/feature_existence': 6, 'GitHubCopilot/performance_quality': 7, 'GitHubCopilot/pricing': 8, 'GitHubCopilot/user_pain': 8, 'Windsurf/feature_existence': 8, 'Windsurf/performance_quality': 6, 'Windsurf/pricing': 8, 'Windsurf/user_pain': 4}

## ⑥ 最终报告

- ④快照证据 96 条 → 补采后最终证据 209 条
- evidence_id 完整性: schema 引用 65 个, 幻觉(不在最终证据) 0 个 ✓
- 报告 14009 字,11 个模块 → `06_report.md`
- 功能矩阵维度: ['内联代码补全', '跨文件上下文理解', '多文件AI自动编辑', '内置AI代码聊天', '补全响应速度表现', '大代码库适配能力']
- 定价: Cursor 2档 | Windsurf 4档 | GitHubCopilot 4档