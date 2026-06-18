# 云端部署限制与性能/成本优化清单

> 整理日期：2026-06-12 · 适用版本：v3（直线控制流）· 部署形态：Render 免费档(512MB) + 本地满血
> 用途：答辩素材（业务价值/技术深度维度）+ 部署运维参考。所有数字均为实测，非估算。

---

## 一、云端限制与应对（Render 免费档）

### 1.1 IP 信誉问题 —— 免费搜索/反爬站点拒连

**现象**：`ddgs`（DuckDuckGo/Startpage 爬虫式免费搜索）在 Render 上 TCP 握手即被拒
（`Connection refused (os error 111)`，连 HTTP 请求都没发出）。

**根因**：Render 出口是数据中心 IP 段且多租户共享，免费搜索站/反爬严的站点（Reddit、
部分官网）对这类 IP 整段拉黑。本地宽带是住宅 IP，信誉高，同样的代码畅通无阻。

**应对**（配置跟环境走，代码全局一份）：

| 措施 | 落点 | 效果 |
|------|------|------|
| 云端禁用 ddg：`DISABLE_DDG_SEARCH=1` | `render.yaml` + `src/search.py` | 不再每次缺口补采白耗 3 次重试+退避（十几秒/次） |
| 搜索主力换 API key 鉴权的 Brave | `BRAVE_API_KEY`（免费 2000 次/月） | key 鉴权不看 IP 脸色，数据中心 IP 照常可用 |
| Tavily 作第二层兜底 | `TAVILY_API_KEY`（免费 1000 credit/月，超限返 432 快速失败） | 月度重置自动复活，失败成本一个 432，远低于 ddg 重试 |
| 反爬站官网证据缺失 → 搜索补位 | EvidenceService 缺口回补 | 二手证据如实降档 `source_reliability`，报告标注来源等级 |

**一句话**：本地靠 IP 信誉白嫖，云端必须靠 API key 走正门。

### 1.2 内存限制 —— 512MB 跑不起 Chromium（实测 OOM）

**现象**：Playwright 浏览器装好后首次真实运行即 OOM 被杀（`Ran out of memory (used over 512MB)`）。

**实测数据**（渲染真实触发页 windsurf.com/pricing，diff 法统计进程树 RSS）：

| 场景 | Chromium 进程数 | RSS 合计 |
|------|---------------|---------|
| 1 个 headless 实例 | 4（主+渲染器+网络+GPU） | **261 MB** |
| 2 个并发实例 | 8 | **634 MB** |

**OOM 数学**：Python 应用本体 ≈150-200MB + 1 个 Chromium 261MB ≈ 460MB（贴天花板）；
collector 线程池 6 并发下 ≥2 个 URL 同时命中渲染条件（Windsurf 0 证据 + 定价页无价格信号
是两个稳定触发源）→ 800MB+，必死。**不是调参问题，是地皮问题。**

**应对**：
- `DISABLE_PLAYWRIGHT_RENDER=1`（云端禁渲染，采集器优雅跳过，见 `collector_adapters._skip_playwright_reason`）
- buildCommand 不装 chromium（构建更快、slug 更小、从根上杜绝拉起）
- 浏览器渲染本来就只是**抢救分支**：静态 httpx 抓取是主路径，5 个产品里 4 个的官网/定价
  一手证据靠静态抓取就拿到了；禁渲染后与历史 244.9s/passed/score 73 的运行形态一致
- 升级 Starter(2GB, $7/月) 即可恢复：render.yaml 注释里有两行恢复说明

**踩坑记录**（防回归）：
- `playwright install --with-deps` 在 Render 必失败（需 root 跑 apt-get），3c08f2b 修过又被
  改回去过一次，现 render.yaml/DEPLOY.md 已写死警告注释
- "之前云端稳定"恰因 chromium 装不上、渲染从未真正执行——修好安装反而暴露内存天花板

### 1.3 其他免费档限制

| 限制 | 影响 | 应对 |
|------|------|------|
| 临时文件系统（重启即清） | cache 三层降级的中间层跨运行失效；source_ledger 学习不积累；报告档案丢失 | live+mock 仍在；Demo 单次跑通足够；持久化需加 Disk 卷 |
| 空闲 15 分钟休眠 | 首请求冷启动数十秒 | 演示前先打开 `/api/reports` 预热 |

