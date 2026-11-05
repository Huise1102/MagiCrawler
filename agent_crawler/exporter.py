"""数据导出模块：JSON / CSV / Excel / Markdown / DOCX。

统一入口 export_result(items, note, fmt) -> (filename, bytes)。
"""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd

SUPPORTED_FORMATS = ("json", "csv", "xlsx", "md", "docx")


def export_result(items: list[dict], note: str = "", fmt: str = "json") -> tuple[str, bytes]:
    """按格式导出，返回 (建议文件名, 内容字节)。"""
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"不支持的导出格式：{fmt}")

    if fmt == "json":
        return "result.json", _to_json(items, note)
    if fmt == "csv":
        return "result.csv", _to_csv(items)
    if fmt == "xlsx":
        return "result.xlsx", _to_excel(items)
    if fmt == "md":
        return "result.md", _to_markdown(items, note)
    if fmt == "docx":
        return "result.docx", _to_docx(items, note)
    raise ValueError(f"不支持的导出格式：{fmt}")


def _to_json(items: list[dict], note: str) -> bytes:
    payload = {"items": items, "_note": note}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _to_csv(items: list[dict]) -> bytes:
    df = pd.DataFrame(items)
    # utf-8-sig 使 Excel 正确识别中文
    return df.to_csv(index=False).encode("utf-8-sig")


def _to_excel(items: list[dict]) -> bytes:
    df = pd.DataFrame(items)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="爬取结果")
    return buf.getvalue()


def _to_markdown(items: list[dict], note: str) -> bytes:
    if not items:
        lines = ["# 爬取结果", "", "（无数据）", "", f"> _note: {note or '无'}"]
        return "\n".join(lines).encode("utf-8")

    headers = list(items[0].keys())
    lines = ["# 爬取结果", ""]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for item in items:
        cells = ["" if v is None else str(v).replace("|", "\\|").replace("\n", " ") for v in [item.get(h) for h in headers]]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"> _note: {note or '无'}")
    return "\n".join(lines).encode("utf-8")


def _to_docx(items: list[dict], note: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("爬取结果", level=1)

    if not items:
        doc.add_paragraph("（无数据）")
    else:
        headers = list(items[0].keys())
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        for i, h in enumerate(headers):
            hdr[i].text = h
            for run in hdr[i].paragraphs[0].runs:
                run.bold = True
        for item in items:
            cells = table.add_row().cells
            for i, h in enumerate(headers):
                cells[i].text = "" if item.get(h) is None else str(item.get(h))

    if note:
        doc.add_paragraph(f"_note: {note}")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()