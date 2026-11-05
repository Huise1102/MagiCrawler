"""SQLite 持久化层：任务与结果的存取。"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from .models import Task, row_to_task

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    task_desc   TEXT NOT NULL,
    start_url   TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


class Storage:
    """基于 SQLite 的任务存储。"""

    def __init__(self, db_path: str = "data/crawler.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA_SQL)
        self._conn.commit()

    # ---------------- 写入 ----------------

    def save(self, task: Task) -> None:
        """插入或更新任务。"""
        from .models import _now

        task.updated_at = _now()
        self._conn.execute(
            """
            INSERT INTO tasks (id, task_desc, start_url, schema_json, status,
                               result_json, note, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_desc=excluded.task_desc,
                start_url=excluded.start_url,
                schema_json=excluded.schema_json,
                status=excluded.status,
                result_json=excluded.result_json,
                note=excluded.note,
                error=excluded.error,
                updated_at=excluded.updated_at
            """,
            (
                task.id,
                task.task_desc,
                task.start_url,
                task.schema_json,
                task.status,
                task.result_json,
                task.note,
                task.error,
                task.created_at,
                task.updated_at,
            ),
        )
        self._conn.commit()

    # ---------------- 读取 ----------------

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

    def close(self) -> None:
        self._conn.close()