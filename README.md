# MagiCrawler · 智能体爬虫

输入一句目标描述、一个起始网址和想要的字段，剩下的交给智能体：它会在浏览器里自己观察页面、决定下一步、翻页点击、按 Schema 提取数据，最终输出结构化结果，并支持一键导出。

项目由 FastAPI + SQLite 后端与 Vue 3 前端组成，覆盖从「采集 → 校验 → 归档 → 定时 → 可视化」的完整链路。

## 核心特性

- **自然语言定义任务**：只需描述目标（如「采集这 5 个视频的标题、作者、点赞数」）、起始网址和字段 Schema，无需编写选择器或爬虫脚本。
- **接口优先 + 配方缓存**：首次由智能体跑通后，自动记录页面背后的 JSON 接口、列表路径、字段映射与翻页参数；同站点再次采集直接走接口，零模型调用、秒级完成，接口失效时自动回退智能体。
- **意图识别与确定性筛选**：把「价格超过 100 元」这类描述解析成结构化条件，由代码执行过滤并统计丢弃条数，不依赖模型「记住」约束。
- **网站记忆**：需要登录的站点扫码登录一次，后续任务复用该站点的 Cookie 与本地存储（独立浏览器 profile），无需重复登录。
- **ReAct 决策链路可视化**：每一步「观察 → 决策 → 动作」实时推送到页面，可看到智能体在做什么、为什么这样做。
- **结构化输出与校验**：按字段 Schema 校验结果，缺失字段置为 `null` 并附 `_note`，不编造数据。
- **多格式导出**：JSON / CSV / Excel / Markdown / Word。
- **定时采集**：支持固定间隔与每日定点，随时启停、立即运行，错过的时间点不补跑。
- **智能可视化**：由模型设计图表方案（趋势、分布、对比），前端用 ECharts 渲染。
- **文档库**：按「单次采集批次」自动分组，可自建子库并整批移动、重命名、删除。
- **历史任务**：保留目标描述、起始网址与字段，可一键重复采集。
- **设置中心**：浅色 / 深色 / 跟随系统主题；模型可视化配置（本地 Ollama 或任意 OpenAI 兼容接口），保存即刻生效。
- **稳定性保护**：单任务超时终止、LLM 连续失败自动收尾、服务重启后自动重置遗留「运行中」任务。

## 页面一览

| 页面 | 路径 | 说明 |
| --- | --- | --- |
| 首页 | `/` | 项目介绍与能力轮播 |
| 智能采集 | `/crawl` | 描述目标 + 起始网址 + 字段 Schema，实时查看决策链路与结果 |
| 定时任务 | `/schedules` | 固定间隔 / 每日定点，启停与立即运行 |
| 可视化 | `/visualize` | 智能体生成图表方案并渲染 |
| 文档库 | `/documents` | 按采集批次分组，自建子库、整批迁移 |
| 网站记忆 | `/sites` | 扫码登录一次，保存站点登录态供任务复用 |
| 历史任务 | `/history` | 任务详情、耗时明细、重复采集 |
| 系统日志 | `/logs` | 运行期日志按级别筛选（内存环形缓冲） |
| 帮助 | `/help` | 常见问题与排查建议 |
| 设置 | `/settings` | 主题与模型配置 |

## 工作原理

```text
目标描述 / 起始网址 / 字段 Schema
              │
              ▼
   意图识别（代码解析筛选条件与目标条数）
              │
              ├── 命中站点配方 ──▶ 接口直采（字段映射 / 翻页 / 去重）
              │                        │
              │                        └─ 失败或条数不足 ─┐
              ▼                                            │
      ┌───────────────┐   observe   ┌──────────────┐     │
      │  Playwright    │ ◀────────── │  ReAct 智能体 │ ◀───┘
      └───────────────┘   navigate  └──────────────┘
              │  click / scroll / extract / finish
              ▼
   筛选过滤 → Schema 校验 → 任务结果入库 → 归档 / 导出 / 可视化
```

首次跑通后，系统会从页面发出的 XHR/Fetch 响应中识别列表数据，交给模型生成一条严格校验过的「采集配方」并入库；下次同域名任务先按配方直采，命中失败才回退上面的智能体流程。

智能体只允许调用以下 6 个工具，既保证可控，也避免「自由发挥」：

| 工具 | 作用 |
| --- | --- |
| `navigate(url)` | 跳转页面，返回状态码与可读文本 |
| `fetch()` | 获取当前页可读文本或精简 HTML（去脚本/样式/噪声） |
| `click(selector)` | 点击翻页、展开详情、切换分类 |
| `scroll()` | 向下滚动触发懒加载 |
| `extract(schema)` | 按目标字段提取候选数据 |
| `finish(data)` | 提交最终结果并结束任务 |

