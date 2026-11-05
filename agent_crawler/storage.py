"""SQLite 持久化层：任务、定时任务与结果的存取。"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Optional

from .models import Schedule, Task, row_to_schedule, row_to_task

_TASKS_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    task_desc    TEXT NOT NULL,
    start_url    TEXT NOT NULL,
    schema_json  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    schedule_id  TEXT NOT NULL DEFAULT '',
    result_json  TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    timings_json TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

_SCHEDULES_SQL = """
CREATE TABLE IF NOT EXISTS schedules (
    id               TEXT PRIMARY KEY,
    task_desc        TEXT NOT NULL,
    start_url        TEXT NOT NULL,
    schema_json      TEXT NOT NULL,
    schedule_type    TEXT NOT NULL DEFAULT 'interval',
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    daily_times      TEXT NOT NULL DEFAULT '["09:00"]',
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_run_at      TEXT NOT NULL DEFAULT '',
    next_run_at      TEXT NOT NULL DEFAULT '',
    last_status      TEXT NOT NULL DEFAULT '',
    last_task_id     TEXT NOT NULL DEFAULT '',
    run_count        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""

# 旧库升级：字段名 -> 列定义
_TASKS_COLUMNS = {
    "schedule_id": "TEXT NOT NULL DEFAULT ''",
    "timings_json": "TEXT NOT NULL DEFAULT ''",
}


class Storage:
    """基于 SQLite 的存储层。"""

    def __init__(self, db_path: str = "data/crawler.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_TASKS_SQL)
        self._conn.execute(_SCHEDULES_SQL)
        self._ensure_columns("tasks", _TASKS_COLUMNS)
        self._conn.commit()

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        """为旧表补齐新增列。"""
        existing = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    # ---------------- 任务 ----------------

    def save(self, task: Task) -> None:
        """插入或更新任务。"""
        from .models import _now

        task.updated_at = _now()
        self._conn.execute(
            """
            INSERT INTO tasks (id, task_desc, start_url, schema_json, status, schedule_id,
                               result_json, note, error, timings_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_desc=excluded.task_desc,
                start_url=excluded.start_url,
                schema_json=excluded.schema_json,
                status=excluded.status,
                schedule_id=excluded.schedule_id,
                result_json=excluded.result_json,
                note=excluded.note,
                error=excluded.error,
                timings_json=excluded.timings_json,
                updated_at=excluded.updated_at
            """,
            (
                task.id,
                task.task_desc,
                task.start_url,
                task.schema_json,
                task.status,
                task.schedule_id,
                task.result_json,
                task.note,
                task.error,
                task.timings_json,
                task.created_at,
                task.updated_at,
            ),
        )
        self._conn.commit()

    def get(self, task_id: str) -> Optional[Task]:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_task(dict(row)) if row else None

    def list(self, status: Optional[str] = None, limit: int = 100) -> list[Task]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [row_to_task(dict(r)) for r in rows]

    def delete(self, task_id: str) -> None:
        self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._conn.commit()

    # ---------------- 定时任务 ----------------

    def save_schedule(self, schedule: Schedule) -> None:
        """插入或更新定时任务。"""
        from .models import _now

        schedule.updated_at = _now()
        row = schedule.to_db()
        self._conn.execute(
            """
            INSERT INTO schedules (id, task_desc, start_url, schema_json, schedule_type,
                                   interval_minutes, daily_times, enabled, last_run_at,
                                   next_run_at, last_status, last_task_id, run_count,
                                   created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_desc=excluded.task_desc,
                start_url=excluded.start_url,
                schema_json=excluded.schema_json,
                schedule_type=excluded.schedule_type,
                interval_minutes=excluded.interval_minutes,
                daily_times=excluded.daily_times,
                enabled=excluded.enabled,
                last_run_at=excluded.last_run_at,
                next_run_at=excluded.next_run_at,
                last_status=excluded.last_status,
                last_task_id=excluded.last_task_id,
                run_count=excluded.run_count,
                updated_at=excluded.updated_at
            """,
            (
                row["id"],
                row["task_desc"],
                row["start_url"],
                row["schema_json"],
                row["schedule_type"],
                row["interval_minutes"],
                row["daily_times"],
                row["enabled"],
                row["last_run_at"],
                row["next_run_at"],
                row["last_status"],
                row["last_task_id"],
                row["run_count"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        self._conn.commit()

    def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        row = self._conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        return row_to_schedule(dict(row)) if row else None

    def list_schedules(self) -> list[Schedule]:
        rows = self._conn.execute(
            "SELECT * FROM schedules ORDER BY created_at DESC"
        ).fetchall()
        return [row_to_schedule(dict(r)) for r in rows]

    def delete_schedule(self, schedule_id: str) -> None:
        self._conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
