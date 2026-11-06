"""FastAPI 后端服务。

提供任务、定时任务、可视化与导出 REST API，并异步执行爬虫任务。
启动：uvicorn backend:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_crawler import Agent, LLMClient, Schedule, Task, TASK_STATUS, Visualizer, load_config
from agent_crawler.exporter import export_result
from agent_crawler.storage import Storage

logger = logging.getLogger("uvicorn.error")

_app_llm_config, _app_crawler_config = load_config()

storage = Storage(_app_crawler_config.db_path)
llm_client = LLMClient(_app_llm_config)

# 轨迹仅保留在内存中（不落库），供前端查看最近执行过的任务
_trajectories: dict[str, list] = {}
_TRAJECTORY_KEEP = 50

# 同一时刻只运行一个爬取任务，避免浏览器实例被并发抢占
_schedule_lock = asyncio.Lock()
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


# ---------------- 定时调度 ----------------


def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _validate_schedule_fields(
    schedule_type: str, interval_minutes: int, daily_times: list[str]
) -> tuple[str, int, list[str]]:
    """校验调度参数并返回规范化后的值。"""
    schedule_type = (schedule_type or "interval").lower()
    if schedule_type not in ("interval", "daily"):
        raise HTTPException(status_code=400, detail="调度方式仅支持 interval 或 daily")

    interval_minutes = int(interval_minutes or 0)
    if schedule_type == "interval" and interval_minutes < 1:
        raise HTTPException(status_code=400, detail="间隔分钟数必须大于等于 1")

    times: list[str] = []
    for raw in daily_times or []:
        m = _TIME_RE.match(str(raw).strip())
        if not m:
            raise HTTPException(status_code=400, detail=f"定点时间格式错误：{raw}（应为 HH:MM）")
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            raise HTTPException(status_code=400, detail=f"定点时间超出范围：{raw}")
        times.append(f"{hour:02d}:{minute:02d}")

    if schedule_type == "daily":
        if not times:
            raise HTTPException(status_code=400, detail="每日定点模式至少需要一个时间点")
        times = sorted(set(times))

    return schedule_type, interval_minutes, times


def _compute_next(schedule: Schedule, now: datetime) -> str:
    """计算下一次运行时间（错过的时间点直接跳过，不补跑）。"""
    if schedule.schedule_type == "daily":
        candidates: list[datetime] = []
        for hhmm in schedule.daily_times or ["09:00"]:
            hour, minute = hhmm.split(":")
            candidate = now.replace(
                hour=int(hour), minute=int(minute), second=0, microsecond=0
            )
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append(candidate)
        return min(candidates).isoformat(timespec="seconds")

    return (now + timedelta(minutes=max(1, schedule.interval_minutes))).isoformat(
        timespec="seconds"
    )


def _reconcile_stale_tasks() -> None:
    """启动时把上次进程遗留的 pending/running 任务标记为失败，避免“永远运行中”。"""
    for status in (TASK_STATUS["PENDING"], TASK_STATUS["RUNNING"]):
        for task in storage.list(status=status, limit=1000):
            task.status = TASK_STATUS["FAILED"]
            task.error = "服务重启导致任务中断，请重新发起"
            storage.save(task)
            logger.warning("已重置中断任务：%s", task.id[:8])


def _init_schedule_times() -> None:
    """启动时校正下次运行时间：停机期间错过的任务不再补跑。"""
    now = datetime.now()
    for schedule in storage.list_schedules():
        if not schedule.enabled:
            continue
        nxt = _parse_dt(schedule.next_run_at)
        if nxt is None or nxt <= now:
            schedule.next_run_at = _compute_next(schedule, now)
            storage.save_schedule(schedule)


async def _run_schedule_task(schedule_id: str, task_id: str) -> None:
    """执行一次定时任务（排队串行），完成后回写运行记录。"""
    schedule = storage.get_schedule(schedule_id)
    task = storage.get(task_id)
    if schedule is None or task is None:
        return

    async with _schedule_lock:
        await _run_task(task.id, task.task_desc, task.start_url, task.schema_json)

        finished = storage.get(task.id)
        schedule.last_run_at = _now_str()
        schedule.last_status = finished.status if finished else ""
        schedule.last_task_id = task.id
        schedule.run_count += 1
        schedule.next_run_at = (
            _compute_next(schedule, datetime.now()) if schedule.enabled else ""
        )
        storage.save_schedule(schedule)


def _create_schedule_task(schedule: Schedule) -> Task:
    """为定时任务创建一条待执行任务记录。"""
    task = Task(
        task_desc=schedule.task_desc,
        start_url=schedule.start_url,
        schema_json=schedule.schema_json,
        schedule_id=schedule.id,
    )
    storage.save(task)
    return task


async def _scheduler_loop() -> None:
    """后台调度循环：每 10 秒检查一次到期任务。"""
    await asyncio.sleep(2)
    while True:
        try:
            now = datetime.now()
            due: Optional[Schedule] = None
            for schedule in storage.list_schedules():
                if not schedule.enabled:
                    continue
                nxt = _parse_dt(schedule.next_run_at)
                if nxt is None:
                    schedule.next_run_at = _compute_next(schedule, now)
                    storage.save_schedule(schedule)
                    continue
                if nxt <= now:
                    due = schedule
                    break

            if due is not None:
                logger.info("定时任务触发：%s %s", due.id[:8], due.start_url)
                task = _create_schedule_task(due)
                await _run_schedule_task(due.id, task.id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("定时调度循环出错")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _reconcile_stale_tasks()
    _init_schedule_times()
    scheduler_task = asyncio.create_task(_scheduler_loop())
    try:
        yield
    finally:
        scheduler_task.cancel()


app = FastAPI(title="智能体爬虫", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------- 请求体 ----------------


class TaskCreate(BaseModel):
    task_desc: str
    start_url: str
    schema_json: str


class ScheduleBody(BaseModel):
    task_desc: str
    start_url: str
    schema_json: str
    schedule_type: str = "interval"
    interval_minutes: int = 60
    daily_times: list[str] = []
    enabled: bool = True


class ToggleBody(BaseModel):
    enabled: bool


class VisualizeBody(BaseModel):
    hint: str = ""


# ---------------- 任务执行 ----------------


async def _run_task(task_id: str, task_desc: str, start_url: str, schema_json: str) -> None:
    """在线程池中执行同步爬虫，并更新状态与耗时。"""
    task = storage.get(task_id)
    if task is None:
        return
    task.status = TASK_STATUS["RUNNING"]
    storage.save(task)
    try:
        agent = Agent(llm_client, _app_crawler_config)
        result = await asyncio.to_thread(agent.run, task_desc, start_url, schema_json)

        task.result_json = json.dumps(result.items, ensure_ascii=False)
        task.note = result.note
        task.trajectory = result.trajectory
        task.timings_json = json.dumps(result.timings or {}, ensure_ascii=False)
        task.status = _status_from(result)

        _trajectories[task_id] = result.trajectory or []
        while len(_trajectories) > _TRAJECTORY_KEEP:
            _trajectories.pop(next(iter(_trajectories)))
    except Exception as e:  # noqa: BLE001
        task.status = TASK_STATUS["FAILED"]
        task.error = str(e)
    storage.save(task)


def _status_from(result) -> str:
    n = len(result.items)
    note = result.note or ""
    if "超时" in note:
        return TASK_STATUS["PARTIAL"] if n else TASK_STATUS["FAILED"]
    if n == 0:
        return TASK_STATUS["PARTIAL"]
    if "少于" in note:
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
    items = []
    for t in storage.list(status=status, limit=limit):
        row = t.to_row()
        row.pop("result_json", None)
        try:
            row["item_count"] = len(json.loads(t.result_json)) if t.result_json else 0
        except ValueError:
            row["item_count"] = 0
        items.append(row)
    return {"items": items}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = storage.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    row = task.to_row()
    row["trajectory"] = _trajectories.get(task_id, [])
    return row


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


@app.post("/api/tasks/{task_id}/visualize")
async def visualize_task(task_id: str, body: VisualizeBody):
    """让 LLM 阅读任务结果并生成可视化方案。"""
    task = storage.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    try:
        items = json.loads(task.result_json) if task.result_json else []
    except ValueError:
        items = []
    if not items:
        raise HTTPException(status_code=400, detail="该任务没有可用于可视化的数据")

    visualizer = Visualizer(llm_client)
    try:
        plan = await asyncio.to_thread(visualizer.plan, task.task_desc, items, body.hint)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"LLM 服务不可用，请确认 Ollama（127.0.0.1:11434）已启动：{e}",
        ) from e
    return plan


@app.get("/api/tasks/{task_id}/export")
def export_task(task_id: str, fmt: str = "json"):
    task = storage.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

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


@app.get("/api/documents")
def list_documents(limit_tasks: int = 50, limit_per_task: int = 50):
    """聚合各任务采集到的条目，供文档库页面浏览。"""
    docs = []
    for task in storage.list(limit=limit_tasks):
        if not task.result_json:
            continue
        try:
            items = json.loads(task.result_json)
        except json.JSONDecodeError:
            continue
        for idx, item in enumerate(items[:limit_per_task]):
            if not isinstance(item, dict):
                continue
            docs.append(
                {
                    "id": f"{task.id}:{idx}",
                    "task_id": task.id,
                    "task_desc": task.task_desc,
                    "start_url": task.start_url,
                    "status": task.status,
                    "created_at": task.created_at,
                    "fields": item,
                }
            )
    return {"items": docs, "total": len(docs)}


# ---------------- 定时任务 CRUD ----------------


@app.get("/api/schedules")
def list_schedules():
    return {"items": [s.to_row() for s in storage.list_schedules()]}


@app.post("/api/schedules")
def create_schedule(body: ScheduleBody):
    schedule_type, interval_minutes, daily_times = _validate_schedule_fields(
        body.schedule_type, body.interval_minutes, body.daily_times
    )
    schedule = Schedule(
        task_desc=body.task_desc.strip(),
        start_url=body.start_url.strip(),
        schema_json=body.schema_json.strip(),
        schedule_type=schedule_type,
        interval_minutes=interval_minutes,
        daily_times=daily_times,
        enabled=body.enabled,
    )
    if schedule.enabled:
        schedule.next_run_at = _compute_next(schedule, datetime.now())
    storage.save_schedule(schedule)
    return schedule.to_row()


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, body: ScheduleBody):
    schedule = storage.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    schedule_type, interval_minutes, daily_times = _validate_schedule_fields(
        body.schedule_type, body.interval_minutes, body.daily_times
    )
    schedule.task_desc = body.task_desc.strip()
    schedule.start_url = body.start_url.strip()
    schedule.schema_json = body.schema_json.strip()
    schedule.schedule_type = schedule_type
    schedule.interval_minutes = interval_minutes
    schedule.daily_times = daily_times
    schedule.enabled = body.enabled
    schedule.next_run_at = (
        _compute_next(schedule, datetime.now()) if schedule.enabled else ""
    )
    storage.save_schedule(schedule)
    return schedule.to_row()


@app.post("/api/schedules/{schedule_id}/toggle")
def toggle_schedule(schedule_id: str, body: ToggleBody):
    schedule = storage.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    schedule.enabled = body.enabled
    schedule.next_run_at = (
        _compute_next(schedule, datetime.now()) if schedule.enabled else ""
    )
    storage.save_schedule(schedule)
    return schedule.to_row()


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    schedule = storage.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    storage.delete_schedule(schedule_id)
    return {"status": "deleted"}


@app.post("/api/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: str):
    """立即执行一次（后台运行，立即返回任务 ID）。"""
    schedule = storage.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    task = _create_schedule_task(schedule)
    asyncio.create_task(_run_schedule_task(schedule.id, task.id))
    return {"task_id": task.id, "status": task.status}


# ---------------- 前端静态资源（Vue 构建产物） ----------------

_DIST_DIR = Path(__file__).resolve().parent / "frontend" / "dist"

if (_DIST_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """未匹配的路径返回前端入口，支持 vue-router history 模式。"""
        if full_path.split("/")[0] in {"api", "health", "docs", "redoc", "openapi.json"}:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (_DIST_DIR / full_path).resolve()
        if full_path and candidate.is_file() and _DIST_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_DIST_DIR / "index.html")
