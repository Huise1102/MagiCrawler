"""网站记忆登录助手：打开有头浏览器，让用户手动登录并保存登录态。

由后端以子进程方式调用，用户完成登录后关闭浏览器窗口即可：
    python -m agent_crawler.site_login <profile_dir> <url>
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path


def _launch_context(pw, profile_dir: Path):
    """按 Chromium → Edge → Chrome 的顺序启动持久化浏览器。"""
    last_err: Exception | None = None
    for channel in (None, "msedge", "chrome"):
        kwargs = {"headless": False, "viewport": {"width": 1280, "height": 800}}
        if channel:
            kwargs["channel"] = channel
        try:
            return pw.chromium.launch_persistent_context(str(profile_dir), **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError("无法启动登录浏览器：Chromium / Edge / Chrome 均不可用") from last_err


def main() -> int:
    if len(sys.argv) < 3:
        print("用法：python -m agent_crawler.site_login <profile_dir> <url>")
        return 2

    profile_dir = Path(sys.argv[1]).resolve()
    url = sys.argv[2].strip()
    profile_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    closed = threading.Event()

    with sync_playwright() as pw:
        context = _launch_context(pw, profile_dir)
        # 浏览器窗口关闭时 Context 会触发 close 事件
        context.on("close", lambda _ctx: closed.set())

        page = context.pages[0] if context.pages else context.new_page()
        if url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:  # noqa: BLE001 - 站点慢或需跳转，交给用户手动操作
                pass

        # 等待用户完成登录并关闭浏览器窗口
        while not closed.wait(1):
            pass

    (profile_dir / "login.json").write_text(
        json.dumps(
            {"logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
