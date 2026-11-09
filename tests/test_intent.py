"""意图识别与确定性筛选的单元测试。"""

from __future__ import annotations

import json

from agent_crawler.intent import (
    Filter,
    apply_filters,
    describe_filters,
    filters_to_payload,
    matches,
    parse_filters,
)
from agent_crawler.agent_prompt import build_system_prompt


FIELDS = ["title", "price", "shop", "rating"]


def test_parse_filters_numeric():
    raw = [{"field": "price", "op": "<=", "value": "50", "unit": "元"}]
    filters = parse_filters(raw, FIELDS)
    assert len(filters) == 1
    assert filters[0].field == "price"
    assert filters[0].op == "lte"
    assert filters[0].value == 50.0
    assert filters[0].label() == "price ≤ 50元"


def test_parse_filters_ignores_unknown_field_and_op():
    raw = [
        {"field": "sales", "op": "gte", "value": 100},  # 字段不在 Schema 内
        {"field": "price", "op": "approx", "value": 10},  # 非法运算符
        {"field": "price", "op": "lte", "value": "未知"},  # 无法转数字
        {"field": "price", "op": "lte", "value": 50},
        {"field": "price", "op": "lte", "value": 50},  # 重复项
    ]
    filters = parse_filters(raw, FIELDS)
    assert len(filters) == 1
    assert filters[0].op == "lte"


def test_parse_filters_text_condition():
    filters = parse_filters([{"field": "shop", "op": "contains", "value": "自营"}], FIELDS)
    assert len(filters) == 1
    assert filters[0].label() == "shop 包含 自营"


def test_matches_numeric_variants():
    flt = Filter("price", "lte", 50.0)
    assert matches({"price": 25.93}, flt)
    assert matches({"price": "¥49.90"}, flt)
    assert matches({"price": "1,234"}, Filter("price", "gte", 1000))
    assert not matches({"price": 75.6}, flt)
    # 区间价取上限，避免「60-80 元」被误判为 ≤50
    assert not matches({"price": "60.00-80.00"}, flt)
    # 缺失或无法解析一律视为不满足
    assert not matches({"price": None}, flt)
    assert not matches({"price": "暂无报价"}, flt)
    assert not matches({}, flt)


def test_matches_text_variants():
    assert matches({"shop": "京东自营旗舰店"}, Filter("shop", "contains", "自营"))
    assert not matches({"shop": "第三方专营店"}, Filter("shop", "contains", "自营"))
    assert matches({"shop": "自营"}, Filter("shop", "eq", "自营"))


def test_apply_filters_counts_dropped():
    items = [
        {"title": "A", "price": 25.93},
        {"title": "B", "price": 75.6},
        {"title": "C", "price": 166},
        {"title": "D", "price": "49.5"},
        {"title": "E"},
    ]
    kept, dropped = apply_filters(items, [Filter("price", "lte", 50.0)])
    assert [item["title"] for item in kept] == ["A", "D"]
    assert dropped == 3


def test_apply_filters_no_condition_is_passthrough():
    items = [{"title": "A", "price": 75.6}]
    kept, dropped = apply_filters(items, [])
    assert kept is items
    assert dropped == 0


def test_apply_filters_requires_all_conditions():
    items = [
        {"title": "A", "price": 30, "shop": "京东自营"},
        {"title": "B", "price": 30, "shop": "第三方"},
    ]
    filters = [Filter("price", "lte", 50.0), Filter("shop", "contains", "自营")]
    kept, dropped = apply_filters(items, filters)
    assert [item["title"] for item in kept] == ["A"]
    assert dropped == 1


def test_describe_and_payload():
    filters = [Filter("price", "lte", 50.0, "元"), Filter("rating", "gte", 4.5)]
    assert describe_filters(filters) == "price ≤ 50元、rating ≥ 4.5"
    payload = filters_to_payload(filters)
    assert payload[0]["label"] == "price ≤ 50元"
    assert json.dumps(payload, ensure_ascii=False)  # 可序列化


def test_payload_uses_display_name():
    filters = [Filter("price", "lte", 50.0, "元")]
    payload = filters_to_payload(filters, {"price": "价格"})
    assert payload[0]["label"] == "价格 ≤ 50元"
    assert payload[0]["field"] == "price"
    assert describe_filters(filters, {"price": "价格"}) == "价格 ≤ 50元"


def test_system_prompt_contains_constraints():
    prompt = build_system_prompt("爬取 50 元以内的书", "https://example.com", '- "price"（类型：number）', "price ≤ 50元")
    assert "硬性筛选条件：price ≤ 50元" in prompt
    assert "不满足条件的条目不得提交" in prompt
