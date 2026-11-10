"""详情页字段解析与快照分块的单元测试。"""

from __future__ import annotations

from agent_crawler.agent import Agent, _absolute_url, _attach_urls, _parse_links, _split_snapshot
from agent_crawler.detail import labels_for, missing_fields, parse_details
from agent_crawler.schema import Field


def _fields() -> list[Field]:
    return [
        Field(name="author", type="string"),
        Field(name="publisher", type="string"),
        Field(name="publication_date", type="string"),
        Field(name="isbn", type="string"),
    ]


def test_parse_details_handles_spaced_labels() -> None:
    """京东书籍详情页常用「作 者：」这种字符间带空格的排版。"""
    text = (
        "商品介绍\n作 者：三毛\n出版社：北京十月文艺出版社\n"
        "出版时间：2011-07-01\nISBN：9787530214782\n"
    )
    found = parse_details(text, _fields())
    assert found == {
        "author": "三毛",
        "publisher": "北京十月文艺出版社",
        "publication_date": "2011-07-01",
        "isbn": "9787530214782",
    }


def test_parse_details_stops_at_next_label_on_same_line() -> None:
    text = "作者：张三 出版社：人民文学出版社 出版时间：2020年1月 ISBN：9787020002207"
    found = parse_details(text, _fields())
    assert found["author"] == "张三"
    assert found["publisher"] == "人民文学出版社"
    assert found["publication_date"] == "2020年1月"
    assert found["isbn"] == "9787020002207"


def test_parse_details_ignores_author_intro_block() -> None:
    """「作者简介」不是作者字段，不能把简介内容当作者。"""
    text = "作者简介：三毛，台湾作家。\n作者：三毛\n"
    found = parse_details(text, _fields())
    assert found.get("author") == "三毛"


def test_parse_details_type_conversion() -> None:
    fields = [Field(name="price", type="number")]
    found = parse_details("定价：￥191.00\n", fields)
    assert found["price"] == 191.0


def test_labels_for_prefers_description() -> None:
    field = Field(name="writer", description="作者")
    assert labels_for(field)[0] == "作者"


def test_missing_fields_only_returns_empty_values() -> None:
    item = {"author": "三毛", "publisher": None}
    missing = missing_fields(item, _fields())
    assert [f.name for f in missing] == ["publisher", "publication_date", "isbn"]


def test_split_snapshot_keeps_all_lines() -> None:
    lines = [f"第{i}行" + "内容" * 30 for i in range(10)]
    chunks = _split_snapshot("\n".join(lines), 300)
    assert len(chunks) > 1
    merged = "\n".join(chunks).splitlines()
    assert merged == lines
    assert all(len(chunk) <= 300 for chunk in chunks)


def test_absolute_url_joins_relative_links() -> None:
    assert _absolute_url("item/1.html", "https://example.com/list") == "https://example.com/item/1.html"
    assert _absolute_url("//cdn.example.com/a", "https://example.com") == "https://cdn.example.com/a"
    assert _absolute_url("", "https://example.com") is None


def test_attach_urls_matches_title_to_snapshot_links() -> None:
    """条目链接由快照链接按标题对齐，不依赖模型输出。"""
    snapshot = (
        "【页面文本】\n三毛全集 ￥191.00\n活着 ￥39.00\n\n"
        "【链接（文本 -> 地址）】\n三毛全集 -> item.jd.com/1001.html\n活着 -> item.jd.com/1002.html\n\n"
        "【按钮/可点击文本】\n下一页"
    )
    links = _parse_links(snapshot)
    assert len(links) == 2
    items = [{"title": "三毛全集", "price": 191.0}, {"title": "活着", "price": 39.0}]
    _attach_urls(items, links, "https://search.jd.com/Search")
    assert items[0]["_url"] == "https://item.jd.com/1001.html"
    assert items[1]["_url"] == "https://item.jd.com/1002.html"


def test_merge_prefers_same_url_over_duplicate_row() -> None:
    base = [{"title": "三毛全集", "price": None, "author": None, "_url": "https://item.jd.com/1.html"}]
    merged = Agent._merge(base, [{"title": "三毛全集", "price": 191.0, "_url": "https://item.jd.com/1.html"}])
    assert len(merged) == 1
    assert merged[0]["price"] == 191.0


def test_merge_drops_items_with_only_internal_fields() -> None:
    """只有内部链接、没有任何业务字段的条目不能计入结果。"""
    merged = Agent._merge([], [{"title": None, "_url": "https://example.com/1"}])
    assert merged == []
