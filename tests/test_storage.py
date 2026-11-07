"""存储层单测：任务、保留策略、子库与文档归一化。

全部使用临时数据库，不触碰 data/crawler.db。
"""

from __future__ import annotations

import json

import pytest

from agent_crawler.models import Task
from agent_crawler.storage import Storage, normalize_fields


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "crawler.db"))
    yield s
    s.close()


def _make_task(
    desc: str = "测试任务",
    url: str = "https://example.com",
    status: str = "success",
    created: str = "2026-01-01T00:00:00",
    result: str = "[]",
) -> Task:
    return Task(
        task_desc=desc,
        start_url=url,
        schema_json='{"title":"string"}',
        status=status,
        created_at=created,
        result_json=result,
    )


class TestTasks:
    def test_save_get_list_delete(self, store):
        task = _make_task()
        store.save(task)

        got = store.get(task.id)
        assert got is not None
        assert got.task_desc == "测试任务"
        assert got.start_url == "https://example.com"

        assert [t.id for t in store.list()] == [task.id]
        assert store.list(status="failed") == []

        store.delete(task.id)
        assert store.get(task.id) is None

    def test_save_updates_existing(self, store):
        task = _make_task(status="running")
        store.save(task)
        task.status = "success"
        store.save(task)
        assert len(store.list()) == 1
        assert store.get(task.id).status == "success"

    def test_filter_by_status(self, store):
        ok = _make_task(desc="成功", created="2026-01-02T00:00:00")
        bad = _make_task(desc="失败", status="failed", created="2026-01-03T00:00:00")
        store.save(ok)
        store.save(bad)
        assert [t.id for t in store.list(status="success")] == [ok.id]


class TestRetention:
    def test_count_and_purge(self, store):
        old = _make_task(desc="旧任务", created="2025-01-01T00:00:00")
        store.save(old)
        store.insert_linked_document("d1", old.id, 0, "条目1", '{"title":"A"}', "2025-01-01T00:00:00")
        store.insert_linked_document("d2", old.id, 1, "条目2", '{"title":"B"}', "2025-01-01T00:00:00")
        store.mark_task_synced(old.id)

        pending = _make_task(desc="旧但未完成", status="pending", created="2025-01-01T00:00:00")
        running = _make_task(desc="旧但运行中", status="running", created="2025-01-01T00:00:00")
        fresh = _make_task(desc="新任务", created="2026-06-01T00:00:00")
        for t in (pending, running, fresh):
            store.save(t)

        store.create_document("手动文档", "", '{"title":"手"}', "https://manual")

        counts = store.count_expired("2026-01-01T00:00:00")
        assert counts == {"tasks": 1, "documents": 2}

        removed = store.purge_expired("2026-01-01T00:00:00")
        assert removed == {"tasks": 1, "documents": 2}

        # 旧任务及其文档、同步标记被清理，其余保留
        assert store.get(old.id) is None
        assert store.is_task_synced(old.id) is False
        assert {t.task_desc for t in store.list()} == {
            "旧但未完成",
            "旧但运行中",
            "新任务",
        }
        docs = store.list_documents()
        assert [d["title"] for d in docs] == ["手动文档"]

    def test_no_expired(self, store):
        store.save(_make_task(created="2026-06-01T00:00:00"))
        assert store.count_expired("2026-01-01T00:00:00") == {"tasks": 0, "documents": 0}
        assert store.purge_expired("2026-01-01T00:00:00") == {"tasks": 0, "documents": 0}


