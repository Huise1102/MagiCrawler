"""接口配方单测：候选识别、配方生成校验、确定性执行与存储。"""

from __future__ import annotations

import json

import pytest

from agent_crawler.browser import NetworkRecorder, scan_candidates
from agent_crawler.intent import Filter
from agent_crawler.recipe import (
    Recipe,
    RecipeError,
    build_recipe,
    execute_recipe,
    json_at,
    page_url,
    parse_recipe,
)
from agent_crawler.schema import parse_schema
from agent_crawler.storage import Storage


def _payload(start: int = 1, count: int = 6, price_base: int = 100) -> dict:
    """构造一个典型的列表接口返回。"""
    return {
        "code": 0,
        "data": {
            "list": [
                {
                    "skuName": f"书{start + i}",
                    "price": f"{price_base + i}.00",
                    "author": "某作者",
                    "link": f"https://example.com/item/{start + i}",
                }
                for i in range(count)
            ],
            "total": 300,
            "page": start,
        },
    }


class FakeLLM:
    """返回固定 JSON 的假模型。"""

    def __init__(self, data):
        self.data = data
        self.calls = 0

    def chat(self, messages, temperature=0, timeout=None):
        self.calls += 1
        return json.dumps(self.data, ensure_ascii=False)

    @staticmethod
    def extract_json(text):
        return json.loads(text)


class FakeTools:
    """按页返回预设 JSON 的假浏览器工具。"""

    def __init__(self, pages):
        self.pages = list(pages)
        self.urls: list[str] = []

    def request_json(self, url, method="GET", headers=None):
        self.urls.append(url)
        return self.pages.pop(0)


class TestCandidates:
    def test_scan_finds_list_interface(self):
        payload = _payload()
        found = scan_candidates(payload, "https://example.com/api/list?page=1")
        assert found
        top = found[0]
        assert top["items_path"] == "data.list"
        assert top["count"] == 6
        assert "skuName" in top["fields"]
        assert top["payload"] is payload

    def test_scan_ignores_short_arrays(self):
        payload = {"data": {"banners": [{"id": 1}, {"id": 2}]}}
        assert scan_candidates(payload) == []

    def test_scan_ignores_arrays_without_enough_fields(self):
        payload = {"data": {"ids": [{"id": 1}, {"id": 2}, {"id": 3}]}}
        assert scan_candidates(payload) == []

    def test_recorder_dedupes_same_interface(self):
        recorder = NetworkRecorder()
        recorder._records = [
            {"url": "https://example.com/api/list?page=1", "method": "GET", "payload": _payload(1)},
            {"url": "https://example.com/api/list?page=2", "method": "GET", "payload": _payload(7)},
        ]
        candidates = recorder.candidates()
        assert len(candidates) == 1
        assert candidates[0]["items_path"] == "data.list"


class TestJsonPath:
    def test_nested_and_index(self):
        data = {"data": {"list": [{"name": "A"}]}, "total": 1}
        assert json_at(data, "total") == 1
        assert json_at(data, "data.list.0.name") == "A"
        assert json_at(data, "data.list") == [{"name": "A"}]
        assert json_at(data, "") == data

    def test_missing_returns_none(self):
        assert json_at({"a": 1}, "b.c") is None
        assert json_at({"a": [1]}, "a.5") is None

    def test_page_url_replaces_and_appends(self):
        recipe = Recipe(
            domain="example.com",
            url="https://example.com/api/list?page=1&size=20",
            items_path="data.list",
            field_map={"title": "skuName"},
        )
        url = page_url(recipe, 3)
        assert "page=3" in url
        assert "size=20" in url

        templated = Recipe(
            domain="example.com",
            url="https://example.com/api/list/{page}",
            items_path="data.list",
            field_map={"title": "skuName"},
        )
        assert page_url(templated, 2).endswith("/2")


