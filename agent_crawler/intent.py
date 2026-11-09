"""意图识别与确定性筛选。

流程：LLM 把目标描述里的筛选条件解析为结构化约束（Filter），
再由 Python 在收集阶段逐条判定，避免把「是否满足阈值」交给模型。

约束只支持可判定的形式：
- 数值比较：lte / lt / gte / gt（价格、评分、销量等）
- 文本比较：eq / ne / contains（如「只看自营」）

无法解析或字段不存在时一律忽略该条件，退化为不筛选，避免误删全部结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import _to_number

_NUMERIC_OPS = {"lte", "lt", "gte", "gt"}
_TEXT_OPS = {"eq", "ne", "contains"}
_OPS = _NUMERIC_OPS | _TEXT_OPS

# 模型可能输出的写法别名
_OP_ALIAS = {
    "lte": "lte", "<=": "lte", "≤": "lte", "le": "lte", "max": "lte",
    "lt": "lt", "<": "lt",
    "gte": "gte", ">=": "gte", "≥": "gte", "ge": "gte", "min": "gte",
    "gt": "gt", ">": "gt",
    "eq": "eq", "=": "eq", "==": "eq",
    "ne": "ne", "!=": "ne", "<>": "ne",
    "contains": "contains", "include": "contains", "in": "contains",
}

_OP_TEXT = {"lte": "≤", "lt": "<", "gte": "≥", "gt": ">", "eq": "=", "ne": "≠", "contains": "包含"}

# 价格区间写法：¥20.00-30.00 / 20~30 / 20至30
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|~|—|–|至)\s*(\d+(?:\.\d+)?)")


@dataclass
class Filter:
    """一条可判定的筛选条件。"""

    field: str
    op: str
    value: Any
    unit: str = ""

    def label(self, name: str | None = None) -> str:
        """人类可读的条件文案，用于界面展示与提示词。"""
        field = name or self.field
        if self.op in _NUMERIC_OPS:
            num = float(self.value)
            shown = int(num) if num.is_integer() else round(num, 2)
            return f"{field} {_OP_TEXT[self.op]} {shown}{self.unit}"
        return f"{field} {_OP_TEXT[self.op]} {self.value}"

    def to_dict(self, name: str | None = None) -> dict:
        return {
            "field": self.field,
            "op": self.op,
            "value": self.value,
            "unit": self.unit,
            "label": self.label(name),
        }


def _num(raw: Any) -> float | None:
    """把页面上的价格写法转成数字；区间价取上限（判定更严格）。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    m = _RANGE_RE.search(text)
    if m:
        try:
            return max(float(m.group(1)), float(m.group(2)))
        except ValueError:
            return None
    try:
        return _to_number(text)
    except (ValueError, TypeError):
        return None


def parse_filters(raw_items: Any, field_names: list[str]) -> list[Filter]:
    """校验模型输出，只保留字段存在且可执行的约束。"""
    allowed = {name.lower(): name for name in field_names}
    parsed: list[Filter] = []
    seen: set[tuple[str, str, Any]] = set()
    for spec in raw_items or []:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("field") or "").strip()
        field = allowed.get(name.lower())
        if not field:
            continue
        op = _OP_ALIAS.get(str(spec.get("op") or "").strip().lower(), "")
        if op not in _OPS:
            continue
        value = spec.get("value")
        if op in _NUMERIC_OPS:
            num = _num(value)
            if num is None:
                continue
            value = num
        else:
            if value is None:
                continue
            value = str(value).strip()
            if not value:
                continue
        key = (field, op, value)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(Filter(field=field, op=op, value=value, unit=str(spec.get("unit") or "").strip()))
    return parsed


def filters_to_payload(filters: list[Filter], display: dict[str, str] | None = None) -> list[dict]:
    """转为接口返回结构；display 可把英文字段名换成中文描述。"""
    names = display or {}
    return [f.to_dict(names.get(f.field)) for f in filters]


def describe_filters(filters: list[Filter], display: dict[str, str] | None = None) -> str:
    """把限制条件拼成一句文案：价格 ≤ 50 元。"""
    names = display or {}
    return "、".join(f.label(names.get(f.field)) for f in filters)


_INTENT_PROMPT = """你是筛选条件解析器。从用户的目标描述中提取**明确写出**的硬性筛选条件。

只输出 JSON，不要解释、不要代码围栏，格式：
{{"filters":[{{"field":"price","op":"lte","value":50,"unit":"元"}}]}}

规则：
- field 只能从这些字段里选：{fields}。条件涉及的字段不在其中时，直接忽略该条件。
- op 只能是：lte（≤）、lt（<）、gte（≥）、gt（>）、eq（=）、ne（≠）、contains（包含）。
- 数值条件（价格、评分、销量等）的 value 用数字，不带单位；单位写进 unit。
- 文本条件的 value 用原文（如「自营」）。
- 「最多 5 条」「前 10 本」这类数量要求不要写进 filters。
- 没有筛选条件时输出 {{"filters":[]}}。

目标描述：{task_desc}"""


def detect_filters(
    llm,
    task_desc: str,
    field_names: list[str],
    timeout: float = 30.0,
) -> list[Filter]:
    """调用大模型识别筛选条件；任何失败都退化为不筛选。"""
    if not task_desc or not field_names:
        return []
    prompt = _INTENT_PROMPT.format(fields="、".join(field_names), task_desc=task_desc)
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": "你是筛选条件解析器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - 识别失败不应阻断采集
        return []
    data = llm.extract_json(raw)
    if isinstance(data, dict):
        return parse_filters(data.get("filters"), field_names)
    if isinstance(data, list):
        return parse_filters(data, field_names)
    return []


def matches(item: dict, flt: Filter) -> bool:
    """判断单条数据是否满足条件；缺失或无法解析视为不满足。"""
    raw = item.get(flt.field)
    if flt.op in _NUMERIC_OPS:
        num = _num(raw)
        if num is None:
            return False
        target = float(flt.value)
        if flt.op == "lte":
            return num <= target
        if flt.op == "lt":
            return num < target
        if flt.op == "gte":
            return num >= target
        return num > target

    text = "" if raw is None else str(raw)
    if flt.op == "eq":
        return text.strip() == str(flt.value).strip()
    if flt.op == "ne":
        return text.strip() != str(flt.value).strip()
    return str(flt.value) in text


def apply_filters(items: list[dict], filters: list[Filter]) -> tuple[list[dict], int]:
    """按约束过滤条目，返回（保留条目, 丢弃条数）。无条件时原样返回。"""
    if not filters:
        return items, 0
    kept = [item for item in items if all(matches(item, f) for f in filters)]
    return kept, len(items) - len(kept)
