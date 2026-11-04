"""Streamlit 前端入口。

提供任务输入表单、实时轨迹、结果可视化（可编辑表格）、历史任务与多格式导出。
启动：streamlit run app.py
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent_crawler import Agent, LLMClient, Task, parse_schema
from agent_crawler.config import CrawlerConfig, LLMConfig, load_config
from agent_crawler.exporter import export_result
from agent_crawler.models import STATUS_COLORS, TASK_STATUS
from agent_crawler.storage import Storage

# ---------------- 页面配置 ----------------

st.set_page_config(page_title="智能体爬虫", layout="wide")

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    .main-title { font-size: 1.6rem; font-weight: 700; margin-bottom: 0.2rem; }
    .sub-title { color: #6b7280; font-size: 0.95rem; margin-bottom: 1.2rem; }
    .section-title { font-size: 1.1rem; font-weight: 600; margin-top: 1rem; margin-bottom: 0.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------- 配置加载 ----------------

_env_llm, _env_crawler = load_config()
storage = Storage(_env_crawler.db_path)


def status_badge(status: str) -> str:
    """状态徽章（彩色圆点 + 文字）。"""
    color = STATUS_COLORS.get(status, "gray")
    return f'<span style="color:{color};font-weight:600;">●</span> {status}'


# ---------------- 侧边栏：配置 ----------------

with st.sidebar:
    st.header("配置")
    st.caption("LLM 接口（OpenAI 兼容）")
    api_key = st.text_input("API Key", value=_env_llm.api_key, type="password")
    base_url = st.text_input("Base URL", value=_env_llm.base_url)
    model = st.text_input("模型", value=_env_llm.model)

    st.divider()
    st.caption("爬取参数")
    max_steps = st.number_input("最大步数", min_value=1, max_value=100, value=_env_crawler.max_steps)
    headless = st.checkbox("无头模式", value=_env_crawler.headless)

    st.divider()
    st.caption(f"数据存储：{_env_crawler.db_path}")

# ---------------- 标题 ----------------

st.markdown('<div class="main-title">智能体爬虫</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">输入目标描述、网址与字段 Schema，AI 智能体自动完成网页采集与结构化输出。</div>',
    unsafe_allow_html=True,
)

# ---------------- Schema 输入区 ----------------

schema_input_mode = st.radio("字段 Schema 输入方式", ["JSON Schema", "可视化字段编辑器"], horizontal=True)

schema_json_text = ""

if schema_input_mode == "JSON Schema":
    schema_json_text = st.text_area(
        "JSON Schema",
        height=180,
        value='{"type": "object", "properties": {"title": {"type": "string", "description": "书名"}, "price": {"type": "string", "description": "价格"}}}',
        help="描述目标字段；也支持简单键值映射，如 {\"title\": \"string\"}",
    )
else:
    st.caption("在下方添加字段，填写后自动生成 Schema")
    df_editor = st.data_editor(
        pd.DataFrame(columns=["字段名", "类型", "描述"]),
        num_rows="dynamic",
        column_config={
            "类型": st.column_config.SelectboxColumn(
                "类型",
                options=["string", "number", "integer", "boolean", "array", "object"],
                default="string",
            )
        },
        key="schema_editor",
        hide_index=True,
    )
    props = {}
    for _, row in df_editor.iterrows():
        name = str(row.get("字段名") or "").strip()
        if name:
            ftype = str(row.get("类型") or "string")
            desc = str(row.get("描述") or "").strip()
            props[name] = {"type": ftype, "description": desc}
    schema_json_text = json.dumps({"type": "object", "properties": props}, ensure_ascii=False)

# ---------------- 任务输入 ----------------

col1, col2 = st.columns([2, 1])
with col1:
    task_desc = st.text_input("目标描述", placeholder="例：抓取某图书网站「计算机」分类下前 10 本书的书名与价格")
with col2:
    start_url = st.text_input("起始网址", placeholder="https://example.com/books")

# ---------------- 开始爬取 ----------------

if st.button("开始爬取", type="primary", use_container_width=True):
    if not api_key.strip():
        st.error("请在侧边栏填写 LLM API Key。")
    elif not task_desc.strip() or not start_url.strip():
        st.error("请完整填写「目标描述」与「起始网址」。")
    else:
        try:
            fields = parse_schema(schema_json_text)
        except Exception as e:  # noqa: BLE001
            st.error(f"Schema 解析失败：{e}")
        else:
            llm_cfg = LLMConfig(base_url=base_url.strip(), api_key=api_key.strip(), model=model.strip())
            crawler_cfg = CrawlerConfig(max_steps=int(max_steps), headless=headless)
            agent = Agent(LLMClient(llm_cfg), crawler_cfg)

            with st.spinner("智能体正在爬取中……"):
                result = agent.run(task_desc.strip(), start_url.strip(), schema_json_text)

            # 持久化
            task = Task(
                task_desc=task_desc.strip(),
                start_url=start_url.strip(),
                schema_json=schema_json_text,
                status=_status_from(result),
                result_json=json.dumps(result.items, ensure_ascii=False),
                note=result.note,
            )
            storage.save(task)

            st.session_state["last_result"] = result
            st.session_state["last_task_id"] = task.id
            st.success(f"采集完成，共 {len(result.items)} 条。")


def _status_from(result) -> str:
    if not result.items:
        return TASK_STATUS["PARTIAL"]
    if "少于" in result.note:
        return TASK_STATUS["PARTIAL"]
    return TASK_STATUS["SUCCESS"]


# ---------------- 结果展示 ----------------

if "last_result" in st.session_state:
    result = st.session_state["last_result"]
    st.divider()
    st.markdown('<div class="section-title">采集结果</div>', unsafe_allow_html=True)

    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("条目数", len(result.items))
    with m2:
        missing = sum(
            1 for it in result.items for v in it.values() if v is None
        )
        st.metric("缺失字段值", missing)
    with m3:
        st.metric("动作步数", len(result.trajectory))

    if result.note:
        st.info(result.note)

    # 轨迹日志（可折叠）
    if result.trajectory:
        with st.expander("查看爬取轨迹", expanded=False):
            for t in result.trajectory:
                st.write(
                    f"**第 {t['step']} 步** · `{t['tool']}` · "
                    f"{json.dumps(t['args'], ensure_ascii=False)} · {t['time']}"
                )

    # 结果可编辑表格
    if result.items:
        df = pd.DataFrame(result.items)
        edited_df = st.data_editor(df, num_rows="dynamic", key="result_editor", hide_index=True)
        items_for_export = edited_df.where(pd.notnull(edited_df), None).to_dict(orient="records")
    else:
        items_for_export = []
        st.warning("未采集到任何数据。")

    # 导出
    st.markdown('<div class="section-title">导出</div>', unsafe_allow_html=True)
    exp_cols = st.columns(5)
    for col, fmt, label in zip(
        exp_cols,
        ["json", "csv", "xlsx", "md", "docx"],
        ["JSON", "CSV", "Excel", "Markdown", "Word"],
    ):
        filename, content = export_result(items_for_export, result.note, fmt)
        col.download_button(label, data=content, file_name=filename, use_container_width=True)

# ---------------- 历史任务 ----------------

st.divider()
st.markdown('<div class="section-title">历史任务</div>', unsafe_allow_html=True)

history = storage.list(limit=50)
if history:
    hist_rows = [
        {
            "ID": t.id[:8],
            "描述": t.task_desc,
            "状态": t.status,
            "条目数": (len(json.loads(t.result_json)) if t.result_json else 0),
            "创建时间": t.created_at,
        }
        for t in history
    ]
    st.dataframe(hist_rows, use_container_width=True, hide_index=True)

    ids = [t.id[:8] for t in history]
    selected = st.selectbox("选择任务查看详情", ids, key="hist_select")
    sel_idx = ids.index(selected) if selected in ids else None
    if sel_idx is not None:
        sel_task = history[sel_idx]
        if sel_task.result_json:
            items = json.loads(sel_task.result_json)
            st.dataframe(items, use_container_width=True, hide_index=True)
            fname, content = export_result(items, sel_task.note, "csv")
            st.download_button("下载该任务 CSV", data=content, file_name=f"task_{sel_task.id[:8]}.csv")
        else:
            st.caption("该任务暂无结果。")
else:
    st.caption("暂无历史任务。")