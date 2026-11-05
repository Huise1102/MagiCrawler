"""配置加载模块。

从环境变量 / .env 读取 LLM 与爬虫相关配置，提供默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 默认在项目根目录加载 .env（若存在）
load_dotenv()


def _get_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("true", "1", "yes", "on")


@dataclass
class LLMConfig:
    """LLM 配置。

    provider=openai 时走 OpenAI 兼容接口（DeepSeek 等）；
    provider=ollama 时走 Ollama 原生 /api/chat，可关闭 Qwen3 的 thinking。
    """

    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai").strip().lower())
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com"))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "4096")))


@dataclass
class CrawlerConfig:
    """爬虫执行参数配置。"""

    # 决策循环防御
    max_steps: int = field(default_factory=lambda: int(os.getenv("MAX_STEPS", "30")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "2")))

    # 浏览器
    timeout_ms: int = field(default_factory=lambda: int(os.getenv("TIMEOUT_MS", "30000")))
    max_snapshot_chars: int = field(default_factory=lambda: int(os.getenv("MAX_SNAPSHOT_CHARS", "8000")))
    headless: bool = field(default_factory=lambda: _get_bool("HEADLESS", True))
    # 拦截的资源类型（逗号分隔，减少无关请求、加快导航）：image,media,font,stylesheet
    block_resources: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip().lower()
            for x in os.getenv("BLOCK_RESOURCES", "image,media,font,stylesheet").split(",")
            if x.strip()
        )
    )
    # 浏览器 channel：留空则自动回退（内置 Chromium -> 系统 Edge -> Chrome）
    browser_channel: str = field(default_factory=lambda: os.getenv("BROWSER_CHANNEL", "").strip())
    # 常驻浏览器复用：跨任务复用同一浏览器实例，省去启动与冷导航开销
    browser_reuse: bool = field(default_factory=lambda: _get_bool("BROWSER_REUSE", True))
    # 浏览器空闲超时（秒）：超过后下次任务重建浏览器；0 表示不超时
    browser_idle_timeout_s: int = field(
        default_factory=lambda: int(os.getenv("BROWSER_IDLE_TIMEOUT", "300"))
    )
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
    )

    # 存储
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/crawler.db"))


def load_config() -> tuple[LLMConfig, CrawlerConfig]:
    """加载 LLM 与爬虫配置。"""
    return LLMConfig(), CrawlerConfig()