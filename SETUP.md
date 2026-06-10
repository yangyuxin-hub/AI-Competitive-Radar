# 🚀 环境配置指南

> 让其他人能够直接跑起来的完整步骤

---

## 📋 系统要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.10+ | 推荐 3.12 |
| Node.js | 18+ | 前端需要 |
| Git | 任意 | 克隆仓库 |

---

## 🔧 第一步：克隆项目

```bash
git clone https://github.com/yangyuxin-hub/AI-Competitive-Radar.git
cd AI-Competitive-Radar
```

---

## 🐍 第二步：Python 后端配置

### 2.1 创建虚拟环境

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# macOS/Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 2.2 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 2.3 安装 Playwright 浏览器（采集用）

```bash
playwright install chromium
```

### 2.4 配置环境变量

复制示例文件并填入你的 API key：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入以下内容：

```env
# ── LLM（必填）──────────────────────────────────────────────
# 方式 1：火山豆包（推荐）
ARK_API_KEY=你的火山API密钥
ARK_EP=ep-xxxxxxx  # 你的 endpoint id

# 方式 2：LLM 变量（优先级更高）
# LLM_API_KEY=你的key
# LLM_MODEL=ep-xxxxxxx
# LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# ── 搜索（可选，不填也能跑）────────────────────────────────
BRAVE_API_KEY=  # 可选，免费额度大
# TAVILY_API_KEY=  # 可选
```

**获取 API Key：**
- 火山豆包：https://console.volcengine.com/ark
- Brave 搜索：https://brave.com/search/api/（可选）

---

## 🌐 第三步：前端配置

### 3.1 安装前端依赖

```bash
cd web
npm install
```

### 3.2 配置前端环境变量（可选）

如果后端不在默认的 `http://127.0.0.1:8000`，创建 `web/.env.local`：

```env
NEXT_PUBLIC_API_BASE=http://你的后端地址:8000
```

---

## ▶️ 第四步：启动项目

### 方式 A：CLI 模式（命令行直接跑）

```powershell
# 在项目根目录
.\.venv\Scripts\Activate.ps1  # 激活虚拟环境

# Mock 模式（无需 API key，用本地数据测试）
$env:ANALYZER_MOCK="1"
python -m src.graph

# 真实 LLM 模式
$env:LLM_API_KEY="你的key"
$env:LLM_MODEL="ep-xxx"
python -m src.graph
```

产物在 `out/` 目录下。

### 方式 B：Web 工作台模式（推荐）

需要开**两个终端窗口**：

**终端 1 — 启动后端 API：**

```powershell
cd D:\openclaw\ai实习\AI-Competitive-Radar
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 就成功了。

**终端 2 — 启动前端：**

```powershell
cd D:\openclaw\ai实习\AI-Competitive-Radar\web
npm run dev
```

看到 `ready - started server on 0.0.0.0:3000` 就成功了。

**打开浏览器：** http://localhost:3000

---

## 🧪 第五步：验证安装

### 快速测试（Mock 模式）

```powershell
$env:ANALYZER_MOCK="1"
python -m src.graph
```

应该看到类似输出：
```
out/ai_coding/report.md        # 生成的报告
out/ai_coding/schema_draft.json
out/ai_coding/quality_report.json
```

### 运行测试套件

```bash
pytest -q
```

应该看到 `201 passed`。

---

## ❓ 常见问题

### 1. `ModuleNotFoundError: No module named 'xxx'`

```bash
# 确保虚拟环境已激活
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Playwright 安装失败

```bash
# Windows 需要安装依赖
playwright install --with-deps chromium

# 如果还是失败，手动下载 Chromium
# 访问 https://playwright.dev/python/docs/browsers#chromium
```

### 3. `LLM_API_KEY 未设置` 错误

```bash
# 方式 1：设置环境变量
$env:LLM_API_KEY="你的key"

# 方式 2：使用 Mock 模式
$env:ANALYZER_MOCK="1"
```

### 4. 前端 CORS 错误

确保后端设置了 `API_CORS_ORIGINS`：

```powershell
$env:API_CORS_ORIGINS="http://localhost:3000"
```

### 5. 端口被占用

```powershell
# 查看占用端口的进程
netstat -ano | findstr :8000
netstat -ano | findstr :3000

# 杀掉进程（替换 PID）
taskkill /PID <PID> /F
```

### 6. 搜索无结果

```bash
# 检查搜索是否可用
python -c "from src.search import search_available; print(search_available())"
```

如果返回 `False`，安装 DuckDuckGo 兜底：

```bash
pip install ddgs
```

---

## 🌍 部署到生产环境

### Render（后端）+ Vercel（前端）

详见 [DEPLOY.md](DEPLOY.md)

### Docker（可选）

```bash
# 后端
docker build -t radar-api -f Dockerfile.api .
docker run -p 8000:8000 -e LLM_API_KEY=xxx radar-api

# 前端
cd web
docker build -t radar-web .
docker run -p 3000:3000 -e NEXT_PUBLIC_API_BASE=http://api地址:8000 radar-web
```

---

## 📁 项目结构

```
AI-Competitive-Radar/
├── src/                    # Python 后端核心
│   ├── graph.py           # LangGraph 编排入口
│   ├── collector.py       # 采集 Agent
│   ├── analyzer.py        # 分析 Agent
│   ├── writer.py          # 撰写 Agent
│   └── reviewer.py        # 质检 Agent
├── api/
│   └── main.py            # FastAPI 后端
├── web/                   # Next.js 前端
│   ├── app/               # 页面
│   ├── components/        # 组件
│   └── lib/               # 工具函数
├── config/                # 配置文件
├── prompts/               # LLM Prompt
├── data/                  # Mock 数据 + 缓存
├── tests/                 # 测试用例
├── requirements.txt       # Python 依赖
└── .env.example           # 环境变量示例
```

---

## 🆘 获取帮助

1. 查看 [README.md](README.md) 了解项目概览
2. 查看 [docs/design-v2.2.md](docs/design-v2.2.md) 了解架构设计
3. 提 Issue：https://github.com/yangyuxin-hub/AI-Competitive-Radar/issues

---

## ✅ 快速检查清单

- [ ] Python 3.10+ 已安装
- [ ] Node.js 18+ 已安装
- [ ] 虚拟环境已创建并激活
- [ ] Python 依赖已安装（`pip install -r requirements.txt`）
- [ ] Playwright 浏览器已安装（`playwright install chromium`）
- [ ] `.env` 文件已配置 API key
- [ ] 前端依赖已安装（`cd web && npm install`）
- [ ] 后端能启动（`uvicorn api.main:app --port 8000`）
- [ ] 前端能启动（`cd web && npm run dev`）
- [ ] 浏览器能访问 http://localhost:3000
