# 智能体爬虫

基于 LLM 智能体 + Playwright 的网页数据采集系统。用户只需输入目标描述、起始网址与字段 Schema，智能体即可自主完成「观察网页 → 决策动作 → 提取数据 → 校验 → 输出」，并支持 JSON / CSV / Excel / Markdown / Word 多格式导出。支持定时爬取（固定间隔 / 每日定点 + 启停开关）、智能可视化（由智能体设计图表）以及全流程耗时统计。内置失败保护：单个任务超过 `MAX_RUN_SECONDS`（默认 600 秒）自动终止，LLM 连续失败自动收尾，服务重启后自动把遗留的“运行中”任务标记为失败。

## 技术栈

- 前端：Vue 3 + Vite（vue-router）
- 后端：FastAPI
- 核心引擎：`agent_crawler`（纯 Python 包，前后端复用）
- 智能体：LLM（OpenAI 兼容接口，可接 DeepSeek / 千问 / Ollama）+ ReAct 决策循环
- 浏览器：Playwright（Chromium）
- 校验：Pydantic v2 风格的动态 Schema 校验
- 存储：SQLite

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

## 配置

复制 `.env.example` 为 `.env`，填入 LLM 配置：

```bash
cp .env.example .env
```

关键项：

```ini
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-你的密钥
LLM_MODEL=deepseek-chat
```

使用本地千问（Ollama）时：

```ini
LLM_PROVIDER=ollama
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen3-8b-16k
```

## 启动

### 1. 构建前端（首次或前端代码变更后）

```bash
cd frontend
npm install
npm run build
```

### 2. 启动服务（单进程，推荐）

```bash
uvicorn backend:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000/` 使用页面（顶部菜单含首页 / 定时任务 / 可视化 / 历史任务），
`http://127.0.0.1:8000/docs` 查看接口文档。

定时任务由后端后台循环驱动（每 10 秒检查一次），服务停止期间错过的时间点会直接跳过，不会补跑。

### 开发模式（前端热更新）

```bash
uvicorn backend:app --reload          # 终端 1
cd frontend && npm run dev            # 终端 2，访问 http://127.0.0.1:5170
```

Vite 已配置将 `/api`、`/health` 代理到后端。

## 测试

```bash
python -m unittest discover -s tests
```

## 目录结构

```
backend.py         # FastAPI 后端入口（并提供前端静态文件）
frontend/          # Vue 3 前端（页面、路由、API 封装）
agent_crawler/     # 核心引擎（纯 Python 包）
  config.py        # 配置加载
  llm.py           # LLM 客户端封装
  agent_prompt.py  # 智能体 System Prompt
  browser.py       # Playwright 工具封装
  agent.py         # ReAct 决策循环（含各阶段耗时埋点）
  visualizer.py    # 智能体生成可视化图表方案
  schema.py        # 动态 Schema 校验
  storage.py       # SQLite 持久化（任务 + 定时任务）
  exporter.py      # 多格式导出
  models.py        # 任务 / 定时任务数据模型
tests/             # 最小单测
```