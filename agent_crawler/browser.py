"""Playwright 浏览器工具封装。

向智能体暴露：navigate / fetch / click / scroll 等浏览器动作，
并负责生成精简页面快照（去脚本/样式噪声、截断）以控制 Token。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse

# 移动端 UA：部分站点（如京东）桌面商品页直接拦截，手机页面仍可访问
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

# 京东桌面商品页地址（item.jd.com/{sku}.html）
_JD_ITEM_RE = re.compile(r"^https?://item\.jd\.com/(?:product/)?(\d+)\.html", re.IGNORECASE)


def mobile_detail_url(url: str) -> str:
    """把京东桌面商品链接换成可访问的移动端链接；其它站点原样返回。"""
    match = _JD_ITEM_RE.match((url or "").strip())
    if not match:
        return url or ""
    return f"https://item.m.jd.com/product/{match.group(1)}.html"

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


def _meaningful(value: Any) -> bool:
    """判断字段值是否是有效内容（用于挑选列表接口）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip() not in ("-", "暂无")
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _iter_arrays(node: Any, path: str = "", depth: int = 0):
    """递归遍历 JSON，产出（路径, 数组）。路径用点号分隔，列表索引用数字。"""
    if depth > 6:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _iter_arrays(value, child, depth + 1)
    elif isinstance(node, list):
        dict_items = [x for x in node[:5] if isinstance(x, dict)]
        if len(dict_items) >= 2:
            # 看起来像列表数据：只产出本层，不再下钻元素内部
            yield path, node
        else:
            for index, value in enumerate(node[:20]):
                if isinstance(value, (dict, list)):
                    child = f"{path}.{index}" if path else str(index)
                    yield from _iter_arrays(value, child, depth + 1)


def scan_candidates(
    payload: Any,
    source: str = "",
    method: str = "GET",
    limit: int = 5,
    min_items: int = 3,
) -> list[dict]:
    """从一段 JSON 中找出「列表数据」候选（数组 + 稳定的标量字段）。

    返回字段：url / method / items_path / count / fields / sample / score / payload。
    评分主要看公共字段数量与列表长度：广告位、配置项这类短小数组会被淘汰。
    """
    found: list[dict] = []
    for path, items in _iter_arrays(payload):
        if not isinstance(items, list) or len(items) < min_items:
            continue
        sample = [x for x in items[:20] if isinstance(x, dict)]
        if len(sample) < min_items:
            continue
        fields: list[str] = []
        for key in sample[0].keys():
            values = [x.get(key) for x in sample]
            if not any(_meaningful(v) for v in values):
                continue
            # 全是嵌套对象/数组的字段对提取帮助不大，跳过
            scalars = [v for v in values if v is not None]
            if scalars and all(isinstance(v, (dict, list)) for v in scalars):
                continue
            fields.append(str(key))
        if len(fields) < 3:
            continue
        score = round(len(fields) + min(len(items), 50) / 10.0, 2)
        found.append(
            {
                "url": source,
                "method": method,
                "items_path": path,
                "count": len(items),
                "fields": fields[:30],
                "sample": sample[0],
                "score": score,
                "payload": payload,
            }
        )
    found.sort(key=lambda c: (c["score"], c["count"]), reverse=True)
    return found[:limit]


# 取值：支持 "css"、"css@属性名"、"css@属性名#JSON字段路径"、"js:window对象路径"
_READ_VALUE_JS = """
  const readJs = (expr) => {
    let cur = window;
    for (const part of expr.slice(3).trim().split('.')) {
      if (!part) continue;
      if (cur == null) return '';
      cur = cur[part];
    }
    return cur == null ? '' : String(cur).trim();
  };
  const read = (root, sel) => {
    if (!sel) return '';
    if (/^js:[A-Za-z_$][\\w$]*(\\.[\\w$]+)+$/.test(sel)) return readJs(sel);
    let css = sel, attr = '', jsonPath = '';
    const at = sel.lastIndexOf('@');
    if (at > 0) {
      css = sel.slice(0, at);
      attr = sel.slice(at + 1);
      const hash = attr.indexOf('#');
      if (hash > 0) { jsonPath = attr.slice(hash + 1); attr = attr.slice(0, hash); }
    }
    let el = null;
    try { el = root.querySelector(css); } catch (e) { return ''; }
    if (!el) return '';
    if (attr) {
      const raw = el.getAttribute(attr);
      if (raw == null) return '';
      if (jsonPath) {
        let value = null;
        try { value = JSON.parse(raw); } catch (e) { return ''; }
        for (const part of jsonPath.split('.')) {
          if (value == null || typeof value !== 'object') return '';
          value = value[part];
        }
        return value == null ? '' : String(value).trim().replace(/\\s+/g, ' ');
      }
      let v = raw.trim();
      if ((attr === 'href' || attr === 'src') && v) {
        try { v = new URL(v, location.href).href; } catch (e) {}
      }
      return v;
    }
    return ((el.innerText || el.textContent || '') + '').trim().replace(/\\s+/g, ' ');
  };
"""

