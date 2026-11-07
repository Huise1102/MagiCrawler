"""任务、定时任务与结果数据模型。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

# 任务状态
TASK_STATUS = {
    "PENDING": "pending",
    "RUNNING": "running",
    "SUCCESS": "success",
    "FAILED": "failed",
    "PARTIAL": "partial",
}

# 状态对应语义色（前端渲染用）
STATUS_COLORS = {
    "pending": "gray",
    "running": "blue",
    "success": "green",
    "failed": "red",
    "partial": "orange",
}

# 调度方式
SCHEDULE_TYPES = ("interval", "daily")


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Task:
    """一个爬取任务的完整描述。"""

    task_desc: str
    start_url: str
    schema_json: str
    status: str = TASK_STATUS["PENDING"]
    id: str = field(default_factory=_new_id)
    schedule_id: str = ""  # 由定时任务触发时记录来源
    site_id: str = ""  # 绑定的网站记忆（复用登录态）
    max_items: int = 0  # 最多采集条数，0 表示不限制
    max_pages: int = 0  # 最多翻页数，0 表示不限制
    result_json: str = ""
    note: str = ""
    error: str = ""
    timings_json: str = ""  # 各阶段耗时（JSON）
    trajectory: list = field(default_factory=list)  # 每步轨迹（不落库，仅内存）
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_row(self) -> dict[str, Any]:
        """转为可存储的字典（轨迹不持久化）。"""
        d = asdict(self)
        d.pop("trajectory", None)
        return d


def row_to_task(row: dict) -> Task:
    """从存储行恢复 Task（轨迹为空）。"""
    return Task(
        id=row["id"],
        task_desc=row["task_desc"],
        start_url=row["start_url"],
        schema_json=row["schema_json"],
        status=row["status"],
        schedule_id=row.get("schedule_id") or "",
        site_id=row.get("site_id") or "",
        max_items=int(row.get("max_items") or 0),
        max_pages=int(row.get("max_pages") or 0),
        result_json=row["result_json"] or "",
        note=row["note"] or "",
        error=row["error"] or "",
        timings_json=row.get("timings_json") or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@dataclass
class Schedule:
    """定时爬取配置：网址 + 需求 + 调度策略 + 启停开关。"""

    task_desc: str
    start_url: str
    schema_json: str
    site_id: str = ""  # 绑定的网站记忆（复用登录态）
    max_items: int = 0  # 最多采集条数，0 表示不限制
    max_pages: int = 0  # 最多翻页数，0 表示不限制
    schedule_type: str = "interval"  # interval | daily
    interval_minutes: int = 60
    daily_times: list[str] = field(default_factory=lambda: ["09:00"])
    enabled: bool = True
    id: str = field(default_factory=_new_id)
    last_run_at: str = ""
    next_run_at: str = ""
    last_status: str = ""
    last_task_id: str = ""
    run_count: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_row(self) -> dict[str, Any]:
        """转为 API / 前端使用的字典。"""
        return asdict(self)

    def to_db(self) -> dict[str, Any]:
        """转为数据库行（布尔与列表需编码）。"""
        d = asdict(self)
        d["enabled"] = 1 if self.enabled else 0
        d["daily_times"] = json.dumps(self.daily_times, ensure_ascii=False)
        return d


def row_to_schedule(row: dict) -> Schedule:
    """从存储行恢复 Schedule。"""
    raw_times = row.get("daily_times") or "09:00"
    try:
        daily_times = json.loads(raw_times)
        if not isinstance(daily_times, list):
            raise ValueError
    except (ValueError, TypeError):
        daily_times = [x.strip() for x in str(raw_times).split(",") if x.strip()] or ["09:00"]

    return Schedule(
        id=row["id"],
        task_desc=row["task_desc"],
        start_url=row["start_url"],
        schema_json=row["schema_json"],
        site_id=row.get("site_id") or "",
        max_items=int(row.get("max_items") or 0),
        max_pages=int(row.get("max_pages") or 0),
        schedule_type=row.get("schedule_type") or "interval",
        interval_minutes=int(row.get("interval_minutes") or 60),
        daily_times=[str(t) for t in daily_times],
        enabled=bool(row.get("enabled")),
        last_run_at=row.get("last_run_at") or "",
        next_run_at=row.get("next_run_at") or "",
        last_status=row.get("last_status") or "",
        last_task_id=row.get("last_task_id") or "",
        run_count=int(row.get("run_count") or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@dataclass
class Site:
    """网站记忆：长期采集的站点，保存域名、登录说明与登录态目录。"""

    name: str
    domain: str = ""
    start_url: str = ""
    login_note: str = ""
    enabled: bool = True
    id: str = field(default_factory=_new_id)
    last_login_at: str = ""
    last_used_at: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_row(self) -> dict[str, Any]:
        """转为 API / 前端使用的字典。"""
        return asdict(self)

    def to_db(self) -> dict[str, Any]:
        """转为数据库行（布尔需编码）。"""
        d = asdict(self)
        d["enabled"] = 1 if self.enabled else 0
        return d


def row_to_site(row: dict) -> Site:
    """从存储行恢复 Site。"""
    return Site(
        id=row["id"],
        name=row["name"],
        domain=row.get("domain") or "",
        start_url=row.get("start_url") or "",
        login_note=row.get("login_note") or "",
        enabled=bool(row.get("enabled")),
        last_login_at=row.get("last_login_at") or "",
        last_used_at=row.get("last_used_at") or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
