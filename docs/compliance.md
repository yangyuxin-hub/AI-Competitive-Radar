# 合规说明

> 评分维度 5「合规、材料」基础文档
> 涵盖:数据来源 / robots.txt / UA 标识 / 频率限制 / 数据脱敏 / 隐私

---

## 一、数据采集合规

### 1.1 抓取的域名与 robots.txt 状态

| 域名 | 用途 | robots.txt | 抓取策略 |
|------|------|-----------|---------|
| `cursor.com` | OfficialPageAdapter | `/features`, `/pricing` allow | ✅ 单次摘要 |
| `codeium.com` | OfficialPageAdapter | `/windsurf`, `/pricing` allow | ✅ 单次摘要 |
| `github.com` | OfficialPageAdapter | `/features/copilot/*` allow | ✅ 单次摘要 |
| `notion.so` | OfficialPageAdapter | `/product`, `/pricing` allow | ✅ 单次摘要 |
| `asana.com` | OfficialPageAdapter | `/product`, `/pricing` allow | ✅ 单次摘要 |
| `linear.app` | OfficialPageAdapter | `/features`, `/pricing` allow | ✅ 单次摘要 |
| `reddit.com` | (未实装真实 Adapter) | API 优先,Web 抓取受限 | ⚠️ 生产应接 PRAW |
| `news.ycombinator.com` | (未实装真实 Adapter) | API allow,Web 抓取慢 | ⚠️ 生产应用 Algolia API |

> Demo 当前默认走 `MockAdapter`(`data/sample_sources.json`)。
> 真实抓取需 `ENABLE_LIVE_FETCH=1` 显式启用,且**只启用 OfficialPageAdapter**(vendor 自家页),不抓 Reddit/HN Web 端。

### 1.2 User-Agent 标识

所有 httpx 请求带 UA:

```
User-Agent: AICompetitiveRadar/0.1 (+https://github.com/yangyuxin-hub/AI-Competitive-Radar; academic)
```

学术 + GitHub 可追溯,符合"识别身份"合规要求。

### 1.3 频率限制

- httpx `connect=5s` / `read=15s` 双层 timeout,避免长连接占用对方资源
- Collector 用 `ThreadPoolExecutor(max_workers=6)` 并发上限,**不会同时打 60+ 请求**
- CacheAdapter 优先 — 同一 URL 7-90 天 TTL 内不重抓(按 claim_type)

### 1.4 抓取量

单次跑 demo 最多 6 次 GET(3 产品 × 2 URL,实际通常 ≤ 3 因为 cache 命中)。**远低于任何 fair use 上限**。

---

## 二、数据使用合规

### 2.1 公开评论引用

`sample_sources.json` 与 `sample_sources_pm.json` 中的 Reddit/HN 评论:

- ✅ 全部来自**公开发布**的论坛内容(无私密 DM、无付费内容)
- ✅ 引用以**摘要形式**呈现,不完整重发原帖
- ✅ 每条带 `source_url` 可追溯到原文
- ✅ 仅在分析报告里以"用户反馈"角度引用,不**冒充原作者**

### 2.2 不做的事

- ❌ 不抓 Reddit/HN 用户名(我们的 evidence 字段里没有 `author` 字段,即使抓回来也丢弃)
- ❌ 不存储 cookies / session
- ❌ 不绕过付费墙、登录墙
- ❌ 不批量爬整站,只针对配置文件里列出的 URL

### 2.3 用户访谈数据(预案,demo 未启用)

若未来接入用户访谈数据(产品经理实际场景):

1. **脱敏**:姓名 → 角色(如"某金融行业 PM"),公司 → 行业
2. **明示授权**:访谈前签同意书,告知数据用途
3. **可撤回**:被访人可要求删除证据,evidence_id 哈希可定位

---

## 三、模型与 API 合规

### 3.1 豆包 API

- API key 通过环境变量 `ARK_API_KEY` 注入,**绝不进入代码或 git 历史**
  - `.gitignore` 已配 `.env` / `.env.*` / `secrets/`
  - 我们做过一次提交前 grep 确认无 key 泄漏
- 仅用于**本课题项目**,不挪作他用
- token 用量本地日志:`[llm] facts: 81.9s · prompt=10165 completion=7420`,无加密信息,可审计

### 3.2 输出报告标注

每份报告 header 自动标注:

```
> 报告 ID: CR-20260524-001 · 数据截止: 2026-05-24
```

**不暗示"实时市场态势"**,只承诺数据截止日之前的快照。

---

## 四、隐私

### 4.1 不收集用户输入

Demo 在本地运行,用户输入(target / competitors / focus)仅本地处理 + 发送给豆包 API(火山引擎)。

- 火山引擎 ARK 隐私政策:https://www.volcengine.com/docs/82379
- 我们不**额外**存储用户输入到任何第三方

### 4.2 本地产物

- `out/<domain>/report.md` `schema_draft.json` `quality_report.json`:本地文件,git 已 ignore
- `data/cache/*.json`:本地缓存,git 已 ignore
- `logs/*.jsonl` `logs/llm_raw_*.txt`:本地日志,git 已 ignore

用户卸载 = 删本地目录,**无残留**。

---

## 五、第三方依赖授权

| 包 | License | 用途 |
|---|---------|------|
| langgraph | MIT | 多 Agent 编排 |
| openai (SDK) | Apache-2.0 | ARK API 客户端 |
| httpx | BSD-3-Clause | HTTP 抓取 |
| beautifulsoup4 | MIT | HTML 解析 |
| pyyaml | MIT | 配置文件 |
| python-dotenv | BSD-3-Clause | 环境变量加载 |
| streamlit | Apache-2.0 | 前端 UI |

全部允许商用与修改。

---

## 六、不合规风险自查清单

- [x] API key 不在 git
- [x] UA 标识带项目 URL
- [x] 抓取目标确认在 robots.txt allow
- [x] 引用评论标 URL + 发布日期
- [x] 报告标数据截止
- [x] 本地数据 git 已 ignore
- [x] 第三方 license 兼容
- [ ] **赛事后:重新申请/轮换 ARK API key**(因 demo 期间多次出现在调试上下文)
- [ ] **生产化时:接入官方 Reddit API(PRAW)替换 Web 抓取**
