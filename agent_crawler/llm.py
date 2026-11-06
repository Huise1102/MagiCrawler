"""LLM 客户端封装（OpenAI 兼容接口 / Ollama 原生接口）。"""

from __future__ import annotations

import json
import re
from typing import Any

import requests
from openai import OpenAI

from .config import LLMConfig


class LLMClient:
    """封装 LLM 调用。

    - provider=openai：走 OpenAI 兼容的 Chat Completions（DeepSeek 等）。
    - provider=ollama：走 Ollama 原生 /api/chat，可传 think=false 关闭 Qwen3 思考。
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self.provider = config.provider
        self.client = (
            OpenAI(base_url=config.base_url, api_key=config.api_key or "EMPTY")
            if self.provider != "ollama"
            else None
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> str:
        """发送消息并返回文本内容。timeout 为本次调用超时（秒）。"""
        temp = self.config.temperature if temperature is None else temperature
        timeout_s = min(timeout or self.config.timeout_s, self.config.timeout_s)
        if self.provider == "ollama":
            return self._chat_ollama(messages, temp, timeout_s)

        resp = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=temp,
            max_tokens=self.config.max_tokens,
            timeout=timeout_s,
        )
        return resp.choices[0].message.content or ""

    def _chat_ollama(self, messages: list[dict[str, Any]], temperature: float, timeout_s: float) -> str:
        """调用 Ollama 原生 /api/chat，关闭 thinking 以避免占满 max_tokens。"""
        base = self.config.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": self.config.max_tokens,
            },
        }
        resp = requests.post(f"{base}/api/chat", json=payload, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message") or {}).get("content", "") or ""

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