class TestParseRecipe:
    def test_valid_recipe(self):
        data = {
            "url": "https://example.com/api/list?page=1",
            "items_path": "data.list",
            "fields": {"title": "skuName", "price": "price", "unknown": "author"},
            "page_param": "page",
        }
        recipe = parse_recipe(data, "example.com", ["title", "price"], payload=_payload())
        assert recipe is not None
        assert recipe.field_map == {"title": "skuName", "price": "price"}
        assert recipe.max_pages == 5

    def test_invalid_items_path(self):
        data = {"url": "https://example.com/api/list", "items_path": "data.nope", "fields": {"title": "skuName"}}
        assert parse_recipe(data, "example.com", ["title"], payload=_payload()) is None

    def test_other_domain_rejected(self):
        data = {"url": "https://evil.com/api", "items_path": "data.list", "fields": {"title": "skuName"}}
        assert parse_recipe(data, "example.com", ["title"], payload=_payload()) is None

    def test_no_mapped_fields_rejected(self):
        data = {"url": "https://example.com/api/list", "items_path": "data.list", "fields": {"nope": "skuName"}}
        assert parse_recipe(data, "example.com", ["title"], payload=_payload()) is None

    def test_no_page_param_single_page(self):
        data = {"url": "https://example.com/api/list", "items_path": "data.list", "fields": {"title": "skuName"}, "page_param": ""}
        recipe = parse_recipe(data, "example.com", ["title"], payload=_payload())
        assert recipe is not None
        assert recipe.max_pages == 1

    def test_build_recipe_with_llm(self):
        llm = FakeLLM(
            {
                "url": "https://example.com/api/list?page=1",
                "items_path": "data.list",
                "fields": {"title": "skuName", "price": "price"},
                "page_param": "page",
                "page_start": 1,
                "page_step": 1,
            }
        )
        fields = parse_schema('{"title":"string","price":"number"}')
        candidate = scan_candidates(_payload(), "https://example.com/api/list?page=1")[0]
        recipe = build_recipe(llm, "example.com", candidate, fields)
        assert recipe is not None
        assert llm.calls == 1
        assert recipe.field_map["price"] == "price"

    def test_build_recipe_llm_failure_returns_none(self):
        class BrokenLLM:
            def chat(self, *args, **kwargs):
                raise RuntimeError("模型不可用")

        fields = parse_schema('{"title":"string"}')
        candidate = scan_candidates(_payload(), "https://example.com/api/list")[0]
        assert build_recipe(BrokenLLM(), "example.com", candidate, fields) is None

    def test_roundtrip(self):
        recipe = Recipe(
            domain="example.com",
            url="https://example.com/api/list",
            items_path="data.list",
            field_map={"title": "skuName"},
            page_param="page",
        )
        restored = Recipe.from_dict(json.loads(json.dumps(recipe.to_dict())))
        assert restored.domain == recipe.domain
        assert restored.field_map == recipe.field_map

    def test_from_dict_invalid(self):
        with pytest.raises(RecipeError):
            Recipe.from_dict({"domain": "example.com"})


class TestExecuteRecipe:
    def _recipe(self, max_pages: int = 3) -> Recipe:
        return Recipe(
            domain="example.com",
            url="https://example.com/api/list?page=1",
            items_path="data.list",
            field_map={"title": "skuName", "price": "price"},
            page_param="page",
            max_pages=max_pages,
        )

    def test_paginate_and_stop_at_target(self):
        tools = FakeTools([_payload(1, 6, 100), _payload(7, 6, 100)])
        fields = parse_schema('{"title":"string","price":"number"}')
        items, dropped, pages = execute_recipe(tools, self._recipe(), fields, target=8)
        assert len(items) == 8
        assert dropped == 0
        assert pages == 2
        assert "page=1" in tools.urls[0]
        assert "page=2" in tools.urls[1]
        assert items[0] == {"title": "书1", "price": 100.0}

    def test_filter_and_dropped(self):
        tools = FakeTools([_payload(1, 6, 90)])
        fields = parse_schema('{"title":"string","price":"number"}')
        filters = [Filter(field="price", op="gte", value=95.0, unit="元")]
        items, dropped, pages = execute_recipe(tools, self._recipe(1), fields, filters=filters)
        # 90..95 共 6 条，其中 >=95 保留 1 条（95.00）
        assert len(items) == 1
        assert dropped == 5
        assert pages == 1

    def test_stops_when_no_new_items(self):
        same = _payload(1, 6)
        tools = FakeTools([same, same])
        fields = parse_schema('{"title":"string","price":"number"}')
        items, _, pages = execute_recipe(tools, self._recipe(3), fields)
        assert len(items) == 6
        assert pages == 2

    def test_invalid_items_path_raises(self):
        tools = FakeTools([{"data": {"list": []}}])
        fields = parse_schema('{"title":"string"}')
        recipe = self._recipe(1)
        recipe.items_path = "data.nope"
        with pytest.raises(RecipeError):
            execute_recipe(tools, recipe, fields)


class TestRecipeStorage:
    def test_crud(self, tmp_path):
        store = Storage(str(tmp_path / "crawler.db"))
        try:
            recipe_id = store.save_recipe(
                "Example.com",
                {"url": "https://example.com/api/list", "items_path": "data.list"},
            )
            row = store.get_recipe_by_domain("example.com")
            assert row is not None
            assert row["id"] == recipe_id
            assert row["recipe"]["items_path"] == "data.list"

            # 同一域名更新而不是新增
            again = store.save_recipe(
                "example.com",
                {"url": "https://example.com/v2", "items_path": "list"},
                name="新配方",
            )
            assert again == recipe_id
            assert store.get_recipe_by_domain("example.com")["name"] == "新配方"
            assert len(store.list_recipes()) == 1

            store.touch_recipe(recipe_id)
            assert store.list_recipes()[0]["use_count"] == 1

            assert store.delete_recipe(recipe_id) is True
            assert store.get_recipe_by_domain("example.com") is None
        finally:
            store.close()
