# 智能体爬虫

基于 LLM 智能体 + Playwright 的网页数据采集系统。用户只需输入目标描述、起始网址与字段 Schema，智能体即可自主完成「观察网页 → 决策动作 → 提取数据 → 校验 → 输出」，并支持 JSON / CSV / Excel / Markdown / Word 多格式导出。

## 技术栈

- 前端：Streamlit
- 后端：FastAPI（可选，用于 API 化）
- 核心引擎：`agent_crawler`（纯 Python 包，前后端复用）
- 智能体：LLM（OpenAI 兼容接口，默认 DeepSeek）+ ReAct 决策循环
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

## 启动

### 方式一：仅前端（推荐，单进程）

```bash
streamlit run app.py
```

在侧边栏填写 API Key，即可在页面中输入任务并开始爬取、查看结果与导出。

### 方式二：后端 API

```bash
uvicorn backend:app --reload
```

访问 `http://127.0.0.1:8000/docs` 查看接口文档。

## 测试

```bash
python -m unittest discover -s tests
```

## 目录结构

```
app.py             # Streamlit 前端入口
backend.py         # FastAPI 后端入口
agent_crawler/     # 核心引擎（纯 Python 包）
  config.py        # 配置加载
  llm.py           # LLM 客户端封装
  agent_prompt.py  # 智能体 System Prompt
  browser.py       # Playwright 工具封装
  agent.py         # ReAct 决策循环
  schema.py        # 动态 Schema 校验
  storage.py       # SQLite 持久化
  exporter.py      # 多格式导出
  models.py        # 任务数据模型
tests/             # 最小单测
```