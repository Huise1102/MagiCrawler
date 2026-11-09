"""SQLite 持久化层：任务、定时任务、文档、分类与结果的存取。"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
from typing import Any, Optional

from .models import Schedule, Site, Task, row_to_schedule, row_to_site, row_to_task

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

_DOCS_SQL = """
CREATE TABLE IF NOT EXISTS categories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    category_id TEXT NOT NULL DEFAULT '',
    library_id  TEXT NOT NULL DEFAULT '',
    task_id     TEXT NOT NULL DEFAULT '',
    doc_index   INTEGER NOT NULL DEFAULT 0,
    title       TEXT NOT NULL DEFAULT '',
    fields_json TEXT NOT NULL DEFAULT '{}',
    source_url  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_task
    ON documents(task_id, doc_index) WHERE task_id != '';
CREATE TABLE IF NOT EXISTS synced_tasks (
    task_id   TEXT PRIMARY KEY,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS libraries (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    domain        TEXT NOT NULL DEFAULT '',
    start_url     TEXT NOT NULL DEFAULT '',
    login_note    TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_login_at TEXT NOT NULL DEFAULT '',
    last_used_at  TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

_RECIPES_SQL = """
CREATE TABLE IF NOT EXISTS recipes (
    id           TEXT PRIMARY KEY,
    domain       TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    recipe_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    last_used_at TEXT NOT NULL DEFAULT '',
    use_count    INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_domain ON recipes(domain);
"""

# 旧库升级：字段名 -> 列定义
_TASKS_COLUMNS = {
    "schedule_id": "TEXT NOT NULL DEFAULT ''",
    "timings_json": "TEXT NOT NULL DEFAULT ''",
    "site_id": "TEXT NOT NULL DEFAULT ''",
    "max_items": "INTEGER NOT NULL DEFAULT 0",
    "max_pages": "INTEGER NOT NULL DEFAULT 0",
}
_SCHEDULES_COLUMNS = {
    "site_id": "TEXT NOT NULL DEFAULT ''",
    "max_items": "INTEGER NOT NULL DEFAULT 0",
    "max_pages": "INTEGER NOT NULL DEFAULT 0",
}
_DOC_COLUMNS = {
    "library_id": "TEXT NOT NULL DEFAULT ''",
}


def normalize_fields(fields: Any) -> dict:
    """展平历史遗留的 {"fields": "<dict 字符串>"} 结构。

    旧版 Schema 生成器把字段列表包成 {"fields": [...]}，导致采集结果被存成
    {"fields": "{'title': ...}"}，这里统一还原为真实字段字典。
    """
    if not isinstance(fields, dict):
        return {}
    inner = fields.get("fields")
    if len(fields) == 1 and isinstance(inner, str):
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(inner)
            except (ValueError, TypeError, SyntaxError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return fields


class Storage:
    """基于 SQLite 的存储层。"""

    def __init__(self, db_path: str = "data/crawler.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_TASKS_SQL)
        self._conn.execute(_SCHEDULES_SQL)
        self._conn.executescript(_DOCS_SQL)
        self._conn.executescript(_RECIPES_SQL)
        self._ensure_columns("tasks", _TASKS_COLUMNS)
        self._ensure_columns("schedules", _SCHEDULES_COLUMNS)
        self._ensure_columns("documents", _DOC_COLUMNS)
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
                               site_id, max_items, max_pages,
                               result_json, note, error, timings_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_desc=excluded.task_desc,
                start_url=excluded.start_url,
                schema_json=excluded.schema_json,
                status=excluded.status,
                schedule_id=excluded.schedule_id,
                site_id=excluded.site_id,
                max_items=excluded.max_items,
                max_pages=excluded.max_pages,
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
                task.site_id,
                task.max_items,
                task.max_pages,
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

    def count_expired(self, cutoff: str) -> dict[str, int]:
        """统计早于 cutoff、且已结束的任务与其文档数量（用于保留策略预览）。"""
        where = "created_at < ? AND status NOT IN ('pending', 'running')"
        tasks = self._conn.execute(
            f"SELECT COUNT(*) AS c FROM tasks WHERE {where}", (cutoff,)
        ).fetchone()["c"]
        docs = self._conn.execute(
            "SELECT COUNT(*) AS c FROM documents WHERE task_id IN"
            f" (SELECT id FROM tasks WHERE {where})",
            (cutoff,),
        ).fetchone()["c"]
        return {"tasks": tasks, "documents": docs}

    def purge_expired(self, cutoff: str) -> dict[str, int]:
        """删除过期任务及其同步文档（手动文档不受影响）。"""
        where = "created_at < ? AND status NOT IN ('pending', 'running')"
        counts = self.count_expired(cutoff)
        self._conn.execute(
            "DELETE FROM documents WHERE task_id IN"
            f" (SELECT id FROM tasks WHERE {where})",
            (cutoff,),
        )
        self._conn.execute(
            "DELETE FROM synced_tasks WHERE task_id IN"
            f" (SELECT id FROM tasks WHERE {where})",
            (cutoff,),
        )
        self._conn.execute(f"DELETE FROM tasks WHERE {where}", (cutoff,))
        self._conn.commit()
        return counts

    def detach_task_documents(self, task_id: str) -> None:
        """删除任务时保留其文档，解除文档与任务的关联。"""
        from .models import _now

        self._conn.execute(
            "UPDATE documents SET task_id = '', doc_index = 0, updated_at = ? WHERE task_id = ?",
            (_now(), task_id),
        )
        self._conn.execute("DELETE FROM synced_tasks WHERE task_id = ?", (task_id,))
        self._conn.commit()

    # ---------------- 定时任务 ----------------

    def save_schedule(self, schedule: Schedule) -> None:
        """插入或更新定时任务。"""
        from .models import _now

        schedule.updated_at = _now()
        row = schedule.to_db()
        self._conn.execute(
            """
            INSERT INTO schedules (id, task_desc, start_url, schema_json, site_id,
                                   max_items, max_pages, schedule_type,
                                   interval_minutes, daily_times, enabled, last_run_at,
                                   next_run_at, last_status, last_task_id, run_count,
                                   created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_desc=excluded.task_desc,
                start_url=excluded.start_url,
                schema_json=excluded.schema_json,
                site_id=excluded.site_id,
                max_items=excluded.max_items,
                max_pages=excluded.max_pages,
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
                row["site_id"],
                row["max_items"],
                row["max_pages"],
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

    # ---------------- 网站记忆 ----------------

    def save_site(self, site: Site) -> None:
        """插入或更新网站记忆。"""
        from .models import _now

        site.updated_at = _now()
        row = site.to_db()
        self._conn.execute(
            """
            INSERT INTO sites (id, name, domain, start_url, login_note, enabled,
                               last_login_at, last_used_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                domain=excluded.domain,
                start_url=excluded.start_url,
                login_note=excluded.login_note,
                enabled=excluded.enabled,
                last_login_at=excluded.last_login_at,
                last_used_at=excluded.last_used_at,
                updated_at=excluded.updated_at
            """,
            (
                row["id"],
                row["name"],
                row["domain"],
                row["start_url"],
                row["login_note"],
                row["enabled"],
                row["last_login_at"],
                row["last_used_at"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        self._conn.commit()

    def get_site(self, site_id: str) -> Optional[Site]:
        row = self._conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        return row_to_site(dict(row)) if row else None

    def list_sites(self) -> list[Site]:
        rows = self._conn.execute(
            "SELECT * FROM sites ORDER BY created_at DESC"
        ).fetchall()
        return [row_to_site(dict(r)) for r in rows]

    def delete_site(self, site_id: str) -> None:
        self._conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        self._conn.commit()

    # ---------------- 采集配方 ----------------

    @staticmethod
    def _recipe_row(row: dict) -> dict[str, Any]:
        """把数据库行还原为接口结构（recipe 为真实字典）。"""
        try:
            recipe = json.loads(row.get("recipe_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            recipe = {}
        return {
            "id": row["id"],
            "domain": row["domain"],
            "name": row.get("name") or "",
            "recipe": recipe if isinstance(recipe, dict) else {},
            "created_at": row.get("created_at") or "",
            "updated_at": row.get("updated_at") or "",
            "last_used_at": row.get("last_used_at") or "",
            "use_count": int(row.get("use_count") or 0),
        }

    def save_recipe(self, domain: str, recipe: dict, name: str = "") -> str:
        """按域名保存配方（同一域名只保留一份），返回配方 id。"""
        from .models import _new_id, _now

        domain = (domain or "").strip().lower()
        if not domain:
            raise ValueError("配方缺少域名")
        now = _now()
        row = self._conn.execute(
            "SELECT id FROM recipes WHERE domain = ?", (domain,)
        ).fetchone()
        payload = json.dumps(recipe, ensure_ascii=False)
        if row:
            recipe_id = row["id"]
            self._conn.execute(
                "UPDATE recipes SET recipe_json = ?, name = ?, updated_at = ? WHERE id = ?",
                (payload, name, now, recipe_id),
            )
        else:
            recipe_id = _new_id()
            self._conn.execute(
                """
                INSERT INTO recipes (id, domain, name, recipe_json, created_at,
                                     updated_at, last_used_at, use_count)
                VALUES (?, ?, ?, ?, ?, ?, '', 0)
                """,
                (recipe_id, domain, name, payload, now, now),
            )
        self._conn.commit()
        return recipe_id

    def get_recipe_by_domain(self, domain: str) -> Optional[dict[str, Any]]:
        domain = (domain or "").strip().lower()
        if not domain:
            return None
        row = self._conn.execute(
            "SELECT * FROM recipes WHERE domain = ?", (domain,)
        ).fetchone()
        return self._recipe_row(dict(row)) if row else None

    def list_recipes(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM recipes ORDER BY updated_at DESC"
        ).fetchall()
        return [self._recipe_row(dict(r)) for r in rows]

    def touch_recipe(self, recipe_id: str) -> None:
        """记录配方命中：更新最近使用时间与使用次数。"""
        from .models import _now

        self._conn.execute(
            "UPDATE recipes SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
            (_now(), recipe_id),
        )
        self._conn.commit()

    def delete_recipe(self, recipe_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ---------------- 分类 ----------------

    def list_categories(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT c.id, c.name, c.created_at,
                   (SELECT COUNT(*) FROM documents d WHERE d.category_id = c.id) AS count
            FROM categories c
            ORDER BY c.created_at, c.name
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def create_category(self, name: str) -> dict[str, Any]:
        from .models import _new_id, _now

        cid = _new_id()
        self._conn.execute(
            "INSERT INTO categories (id, name, created_at) VALUES (?, ?, ?)",
            (cid, name, _now()),
        )
        self._conn.commit()
        return {"id": cid, "name": name, "count": 0}

    def update_category(self, category_id: str, name: str) -> bool:
        cur = self._conn.execute(
            "UPDATE categories SET name = ? WHERE id = ?", (name, category_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_category(self, category_id: str) -> bool:
        """删除分类，其下文档归为未分类。"""
        self._conn.execute(
            "UPDATE documents SET category_id = '' WHERE category_id = ?", (category_id,)
        )
        cur = self._conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ---------------- 子库 ----------------

    def list_libraries(self) -> list[dict[str, Any]]:
        """子库列表；id 为空的「默认库」始终排在最前。"""
        rows = self._conn.execute(
            """
            SELECT l.id, l.name, l.created_at,
                   (SELECT COUNT(*) FROM documents d WHERE d.library_id = l.id) AS doc_count,
                   (SELECT COUNT(DISTINCT d.task_id) FROM documents d
                     WHERE d.library_id = l.id AND d.task_id != '') AS task_count
            FROM libraries l
            ORDER BY l.created_at, l.name
            """
        ).fetchall()
        default_docs = self._conn.execute(
            "SELECT COUNT(*) AS c FROM documents WHERE library_id = ''"
        ).fetchone()["c"]
        default_tasks = self._conn.execute(
            "SELECT COUNT(DISTINCT task_id) AS c FROM documents"
            " WHERE library_id = '' AND task_id != ''"
        ).fetchone()["c"]
        items = [
            {
                "id": "",
                "name": "默认库",
                "created_at": "",
                "doc_count": default_docs,
                "task_count": default_tasks,
                "default": True,
            }
        ]
        items.extend(dict(r) for r in rows)
        return items

    def create_library(self, name: str) -> dict[str, Any]:
        from .models import _new_id, _now

        lid = _new_id()
        self._conn.execute(
            "INSERT INTO libraries (id, name, created_at) VALUES (?, ?, ?)",
            (lid, name, _now()),
        )
        self._conn.commit()
        return {"id": lid, "name": name}

    def update_library(self, library_id: str, name: str) -> bool:
        cur = self._conn.execute(
            "UPDATE libraries SET name = ? WHERE id = ?", (name, library_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_library(self, library_id: str) -> bool:
        """删除子库，其下文档回到默认库。"""
        from .models import _now

        self._conn.execute(
            "UPDATE documents SET library_id = '', updated_at = ? WHERE library_id = ?",
            (_now(), library_id),
        )
        cur = self._conn.execute("DELETE FROM libraries WHERE id = ?", (library_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def set_task_library(self, task_id: str, library_id: str) -> int:
        """把一次采集产生的全部文档移入指定子库。"""
        from .models import _now

        cur = self._conn.execute(
            "UPDATE documents SET library_id = ?, updated_at = ? WHERE task_id = ?",
            (library_id, _now(), task_id),
        )
        self._conn.commit()
        return cur.rowcount

    def delete_task_documents(self, task_id: str) -> int:
        """删除一次采集批次下的所有文档（保留同步标记，避免自动复活）。"""
        cur = self._conn.execute("DELETE FROM documents WHERE task_id = ?", (task_id,))
        self._conn.commit()
        return cur.rowcount

    def set_document_library(self, doc_id: str, library_id: str) -> bool:
        from .models import _now

        cur = self._conn.execute(
            "UPDATE documents SET library_id = ?, updated_at = ? WHERE id = ?",
            (library_id, _now(), doc_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ---------------- 文档 ----------------

    def is_task_synced(self, task_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM synced_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return row is not None

    def mark_task_synced(self, task_id: str) -> None:
        from .models import _now

        self._conn.execute(
            "INSERT OR IGNORE INTO synced_tasks (task_id, synced_at) VALUES (?, ?)",
            (task_id, _now()),
        )
        self._conn.commit()

    def insert_linked_document(
        self,
        doc_id: str,
        task_id: str,
        doc_index: int,
        title: str,
        fields_json: str,
        created_at: str,
    ) -> None:
        """写入爬取产生的文档（已存在则跳过）。"""
        from .models import _now

        self._conn.execute(
            """
            INSERT OR IGNORE INTO documents
                (id, category_id, library_id, task_id, doc_index, title, fields_json,
                 source_url, created_at, updated_at)
            VALUES (?, '', '', ?, ?, ?, ?, '', ?, ?)
            """,
            (doc_id, task_id, doc_index, title, fields_json, created_at, _now()),
        )
        self._conn.commit()

    def create_document(
        self, title: str, library_id: str, fields_json: str, source_url: str
    ) -> dict[str, Any]:
        from .models import _new_id, _now

        did = _new_id()
        now = _now()
        self._conn.execute(
            """
            INSERT INTO documents
                (id, category_id, library_id, task_id, doc_index, title, fields_json,
                 source_url, created_at, updated_at)
            VALUES (?, '', ?, '', 0, ?, ?, ?, ?, ?)
            """,
            (did, library_id, title, fields_json, source_url, now, now),
        )
        self._conn.commit()
        return {"id": did}

    def update_document(
        self,
        doc_id: str,
        title: str,
        library_id: str,
        fields_json: str,
        source_url: str,
    ) -> bool:
        from .models import _now

        cur = self._conn.execute(
            """
            UPDATE documents
               SET title = ?, library_id = ?, fields_json = ?, source_url = ?, updated_at = ?
             WHERE id = ?
            """,
            (title, library_id, fields_json, source_url, _now(), doc_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_document(self, doc_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def set_document_category(self, doc_id: str, category_id: str) -> bool:
        """只更新文档所属分类（自动分类使用，避免覆盖其它字段）。"""
        from .models import _now

        cur = self._conn.execute(
            "UPDATE documents SET category_id = ?, updated_at = ? WHERE id = ?",
            (category_id, _now(), doc_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_documents(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT d.*, COALESCE(l.name, '默认库') AS library_name,
                   t.task_desc AS task_desc
            FROM documents d
            LEFT JOIN libraries l ON l.id = d.library_id
            LEFT JOIN tasks t ON t.id = d.task_id
            ORDER BY d.created_at DESC, d.rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        docs: list[dict[str, Any]] = []
        for row in rows:
            doc = dict(row)
            raw = doc.pop("fields_json", "") or "{}"
            try:
                doc["fields"] = normalize_fields(json.loads(raw))
            except json.JSONDecodeError:
                doc["fields"] = {}
            doc["manual"] = not doc["task_id"]
            docs.append(doc)
        return docs

    def close(self) -> None:
        self._conn.close()