# 读取单个元素：支持 "css@属性名" 与 JSON 属性路径，href/src 自动补全为绝对地址
_EXTRACT_FIELDS_JS = """(fieldMap) => {""" + _READ_VALUE_JS + """
  const out = {};
  for (const name of Object.keys(fieldMap)) {
    const value = read(document, fieldMap[name]);
    if (value) out[name] = value;
  }
  return out;
}"""

# 按卡片选择器批量取值，同时从每张卡片里取出详情页链接
_EXTRACT_CARDS_JS = """([itemSel, fieldMap, linkSel, limit]) => {""" + _READ_VALUE_JS + """
  const out = [];
  let nodes = [];
  try { nodes = document.querySelectorAll(itemSel); } catch (e) { return out; }
  for (const el of nodes) {
    if (limit > 0 && out.length >= limit) break;
    const row = {};
    let has = false;
    for (const name of Object.keys(fieldMap)) {
      const value = read(el, fieldMap[name]);
      if (value) { row[name] = value; has = true; }
    }
    if (!has) continue;
    const href = read(el, linkSel || 'a[href]');
    if (href) row['_url'] = href;
    out.push(row);
  }
  return out;
}"""

# 去掉脚本/样式后的 HTML：给模型当详情页样例，避免被内联脚本挤占长度
_HTML_SNAPSHOT_JS = """() => {
  if (!document.body) return '';
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script, style, noscript, iframe, link').forEach(e => e.remove());
  return clone.innerHTML;
}"""

# 在 window 上的内嵌数据里按键名找值（如 upc/isbn），给模型提供 js: 路径
_JS_VALUES_JS = """([keys, limit]) => {
  const lower = keys.map(k => String(k).toLowerCase());
  const out = [];
  const seen = new Set();
  const walk = (node, path, depth) => {
    if (!node || typeof node !== 'object' || depth > 4 || out.length >= limit) return;
    if (node === window || node.window === node) return;
    if (seen.has(node)) return;
    seen.add(node);
    for (const key of Object.keys(node)) {
      if (out.length >= limit) return;
      if (key === 'top' || key === 'parent' || key === 'self' || key === 'window') continue;
      let value;
      try { value = node[key]; } catch (e) { continue; }
      if (value && typeof value === 'object') { walk(value, path + '.' + key, depth + 1); continue; }
      if (value == null || value === '' || typeof value === 'function') continue;
      if (typeof value === 'string' && value.length > 80) continue;
      const name = String(key).toLowerCase();
      if (!lower.some(k => name.includes(k))) continue;
      out.push({ path: path + '.' + key, value: String(value) });
    }
  };
  // 先扫下划线开头的全局数据对象（页面数据通常挂在这里），再扫其它
  const names = Object.keys(window).sort((a, b) => {
    const pa = a.startsWith('_') ? 0 : 1, pb = b.startsWith('_') ? 0 : 1;
    return pa - pb;
  });
  for (const key of names) {
    let value;
    try { value = window[key]; } catch (e) { continue; }
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
    walk(value, key, 0);
  }
  return out;
}"""


