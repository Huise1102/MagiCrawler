"""Playwright 浏览器工具封装。

向智能体暴露：navigate / fetch / click / scroll 等浏览器动作，
并负责生成精简页面快照（去脚本/样式噪声、截断）以控制 Token。
"""

from __future__ import annotations

import threading
import time
from typing import Any

# 常驻浏览器池：按线程复用 Playwright/Browser，避免每次任务重复启动与冷导航。
# Playwright 同步 API 不可跨线程使用，因此按线程 id 隔离。
_POOL: dict[int, dict[str, Any]] = {}
_POOL_LOCK = threading.Lock()


def _dispose_entry(entry: dict[str, Any]) -> None:
    """关闭并释放一条池化浏览器。"""
    try:
        entry["browser"].close()
    except Exception:
        pass
    try:
        entry["pw"].stop()
    except Exception:
        pass


class BrowserTools:
    """基于 Playwright 的浏览器操作封装。

    reuse=True 时浏览器在本线程内常驻，close() 只关闭本次任务的 Context/Page，
    下次任务直接复用浏览器（页面状态隔离）。空闲超过 idle_timeout_s 后，
    下次启动时自动重建。
    """

    def __init__(
        self,
        timeout_ms: int = 30000,
        user_agent: str | None = None,
        headless: bool = True,
        channel: str | None = None,
        block_resources: tuple[str, ...] = (),
        reuse: bool = False,
        idle_timeout_s: int = 300,
    ):
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent
        self.headless = headless
        self.channel = channel
        self.block_resources = tuple(x.lower() for x in block_resources)
        self.reuse = reuse
        self.idle_timeout_s = idle_timeout_s
        self._pw = None
        self._browser = None
        self._context = None
        self.page: Any = None

    def _launch_browser(self):
        """启动浏览器：优先使用指定 channel，否则回退到系统 Edge/Chrome。"""
        if self.channel:
            return self._pw.chromium.launch(
                headless=self.headless, channel=self.channel, timeout=self.timeout_ms
            )
        last_err: Exception | None = None
        for channel in (None, "msedge", "chrome"):
            try:
                kwargs: dict[str, Any] = {"headless": self.headless, "timeout": self.timeout_ms}
                if channel:
                    kwargs["channel"] = channel
                return self._pw.chromium.launch(**kwargs)
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError(
            "无法启动浏览器：未安装 Playwright Chromium，且系统 Edge/Chrome 均不可用。"
        ) from last_err

    def start(self) -> None:
        """启动（或复用）浏览器，并为本次任务创建独立 Context/Page。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # 延迟导入：缺 playwright 时给出明确提示
            raise RuntimeError(
                "未安装 playwright。请先执行：pip install playwright 并 playwright install chromium"
            ) from e

        key = threading.get_ident()
        with _POOL_LOCK:
            entry = _POOL.get(key) if self.reuse else None
            if entry:
                expired = (
                    self.idle_timeout_s > 0
                    and time.time() - entry["last_used"] > self.idle_timeout_s
                )
                if expired or not entry["browser"].is_connected():
                    _POOL.pop(key, None)
                    _dispose_entry(entry)
                    entry = None
                else:
                    entry["last_used"] = time.time()
                    self._pw = entry["pw"]
                    self._browser = entry["browser"]

            if self._browser is None:
                self._pw = sync_playwright().start()
                self._browser = self._launch_browser()
                if self.reuse:
                    _POOL[key] = {
                        "pw": self._pw,
                        "browser": self._browser,
                        "last_used": time.time(),
                    }

        context = self._browser.new_context(
            user_agent=self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        context.set_default_timeout(self.timeout_ms)
        if self.block_resources:
            # 拦截图片/媒体/字体/样式，减少无关请求、显著加快导航
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in self.block_resources
                else route.continue_(),
            )
        self._context = context
        self.page = context.new_page()

    def close(self) -> None:
        """关闭本次任务的页面；复用模式下保留浏览器供后续任务使用。"""
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        self._context = None
        self.page = None

        if self.reuse:
            key = threading.get_ident()
            with _POOL_LOCK:
                entry = _POOL.get(key)
                if entry and entry["browser"] is self._browser:
                    entry["last_used"] = time.time()
                    self._browser = None
                    self._pw = None
                    return
            # 池中已无对应当前浏览器：按普通模式关闭
        self._shutdown_browser()

    def _shutdown_browser(self) -> None:
        """彻底关闭浏览器与 Playwright。"""
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None

    # ---------------- 页面快照 ----------------

    def snapshot(self, max_chars: int = 8000) -> str:
        """生成精简可读快照：正文文本 + 关键链接 + 按钮文本。"""
        page = self.page
        text = page.evaluate(
            "() => document.body ? document.body.innerText || '' : ''"
        )
        text = (text or "").strip()

        # 关键链接（去重、限量）
        links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a'))
              .map(a => (a.innerText || '').trim())
              .filter(t => t && t.length <= 60)
              .filter((t, i, arr) => arr.indexOf(t) === i)
              .slice(0, 80)"""
        )
        buttons = page.evaluate(
            """() => Array.from(document.querySelectorAll('button, [role=button], input[type=submit]'))
              .map(b => (b.innerText || b.value || '').trim())
              .filter(t => t && t.length <= 60)
              .filter((t, i, arr) => arr.indexOf(t) === i)
              .slice(0, 80)"""
        )

        parts = []
        if text:
            parts.append("【页面文本】\n" + text[:max_chars])
        if links:
            parts.append("【链接文本】\n" + " | ".join(links))
        if buttons:
            parts.append("【按钮/可点击文本】\n" + " | ".join(buttons))
        return "\n\n".join(parts)

    # ---------------- 工具动作 ----------------

    def navigate(self, url: str) -> str:
        """跳转到指定 URL，返回状态码 + 快照。

        首次用 domcontentloaded 等待；若超时则退化为 commit（导航已提交即返回），
        并把重试超时收紧到 15s，避免冷启动慢站点连续等两个完整超时。
        """
        last_err: Exception | None = None
        for wait_until in ("domcontentloaded", "commit"):
            try:
                if wait_until == "commit":
                    resp = self.page.goto(url, wait_until=wait_until, timeout=15000)
                else:
                    resp = self.page.goto(url, wait_until=wait_until)
                status = resp.status if resp else 0
                if wait_until == "commit":
                    self.page.wait_for_timeout(800)
                return f"[navigate] HTTP {status}\n{self.snapshot()}"
            except Exception as e:  # noqa: BLE001
                last_err = e
        return f"[navigate] 失败：{last_err}"

    def fetch(self) -> str:
        """获取当前页面可读文本。"""
        return self.snapshot()

    def click(self, selector: str) -> str:
        """点击元素（支持 CSS、XPath 或可见文本）。"""
        page = self.page
        # 1) 先尝试按 CSS/XPath 定位
        loc = page.locator(selector).first
        if loc.count() == 0:
            # 2) 退化为按文本定位
            loc = page.get_by_text(selector, exact=True).first
            if loc.count() == 0:
                loc = page.get_by_text(selector).first
        if loc.count() == 0:
            return f"[click] 未找到元素：{selector}"

        try:
            loc.click()
        except Exception:
            loc.click(force=True)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)
        return f"[click] 已点击：{selector}\n{self.snapshot()}"

    def scroll(self, times: int = 1) -> str:
        """向下滚动页面，触发懒加载。"""
        for _ in range(max(1, min(times, 10))):
            self.page.mouse.wheel(0, 1500)
            self.page.wait_for_timeout(500)
        return f"[scroll] 已滚动 {times} 次\n{self.snapshot()}"