### 1.4 云端 vs 本地形态对照

两边**同一份代码**，仅 env 裁剪行为；分析/质量层（LLM 两步、R0-R10、guard、溯源 chip）零差异。

| 能力 | 本地 | Render 免费档 |
|------|------|--------------|
| 官网/定价静态直爬（主路径） | ✅ | ✅ |
| JS 页面 Playwright 抢救 | ✅ | ❌ 禁（内存） |
| 搜索链 | brave → tavily → ddg | brave → tavily（ddg 禁，IP） |
| cache 跨运行复用 | ✅ | ❌（临时文件系统） |
| 适用场景 | 答辩正式报告（满血） | 流程演示/实时性展示 |

---

## 二、性能优化 —— 并发执行

| 优化 | 位置 | 说明 |
|------|------|------|
| Collector 多产品×多适配器并行 | `collector.py`（ThreadPoolExecutor=6）；适配器内 `max_workers=len(applicable)` | 5 产品官网/搜索/缓存抓取并行 |
| Analyzer facts 三 section 并行 | `analyzer.py`（feature/pricing/persona 各自独立 LLM 调用） | 互不依赖的事实段并行出 |
| feature 拆 spine→fill 两段，fill 按产品并行 | `analyzer.py` | 实测 log：spine 3.6s + 5 产品 fill 各 ~7s **并行重叠**（墙钟≈8s，串行需 ~38s） |
| derivations 各 section 并行 | `analyzer.py`（swot/recommendations） | 同上模式 |
| claim 分类 LLM 批并行 | `collector_common.py`（`CLAIM_LLM_WORKERS=6`） | 证据分类批次并行 |
| 跨线程上下文隔离 | `progress.py` `CtxThreadPoolExecutor`（copy_context） | 并发不串台：progress/token 回调按 run 隔离（修过跨 run 串台 bug） |

## 三、成本/时延优化 —— 提示词与 LLM 调用

> v3 M4b 原则：**最优配置即代码默认**，env 只用于回退。以下全部默认开启。

| 优化 | 默认 | 实测收益 |
|------|------|---------|
| thinking 分层：机械任务关思维链（`LLM_THINKING=disabled`） | 开 | Doubao 隐藏思维链占 43-91% completion tokens；机械分类 24.8s→1.4s |
| 深度推理保留思维链（`LLM_THINKING_DEEP=enabled`，swot/rec/R6/judge/intake） | 开 | 质量不掉；端到端整体 **-34%** 时延 |
| payload 瘦身（`ANALYZER_PROMPT_SLIM=1`：按 section 过滤证据类型，不共享全量池） | 开 | derivations prompt **-23~38%**；recommendations 74s→40s；judge 盲评零掉分 |
| 证据压缩与截断（`_compact_evidence` + `ANALYZER_DERIV_MAX_PER_TYPE=5` + snippet 140 字） | 开 | 防证据过多 LLM 超时 |
| 池内回捞（`ANALYZER_POOL_RECALL=1`：缺口先回捞被 top-K 挡住的池内证据再外搜） | 开 | 省一次外搜+采集的网络/token 成本 |
| Analyzer 两步拆分（facts→derivations，各带 quick_validate） | 架构内置 | 单 prompt 体积减半量级；错误早拦截不浪费下游调用 |
| LLM 客户端：`trust_env=False` 关系统代理 | 内置 | 每次调用省 ~10s 代理绕行 |
| timeout 200s / `max_retries=1` | 内置 | 失败快速降级，不挂死流水线 |
| 搜索结果磁盘缓存（TTL 72h） | 开 | 重复查询零 API 消耗（本地有效；云端单次运行内有效） |
| LLM 日志 system_prompt 去重外存（`llm_prompts/`） | 内置 | `llm_calls.jsonl` 体积可控，按 run/stage 归因成本 |

## 四、基线数字（答辩引用口径）

- 端到端 live 全链路：**244.9s / passed / 200 chips / judge score 73**（54 run 统计中打回仅触发 1 次 → 据此把控制流改直线，删打回机器）
- thinking 分层端到端提速：**-34%**
- 单 headless Chromium 渲染实测：**261MB**（4 进程）；2 并发 **634MB** → 512MB 免费档禁渲染的依据
- Brave 免费额度 2000 次/月，Tavily 1000 credit/月（432=超限快速失败）
