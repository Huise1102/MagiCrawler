"""LLM 客户端封装（OpenAI 兼容接口）。"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .config import LLMConfig


class LLMClient:
    """封装 OpenAI 兼容的 Chat Completions 接口。"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    def chat(self, messages: list[dict[str, Any]], temperature: float | None = None) -> str:
        """发送消息并返回文本内容。"""
        resp = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature if temperature is None else temperature,
            max_tokens=self.config.max_tokens,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def extract_json(text: str) -> Any | None:
        """从 LLM 输出中鲁棒地提取 JSON 对象/数组。

        依次尝试：直接解析 → 去除代码围栏后解析 → 正则提取首个 JSON 片段。
        """
        if not text:
            return None
        cleaned = text.strip()

        # 去除 ```json ... ``` 围栏
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
        if fence:
            cleaned = fence.group(1).strip()

        # 直接解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 正则提取首个平衡的 {} 或 []
        for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
            m = re.search(pattern, cleaned)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
        return None