结束条件：已收集到目标数量、翻完所有相关页面，或连续两次工具调用没有新数据。

## 快速开始

环境要求：Python 3.10+、Node.js 18+（仅前端开发或重新构建时需要）。

### 一键启动（推荐）

依赖装好后，在项目根目录执行：

```bash
python start.py
```

脚本会自动完成：检测并启动本地 Ollama（模型配置为云端接口时跳过）→ 启动后端 8000 → 启动前端 5170；按 `Ctrl+C` 同时关闭全部服务。

### 手动启动

```bash
git clone https://github.com/Huise1102/MagiCrawler.git
cd MagiCrawler

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
playwright install chromium     # 也可设置 BROWSER_CHANNEL=msedge/chrome 复用系统浏览器

copy .env.example .env          # Windows：copy；macOS/Linux：cp，然后填入模型配置

cd frontend
npm install
npm run build                   # 产物 frontend/dist 由后端托管（dist 不入库，重新克隆后需构建）
cd ..

uvicorn backend:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/ 即可使用，接口文档在 http://127.0.0.1:8000/docs。

### 前端开发模式

```bash
cd frontend
npm install
npm run dev      # http://127.0.0.1:5170，/api 与 /health 已代理到 8000
npm run build    # 产物输出到 frontend/dist，由后端自动托管
```

## 模型配置

两种方式，任选其一：

1. **页面配置（推荐）**：进入「设置 → 模型」，选择「本地 Ollama」或「OpenAI 兼容接口」，可用预设一键填入，填写 Base URL、API Key 与模型名；支持「读取模型」拉取本地模型列表、「测试连接」验证，点「保存并启用」即时生效，无需重启。
2. **`.env`**：按 `.env.example` 填写，重启后端生效。

| 服务 | Base URL | 示例模型 |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 通义千问（DashScope） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen3:8b` |

页面保存的配置写入 `data/settings.json`，优先级高于 `.env`；该文件包含 API Key，已被 `.gitignore` 排除。文件缺失或损坏时才回退到 `.env`。主题偏好（`ui.theme`）也存在这里。

本地 Ollama 示例：

```bash
ollama pull qwen3:8b
# 设置页选择「本地 Ollama」，Base URL 保持 http://127.0.0.1:11434/v1，模型填 qwen3:8b
```

## 主要配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_STEPS` | `30` | 单任务最大决策步数 |
| `MAX_RUN_SECONDS` | `600` | 单任务最长运行时间，超时终止 |
| `MAX_RETRIES` | `2` | 单次工具失败重试次数 |
| `TIMEOUT_MS` | `30000` | 页面操作超时（毫秒） |
| `MAX_SNAPSHOT_CHARS` | `8000` | 每次喂给模型的页面文本上限 |
| `HEADLESS` | `true` | 是否无头运行浏览器 |
| `BROWSER_CHANNEL` | 空 | `msedge` / `chrome` 复用系统浏览器；留空回退 Playwright Chromium |
| `BLOCK_RESOURCES` | `image,media,font,stylesheet` | 拦截无关资源，加快导航 |
| `BROWSER_REUSE` | `true` | 跨任务复用同一浏览器，省去启动开销 |
| `BROWSER_IDLE_TIMEOUT` | `300` | 浏览器空闲回收时间（秒） |
| `DB_PATH` | `data/crawler.db` | SQLite 数据库路径 |

## 数据存储

- `data/crawler.db`：任务、定时任务、文档库与子库、文档记录、网站记忆与采集配方。
- `data/settings.json`：模型配置与主题偏好（含 API Key，勿提交）。
- `data/site_profiles/`：网站记忆的浏览器 profile（含登录 Cookie，勿提交）。
- `data/` 整个目录已在 `.gitignore` 中排除。

文档库按 `task_id` 分批：每次采集的所有条目归为一个批次，批次可整体移入自建子库；删除批次有同步记录，重新打开文档库也不会「复活」。

## 项目结构

```text
backend.py            # FastAPI 后端入口：任务 / 定时 / 文档库 / 日志 / 设置接口 + 前端静态托管
frontend/
  index.html          # 首屏前恢复主题，避免刷新闪白
  src/
    views/            # 10 个页面组件
    api.js            # 后端接口封装
    router.js         # 路由与页面标题
    theme.js          # 主题偏好（localStorage + 服务端同步）
    style.css         # 全局样式与浅色 / 深色变量
agent_crawler/        # 核心引擎（纯 Python 包）
  config.py           # 配置加载
  llm.py              # LLM 客户端（Ollama 原生接口 / OpenAI 兼容接口）
  agent_prompt.py     # 智能体 System Prompt
  browser.py          # Playwright 工具封装
  agent.py            # ReAct 决策循环 + 各阶段耗时埋点
  intent.py           # 意图识别：描述 → 结构化筛选条件 + 确定性过滤
  recipe.py           # 采集配方：候选识别 / 生成校验 / 接口直采执行
  site_login.py       # 网站记忆：登录助手与登录态保存
  visualizer.py       # 图表方案生成
  classifier.py       # 文档归类
  schema.py           # 动态 Schema 校验
  storage.py          # SQLite 持久化
  exporter.py         # 多格式导出
  models.py           # 任务 / 定时任务数据模型
data/                 # 运行时数据（已 gitignore）
tests/                # 单元测试
```

