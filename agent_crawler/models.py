"""任务与结果数据模型。"""

from __future__ import annotations

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
    result_json: str = ""
    note: str = ""
    error: str = ""
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
        result_json=row["result_json"] or "",
        note=row["note"] or "",
        error=row["error"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )