"""文档自动分类：用 LLM 为未分类文档挑选合适的分类。

LLM 不可用或返回无法解析时，回退到关键词规则（命中已有分类名 / 按来源任务归类）。
"""

from __future__ import annotations

import json
from typing import Any

from .llm import LLMClient

_BATCH_SIZE = 20
_MAX_CATEGORY_LEN = 30
_MAX_TEXT = 120
_EMPTY_LABELS = {"", "未分类", "无", "未知", "none", "null"}


class DocumentClassifier:
    """把文档归入分类，可复用现有分类或提出新分类。"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def classify(self, docs: list[dict], existing: list[str]) -> dict[str, Any]:
        """返回 assignments（doc_id → 分类名）与 LLM 失败批次数。"""
        assignments: dict[str, str] = {}
        failures = 0
        for start in range(0, len(docs), _BATCH_SIZE):
            batch = docs[start : start + _BATCH_SIZE]
            llm_result: dict[str, str] = {}
            try:
                raw = self.llm.chat(
                    [
                        {
                            "role": "system",
                            "content": "你是文档分类助手，只输出 JSON，不要任何解释文字。",
                        },
                        {"role": "user", "content": self._build_prompt(batch, existing)},
                    ],
                    temperature=0,
                )
                llm_result = self._sanitize(self.llm.extract_json(raw), batch)
            except Exception:  # noqa: BLE001 - LLM 失败时回退规则
                failures += 1

            for doc in batch:
                category = llm_result.get(doc["id"])
                if not category:
                    category = self._fallback(doc, existing)
                if category:
                    assignments[doc["id"]] = category

        return {
            "assignments": assignments,
            "llm_failed": failures,
            "batches": (len(docs) + _BATCH_SIZE - 1) // _BATCH_SIZE,
        }

    # ---------------- 内部实现 ----------------

    def _build_prompt(self, batch: list[dict], existing: list[str]) -> str:
        existing_text = "、".join(existing) if existing else "（暂无分类）"
        compact = [self._compact(doc) for doc in batch]
        return (
            "请为下面的文档各选一个分类。只输出紧凑 JSON 数组，格式：\n"
            '[{"id":"文档ID","category":"分类名"}]\n'
            "规则：\n"
            "- 优先归入现有分类；现有分类都不合适时才可以新建分类\n"
            "- 分类名 2-8 个汉字（英文分类不超过 30 字符），同一批文档尽量复用同一分类，"
            "不要为每篇文档单独建类\n"
            "- 无法判断时 category 输出空字符串\n"
            "- id 必须与给定文档 id 完全一致，不要输出多余字段\n\n"
            f"现有分类：{existing_text}\n"
            f"文档：\n{json.dumps(compact, ensure_ascii=False)}"
        )

    def _compact(self, doc: dict) -> dict:
        fields = doc.get("fields") or {}
        compact_fields: dict[str, str] = {}
        for key, value in list(fields.items())[:8]:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            compact_fields[str(key)] = text[:_MAX_TEXT]
        return {
            "id": doc.get("id", ""),
            "title": (doc.get("title") or "")[:_MAX_TEXT],
            "task": (doc.get("task_desc") or "")[:_MAX_TEXT],
            "fields": compact_fields,
        }

    def _sanitize(self, data: Any, batch: list[dict]) -> dict[str, str]:
        """校验 LLM 输出：只保留本批文档 id，清理分类名。"""
        ids = {doc["id"] for doc in batch}
        items = data.get("items") if isinstance(data, dict) else data
        result: dict[str, str] = {}
        if not isinstance(items, list):
            return result
        for raw in items:
            if not isinstance(raw, dict):
                continue
            doc_id = str(raw.get("id") or "").strip()
            if doc_id not in ids:
                continue
            category = str(raw.get("category") or "").strip().strip("\"'")
            category = " ".join(category.split())
            if category.lower() in _EMPTY_LABELS or len(category) > _MAX_CATEGORY_LEN:
                continue
            result[doc_id] = category
        return result

    def _fallback(self, doc: dict, existing: list[str]) -> str:
        """规则兜底：优先匹配现有分类名，其次按来源任务描述归类。"""
        text = " ".join(
            [
                str(doc.get("title") or ""),
                str(doc.get("task_desc") or ""),
                json.dumps(doc.get("fields") or {}, ensure_ascii=False),
            ]
        ).lower()
        for name in existing:
            if name and name.lower() in text:
                return name

        task = (doc.get("task_desc") or "").strip().splitlines()
        if task and task[0].strip():
            return " ".join(task[0].split())[:_MAX_CATEGORY_LEN]
        return ""
