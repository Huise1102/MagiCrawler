"""最小单测：Schema 校验与导出。

运行：python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from agent_crawler.exporter import export_result
from agent_crawler.schema import parse_schema, validate_items


class TestSchema(unittest.TestCase):
    def test_parse_json_schema(self):
        fields = parse_schema(
            '{"type":"object","properties":{"title":{"type":"string"},"price":{"type":"number"}}}'
        )
        self.assertEqual([f.name for f in fields], ["title", "price"])
        self.assertEqual(fields[0].type, "string")
        self.assertEqual(fields[1].type, "number")

    def test_parse_simple_map(self):
        fields = parse_schema('{"title":"string","author":"string"}')
        self.assertEqual([f.name for f in fields], ["title", "author"])

    def test_validate_missing_fills_null(self):
        fields = parse_schema('{"title":"string","price":"number"}')
        items = validate_items([{"title": "A", "price": "19.9"}, {"title": "B"}], fields)
        self.assertEqual(items[0]["price"], 19.9)
        self.assertIsNone(items[1]["price"])

    def test_validate_items_wrapped(self):
        fields = parse_schema('{"title":"string"}')
        items = validate_items({"items": [{"title": "x"}, {"title": "y"}]}, fields)
        self.assertEqual(len(items), 2)


class TestExport(unittest.TestCase):
    def test_export_markdown(self):
        filename, content = export_result([{"title": "A", "price": "1"}], "note", "md")
        self.assertEqual(filename, "result.md")
        self.assertTrue(content.startswith(b"#"))

    def test_export_json(self):
        filename, content = export_result([{"title": "A"}], "note", "json")
        self.assertEqual(filename, "result.json")
        self.assertIn(b'"items"', content)


if __name__ == "__main__":
    unittest.main()