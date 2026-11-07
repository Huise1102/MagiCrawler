"""Schema 解析、结果校验与导出单测。

运行：python -m pytest tests -q
"""

from __future__ import annotations

import json
import unittest

from agent_crawler.exporter import export_result
from agent_crawler.schema import (
    SchemaParseError,
    _to_number,
    parse_schema,
    validate_items,
)


class TestParseSchema(unittest.TestCase):
    def test_parse_json_schema(self):
        fields = parse_schema(
            '{"type":"object","properties":{"title":{"type":"string"},"price":{"type":"number"}}}'
        )
        self.assertEqual([f.name for f in fields], ["title", "price"])
        self.assertEqual(fields[0].type, "string")
        self.assertEqual(fields[1].type, "number")

    def test_parse_json_schema_array(self):
        fields = parse_schema(
            '{"type":"array","items":{"type":"object","properties":'
            '{"title":{"type":"string"},"stock":{"type":"integer"}}}}'
        )
        self.assertEqual([f.name for f in fields], ["title", "stock"])
        self.assertEqual(fields[1].type, "integer")

    def test_parse_simple_map(self):
        fields = parse_schema('{"title":"string","author":"string","pages":"number"}')
        self.assertEqual([f.name for f in fields], ["title", "author", "pages"])
        self.assertEqual(fields[2].type, "number")

    def test_parse_legacy_fields_list(self):
        """兼容历史 {"fields": [{"name","type","desc"}]} 格式。"""
        fields = parse_schema(
            '{"fields":[{"name":"title","type":"string","desc":"标题"},'
            '{"name":"price","type":"number"}]}'
        )
        self.assertEqual([f.name for f in fields], ["title", "price"])
        self.assertEqual(fields[0].description, "标题")
        self.assertEqual(fields[1].type, "number")

    def test_parse_invalid_json(self):
        with self.assertRaises(SchemaParseError):
            parse_schema("{not json")

    def test_parse_empty(self):
        with self.assertRaises(SchemaParseError):
            parse_schema("   ")

    def test_parse_no_fields(self):
        with self.assertRaises(SchemaParseError):
            parse_schema('{"type":"object","properties":{}}')


class TestToNumber(unittest.TestCase):
    def test_plain_and_thousands(self):
        self.assertEqual(_to_number("1,234"), 1234.0)
        self.assertEqual(_to_number("19.9"), 19.9)

    def test_chinese_units(self):
        self.assertEqual(_to_number("1.7万"), 17000.0)
        self.assertEqual(_to_number("3.2亿"), 320000000.0)
        self.assertEqual(_to_number("120w"), 1200000.0)
        self.assertEqual(_to_number("8k"), 8000.0)

    def test_duration(self):
        self.assertEqual(_to_number("03:06"), 186.0)
        self.assertEqual(_to_number("1:02:03"), 3723.0)

    def test_currency(self):
        self.assertEqual(_to_number("£51.77"), 51.77)
        self.assertEqual(_to_number("199元"), 199.0)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            _to_number("暂无")


class TestValidateItems(unittest.TestCase):
    def test_validate_missing_fills_null(self):
        fields = parse_schema('{"title":"string","price":"number"}')
        items = validate_items([{"title": "A", "price": "19.9"}, {"title": "B"}], fields)
        self.assertEqual(items[0]["price"], 19.9)
        self.assertIsNone(items[1]["price"])

    def test_validate_items_wrapped(self):
        fields = parse_schema('{"title":"string"}')
        items = validate_items({"items": [{"title": "x"}, {"title": "y"}]}, fields)
        self.assertEqual(len(items), 2)

    def test_string_null_treated_as_missing(self):
        fields = parse_schema('{"title":"string","price":"number"}')
        items = validate_items([{"title": "A", "price": "null"}, {"title": None}], fields)
        self.assertIsNone(items[0]["price"])
        self.assertIsNone(items[1]["title"])

    def test_integer_and_boolean(self):
        fields = parse_schema('{"stock":"integer","available":"boolean"}')
        items = validate_items(
            [{"stock": "1,234", "available": "是"}, {"stock": "x", "available": "false"}],
            fields,
        )
        self.assertEqual(items[0]["stock"], 1234)
        self.assertIs(items[0]["available"], True)
        self.assertIsNone(items[1]["stock"])
        self.assertIs(items[1]["available"], False)

    def test_array_object_passthrough(self):
        fields = parse_schema('{"tags":"array","meta":"object"}')
        tags = ["a", "b"]
        meta = {"k": "v"}
        items = validate_items([{"tags": tags, "meta": meta}], fields)
        self.assertEqual(items[0]["tags"], tags)
        self.assertEqual(items[0]["meta"], meta)

    def test_skips_non_dict_and_non_list(self):
        fields = parse_schema('{"title":"string"}')
        self.assertEqual(validate_items("oops", fields), [])
        self.assertEqual(validate_items(["oops", 1, None], fields), [])


class TestExport(unittest.TestCase):
    def test_export_json(self):
        filename, content = export_result([{"title": "A"}], "note", "json")
        self.assertEqual(filename, "result.json")
        payload = json.loads(content.decode("utf-8"))
        self.assertEqual(payload["items"], [{"title": "A"}])
        self.assertEqual(payload["_note"], "note")

    def test_export_csv_with_bom(self):
        filename, content = export_result([{"title": "中文", "price": 1}], "", "csv")
        self.assertEqual(filename, "result.csv")
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        text = content.decode("utf-8-sig")
        self.assertIn("title", text)
        self.assertIn("中文", text)

    def test_export_markdown_escapes_pipe(self):
        filename, content = export_result([{"title": "A|B\nC"}], "备注", "md")
        self.assertEqual(filename, "result.md")
        text = content.decode("utf-8")
        self.assertIn("A\\|B C", text)
        self.assertIn("_note: 备注", text)

    def test_export_markdown_empty(self):
        _, content = export_result([], "", "md")
        self.assertIn("（无数据）", content.decode("utf-8"))

    def test_export_xlsx_is_zip(self):
        filename, content = export_result([{"title": "A"}], "", "xlsx")
        self.assertEqual(filename, "result.xlsx")
        self.assertTrue(content.startswith(b"PK"))

    def test_unsupported_format(self):
        with self.assertRaises(ValueError):
            export_result([{"title": "A"}], "", "txt")


if __name__ == "__main__":
    unittest.main()