class TestLibraries:
    def test_default_library_first(self, store):
        libs = store.list_libraries()
        assert libs[0]["id"] == ""
        assert libs[0]["default"] is True
        assert libs[0]["doc_count"] == 0

    def test_create_update_delete(self, store):
        lib = store.create_library("财经")
        assert lib["name"] == "财经"
        assert lib["id"]

        assert store.update_library(lib["id"], "财经资讯") is True
        assert [x["name"] for x in store.list_libraries() if x["id"] == lib["id"]] == ["财经资讯"]

        assert store.delete_library(lib["id"]) is True
        assert [x["id"] for x in store.list_libraries()] == [""]

    def test_move_task_and_delete_library_returns_docs_to_default(self, store):
        task = _make_task(created="2026-01-01T00:00:00")
        store.save(task)
        store.insert_linked_document("d1", task.id, 0, "条目1", '{"title":"A"}', "2026-01-01T00:00:00")
        store.insert_linked_document("d2", task.id, 1, "条目2", '{"title":"B"}', "2026-01-01T00:00:00")

        lib = store.create_library("财经")
        assert store.set_task_library(task.id, lib["id"]) == 2

        libs = store.list_libraries()
        moved = next(x for x in libs if x["id"] == lib["id"])
        assert moved["doc_count"] == 2
        assert moved["task_count"] == 1
        assert any(d["library_id"] == lib["id"] for d in store.list_documents())

        store.delete_library(lib["id"])
        assert all(d["library_id"] == "" for d in store.list_documents())

    def test_set_document_library_and_delete_batch(self, store):
        task = _make_task(created="2026-01-01T00:00:00")
        store.save(task)
        store.insert_linked_document("d1", task.id, 0, "条目1", '{"title":"A"}', "2026-01-01T00:00:00")
        store.insert_linked_document("d2", task.id, 1, "条目2", '{"title":"B"}', "2026-01-01T00:00:00")

        lib = store.create_library("临时")
        assert store.set_document_library("d1", lib["id"]) is True
        assert store.set_document_library("missing", lib["id"]) is False

        assert store.delete_task_documents(task.id) == 2
        assert store.list_documents() == []
        # 同步标记不受批次删除影响，避免自动同步复活
        store.mark_task_synced(task.id)
        assert store.is_task_synced(task.id) is True


class TestDocuments:
    def test_normalize_fields_legacy(self):
        # 旧数据：JSON 解析失败，但可被 literal_eval 还原
        assert normalize_fields({"fields": "{'title': 'A', 'price': 19.9}"}) == {
            "title": "A",
            "price": 19.9,
        }
        # 旧数据：内部是标准 JSON 字符串
        assert normalize_fields({"fields": '{"title": "B"}'}) == {"title": "B"}
        # 正常结构原样返回
        normal = {"title": "C"}
        assert normalize_fields(normal) == normal
        # 非字典返回空
        assert normalize_fields(None) == {}
        assert normalize_fields("x") == {}

    def test_list_documents_normalizes_and_flags_manual(self, store):
        task = _make_task(created="2026-01-01T00:00:00")
        store.save(task)
        store.insert_linked_document(
            "d1", task.id, 0, "条目1", json.dumps({"fields": "{'title': 'A'}"}), "2026-01-01T00:00:00"
        )
        store.create_document("手动", "", '{"title":"手"}', "https://manual")

        docs = {d["title"]: d for d in store.list_documents()}
        assert docs["条目1"]["fields"] == {"title": "A"}
        assert docs["条目1"]["manual"] is False
        assert docs["条目1"]["task_desc"] == "测试任务"
        assert docs["手动"]["manual"] is True
        assert docs["手动"]["library_name"] == "默认库"

    def test_create_update_delete_document(self, store):
        lib = store.create_library("读书")
        created = store.create_document("旧标题", lib["id"], '{"title":"旧"}', "https://a")
        doc_id = created["id"]

        assert store.update_document(doc_id, "新标题", "", '{"title":"新"}', "https://b") is True
        doc = next(d for d in store.list_documents() if d["id"] == doc_id)
        assert doc["title"] == "新标题"
        assert doc["library_id"] == ""
        assert doc["fields"] == {"title": "新"}

        assert store.delete_document(doc_id) is True
        assert store.list_documents() == []

    def test_detach_task_documents_keeps_docs(self, store):
        task = _make_task(created="2026-01-01T00:00:00")
        store.save(task)
        store.insert_linked_document("d1", task.id, 0, "条目1", '{"title":"A"}', "2026-01-01T00:00:00")
        store.mark_task_synced(task.id)

        store.detach_task_documents(task.id)

        docs = store.list_documents()
        assert len(docs) == 1
        assert docs[0]["task_id"] == ""
        assert docs[0]["manual"] is True
        assert store.is_task_synced(task.id) is False
