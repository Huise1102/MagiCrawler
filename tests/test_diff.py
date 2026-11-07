"""历史结果对比与检索辅助逻辑单测。

导入 backend 前把 DB_PATH 指向临时目录，避免读写真实 data/crawler.db。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="crawler-test-"))
os.environ["DB_PATH"] = str(_TMP_DIR / "crawler.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend  # noqa: E402
from agent_crawler.models import Task  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _save(desc: str, url: str, created: str, items: list, status: str = "success") -> Task:
    task = Task(
        task_desc=desc,
        start_url=url,
        schema_json='{"title":"string","price":"number"}',
        status=status,
        created_at=created,
        result_json=json.dumps(items, ensure_ascii=False),
    )
    backend.storage.save(task)
    return task


@pytest.fixture(autouse=True)
def _isolated_db():
    # 每个测试使用不同网址，避免相互干扰；这里额外断言确实指向临时库
    assert backend.storage.db_path.startswith(str(_TMP_DIR))
    yield


class TestHelpers:
    def test_normalize_items_flattens_legacy(self):
        items = backend._normalize_items([{"fields": "{'title': 'A'}"}, {"title": "B"}, "oops"])
        assert items == [{"title": "A"}, {"title": "B"}]

    def test_item_key_prefers_unique_fields(self):
        assert backend._item_key({"link": "https://a/1", "title": "A"}) == "link:https://a/1"
        assert backend._item_key({"url": "https://a/2"}) == "url:https://a/2"
        assert backend._item_key({"title": "书名"}) == "title:书名"

    def test_item_key_falls_back_to_hash(self):
        key1 = backend._item_key({"author": "张三", "pages": 300})
        key2 = backend._item_key({"pages": 300, "author": "张三"})
        assert key1 == key2
        assert key1.startswith("raw:")

    def test_item_label(self):
        assert backend._item_label({"title": "标题值"}) == "标题值"
        assert backend._item_label({"author": "张三"}) == "张三"
        # 无标准标题字段时退回链接
        assert backend._item_label({"link": "https://a"}) == "https://a"
        # 完全没有可用字段时给出占位名
        assert backend._item_label({"note": None}) == "未命名条目"
        assert len(backend._item_label({"title": "x" * 100})) == 80


class TestTaskDiff:
    def test_missing_task_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            backend.task_diff("not-exists")
        assert exc.value.status_code == 404

    def test_no_previous_returns_not_comparable(self):
        task = _save("首次采集", "https://unique-none.example/a", "2026-02-01T00:00:00", [{"title": "A"}])
        result = backend.task_diff(task.id)
        assert result["comparable"] is False
        assert result["previous"] is None

    def test_diff_added_removed_changed_unchanged(self):
        url = "https://unique-diff.example/list"
        prev = _save(
            "上一次",
            url,
            "2026-01-01T00:00:00",
            [
                {"title": "A", "price": 10},
                {"title": "B", "price": 20},
                {"title": "D", "price": 30},
            ],
        )
        cur = _save(
            "这一次",
            url,
            "2026-02-01T00:00:00",
            [
                {"title": "A", "price": 10},
                {"title": "C", "price": 40},
                {"title": "D", "price": 35},
            ],
        )

        result = backend.task_diff(cur.id)
        assert result["comparable"] is True
        assert result["previous"]["id"] == prev.id
        assert result["counts"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 1}
        assert [x["label"] for x in result["added"]] == ["C"]
        assert [x["label"] for x in result["removed"]] == ["B"]
        assert result["changed"][0]["label"] == "D"
        assert result["changed"][0]["changes"] == [{"field": "price", "before": 30, "after": 35}]

    def test_diff_ignores_previous_failed_task(self):
        url = "https://unique-failed.example/list"
        _save("失败记录", url, "2026-01-01T00:00:00", [{"title": "A"}], status="failed")
        task = _save("成功记录", url, "2026-02-01T00:00:00", [{"title": "A"}])
        assert backend.task_diff(task.id)["comparable"] is False