# 找出页面上重复出现的卡片结构（选择器 + 第一条样例 HTML）与可用的「下一页」选择器
_LIST_HINT_JS = """([limit, sampleChars]) => {
  const classesOf = (el) => {
    const raw = typeof el.className === 'string' ? el.className : '';
    return raw.split(/\\s+/).filter(c => c && /^[A-Za-z_][\\w-]*$/.test(c)).slice(0, 3);
  };
  const isDisabled = (el) => {
    const cls = typeof el.className === 'string' ? el.className : '';
    const style = window.getComputedStyle(el);
    return el.getAttribute('aria-disabled') === 'true' || el.disabled === true
      || /(^|[\\s-])(disabled|is-disabled)(\\s|$)/.test(cls)
      || style.display === 'none' || style.visibility === 'hidden';
  };

  const groups = new Map();
  for (const el of document.querySelectorAll('body *')) {
    if (!el.children || el.children.length === 0) continue;
    const cls = classesOf(el);
    if (!cls.length && el.tagName.toLowerCase() !== 'li') continue;
    const text = ((el.innerText || '') + '').trim();
    if (text.length < 8 || text.length > 500) continue;
    const selector = el.tagName.toLowerCase() + cls.map(c => '.' + c).join('');
    if (selector.length > 80) continue;
    let group = groups.get(selector);
    if (!group) {
      group = { selector: selector, count: 0, len: 0, sample: el.outerHTML };
      groups.set(selector, group);
    }
    group.count += 1;
    group.len += text.length;
  }

  const scored = [];
  for (const group of groups.values()) {
    if (group.count < 3 || group.count > 200) continue;
    const avg = group.len / group.count;
    if (avg < 15) continue;
    scored.push({
      selector: group.selector,
      count: group.count,
      score: group.count * (1 + Math.min(avg, 400) / 400),
      sample: group.sample,
    });
  }
  scored.sort((a, b) => b.score - a.score);
  const items = scored.slice(0, Math.max(1, limit)).map(x => {
    const holder = document.createElement('div');
    holder.innerHTML = x.sample;
    const node = holder.firstElementChild;
    let jsonAttr = '', jsonFields = {};
    if (node) {
      for (const el of [node].concat(Array.from(node.querySelectorAll('*')))) {
        for (const attr of Array.from(el.attributes || [])) {
          const raw = attr.value || '';
          if (raw.length < 20 || raw[0] !== '{') continue;
          try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) continue;
            const flat = {};
            for (const key of Object.keys(parsed)) {
              const value = parsed[key];
              if (value == null || typeof value === 'object') continue;
              flat[key] = String(value).slice(0, 120);
            }
            if (Object.keys(flat).length >= 2) {
              jsonAttr = el.tagName.toLowerCase() + '@' + attr.name;
              jsonFields = flat;
            }
          } catch (e) {}
        }
        if (jsonAttr) break;
      }
    }
    return {
      selector: x.selector,
      count: x.count,
      sample_html: String(x.sample || '').slice(0, sampleChars),
      json_attr: jsonAttr,
      json_fields: jsonFields,
    };
  });

  let nextSelector = '';
  const known = ['a.pn-next', '.pn-next', 'a[rel=next]', '.pagination-next',
    '.pagination-next-btn', '.next-page', '.page-next'];
  for (const sel of known) {
    let el = null;
    try { el = document.querySelector(sel); } catch (e) { continue; }
    if (el && !isDisabled(el)) { nextSelector = sel; break; }
  }
  if (!nextSelector) {
    for (const el of document.querySelectorAll('a, button, div, span, li')) {
      const text = ((el.innerText || '') + '').trim();
      if (!['下一页', '下页', 'Next', 'next'].includes(text)) continue;
      if (isDisabled(el)) continue;
      const cls = classesOf(el);
      if (!cls.length) continue;
      nextSelector = el.tagName.toLowerCase() + '.' + cls[0];
      break;
    }
  }
  return { items: items, next_selector: nextSelector };
}"""


