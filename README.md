# 智能体爬虫

基于 LLM 智能体 + Playwright 的网页数据采集系统。用户只需输入目标描述、起始网址与字段 Schema，智能体即可自主完成「观察网页 → 决策动作 → 提取数据 → 校验 → 输出」，并支持 JSON / CSV / Excel / Markdown / Word 多格式导出。支持定时爬取（固定间隔 / 每日定点 + 启停开关）、智能可视化（由智能体设计图表）以及全流程耗时统计。内置失败保护：单个任务超过 `MAX_RUN_SECONDS`（默认 600 秒）自动终止，LLM 连续失败自动收尾，服务重启后自动把遗留的“运行中”任务标记为失败。

界面包含 9 个页面：首页（项目介绍 + 轮播）、智能采集、定时任务、可视化、文档库（按采集批次分组 + 自建子库）、历史任务、系统日志、帮助（常见问题）、设置。设置页可切换浅色 / 深色 / 跟随系统主题，并可视化配置模型（本地 Ollama 或任意 OpenAI 兼容接口，只需填 Base URL 与 API Key）。

## 技术栈

- 前端：Vue 3 + Vite（vue-router）
- 后端：FastAPI
- 核心引擎：`agent_crawler`（纯 Python 包，前后端复用）
- 智能体：LLM（OpenAI 兼容接口，可接 DeepSeek / 千问 / Kimi / 智谱 GLM / Ollama 本地模型）+ ReAct 决策循环
- 浏览器：Playwright（Chromium）
- 校验：Pydantic v2 风格的动态 Schema 校验
- 存储：SQLite（任务、定时任务、文档库与子库）
- 主题：CSS 变量 + `data-theme`，支持浅色 / 深色 / 跟随系统

## 功能页面

| 页面 | 路径 | 说明 |
| --- | --- | --- |
| 首页 | `/` | 项目介绍与能力轮播，引导进入采集 |
| 智能采集 | `/crawl` | 描述目标 + 起始网址 + 字段 Schema，实时展示智能体决策链路 |
| 定时任务 | `/schedules` | 固定间隔 / 每日定点，支持启停与立即运行 |
| 可视化 | `/visualize` | 由智能体生成图表方案并渲染 |
| 文档库 | `/documents` | 按单次采集批次分组，可自建子库并整批移动 |
| 历史任务 | `/history` | 任务详情、结果查看与重复采集 |
| 系统日志 | `/logs` | 运行期日志按级别筛选 |
| 帮助 | `/help` | 常见问题（登录墙、部分完成、字段缺失、模型失败等） |
| 设置 | `/settings` | 外观主题与模型配置 |


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

也可以不改 `.env`，直接在页面「设置 → 模型」里配置：选择「本地 Ollama」或「OpenAI 兼容接口」，用预设快速填入 OpenAI / DeepSeek / 通义千问 / Kimi / 智谱 GLM，或自定义 Base URL；只需填地址、API Key 与模型名，可点「读取模型」拉取本地模型列表、「测试连接」验证，再「保存并启用」，保存后立即生效，无需重启。

页面保存的配置写入 `data/settings.json`，优先级高于 `.env`；该文件含 API Key，已被 `.gitignore` 排除，请勿提交。该文件缺失或损坏时才回退到 `.env`。主题偏好（`ui.theme`）也保存在这里。

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

访问 `http://127.0.0.1:8000/` 使用页面（顶部菜单含首页 / 智能采集 / 定时任务 / 可视化 / 文档库 / 历史任务 / 系统日志 / 帮助 / 设置），
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
backend.py         # FastAPI 后端入口（任务/定时/文档库/日志/设置接口 + 前端静态托管）
frontend/
  index.html       # 首屏前恢复主题，避免刷新闪白
  src/
    views/         # 9 个页面组件（首页 / 智能采集 / 定时任务 / 可视化 / 文档库 / 历史 / 日志 / 帮助 / 设置）
    api.js         # 后端接口封装
    router.js      # 路由与页面标题
    theme.js       # 主题偏好（localStorage + 服务端同步）
    style.css      # 全局样式与浅色 / 深色变量
agent_crawler/     # 核心引擎（纯 Python 包）
  config.py        # 配置加载
  llm.py           # LLM 客户端封装
  agent_prompt.py  # 智能体 System Prompt
  browser.py       # Playwright 工具封装
  agent.py         # ReAct 决策循环（含各阶段耗时埋点）
  visualizer.py    # 智能体生成可视化图表方案
  schema.py        # 动态 Schema 校验
  storage.py       # SQLite 持久化（任务 / 定时任务 / 文档库与子库）
  exporter.py      # 多格式导出
  models.py        # 任务 / 定时任务数据模型
data/              # 运行时数据：crawler.db、settings.json（均已 gitignore）
tests/             # 最小单测
```