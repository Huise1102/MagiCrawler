"""详情页字段补齐：从页面文本里按「标签：值」解析字段。

列表页通常只有书名与价格，作者 / 出版社 / 出版时间 / ISBN 等字段
只出现在详情页。这里用通用标签词典做确定性解析，避免逐条调用模型。
"""

from __future__ import annotations

import re
from typing import Any

from .schema import Field, coerce_value

# 常见字段 -> 可能出现的标签（中英文按站点不同）
FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "author": ("作者", "著者", "Author"),
    "authors": ("作者", "著者", "Author"),
    "publisher": ("出版社", "出版商", "Publisher"),
    "publication_date": ("出版时间", "出版日期", "出版年月", "Publication Date"),
    "publish_date": ("出版时间", "出版日期", "出版年月", "Published"),
    "pub_date": ("出版时间", "出版日期", "出版年月", "Published"),
    "isbn": ("ISBN-13", "ISBN-10", "ISBN"),
    "title": ("书名", "标题", "Title"),
    "price": ("现价", "售价", "价格", "定价", "Price"),
    "rating": ("评分", "Rating"),
    "pages": ("页数", "Pages"),
    "binding": ("装帧", "Binding"),
    "category": ("分类", "类别", "Category"),
    "description": ("内容简介", "简介", "Description"),
}


def labels_for(field: Field) -> list[str]:
    """汇总一个字段可能使用的标签文本（描述 > 内置词典 > 字段名）。"""
    labels: list[str] = []
    desc = (field.description or "").strip()
    if desc and len(desc) <= 12:
        labels.append(desc)
    labels.extend(FIELD_LABELS.get(field.name.lower(), ()))
    if not field.name.isascii():
        labels.append(field.name)
    seen: set[str] = set()
    return [x for x in labels if not (x.lower() in seen or seen.add(x.lower()))]


def _label_pattern(label: str) -> str:
    """把标签转成正则：允许字符间夹空格，兼容「作 者：」这类排版。"""
    chars = [re.escape(ch) for ch in label.strip() if not ch.isspace()]
    return r"\s*".join(chars)


def parse_details(text: str, fields: list[Field]) -> dict[str, Any]:
    """从页面文本中解析字段值，未命中的字段不出现在返回值里。"""
    if not text:
        return {}
    patterns = [_label_pattern(x) for f in fields for x in labels_for(f)]
    if not patterns:
        return {}
    boundary = "|".join(patterns)

    found: dict[str, Any] = {}
    for field in fields:
        for label in labels_for(field):
            pattern = (
                rf"{_label_pattern(label)}\s*[:：]\s*"
                rf"([^\n]{{1,120}}?)(?=\s*(?:{boundary})\s*[:：]|\n|$)"
            )
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            value = coerce_value(match.group(1).strip(" \t|·,，;；"), field.type)
            if value is None:
                continue
            found[field.name] = value
            break
    return found


def missing_fields(item: dict, fields: list[Field]) -> list[Field]:
    """返回条目里仍为空的字段。"""
    return [f for f in fields if item.get(f.name) is None]