## 接口概览

| 分组 | 路径 |
| --- | --- |
| 任务 | `POST /api/tasks`、`GET /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/cancel`、`DELETE /api/tasks/{id}` |
| 网站记忆 | `GET / POST /api/sites`、`PUT / DELETE /api/sites/{id}`、`POST /api/sites/{id}/login` |
| 采集配方 | `GET /api/recipes`、`DELETE /api/recipes/{id}` |
| Schema | `POST /api/schema/generate` |
| 导出 | `GET /api/tasks/{id}/export?fmt=json`（支持 `csv` / `xlsx` / `md` / `docx`） |
| 可视化 | `POST /api/tasks/{id}/visualize` |
| 定时任务 | `GET / POST /api/schedules`、`PUT / DELETE /api/schedules/{id}`、`POST /api/schedules/{id}/toggle`、`POST /api/schedules/{id}/run` |
| 文档库 | `GET /api/documents`、`GET / POST /api/libraries`、`PUT / DELETE /api/libraries/{id}`、`PUT /api/tasks/{id}/library`、`DELETE /api/document-batches/{id}` |
| 日志 | `GET /api/logs`、`DELETE /api/logs` |
| 设置 | `GET / PUT /api/settings`、`PUT /api/settings/theme`、`POST /api/settings/models`、`POST /api/settings/test` |
| 健康检查 | `GET /health` |

## 常见问题

- **为什么任务显示「部分完成」？** 常见原因是页面需要登录（如抖音未登录时主页只剩空壳），智能体拿不到真实数据；也可能是字段只出现在详情页，需要点击后才能取到。需要登录的站点请先在「网站记忆」里扫码登录一次，之后同站点任务会自动复用登录态。项目不提供验证码破解或风控规避能力。
- **为什么结果只有几条甚至 0 条？** 先看任务详情里的链路与说明：显示「接口直采」说明命中配方；页面出现「访问频繁」等提示说明目标站点风控拒绝了请求，可稍后重试或更换网络/账号；页面为服务端渲染且滚动不加载更多时，智能体只能取到当前可见内容。
- **为什么结果条目数比要求的多？** 若目标里写「5 个视频」，建议同时给出明确的筛选条件或示例链接；智能体按页面可见内容提取，列表页可能一次呈现更多条目。
- **模型连不上？** 在「设置 → 模型」点「测试连接」查看错误；本地 Ollama 需先 `ollama serve` 且模型已 `pull`。常见错误是 Base URL 多了 `/v1` 或少了 `/v1`，Ollama 用 `http://127.0.0.1:11434/v1`。
- **端口被占用？** 后端启动命令里的 `--port 8000`、前端 `frontend/vite.config.js` 的 `server.port`（默认 5170）都可以改。
- **导出的 CSV 用 Excel 打开乱码？** 请用 Excel 的「数据 → 从文本/CSV」并选择 UTF-8。
- **浏览器打不开？** 确认已执行 `playwright install chromium`，或设置 `BROWSER_CHANNEL=msedge` 复用系统浏览器。

## 合规说明

请仅采集公开、可合法访问的数据，并遵守目标网站的 `robots.txt`、服务条款与当地法律法规。本项目不提供登录绕过、验证码破解或风控规避能力。

## 测试

```bash
python -m pytest tests -q
```

## 致谢

本项目的实现离不开以下开源项目与社区的支持：

- [FastAPI](https://fastapi.tiangolo.com/)、[Starlette](https://www.starlette.io/)、[Uvicorn](https://www.uvicorn.org/)：后端接口与运行服务。
- [Playwright](https://playwright.dev/)：浏览器自动化与页面观察能力。
- [Vue 3](https://vuejs.org/)、[Vite](https://vitejs.dev/)、[Vue Router](https://router.vuejs.org/)、[ECharts](https://echarts.apache.org/)：前端界面、构建与可视化。
- [SQLite](https://www.sqlite.org/)、[Pydantic](https://docs.pydantic.dev/)：数据持久化与接口校验。

同时感谢所有提交 Issue、反馈建议与使用本项目的人，你们的真实场景让这个工具变得更好用。
