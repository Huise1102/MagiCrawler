"""LLM 驱动的可视化方案生成：根据采集数据自动设计图表。"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .llm import LLMClient

CHART_TYPES = ("bar", "line", "pie", "scatter")
AGGREGATES = ("count", "sum", "avg", "none")


def _as_number(value: Any) -> float | None:
    """尽力把单元格转为数值（兼容 "£51.77"、千分位等）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "")
        m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


class Visualizer:
    """让 LLM 阅读数据并输出可直接渲染的图表方案。"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def plan(self, task_desc: str, items: list[dict], hint: str = "") -> dict:
        """生成可视化方案（图表类型 + 字段映射 + 聚合方式）。"""
        start = time.perf_counter()
        fields = self._fields(items)
        sample = items[:40]
        raw = self.llm.chat(
            [
                {"role": "system", "content": "你是数据可视化助手，只输出 JSON。"},
                {"role": "user", "content": self._build_prompt(task_desc, fields, sample, hint)},
            ],
            temperature=0,
        )
        data = self.llm.extract_json(raw)
        charts = self._sanitize(data, fields, items)

        summary = data.get("summary", "") if isinstance(data, dict) else ""
        if not isinstance(summary, str) or not summary.strip():
            summary = "已根据采集数据生成可视化方案。"

        return {
            "summary": summary.strip(),
            "charts": charts,
            "fields": fields,
            "total": len(items),
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
        }

    # ---------------- 内部实现 ----------------

    @staticmethod
    def _fields(items: list[dict]) -> list[str]:
        fields: list[str] = []
        for item in items:
            for key in item or {}:
                if key not in fields:
                    fields.append(key)
        return fields

    def _build_prompt(
        self, task_desc: str, fields: list[str], sample: list[dict], hint: str
    ) -> str:
        field_text = "\n".join(f"- {f}" for f in fields)
        return (
            "根据下面的采集数据设计 1-4 个图表。\n"
            "只输出紧凑 JSON（单行、无缩进、无多余空白），格式：\n"
            '{"summary":"一句话数据结论","charts":[{"type":"bar","title":"图表标题",'
            '"x":"字段名","y":"字段名","aggregate":"count"}]}\n'
            "约束：\n"
            "- type 只能是 bar / line / pie / scatter\n"
            "- x 必须是给定字段之一；y 必须是字段之一，若只统计数量则 y 留空\n"
            "- aggregate 只能是 count / sum / avg / none（scatter 用 none）\n"
            "- 字段名必须与给定列表完全一致，不要发明字段\n"
            "- 不要输出任何说明文字\n\n"
            f"任务：{task_desc}\n"
            f"可视化方向：{hint.strip() or '无（由你决定最能体现数据的图表）'}\n"
            f"字段列表：\n{field_text}\n\n"
            f"数据样例（最多 40 条）：\n{json.dumps(sample, ensure_ascii=False)}"
        )

    def _sanitize(self, data: Any, fields: list[str], items: list[dict]) -> list[dict]:
        """校验 LLM 返回的方案，剔除非法字段；必要时使用兜底方案。"""
        charts: list[dict] = []
        raw_charts = data.get("charts") if isinstance(data, dict) else None
        if isinstance(raw_charts, list):
            for raw in raw_charts:
                if not isinstance(raw, dict):
                    continue
                chart = self._normalize_chart(raw, fields)
                if chart:
                    charts.append(chart)
        if not charts:
            charts = self._fallback(fields, items)
        return charts[:4]

    def _normalize_chart(self, raw: dict, fields: list[str]) -> dict | None:
        ctype = str(raw.get("type") or "bar").lower()
        if ctype not in CHART_TYPES:
            ctype = "bar"
        x = str(raw.get("x") or "")
        y = str(raw.get("y") or "")
        if x not in fields:
            return None
        if y and y not in fields:
            y = ""
        aggregate = str(raw.get("aggregate") or ("none" if ctype == "scatter" else "count")).lower()
        if aggregate not in AGGREGATES:
            aggregate = "count"
        title = str(raw.get("title") or "").strip() or f"{x} 分布"
        return {
            "type": ctype,
            "title": title,
            "x": x,
            "y": y,
            "aggregate": aggregate,
        }

    def _fallback(self, fields: list[str], items: list[dict]) -> list[dict]:
        """LLM 方案不可用时的兜底：优先「按类别计数 + 数值求和」。"""
        if not fields:
            return []
        x = fields[0]
        for f in fields:
            distinct = {str(item.get(f)) for item in items if item.get(f) not in (None, "")}
            if 1 < len(distinct) <= 20:
                x = f
                break

        charts = [
            {"type": "bar", "title": f"{x} 分布", "x": x, "y": "", "aggregate": "count"}
        ]
        for f in fields:
            if f == x:
                continue
            values = [item.get(f) for item in items]
            numeric = [v for v in (_as_number(v) for v in values) if v is not None]
            if numeric and len(numeric) >= max(3, len(values) // 2):
                charts.append(
                    {"type": "bar", "title": f"{x} 的 {f} 汇总", "x": x, "y": f, "aggregate": "sum"}
                )
                break
        return charts