class NetworkRecorder:
    """捕获页面自身的 XHR/Fetch JSON 响应，并识别其中的列表接口。

    站点通常由前端调用接口渲染列表；把这个接口记下来，后续即可直接请求
    结构化数据，不必再把整页文本交给模型逐条抄写。
    """

    def __init__(self, max_records: int = 60, max_body_bytes: int = 3_000_000):
        self.max_records = max_records
        self.max_body_bytes = max_body_bytes
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def attach(self, page: Any) -> None:
        """挂到页面的 response 事件上；失败不影响正常采集。"""
        try:
            page.on("response", self._on_response)
        except Exception:
            pass

    def _on_response(self, response: Any) -> None:
        try:
            request = response.request
            if request.resource_type not in ("xhr", "fetch"):
                return
            content_type = str((response.headers or {}).get("content-type") or "").lower()
            if "json" not in content_type:
                return
            body = response.body()
            if not body or len(body) > self.max_body_bytes:
                return
            payload = json.loads(body.decode("utf-8", "ignore"))
        except Exception:  # noqa: BLE001 - 捕获失败不能影响页面
            return
        with self._lock:
            if len(self._records) >= self.max_records:
                return
            self._records.append(
                {"url": response.url, "method": request.method, "payload": payload}
            )

    def candidates(self, limit: int = 5, min_items: int = 3) -> list[dict]:
        """汇总所有已捕获响应，按「像列表接口」的程度排序返回。"""
        with self._lock:
            records = list(self._records)
        merged: list[dict] = []
        for record in records:
            merged.extend(
                scan_candidates(
                    record["payload"],
                    source=record["url"],
                    method=record["method"],
                    limit=limit,
                    min_items=min_items,
                )
            )
        merged.sort(key=lambda c: (c["score"], c["count"]), reverse=True)
        # 同一接口的多个分页响应只保留一条
        seen: set[tuple[str, str]] = set()
        unique: list[dict] = []
        for item in merged:
            key = (urlparse(item["url"]).path, item["items_path"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique[:limit]


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
        profile_dir: str | None = None,
    ):
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent
        self.headless = headless
        self.channel = channel
        self.block_resources = tuple(x.lower() for x in block_resources)
        self.reuse = reuse
        self.idle_timeout_s = idle_timeout_s
        # 指定 profile 目录时使用持久化浏览器上下文，复用该站点的登录态
        self.profile_dir = profile_dir
        self._pw = None
        self._browser = None
        self._context = None
        self._mobile_context = None
        self.page: Any = None
        # 记录页面自身的接口响应，用于发现列表接口
        self.recorder = NetworkRecorder()

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

    def _launch_persistent(self, profile_dir: str):
        """启动持久化上下文（携带登录态）：与 _launch_browser 相同的回退顺序。"""
        user_agent = (
            self.user_agent
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        last_err: Exception | None = None
        for channel in (None, "msedge", "chrome"):
            kwargs: dict[str, Any] = {
                "headless": self.headless,
                "user_agent": user_agent,
                "viewport": {"width": 1280, "height": 800},
            }
            if channel:
                kwargs["channel"] = channel
            try:
                return self._pw.chromium.launch_persistent_context(profile_dir, **kwargs)
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

        if self.profile_dir:
            # 网站记忆模式：用持久化上下文加载该站点的登录态（Cookie/本地存储）
            self._pw = sync_playwright().start()
            try:
                self._context = self._launch_persistent(self.profile_dir)
            except Exception as e:  # noqa: BLE001
                self._pw.stop()
                self._pw = None
                raise RuntimeError(
                    "无法加载网站记忆的登录态：请先关闭该站点的登录窗口后重试"
                ) from e
            self._context.set_default_timeout(self.timeout_ms)
            if self.block_resources:
                self._context.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in self.block_resources
                    else route.continue_(),
                )
            self.page = (
                self._context.pages[0]
                if self._context.pages
                else self._context.new_page()
            )
            self.recorder.attach(self.page)
            return

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
        self.recorder.attach(self.page)

    def close(self) -> None:
        """关闭本次任务的页面；复用模式下保留浏览器供后续任务使用。"""
        try:
            if self._mobile_context:
                self._mobile_context.close()
        except Exception:
            pass
        self._mobile_context = None
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        self._context = None
        self.page = None

        if self.profile_dir:
            # 持久化上下文关闭后直接释放 Playwright（不进入线程池）
            self._shutdown_browser()
            return

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
        """生成精简可读快照：正文文本 + 关键链接（含地址）+ 按钮文本。"""
        page = self.page
        text = page.evaluate(
            "() => document.body ? document.body.innerText || '' : ''"
        )
        text = (text or "").strip()

        # 关键链接：保留「文本 -> 地址」，便于提取详情页 URL / 资源 ID
        links = page.evaluate(
            """() => {
              const seen = new Set();
              const out = [];
              for (const a of document.querySelectorAll('a[href]')) {
                const t = (a.innerText || '').trim();
                const h = a.href || '';
                if (!t || t.length > 80 || !h.startsWith('http')) continue;
                const key = t + '|' + h;
                if (seen.has(key)) continue;
                seen.add(key);
                out.push([t, h]);
                if (out.length >= 80) break;
              }
              return out;
            }"""
        )
        buttons = page.evaluate(
            """() => Array.from(document.querySelectorAll('button, [role=button], input[type=submit]'))
              .map(b => (b.innerText || b.value || '').trim())
              .filter(t => t && t.length <= 60)
              .filter((t, i, arr) => arr.indexOf(t) === i)
              .slice(0, 80)"""
        )
        # 可点击元素编号：不依赖标签类型，翻页 / 商品卡片这类元素交给 click_ref(index) 点击
        clickables = page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              const nodes = document.querySelectorAll(
                'a, button, [role=button], [onclick], div[class*=page], div[class*=next], ' +
                'span[class*=next], li[class*=next], div[class*=more], span[class*=more]'
              );
              for (const el of nodes) {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const text = ((el.innerText || el.value || '') + '').trim().replace(/\\s+/g, ' ');
                if (!text || text.length > 40) continue;
                const cls = typeof el.className === 'string' ? el.className : '';
                const disabled = el.getAttribute('aria-disabled') === 'true'
                  || el.disabled === true
                  || /(^|[\\s-])(disabled|is-disabled)(\\s|$)/.test(cls);
                const key = el.tagName + '|' + text;
                if (seen.has(key)) continue;
                seen.add(key);
                el.setAttribute('data-magi-ref', String(out.length));
                out.push({ i: out.length, tag: el.tagName.toLowerCase(), text: text, disabled: disabled });
                if (out.length >= 40) break;
              }
              return out;
            }"""
        )

        parts = []
        if text:
            # 同一页面常重复渲染列表，保留前 max_chars 字符即可
            parts.append("【页面文本】\n" + text[:max_chars])
        if links:
            # 去掉域名前缀，控制 token 消耗
            rows = []
            for label, href in links:
                path = href.replace("https://", "").replace("http://", "")
                path = path[:100]
                rows.append(f"{label} -> {path}")
            parts.append("【链接（文本 -> 地址）】\n" + "\n".join(rows))
        if buttons:
            parts.append("【按钮/可点击文本】\n" + " | ".join(buttons))
        if clickables:
            rows = []
            for item in clickables:
                mark = "（禁用）" if item.get("disabled") else ""
                rows.append(f"#{item['i']} {item['tag']} {item['text']}{mark}")
            parts.append("【可点击元素（序号 标签 文本）】\n" + "\n".join(rows))
        return "\n\n".join(parts).strip()

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

    def current_url(self) -> str:
        """返回当前页面实际地址（导航后可能与起始地址不同）。"""
        try:
            return self.page.url if self.page else ""
        except Exception:  # noqa: BLE001
            return ""

    def _body_hash(self) -> str:
        """当前页面正文指纹，用于判断点击后内容是否真的变化。"""
        try:
            text = self.page.inner_text("body")
        except Exception:  # noqa: BLE001
            return ""
        return hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()

    def _wait_after_click(self, before_hash: str, timeout_ms: int = 3000) -> bool:
        """点击后等待加载与内容变化，返回内容是否发生变化。"""
        page = self.page
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 4000))
        except Exception:  # noqa: BLE001 - SPA 点击没有页面加载事件
            pass
        waited = 0
        while waited < timeout_ms:
            if self._body_hash() != before_hash:
                return True
            page.wait_for_timeout(300)
            waited += 300
        return False

    def _clear_item_marks(self) -> None:
        """清理卡片定位标记，避免下一轮定位到旧卡片。"""
        try:
            self.page.evaluate(
                "() => document.querySelectorAll('[data-magi-item]')"
                ".forEach(e => e.removeAttribute('data-magi-item'))"
            )
        except Exception:  # noqa: BLE001
            pass

    def _read_mobile_detail(self, url: str) -> tuple[str, str]:
        """用手机 UA 打开商品详情页，返回 (实际地址, 正文)；失败返回空文本。"""
        ctx = self._ensure_mobile_context()
        target = mobile_detail_url(url)
        if ctx is None or not target:
            return target, ""
        page = None
        try:
            page = ctx.new_page()
            page.set_default_timeout(min(15000, self.timeout_ms))
            page.goto(target, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(400)
            text = page.inner_text("body")[:6000]
            return page.url, text
        except Exception:  # noqa: BLE001
            return target, ""
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def click_ref(self, index: Any) -> str:
        """按快照中的可点击元素编号点击，不依赖标签类型（兼容 div 分页、卡片等）。"""
        page = self.page
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return f"[click_ref] 编号无效：{index}"
        loc = page.locator(f'[data-magi-ref="{idx}"]').first
        if loc.count() == 0:
            return f"[click_ref] 未找到编号 #{idx}，页面可能已刷新，请重新观察快照后再点击"
        before_hash = self._body_hash()
        pages_before = len(self._context.pages) if self._context else 1
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=5000)
        except Exception:
            try:
                loc.click(force=True)
            except Exception as e:  # noqa: BLE001
                return f"[click_ref] 点击 #{idx} 失败：{e}"
        changed = self._wait_after_click(before_hash)
        suffix = "" if changed else "（点击后页面内容没有变化）"
        opened = ""
        # 点击打开了新标签页时记录地址并关闭，避免标签页越积越多
        if self._context and len(self._context.pages) > pages_before:
            extra = self._context.pages[-1]
            opened = f"，新标签页：{extra.url}"
            try:
                extra.close()
            except Exception:  # noqa: BLE001
                pass
        return f"[click_ref] 已点击 #{idx}{suffix}{opened}，当前地址：{self.current_url()}\n{self.snapshot()}"

    def open_item(self, title: str) -> tuple[str, str, bool]:
        """点击文本包含 title 的商品卡片，返回 (详情页地址, 快照, 是否同一标签页)。

        用于卡片没有 href、只能点击进入的站点（如京东推广页 / SPA 列表）。
        找不到卡片或点击后没有跳转时返回空值。
        """
        page = self.page
        key = (title or "").strip()
        if not key:
            return "", "", False
        pages_before = len(self._context.pages) if self._context else 1
        before_hash = self._body_hash()
        before_url = self.current_url()
        self._clear_item_marks()
        marked = page.evaluate(
            """(title) => {
              const norm = s => (s || '').replace(/\\s+/g, '');
              const key = norm(title).slice(0, 16);
              if (!key) return false;
              let best = null, bestLen = Infinity;
              const nodes = document.querySelectorAll(
                'li, a, div[class*=item], div[class*=goods], div[class*=product], div[class*=card]'
              );
              for (const el of nodes) {
                const txt = norm(el.innerText || '');
                if (!txt || !txt.includes(key)) continue;
                if (txt.length < bestLen) { best = el; bestLen = txt.length; }
              }
              if (!best) return false;
              best.setAttribute('data-magi-item', '1');
              return true;
            }""",
            key,
        )
        if not marked:
            return "", "", False
        # 卡片自带商品地址时优先直连：京东桌面详情被拦截，直接走手机页面
        hinted_url = page.evaluate(
            """() => {
              const el = document.querySelector('[data-magi-item="1"]');
              if (!el) return '';
              const img = el.querySelector('img[data-item]');
              if (img) {
                try {
                  const data = JSON.parse(img.getAttribute('data-item') || '{}');
                  if (data.landUrl) return data.landUrl;
                } catch (e) {}
              }
              const a = el.querySelector('a[href]');
              if (a) {
                const href = a.getAttribute('href') || '';
                if (href && !/^javascript:/i.test(href)) return a.href;
              }
              return '';
            }"""
        )
        if hinted_url and _JD_ITEM_RE.match(hinted_url):
            self._clear_item_marks()
            mobile_url, mobile_text = self._read_mobile_detail(hinted_url)
            if mobile_text:
                return mobile_url, mobile_text, False
            return "", "", False
        loc = page.locator('[data-magi-item="1"]').first
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=6000)
        except Exception:
            try:
                loc.click(force=True)
            except Exception:  # noqa: BLE001
                return "", "", False
        try:
            page.wait_for_load_state("domcontentloaded", timeout=6000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(800)
        self._clear_item_marks()

        # 卡片在新标签页打开：读取新页内容后关闭，主页面仍是列表
        if self._context and len(self._context.pages) > pages_before:
            detail = self._context.pages[-1]
            try:
                detail.wait_for_load_state("domcontentloaded", timeout=6000)
            except Exception:  # noqa: BLE001
                pass
            url = detail.url
            blocked = "pc-frequent-pro" in url or "reason=403" in url
            text = ""
            if not blocked:
                try:
                    text = detail.inner_text("body")[:6000]
                except Exception:  # noqa: BLE001
                    text = ""
            try:
                detail.close()
            except Exception:  # noqa: BLE001
                pass
            # 京东桌面详情被风控拦截：用卡片里的商品地址改走手机页面
            if blocked or _JD_ITEM_RE.match(url):
                source = url if _JD_ITEM_RE.match(url) else hinted_url
                mobile_url, mobile_text = self._read_mobile_detail(source)
                if mobile_text:
                    return mobile_url, mobile_text, False
            return url, text, False

        # 同标签页跳转：地址变化或内容变化才算打开成功
        changed = self._wait_after_click(before_hash, timeout_ms=1500)
        after_url = self.current_url()
        if not changed and after_url == before_url:
            return "", "", False
        # 同标签页跳到京东商品页（或被风控页拦截）时改走手机页面，并退回列表
        blocked = "pc-frequent-pro" in after_url or "reason=403" in after_url
        if _JD_ITEM_RE.match(after_url) or blocked:
            source = after_url if _JD_ITEM_RE.match(after_url) else hinted_url
            try:
                page.go_back()
            except Exception:  # noqa: BLE001
                pass
            mobile_url, mobile_text = self._read_mobile_detail(source)
            if mobile_text:
                return mobile_url, mobile_text, False
        return after_url, self.snapshot(6000), True

    def click_next_page(self) -> str:
        """点击「下一页」并返回新页面快照；找不到或已到末页时返回空字符串。"""
        page = self.page
        selectors = (
            "a.pn-next",
            ".pn-next",
            "a[rel=next]",
            "div.pagination-next",
            ".pagination-next",
            ".pagination-next-btn",
            "a:has-text('下一页')",
            "button:has-text('下一页')",
            "a:has-text('下页')",
        )
        before_hash = self._body_hash()
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() == 0 or not loc.is_visible():
                    continue
                cls = f"{loc.get_attribute('class') or ''} {loc.get_attribute('aria-disabled') or ''}"
                if "disabled" in cls.lower():
                    return ""
                loc.scroll_into_view_if_needed(timeout=3000)
                loc.click()
                if not self._wait_after_click(before_hash):
                    continue
                page.wait_for_timeout(300)
                return self.snapshot()
            except Exception:  # noqa: BLE001 - 换下一个候选选择器
                continue
        # 兜底：按文本找「下一页」，兼容 div / span / li 分页控件
        marked = page.evaluate(
            """() => {
              const targets = ['下一页', '下页', 'Next'];
              for (const el of document.querySelectorAll('a, button, div, span, li')) {
                const t = ((el.innerText || '') + '').trim();
                if (!targets.includes(t)) continue;
                const cls = typeof el.className === 'string' ? el.className : '';
                if (/(^|[\\s-])disabled/.test(cls.toLowerCase())) continue;
                if (el.getAttribute('aria-disabled') === 'true') continue;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                el.setAttribute('data-magi-next', '1');
                return true;
              }
              return false;
            }"""
        )
        if not marked:
            return ""
        try:
            loc = page.locator('[data-magi-next="1"]').first
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=5000)
            if not self._wait_after_click(before_hash):
                return ""
            return self.snapshot()
        except Exception:  # noqa: BLE001
            return ""

    # ---------------- DOM 配方支持 ----------------

    def extract_dom(
        self,
        item_selector: str,
        field_selectors: dict[str, str],
        link_selector: str = "",
        limit: int = 0,
    ) -> list[dict]:
        """按卡片选择器批量取值：返回每张卡片的字段字典（含 _url 详情链接）。

        field_selectors 的值为标准 CSS 选择器；"css@属性名" 表示取属性值。
        """
        if not self.page or not item_selector or not field_selectors:
            return []
        try:
            rows = self.page.evaluate(
                _EXTRACT_CARDS_JS,
                [item_selector, field_selectors, link_selector, max(0, int(limit or 0))],
            )
        except Exception:  # noqa: BLE001 - 选择器失效时交给调用方回退
            return []
        return rows if isinstance(rows, list) else []

    def click_selector(self, selector: str) -> bool:
        """按 CSS 选择器点击（翻页等），内容确实变化才返回 True。"""
        if not selector or not self.page:
            return False
        page = self.page
        try:
            loc = page.locator(selector).first
            if loc.count() == 0 or not loc.is_visible():
                return False
            marks = " ".join(
                filter(
                    None,
                    [
                        loc.get_attribute("class") or "",
                        loc.get_attribute("aria-disabled") or "",
                    ],
                )
            ).lower()
            if "disabled" in marks:
                return False
            before = self._body_hash()
            pages_before = len(self._context.pages) if self._context else 1
            loc.scroll_into_view_if_needed(timeout=3000)
            try:
                loc.click(timeout=5000)
            except Exception:
                loc.click(force=True)
            changed = self._wait_after_click(before)
            # 翻页按钮若误开新标签页，直接关掉，保持主页面在列表
            if self._context and len(self._context.pages) > pages_before:
                try:
                    self._context.pages[-1].close()
                except Exception:  # noqa: BLE001
                    pass
            return changed
        except Exception:  # noqa: BLE001
            return False

    def _ensure_mobile_context(self):
        """懒加载手机 UA 上下文：桌面详情页被风控拦截时用它兜底。"""
        if self._mobile_context:
            return self._mobile_context
        browser = self._browser
        if browser is None and self._context is not None:
            try:
                browser = self._context.browser
            except Exception:  # noqa: BLE001
                browser = None
        if browser is None:
            return None
        try:
            ctx = browser.new_context(
                user_agent=IPHONE_UA,
                viewport={"width": 390, "height": 844},
                device_scale_factor=2,
                is_mobile=True,
                has_touch=True,
            )
            ctx.set_default_timeout(self.timeout_ms)
            self._mobile_context = ctx
        except Exception:  # noqa: BLE001
            return None
        return self._mobile_context

    def _detail_context(self, mobile: bool):
        """详情页使用哪个上下文：mobile=True 时优先手机上下文。"""
        if mobile:
            ctx = self._ensure_mobile_context()
            if ctx is not None:
                return ctx
        return self._context

    def extract_detail(
        self, url: str, field_selectors: dict[str, str], mobile: bool = False
    ) -> dict:
        """在后台标签页打开详情页，按选择器取值后关闭标签页。"""
        ctx = self._detail_context(mobile)
        if ctx is None or not url or not field_selectors:
            return {}
        target = mobile_detail_url(url) if mobile else url
        page = None
        try:
            page = ctx.new_page()
            page.set_default_timeout(min(15000, self.timeout_ms))
            page.goto(target, wait_until="domcontentloaded")
            if mobile:
                # 手机详情页的内嵌数据（作者/ISBN）在脚本执行后才就绪，多等一会
                page.wait_for_timeout(1000)
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(400)
            else:
                page.wait_for_timeout(300)
            found = page.evaluate(_EXTRACT_FIELDS_JS, field_selectors)
            return found if isinstance(found, dict) else {}
        except Exception:  # noqa: BLE001 - 单条详情失败不影响整体
            return {}
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def fetch_detail_sample(
        self,
        url: str,
        max_chars: int = 8000,
        mobile: bool = False,
        js_keys: list[str] | None = None,
    ) -> dict:
        """打开详情页，返回去脚本的 HTML 片段与内嵌数据里的目标键值。"""
        ctx = self._detail_context(mobile)
        if ctx is None or not url:
            return {"html": "", "js_values": []}
        target = mobile_detail_url(url) if mobile else url
        page = None
        try:
            page = ctx.new_page()
            page.set_default_timeout(min(15000, self.timeout_ms))
            page.goto(target, wait_until="domcontentloaded")
            page.wait_for_timeout(300)
            html = str(page.evaluate(_HTML_SNAPSHOT_JS) or "")[:max_chars]
            js_values = (
                page.evaluate(_JS_VALUES_JS, [list(js_keys), 30]) if js_keys else []
            )
            return {
                "html": html,
                "js_values": js_values if isinstance(js_values, list) else [],
            }
        except Exception:  # noqa: BLE001
            return {"html": "", "js_values": []}
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def find_js_values(self, keys: list[str], limit: int = 30) -> list[dict]:
        """在当前页面的内嵌数据里按键名找值，返回 [{path, value}]。"""
        if not self.page or not keys:
            return []
        try:
            found = self.page.evaluate(_JS_VALUES_JS, [list(keys), max(1, limit)])
        except Exception:  # noqa: BLE001
            return []
        return found if isinstance(found, list) else []

    def list_hints(self, limit: int = 2, sample_chars: int = 2500) -> dict:
        """识别页面上重复出现的卡片结构与「下一页」按钮，供生成 DOM 配方。"""
        if not self.page:
            return {"items": [], "next_selector": ""}
        try:
            hint = self.page.evaluate(_LIST_HINT_JS, [limit, sample_chars])
        except Exception:  # noqa: BLE001
            return {"items": [], "next_selector": ""}
        if not isinstance(hint, dict):
            return {"items": [], "next_selector": ""}
        items = hint.get("items")
        return {
            "items": items if isinstance(items, list) else [],
            "next_selector": str(hint.get("next_selector") or ""),
        }

    # ---------------- 接口直采 ----------------

    def network_candidates(self, limit: int = 5) -> list[dict]:
        """返回页面自身请求到的列表接口候选（按相似度排序）。"""
        try:
            return self.recorder.candidates(limit=limit)
        except Exception:  # noqa: BLE001 - 候选仅用于加速，失败可忽略
            return []

    def request_json(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        timeout_ms: int | None = None,
    ) -> Any:
        """复用当前浏览器上下文的 Cookie 直接请求接口，返回 JSON。

        这样能沿用站点登录态与已获得的匿名 Cookie，同时避免再渲染页面。
        """
        if not self._context:
            raise RuntimeError("浏览器尚未启动")
        merged = {"Accept": "application/json, text/plain, */*"}
        try:
            page_url = self.page.url if self.page else ""
        except Exception:  # noqa: BLE001
            page_url = ""
        if page_url.startswith("http"):
            merged.setdefault("Referer", page_url)
        merged.update(headers or {})

        request = self._context.request
        kwargs = {"headers": merged, "timeout": timeout_ms or self.timeout_ms}
        resp = request.post(url, **kwargs) if method.upper() == "POST" else request.get(url, **kwargs)
        if not resp.ok:
            raise RuntimeError(f"接口返回 HTTP {resp.status}")
        return resp.json()