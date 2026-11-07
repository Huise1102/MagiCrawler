"""Schema 解析与结果校验模块。

支持三种用户输入格式：
1. JSON Schema 对象：{"type":"object","properties":{"title":{"type":"string","description":"..."}}}
2. 简单字段映射：{"title":"string","author":"number"}
3. JSON Schema 数组：{"type":"array","items":{"type":"object","properties":{...}}}

统一提取为「字段列表」，并据此动态校验智能体输出（缺失字段填 null）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Field:
    """目标字段定义。"""

    name: str
    type: str = "string"  # string / number / integer / boolean / array / object
    description: str = ""


# JSON Schema 类型 -> 精简标记
_TYPE_ALIAS = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}

# 精简标记 -> Python 类型（用于 type hint 描述，实际校验手动转换）
_PYTHON_TYPE = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class SchemaParseError(ValueError):
    """Schema 解析失败。"""


def _extract_properties(obj: dict) -> dict:
    """从多种结构里定位字段描述 properties 字典。"""
    if "properties" in obj and isinstance(obj["properties"], dict):
        return obj["properties"]
    if obj.get("type") == "array" and isinstance(obj.get("items"), dict):
        if "properties" in obj["items"]:
            return obj["items"]["properties"]
    return {}


def parse_schema(schema_text: str) -> list[Field]:
    """解析用户输入的 Schema，返回字段列表。"""
    schema_text = (schema_text or "").strip()
    if not schema_text:
        raise SchemaParseError("Schema 为空，请提供目标字段。")

    try:
        data = json.loads(schema_text)
    except json.JSONDecodeError as e:
        raise SchemaParseError(f"Schema 不是合法 JSON：{e}") from e

    if not isinstance(data, dict):
        raise SchemaParseError("Schema 顶层必须是对象或简单键值映射。")

    # 尝试从 JSON Schema 提取 properties
    props = _extract_properties(data)
    if props:
        fields: list[Field] = []
        for name, spec in props.items():
            if isinstance(spec, dict):
                ftype = _TYPE_ALIAS.get(spec.get("type", "string"), "string")
                desc = spec.get("description", "") or ""
            else:
                ftype = "string"
                desc = ""
            fields.append(Field(name=name, type=ftype, description=desc))
        return fields

    # 兼容 {"fields": [{"name": ..., "type": ..., "desc": ...}]} 字段列表格式
    raw_fields = data.get("fields")
    if isinstance(raw_fields, list):
        listed: list[Field] = []
        for spec in raw_fields:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            if not name:
                continue
            ftype = _TYPE_ALIAS.get(str(spec.get("type") or "string").lower(), "string")
            desc = str(spec.get("desc") or spec.get("description") or "")
            listed.append(Field(name=name, type=ftype, description=desc))
        if listed:
            return listed

    # 兜底：把整个对象视为「字段名 -> 类型」的简单映射
    fields = []
    for name, t in data.items():
        if name in ("type", "items", "properties"):
            continue
        ftype = _TYPE_ALIAS.get(str(t).lower(), "string") if isinstance(t, str) else "string"
        fields.append(Field(name=name, type=ftype))
    if not fields:
        raise SchemaParseError("未能从 Schema 中解析出任何字段。")
    return fields


def schema_fields_to_text(fields: list[Field]) -> str:
    """把字段列表转成注入 LLM 的描述文本。"""
    lines = []
    for f in fields:
        desc = f" —— {f.description}" if f.description else ""
        lines.append(f'  - "{f.name}"（类型：{f.type}）{desc}')
    return "\n".join(lines)


# 中文数量单位换算（1.7万 -> 17000、3.2亿 -> 320000000）
_UNIT_FACTORS = {"万": 10_000, "亿": 100_000_000, "w": 10_000, "k": 1_000}
_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿|w|W|k|K)\+?")


def _to_number(value: Any) -> float:
    """把网页上常见的数值写法转为数字。

    支持：千分位（1,234）、中文单位（1.7万 / 3.2亿）、缩写（120w / 8k）、
    时长（03:06 -> 186 秒、1:02:03 -> 3723 秒）。无法识别时抛出 ValueError。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip().replace(",", "").replace("，", "").replace("+", "").replace("＋", "")
    # 去掉货币符号与「元」，如 £51.77 / ¥129 / 199元
    s = re.sub(r"[¥￥$€£]|元", "", s).strip()
    if not s:
        raise ValueError("empty")
    if _TIME_RE.fullmatch(s):
        seconds = 0
        for part in s.split(":"):
            seconds = seconds * 60 + int(part)
        return float(seconds)
    m = _UNIT_RE.fullmatch(s)
    if m:
        return float(m.group(1)) * _UNIT_FACTORS[m.group(2).lower()]
    return float(s)


def _coerce(value: Any, ftype: str) -> Any:
    """把单值尽力转换为目标类型；失败返回 None。"""
    if value is None:
        return None
    try:
        if ftype == "string":
            s = str(value).strip()
            # 模型有时会把缺失值写成字符串 "null"/"none"/"nil"，统一视为缺失
            if not s or s.lower() in ("null", "none", "nil"):
                return None
            return s
        if ftype == "number":
            return _to_number(value)
        if ftype == "integer":
            if isinstance(value, bool):
                return None
            return int(_to_number(value))
        if ftype == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("true", "1", "是", "yes")
            return bool(value)
        # array / object 直接透传
        return value
    except (ValueError, TypeError):
        return None


def validate_items(raw_items: Any, fields: list[Field]) -> list[dict]:
    """把智能体原始输出转为规范条目列表。

    规则：缺失字段填 None，类型错误尽力转换（失败置 None），绝不编造数据。
    """
    items: list[dict] = []
    if isinstance(raw_items, dict):
        # 顶层可能是 {"items": [...], ...}，或单条记录
        if isinstance(raw_items.get("items"), list):
            raw_items = raw_items["items"]
        else:
            raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return items

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        entry: dict[str, Any] = {}
        for f in fields:
            entry[f.name] = _coerce(raw.get(f.name), f.type)
        items.append(entry)
    return items