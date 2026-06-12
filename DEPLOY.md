# 部署指南 — 让别人在线访问

方案：**后端 Render + 前端 Vercel**（都有免费档，无需买服务器）。

```
用户浏览器  →  Vercel(前端 Next.js)  →  Render(后端 FastAPI/SSE)  →  LLM / Web 搜索
```

前置：代码已推到 **GitHub** 仓库；准备好 `LLM_API_KEY`（可选 `BRAVE_API_KEY`）。

---

## 第 1 步：部署后端到 Render

1. 注册 https://render.com （用 GitHub 账号登录最方便）。
2. Dashboard → **New +** → **Blueprint** → 选中本仓库 → Render 会自动读取根目录的 `render.yaml`。
3. 点 **Apply**，进入服务后到 **Environment** 标签，填入标了 `sync:false` 的密钥：
   - `LLM_API_KEY` = 你的火山豆包 key（**必填**）
   - `BRAVE_API_KEY` = Brave 搜索 key（推荐；不填则 Render 默认禁用 DuckDuckGo，避免免费搜索在云端超时拖慢采集）
   - `API_CORS_ORIGINS` 先**留空**，等第 2 步拿到前端域名再回填。
4. 等首次构建完成（约 3-5 分钟），拿到后端地址，形如
   `https://ai-competitive-radar-api.onrender.com`
5. 自测：浏览器打开 `<后端地址>/api/reports`，能返回 JSON（哪怕是空列表）就算活了。

> 免费档注意：空闲约 15 分钟会休眠，下次访问要冷启动几十秒。答辩前先访问一次「预热」。

---

## 第 2 步：部署前端到 Vercel

1. 注册 https://vercel.com （GitHub 登录）。
2. **Add New → Project** → 选本仓库 → **Root Directory 改成 `web`**（重要，前端在子目录）。
3. **Environment Variables** 加一条：
   - `NEXT_PUBLIC_API_BASE` = 第 1 步的后端地址（如 `https://ai-competitive-radar-api.onrender.com`，**末尾不要带斜杠**）
4. **Deploy**，拿到前端地址，形如 `https://your-app.vercel.app`。

---

## 第 3 步：回填 CORS，打通跨域

1. 回 Render → 后端服务 → Environment → 把 `API_CORS_ORIGINS` 设为第 2 步的前端域名：
   ```
   https://your-app.vercel.app
   ```
   （多个域名用逗号分隔，比如再加上 Vercel 给的 preview 域名）
2. 保存后 Render 会自动重启。完成。

把前端地址 `https://your-app.vercel.app` 发给别人，即可访问使用。

---

## 常见问题

| 现象 | 原因 / 处理 |
|------|------|
| 前端能开但点「分析」无响应/报 CORS | `API_CORS_ORIGINS` 没填或填错前端域名；带不带 `https://`、末尾斜杠都要对上 |
| 第一次请求很久没动静 | Render 免费档冷启动；先打开 `<后端>/api/reports` 预热 |
| 分析跑一半断了 | 多为 LLM key 失效/额度耗尽，看 Render 服务的 **Logs** 标签 |
| 报告/缓存重启后丢了 | 免费档文件系统是临时的，属正常；Demo 无需持久化，要持久化需在 Render 加 Disk 卷 |
| `Executable doesn't exist ... ms-playwright ... chromium` | 后端没有按最新 `render.yaml` 重新构建，或服务不是 Blueprint 创建的；Build Command 应包含 `python -m playwright install --with-deps chromium`，并设置 `PLAYWRIGHT_BROWSERS_PATH=0` 后 Clear build cache 再 redeploy |
| 日志里 DuckDuckGo/DDG 一直 `ConnectTimeout` | Render 出站网络访问免费搜索不稳定；保持 `DISABLE_DDG_SEARCH=1`，并优先配置 `BRAVE_API_KEY` |
| 国内访问慢 | Render/Vercel 是海外节点；要国内稳定走「云服务器+Docker」方案（另说） |

## 本地变量对照（部署时设到平台环境变量里）

- 后端必填：`LLM_API_KEY`
- 后端可选：`LLM_BASE_URL`、`LLM_MODEL`、`BRAVE_API_KEY`、`TAVILY_API_KEY`、`API_CORS_ORIGINS`、`DISABLE_DDG_SEARCH`
- 前端必填：`NEXT_PUBLIC_API_BASE`
