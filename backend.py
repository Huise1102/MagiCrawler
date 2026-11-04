"""FastAPI 后端服务。

提供任务创建、查询、列表、取消与导出 REST API，并异步执行爬虫任务。
启动：uvicorn backend:app --reload
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from agent_crawler import Agent, LLMClient, Task, TASK_STATUS, load_config
from agent_crawler.exporter import export_result
from agent_crawler.storage import Storage

_app_llm_config, _app_crawler_config = load_config()

app = FastAPI(title="智能体爬虫", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = Storage(_app_crawler_config.db_path)
llm_client = LLMClient(_app_llm_config)


class TaskCreate(BaseModel):
    task_desc: str
    start_url: str
    schema_json: str


async def _run_task(task_id: str, task_desc: str, start_url: str, schema_json: str) -> None:
    """在线程池中执行同步爬虫，并更新状态。"""
    task = storage.get(task_id)
    if task is None:
        return
    task.status = TASK_STATUS["RUNNING"]
    storage.save(task)
    try:
        agent = Agent(llm_client, _app_crawler_config)
        result = await asyncio.to_thread(agent.run, task_desc, start_url, schema_json)

        import json

        task.result_json = json.dumps(result.items, ensure_ascii=False)
        task.note = result.note
        task.trajectory = result.trajectory
        task.status = _status_from(result)
    except Exception as e:  # noqa: BLE001
        task.status = TASK_STATUS["FAILED"]
        task.error = str(e)
    storage.save(task)


def _status_from(result) -> str:
    n = len(result.items)
    if n == 0:
        return TASK_STATUS["PARTIAL"]
    if "少于" in result.note:
        return TASK_STATUS["PARTIAL"]
    return TASK_STATUS["SUCCESS"]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/tasks")
async def create_task(body: TaskCreate):
    task = Task(
        task_desc=body.task_desc.strip(),
        start_url=body.start_url.strip(),
        schema_json=body.schema_json.strip(),
    )
    storage.save(task)
    asyncio.create_task(_run_task(task.id, task.task_desc, task.start_url, task.schema_json))
    return {"task_id": task.id, "status": task.status}


@app.get("/api/tasks")
def list_tasks(status: Optional[str] = None, limit: int = 100):
    return {"items": [t.to_row() for t in storage.list(status=status, limit=limit)]}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = storage.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.to_row()


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    task = storage.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == TASK_STATUS["PENDING"]:
        task.status = TASK_STATUS["FAILED"]
        task.error = "已取消"
        storage.save(task)
        return {"status": "cancelled"}
    if task.status == TASK_STATUS["RUNNING"]:
        raise HTTPException(status_code=400, detail="任务执行中，暂不支持中断")
    return {"status": task.status}


@app.get("/api/tasks/{task_id}/export")
def export_task(task_id: str, fmt: str = "json"):
    task = storage.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    import json

    items = json.loads(task.result_json) if task.result_json else []
    filename, content = export_result(items, task.note, fmt)
    media_types = {
        "json": "application/json",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "md": "text/markdown",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return Response(
        content=content,
        media_type=media_types.get(fmt, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )