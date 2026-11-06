"""FastAPI 后端服务。

提供任务、定时任务、可视化与导出 REST API，并异步执行爬虫任务。
启动：uvicorn backend:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

from agent_crawler import (
    Agent,
    DocumentClassifier,
    LLMClient,
    LLMConfig,
    Schedule,
    Task,
    TASK_STATUS,
    Visualizer,
    load_config,
)
from agent_crawler.exporter import export_result
from agent_crawler.storage import Storage

logger = logging.getLogger("uvicorn.error")

# 系统日志：把运行期日志写入内存环形缓冲，供“系统日志”页面查看
_LOG_BUFFER: deque = deque(maxlen=800)
_LOG_RECORDS = 0
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class _MemoryLogHandler(logging.Handler):
    """收集日志记录到内存中（不落盘，重启后清空）。"""

    def emit(self, record: logging.LogRecord) -> None:
        global _LOG_RECORDS
        try:
            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{logging.Formatter().formatException(record.exc_info)}"
            _LOG_RECORDS += 1
            _LOG_BUFFER.append(
                {
                    "id": _LOG_RECORDS,
                    "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                }
            )
        except Exception:  # noqa: BLE001 - 日志收集本身不能抛错
            pass


_memory_handler = _MemoryLogHandler()
_memory_handler.setLevel(logging.DEBUG)
logger.addHandler(_memory_handler)
logging.getLogger().addHandler(_memory_handler)

_app_llm_config, _app_crawler_config = load_config()

# 模型设置持久化在 data/settings.json，优先于 .env，保存后即时生效
_SETTINGS_FILE = Path(_app_crawler_config.db_path).resolve().parent / "settings.json"


def _settings_payload(cfg: LLMConfig) -> dict[str, object]:
    """返回可序列化的模型配置（本地服务，允许前端回显 API Key）。"""
    return {
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "api_key": cfg.api_key,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "timeout_s": cfg.timeout_s,
    }


def _read_settings_file() -> dict:
    """读取整个设置文件；不存在或损坏时返回空字典。"""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取设置文件失败：%s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings_file(payload: dict) -> None:
    """原子写入设置文件，避免半截 JSON。"""
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_SETTINGS_FILE)


def _load_saved_theme() -> str:
    ui = _read_settings_file().get("ui")
    theme = ui.get("theme") if isinstance(ui, dict) else None
    return theme if theme in ("light", "dark", "system") else "system"


def _load_saved_llm() -> Optional[LLMConfig]:
    """读取已保存的模型设置；文件缺失或损坏时回退到 .env。"""
    data = _read_settings_file()
    llm = data.get("llm") if isinstance(data, dict) else None
    if not isinstance(llm, dict):
        return None

    cfg = LLMConfig()
    for key in ("provider", "base_url", "api_key", "model"):
        value = llm.get(key)
        if isinstance(value, str) and value.strip():
            setattr(cfg, key, value.strip())
    for key, cast, low, high in (
        ("temperature", float, 0.0, 2.0),
        ("max_tokens", int, 64, 32768),
        ("timeout_s", int, 10, 600),
    ):
        try:
            value = cast(llm.get(key))
        except (TypeError, ValueError):
            continue
        setattr(cfg, key, max(low, min(high, value)))
    if cfg.provider not in ("ollama", "openai"):
        cfg.provider = "ollama"
    return cfg


def _save_settings(cfg: LLMConfig) -> None:
    """保存模型设置，保留已有外观设置。"""
    data = _read_settings_file()
    data["llm"] = _settings_payload(cfg)
    _write_settings_file(data)


_app_llm_config = _load_saved_llm() or _app_llm_config
_ui_theme = _load_saved_theme()

storage = Storage(_app_crawler_config.db_path)
llm_client = LLMClient(_app_llm_config)

# 轨迹仅保留在内存中（不落库），供前端查看最近执行过的任务
_trajectories: dict[str, list] = {}
_TRAJECTORY_KEEP = 50

# 采集链路事件（逐步追加，供首页实时展示智能体决策链路）
_chains: dict[str, list] = {}
_CHAIN_KEEP = 50
_CHAIN_MAX_STEPS = 80

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
    logger.info("服务启动完成：后端 8000，定时调度已就绪")
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


class SchemaBody(BaseModel):
    task_desc: str
    start_url: str = ""


class LLMSettingsBody(BaseModel):
    provider: str = "ollama"
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0
    max_tokens: int = 4096
    timeout_s: int = 120


class ThemeBody(BaseModel):
    theme: str


class ModelQueryBody(BaseModel):
    provider: str = "ollama"
    base_url: str
    api_key: str = ""


def _normalize_ollama_base(base_url: str) -> str:
    """Ollama 原生接口根地址：去掉末尾斜杠与 /v1。"""
    base = base_url.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _build_llm_config(body: LLMSettingsBody) -> LLMConfig:
    """校验设置请求并构造 LLM 配置。"""
    provider = (body.provider or "").strip().lower()
    if provider not in ("ollama", "openai"):
        raise HTTPException(status_code=400, detail="模型服务仅支持 ollama 或 openai")
    base_url = (body.base_url or "").strip()
    model = (body.model or "").strip()
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="接口地址需以 http:// 或 https:// 开头")
    if not model:
        raise HTTPException(status_code=400, detail="请填写模型名称")
    temperature = float(body.temperature)
    if not 0 <= temperature <= 2:
        raise HTTPException(status_code=400, detail="temperature 需在 0-2 之间")
    max_tokens = int(body.max_tokens)
    if not 64 <= max_tokens <= 32768:
        raise HTTPException(status_code=400, detail="max_tokens 需在 64-32768 之间")
    timeout_s = int(body.timeout_s)
    if not 10 <= timeout_s <= 600:
        raise HTTPException(status_code=400, detail="超时时间需在 10-600 秒之间")

    cfg = LLMConfig()
    cfg.provider = provider
    cfg.base_url = base_url
    cfg.api_key = (body.api_key or "").strip()
    cfg.model = model
    cfg.temperature = temperature
    cfg.max_tokens = max_tokens
    cfg.timeout_s = timeout_s
    return cfg


# ---------------- 设置（外观与模型配置均持久化到 data/settings.json） ----------------


@app.get("/api/settings")
def get_settings():
    return {
        "llm": _settings_payload(_app_llm_config),
        "ui": {"theme": _ui_theme},
        "settings_file": str(_SETTINGS_FILE),
    }


@app.put("/api/settings")
def update_settings(body: LLMSettingsBody):
    """保存模型设置并即时切换到新的模型服务。"""
    global llm_client, _app_llm_config
    cfg = _build_llm_config(body)
    _save_settings(cfg)
    _app_llm_config = cfg
    llm_client = LLMClient(cfg)
    logger.info("模型设置已更新：provider=%s model=%s", cfg.provider, cfg.model)
    return {"status": "saved", "llm": _settings_payload(cfg)}


@app.put("/api/settings/theme")
def update_theme(body: ThemeBody):
    """保存外观主题，供不同浏览器/设备共享同一偏好。"""
    global _ui_theme
    theme = (body.theme or "").strip().lower()
    if theme not in ("light", "dark", "system"):
        raise HTTPException(status_code=400, detail="主题仅支持 light / dark / system")
    _ui_theme = theme
    data = _read_settings_file()
    data["ui"] = {"theme": theme}
    _write_settings_file(data)
    return {"status": "saved", "theme": theme}


@app.post("/api/settings/models")
def list_models(body: ModelQueryBody):
    """读取 Ollama 本地模型列表或 OpenAI 兼容服务的模型列表。"""
    provider = (body.provider or "").strip().lower()
    base_url = (body.base_url or "").strip()
    if provider not in ("ollama", "openai"):
        raise HTTPException(status_code=400, detail="模型服务仅支持 ollama 或 openai")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="接口地址需以 http:// 或 https:// 开头")
    try:
        if provider == "ollama":
            resp = requests.get(f"{_normalize_ollama_base(base_url)}/api/tags", timeout=10)
            resp.raise_for_status()
            models = [
                str(m.get("name"))
                for m in resp.json().get("models", [])
                if isinstance(m, dict) and m.get("name")
            ]
        else:
            client = OpenAI(base_url=base_url, api_key=body.api_key or "EMPTY", timeout=10)
            models = [m.id for m in client.models.list().data]
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        raise HTTPException(status_code=400, detail=f"获取模型列表失败：{exc}") from exc
    return {"provider": provider, "models": sorted(set(models))}


@app.post("/api/settings/test")
def test_settings(body: LLMSettingsBody):
    """用一次极短的对话验证接口地址、密钥与模型是否可用。"""
    cfg = _build_llm_config(body)
    cfg.max_tokens = min(cfg.max_tokens, 128)
    started = time.perf_counter()
    try:
        reply = LLMClient(cfg).chat(
            [{"role": "user", "content": "只回复 OK 两个字母"}],
            temperature=0,
            timeout=min(20, cfg.timeout_s),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc
    return {
        "ok": True,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "reply": (reply or "").strip()[:120],
        "provider": cfg.provider,
        "model": cfg.model,
    }


# ---------------- 字段 Schema 生成 ----------------


_SCHEMA_TYPES = {"string", "number", "integer", "boolean"}

_SCHEMA_PROMPT = (
    "你是网页数据采集的需求分析助手。根据用户的目标描述（必要时结合起始网址），"
    "识别要采集的实体，并给出采集字段的 JSON Schema。\n"
    "只输出紧凑 JSON，格式：\n"
    '{"type":"object","properties":{"title":{"type":"string","description":"书名"},'
    '"price":{"type":"number","description":"价格"}},"required":["title"]}\n'
    "约束：\n"
    "- 字段名使用英文小写下划线命名，共 3-8 个字段\n"
    "- type 只能是 string / number / integer / boolean\n"
    "- description 用中文简短说明字段含义\n"
    "- required 只列出核心字段\n"
    "- 不要输出任何解释文字\n"
)


def _clean_generated_schema(data) -> Optional[dict]:
    """校验 LLM 生成的 Schema，剔除非法字段并统一格式。"""
    if not isinstance(data, dict):
        return None
    props = data.get("properties")
    if not isinstance(props, dict):
        # 兼容模型直接返回「字段名 -> 类型」的简单映射
        props = {
            k: v
            for k, v in data.items()
            if k not in ("type", "properties", "required", "items")
        }
    if not props:
        return None

    cleaned: dict[str, dict] = {}
    for name, spec in props.items():
        key = str(name).strip()
        if not key or len(key) > 40 or len(cleaned) >= 12:
            continue
        ftype = "string"
        desc = ""
        if isinstance(spec, dict):
            ftype = str(spec.get("type") or "string").lower()
            desc = str(spec.get("description") or "").strip()
        elif isinstance(spec, str):
            ftype = spec.lower()
        if ftype not in _SCHEMA_TYPES:
            ftype = "string"
        entry: dict = {"type": ftype}
        if desc:
            entry["description"] = desc
        cleaned[key] = entry

    if not cleaned:
        return None

    schema: dict = {"type": "object", "properties": cleaned}
    required = data.get("required")
    if isinstance(required, list):
        picked = [str(r) for r in required if str(r) in cleaned]
        if picked:
            schema["required"] = picked
    return schema


def _generate_schema(task_desc: str, start_url: str) -> dict:
    """调用 LLM 生成字段 Schema。"""
    raw = llm_client.chat(
        [
            {"role": "system", "content": "你是数据采集字段设计助手，只输出 JSON。"},
            {
                "role": "user",
                "content": f"{_SCHEMA_PROMPT}目标描述：{task_desc}\n起始网址：{start_url or '未提供'}",
            },
        ],
        temperature=0,
        timeout=60,
    )
    schema = _clean_generated_schema(llm_client.extract_json(raw))
    if not schema:
        raise ValueError("大模型未返回有效字段，请调整目标描述后重试")
    return {
        "schema": schema,
        "schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
        "field_count": len(schema["properties"]),
    }


# ---------------- 任务执行 ----------------


async def _run_task(task_id: str, task_desc: str, start_url: str, schema_json: str) -> None:
    """在线程池中执行同步爬虫，并更新状态与耗时。"""
    task = storage.get(task_id)
    if task is None:
        return
    task.status = TASK_STATUS["RUNNING"]
    storage.save(task)
    logger.info("任务开始 %s：%s（%s）", task_id[:8], task_desc, start_url)

    chain: list = []
    _chains[task_id] = chain

    def _on_step(entry: dict) -> None:
        """爬虫每完成一步就追加到链路（线程内调用，列表 append 足够安全）。"""
        if len(chain) < _CHAIN_MAX_STEPS:
            chain.append(entry)

    try:
        agent = Agent(llm_client, _app_crawler_config, on_step=_on_step)
        result = await asyncio.to_thread(agent.run, task_desc, start_url, schema_json)

        task.result_json = json.dumps(result.items, ensure_ascii=False)
        task.note = result.note
        task.trajectory = result.trajectory
        task.timings_json = json.dumps(result.timings or {}, ensure_ascii=False)
        task.status = _status_from(result)
        if not result.items:
            task.error = (
                "未采集到任何数据：页面可能是空壳、需要登录或触发风控，"
                "请检查起始网址，或在浏览器中登录后重试。"
            )

        # 补一个收尾节点，让链路末端有明确终点（提前达标时爬虫不会记 finish）
        if chain and not any(x.get("tool") == "finish" for x in chain):
            chain.append(
                {
                    "step": len(chain),
                    "tool": "finish",
                    "args": {},
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "llm_ms": 0.0,
                    "exec_ms": 0.0,
                    "total_ms": 0.0,
                    "items": len(result.items),
                }
            )

        logger.info(
            "任务结束 %s：%s，采集 %d 条%s",
            task_id[:8],
            task.status,
            len(result.items),
            f"（{result.note}）" if result.note else "",
        )

        _trajectories[task_id] = result.trajectory or []
        while len(_trajectories) > _TRAJECTORY_KEEP:
            _trajectories.pop(next(iter(_trajectories)))
        while len(_chains) > _CHAIN_KEEP:
            _chains.pop(next(iter(_chains)))
    except Exception as e:  # noqa: BLE001
        task.status = TASK_STATUS["FAILED"]
        task.error = str(e)
        logger.exception("任务失败 %s：%s", task_id[:8], e)
    storage.save(task)


def _status_from(result) -> str:
    n = len(result.items)
    note = result.note or ""
    if "超时" in note:
        return TASK_STATUS["PARTIAL"] if n else TASK_STATUS["FAILED"]
    if n == 0:
        return TASK_STATUS["FAILED"]
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
    logger.info("创建任务 %s：%s", task.id[:8], task.task_desc)
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
    row["chain"] = _chains.get(task_id, [])
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
        logger.info("取消任务 %s", task_id[:8])
        return {"status": "cancelled"}
    if task.status == TASK_STATUS["RUNNING"]:
        raise HTTPException(status_code=400, detail="任务执行中，暂不支持中断")
    return {"status": task.status}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    """删除历史任务记录；文档库中由它同步的文档保留，仅解除关联。"""
    task = storage.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == TASK_STATUS["RUNNING"]:
        raise HTTPException(status_code=400, detail="任务执行中，暂不支持删除")

    storage.delete(task_id)
    storage.detach_task_documents(task_id)
    _trajectories.pop(task_id, None)
    _chains.pop(task_id, None)
    logger.info("删除任务 %s：%s", task_id[:8], task.task_desc)
    return {"status": "deleted"}


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


@app.post("/api/schema/generate")
async def generate_schema(body: SchemaBody):
    """根据目标描述让 LLM 生成字段 Schema。"""
    task_desc = body.task_desc.strip()
    if not task_desc:
        raise HTTPException(status_code=400, detail="请先填写目标描述")

    try:
        result = await asyncio.to_thread(_generate_schema, task_desc, body.start_url.strip())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"LLM 服务不可用，请确认 Ollama（127.0.0.1:11434）已启动：{e}",
        ) from e

    logger.info("字段生成：%s → %d 个字段", task_desc[:30], result["field_count"])
    return result


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
def list_documents(limit: int = 500):
    """返回文档库内容（爬取结果首次访问时自动入库）。"""
    _sync_documents()
    docs = storage.list_documents(limit=limit)
    return {"items": docs, "total": len(docs)}


_TITLE_KEYS = ("title", "name", "标题", "名称", "书名", "姓名", "headline", "article")


def _derive_title(fields: dict) -> str:
    """从字段中挑选合适的标题。"""
    for key, value in fields.items():
        if any(k in key.lower() for k in _TITLE_KEYS) and isinstance(value, str) and value.strip():
            return value.strip()
    for value in fields.values():
        if isinstance(value, str) and value.strip() and not value.lower().startswith("http"):
            return value.strip()
    return "未命名文档"


def _sync_documents() -> None:
    """把尚未入库的爬取结果同步为文档（每个任务只同步一次，删除后不会重现）。"""
    for task in storage.list(limit=100):
        if not task.result_json or storage.is_task_synced(task.id):
            continue
        try:
            items = json.loads(task.result_json)
        except json.JSONDecodeError:
            items = []
        for idx, item in enumerate(items[:100]):
            if not isinstance(item, dict):
                continue
            storage.insert_linked_document(
                doc_id=f"{task.id}:{idx}",
                task_id=task.id,
                doc_index=idx,
                title=_derive_title(item),
                fields_json=json.dumps(item, ensure_ascii=False),
                created_at=task.created_at,
            )
        storage.mark_task_synced(task.id)


# ---------------- 子库与文档 CRUD ----------------


class LibraryBody(BaseModel):
    name: str


class MoveLibraryBody(BaseModel):
    library_id: str = ""


class DocumentBody(BaseModel):
    title: str = ""
    library_id: str = ""
    fields_json: str = "{}"
    source_url: str = ""


def _library_name(body: LibraryBody) -> str:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="子库名称不能为空")
    if len(name) > 30:
        raise HTTPException(status_code=400, detail="子库名称不能超过 30 个字符")
    return name


def _ensure_library(library_id: str) -> str:
    library_id = (library_id or "").strip()
    if library_id and not any(
        lib["id"] == library_id for lib in storage.list_libraries()
    ):
        raise HTTPException(status_code=400, detail="子库不存在")
    return library_id


@app.get("/api/libraries")
def list_libraries():
    return {"items": storage.list_libraries()}


@app.post("/api/libraries")
def create_library(body: LibraryBody):
    name = _library_name(body)
    if any(lib["name"] == name for lib in storage.list_libraries()):
        raise HTTPException(status_code=400, detail="子库名称已存在")
    return storage.create_library(name)


@app.put("/api/libraries/{library_id}")
def update_library(library_id: str, body: LibraryBody):
    name = _library_name(body)
    if any(
        lib["name"] == name and lib["id"] != library_id
        for lib in storage.list_libraries()
    ):
        raise HTTPException(status_code=400, detail="子库名称已存在")
    if not storage.update_library(library_id, name):
        raise HTTPException(status_code=404, detail="子库不存在")
    return {"status": "updated"}


@app.delete("/api/libraries/{library_id}")
def delete_library(library_id: str):
    if library_id == "":
        raise HTTPException(status_code=400, detail="默认库不能删除")
    if not storage.delete_library(library_id):
        raise HTTPException(status_code=404, detail="子库不存在")
    return {"status": "deleted"}


@app.put("/api/tasks/{task_id}/library")
def move_task_library(task_id: str, body: MoveLibraryBody):
    """把一次采集批次移入指定子库。"""
    if not storage.get(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    library_id = _ensure_library(body.library_id)
    _sync_documents()
    updated = storage.set_task_library(task_id, library_id)
    if not updated:
        raise HTTPException(status_code=404, detail="该采集没有可归档的文档")
    return {"status": "updated", "updated": updated}


@app.delete("/api/document-batches/{task_id}")
def delete_document_batch(task_id: str):
    """删除一次采集批次的所有文档。"""
    updated = storage.delete_task_documents(task_id)
    if not updated:
        raise HTTPException(status_code=404, detail="该采集没有可删除的文档")
    return {"status": "deleted", "deleted": updated}


class AutoClassifyBody(BaseModel):
    limit: int = 200


def _clean_document(body: DocumentBody) -> dict:
    """校验并规范化文档输入。"""
    title = body.title.strip()
    library_id = _ensure_library(body.library_id)
    try:
        fields = json.loads(body.fields_json or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"字段 JSON 解析失败：{e}") from e
    if not isinstance(fields, dict):
        raise HTTPException(status_code=400, detail="字段内容必须是 JSON 对象")
    return {
        "title": title or _derive_title(fields),
        "library_id": library_id,
        "fields_json": json.dumps(fields, ensure_ascii=False, indent=2),
        "source_url": body.source_url.strip(),
    }


class CategoryBody(BaseModel):
    name: str


def _category_name(body: CategoryBody) -> str:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
    if len(name) > 30:
        raise HTTPException(status_code=400, detail="分类名称不能超过 30 个字符")
    return name


@app.get("/api/categories")
def list_categories():
    return {"items": storage.list_categories()}


@app.post("/api/categories")
def create_category(body: CategoryBody):
    name = _category_name(body)
    try:
        return storage.create_category(name)
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail="分类名称已存在") from e


@app.put("/api/categories/{category_id}")
def update_category(category_id: str, body: CategoryBody):
    name = _category_name(body)
    try:
        ok = storage.update_category(category_id, name)
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail="分类名称已存在") from e
    if not ok:
        raise HTTPException(status_code=404, detail="分类不存在")
    return {"status": "updated"}


@app.delete("/api/categories/{category_id}")
def delete_category(category_id: str):
    if not storage.delete_category(category_id):
        raise HTTPException(status_code=404, detail="分类不存在")
    return {"status": "deleted"}


@app.post("/api/documents")
def create_document(body: DocumentBody):
    payload = _clean_document(body)
    return storage.create_document(**payload)


@app.put("/api/documents/{doc_id}")
def update_document(doc_id: str, body: DocumentBody):
    payload = _clean_document(body)
    if not storage.update_document(doc_id, **payload):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"status": "updated"}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    if not storage.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"status": "deleted"}


@app.post("/api/documents/auto-classify")
async def auto_classify_documents(body: Optional[AutoClassifyBody] = None):
    """用 LLM 给未分类文档自动归类（已分类的文档不会被改动）。"""
    _sync_documents()
    uncategorized = [d for d in storage.list_documents(limit=1000) if not d["category_id"]]
    if not uncategorized:
        return {
            "classified": 0,
            "remaining": 0,
            "created_categories": [],
            "llm_failed": 0,
            "note": "没有未分类的文档",
        }

    limit = max(1, min((body.limit if body else 200), 500))
    targets = uncategorized[:limit]
    classifier = DocumentClassifier(llm_client)
    try:
        result = await asyncio.to_thread(
            classifier.classify,
            targets,
            [c["name"] for c in storage.list_categories()],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("自动分类失败")
        raise HTTPException(status_code=503, detail=f"自动分类失败：{e}") from e

    assignments = result.get("assignments") or {}
    by_name = {c["name"].lower(): c for c in storage.list_categories()}
    created: list[str] = []
    classified = 0
    for doc_id, name in assignments.items():
        key = name.lower()
        category = by_name.get(key)
        if category is None:
            try:
                category = storage.create_category(name)
            except sqlite3.IntegrityError:
                by_name = {c["name"].lower(): c for c in storage.list_categories()}
                category = by_name.get(key)
                if category is None:
                    continue
            else:
                by_name[key] = category
                created.append(name)
        if storage.set_document_category(doc_id, category["id"]):
            classified += 1

    logger.info("自动分类完成：归类 %d 篇，新建分类 %d 个", classified, len(created))
    return {
        "classified": classified,
        "remaining": len(uncategorized) - classified,
        "created_categories": created,
        "llm_failed": result.get("llm_failed", 0),
        "note": "LLM 调用失败，已按来源任务回退归类" if result.get("llm_failed") else "",
    }


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
    logger.info(
        "定时任务 %s 已%s", schedule.id[:8], "启用" if schedule.enabled else "停用"
    )
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
    logger.info("手动触发定时任务 %s", schedule.id[:8])
    asyncio.create_task(_run_schedule_task(schedule.id, task.id))
    return {"task_id": task.id, "status": task.status}


# ---------------- 系统日志 ----------------------------


@app.get("/api/logs")
def list_logs(level: Optional[str] = None, q: Optional[str] = None, limit: int = 300):
    """返回最近的后端运行日志（按时间倒序，内存缓冲最多 800 条）。"""
    limit = max(1, min(limit, 800))
    entries = list(_LOG_BUFFER)
    counts = {name: 0 for name in _LOG_LEVELS}
    for entry in entries:
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1
    if level:
        entries = [e for e in entries if e["level"] == level.upper()]
    keyword = (q or "").strip().lower()
    if keyword:
        entries = [
            e
            for e in entries
            if keyword in e["message"].lower() or keyword in e["logger"].lower()
        ]
    total = len(entries)
    return {"items": list(reversed(entries))[:limit], "total": total, "counts": counts}


@app.delete("/api/logs")
def clear_logs():
    global _LOG_RECORDS
    _LOG_BUFFER.clear()
    _LOG_RECORDS = 0
    logger.info("系统日志已清空")
    return {"status": "cleared"}


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
