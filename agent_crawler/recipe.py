"""采集配方：接口配方 + 页面结构（DOM）配方。

接口配方：站点列表由前端调用 JSON 接口渲染时，记下接口地址、列表路径、
字段映射与翻页参数，下次直接请求接口、确定性取值。

DOM 配方：列表由服务端直出 HTML 时，记下卡片选择器、字段选择器、翻页
按钮与详情页字段选择器，下次直接按选择器取文本。

两种配方都只在「生成」时让模型参与一次；执行阶段完全确定，失败则由
调用方回退到原有 ReAct 循环。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit

from .intent import Filter, apply_filters
from .schema import Field, validate_items

DEFAULT_MAX_PAGES = 5


class RecipeError(ValueError):
    """配方无效或执行失败。"""


def domain_of(url: str) -> str:
    """从网址中取出纯域名（小写、去端口）。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    return (urlparse(raw).netloc or "").split(":")[0].lower()


def _same_site(left: str, right: str) -> bool:
    """判断两个域名是否属于同一站点（允许子域名差异）。"""
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def json_at(data: Any, path: str) -> Any:
    """按点号路径取值：data.list、items.0.name；缺失返回 None。"""
    if not path:
        return data
    current = data
    for part in str(path).split("."):
        if part == "":
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def set_query(url: str, key: str, value: str) -> str:
    """在 URL 上设置查询参数（保留其它参数与锚点）。"""
    parts = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    pairs.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def page_url(recipe: "Recipe", page: int) -> str:
    """生成第 page 页的接口地址。"""
    if "{page}" in recipe.url:
        return recipe.url.replace("{page}", str(page))
    if not recipe.page_param:
        return recipe.url
    return set_query(recipe.url, recipe.page_param, str(page))


@dataclass
class Recipe:
    """一份可确定性执行的采集配方。"""

    domain: str
    url: str  # 接口地址；可含 {page} 占位
    items_path: str  # 列表在 JSON 中的路径，如 data.list
    field_map: dict[str, str] = field(default_factory=dict)  # 目标字段 -> 接口字段路径
    page_param: str = "page"  # 翻页参数名；为空表示接口不支持翻页
    page_start: int = 1
    page_step: int = 1
    max_pages: int = DEFAULT_MAX_PAGES
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    id: str = ""
    name: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
    use_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "Recipe":
        """从存储/接口数据恢复配方；结构不合法时抛 RecipeError。"""
        if isinstance(data, cls):
            return data
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                raise RecipeError("配方不是合法 JSON") from e
        if not isinstance(data, dict):
            raise RecipeError("配方结构不合法")

        domain = str(data.get("domain") or "").strip().lower()
        url = str(data.get("url") or "").strip()
        items_path = str(data.get("items_path") or "").strip()
        if not domain or not url.startswith("http") or not items_path:
            raise RecipeError("配方缺少域名、接口地址或列表路径")

        raw_map = data.get("field_map") or {}
        field_map = (
            {str(k): str(v) for k, v in raw_map.items() if str(v).strip()}
            if isinstance(raw_map, dict)
            else {}
        )
        if not field_map:
            raise RecipeError("配方没有字段映射")

        def _int(name: str, default: int) -> int:
            try:
                return int(data.get(name) or default)
            except (TypeError, ValueError):
                return default

        page_param = str(data.get("page_param") or "").strip()
        return cls(
            domain=domain,
            url=url,
            items_path=items_path,
            field_map=field_map,
            page_param=page_param,
            page_start=_int("page_start", 1),
            page_step=max(1, _int("page_step", 1)),
            max_pages=max(1, _int("max_pages", DEFAULT_MAX_PAGES if page_param else 1)),
            method=str(data.get("method") or "GET").upper(),
            headers=data.get("headers") if isinstance(data.get("headers"), dict) else {},
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_used_at=str(data.get("last_used_at") or ""),
            use_count=_int("use_count", 0),
        )


_RECIPE_PROMPT = """你在为一个网页的列表接口生成「采集配方」。

接口地址：{url}
列表位置：{items_path}（数组，共 {count} 条）
第一条数据示例：{sample}
该接口返回的其它字段：{top_fields}
需要提取的目标字段：{fields}

只输出紧凑 JSON（单行、无缩进、无解释）：
{{"url":"<接口完整地址>","items_path":"<列表在JSON中的路径>","fields":{{"目标字段":"接口字段"}},"page_param":"<翻页参数名>","page_start":1,"page_step":1}}

要求：
- fields 的键只能是上面的目标字段；值是接口里对应的字段名（嵌套用 a.b 表示）。
- 接口里确实找不到对应数据的目标字段不要写进 fields。
- page_param 填 URL 中控制页码/起始位置的参数名（如 page、pageNum、pageNo、offset）；看不出来或不支持翻页就填空字符串。
- url 保持接口原始地址，不要改域名与路径。"""


def build_recipe(
    llm,
    domain: str,
    candidate: dict,
    fields: list[Field],
    timeout: float = 60.0,
) -> Recipe | None:
    """让 LLM 基于一个接口候选生成配方；任何失败返回 None。"""
    payload = candidate.get("payload")
    if not domain or payload is None or not fields:
        return None
    names = [f.name for f in fields]
    prompt = _RECIPE_PROMPT.format(
        url=candidate.get("url", ""),
        items_path=candidate.get("items_path", ""),
        count=candidate.get("count", 0),
        sample=json.dumps(candidate.get("sample") or {}, ensure_ascii=False)[:1500],
        top_fields="、".join(candidate.get("fields") or [])[:600],
        fields="、".join(names),
    )
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": "你是接口采集配方生成器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - 生成失败走原有采集流程
        return None
    data = llm.extract_json(raw)
    return parse_recipe(data, domain, names, payload=payload)


def parse_recipe(
    data: Any,
    domain: str,
    field_names: list[str],
    payload: Any = None,
) -> Recipe | None:
    """严格校验模型输出；任何一处不合法即返回 None。"""
    if not isinstance(data, dict):
        return None

    url = str(data.get("url") or "").strip()
    if not url.startswith("http") or not _same_site(domain, domain_of(url)):
        return None

    items_path = str(data.get("items_path") or "").strip()
    if payload is not None:
        items = json_at(payload, items_path)
        if not isinstance(items, list) or len(items) < 1:
            return None

    allowed = set(field_names)
    raw_map = data.get("fields") or data.get("field_map") or {}
    field_map: dict[str, str] = {}
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            name = str(key).strip()
            source = str(value or "").strip()
            if name in allowed and source and source.lower() not in ("null", "none", "-"):
                field_map[name] = source
    if not field_map:
        return None

    # 映射至少要有一条能在真实数据里取到值，避免模型凭空编字段名
    if payload is not None:
        items = json_at(payload, items_path)
        sample = next(
            (x for x in (items or []) if isinstance(x, dict)), None
        ) if isinstance(items, list) else None
        if sample is not None:
            resolved = sum(1 for src in field_map.values() if json_at(sample, src) is not None)
            if resolved == 0:
                return None

    def _int(name: str, default: int) -> int:
        try:
            return int(data.get(name) or default)
        except (TypeError, ValueError):
            return default

    page_param = str(data.get("page_param") or "").strip()
    return Recipe(
        domain=domain.strip().lower(),
        url=url,
        items_path=items_path,
        field_map=field_map,
        page_param=page_param,
        page_start=_int("page_start", 1),
        page_step=max(1, _int("page_step", 1)),
        max_pages=DEFAULT_MAX_PAGES if page_param else 1,
        method=str(data.get("method") or "GET").upper(),
        name=f"{domain} 自动配方",
    )


def execute_recipe(
    tools,
    recipe: Recipe,
    fields: list[Field],
    filters: list[Filter] | None = None,
    target: int | None = None,
    max_pages: int | None = None,
) -> tuple[list[dict], int, int]:
    """按配方分页拉取数据，返回（条目, 被筛选丢弃数, 实际页数）。

    接口不可用或返回结构不符时抛 RecipeError，由调用方回退到智能体循环。
    """
    pages_limit = max(1, min(max_pages or recipe.max_pages, 50))
    if not recipe.page_param:
        pages_limit = 1

    collected: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    pages = 0
    page = recipe.page_start

    for _ in range(pages_limit):
        if target and len(collected) >= target:
            break
        url = page_url(recipe, page)
        try:
            payload = tools.request_json(url, method=recipe.method, headers=recipe.headers)
        except Exception as e:  # noqa: BLE001
            if pages == 0:
                raise RecipeError(f"接口请求失败：{e}") from e
            break

        items = json_at(payload, recipe.items_path)
        if not isinstance(items, list):
            if pages == 0:
                raise RecipeError(f"接口返回中没有列表字段 {recipe.items_path}")
            break

        raw_rows: list[dict] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            raw_rows.append(
                {
                    f.name: (json_at(raw, recipe.field_map[f.name]) if recipe.field_map.get(f.name) else None)
                    for f in fields
                }
            )
        candidates = validate_items(raw_rows, fields)
        kept, drop = apply_filters(candidates, filters or [])
        dropped += drop

        new_count = 0
        for item in kept:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen or not any(v is not None for v in item.values()):
                continue
            seen.add(key)
            collected.append(item)
            new_count += 1
        pages += 1

        if new_count == 0:
            break
        page += recipe.page_step

    if target:
        collected = collected[:target]
    return collected, dropped, pages


# ---------------- DOM 配方 ----------------

DEFAULT_DETAIL_LIMIT = 10


def is_dom_recipe(data: Any) -> bool:
    """判断存储的配方是页面结构配方还是接口配方。"""
    if isinstance(data, DomRecipe):
        return True
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return False
    if not isinstance(data, dict):
        return False
    return str(data.get("kind") or "").lower() == "dom" or "item_selector" in data


def _selector_map(raw: Any, allowed: set[str] | None = None) -> dict[str, str]:
    """把「字段 -> 选择器」清洗成字典，丢掉空值与无效键。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        selector = str(value or "").strip()
        if not name or not selector or selector.lower() in ("null", "none", "-"):
            continue
        if allowed is not None and name not in allowed:
            continue
        out[name] = selector
    return out


@dataclass
class DomRecipe:
    """一份可确定性重放的页面结构配方。"""

    domain: str
    list_url: str  # 列表页地址
    item_selector: str  # 单张卡片的 CSS 选择器
    field_selectors: dict[str, str] = field(default_factory=dict)  # 目标字段 -> 卡片内选择器
    link_selector: str = ""  # 卡片内详情页链接
    next_selector: str = ""  # 下一页按钮选择器；为空时用通用兜底
    detail_selectors: dict[str, str] = field(default_factory=dict)  # 目标字段 -> 详情页选择器
    detail_mobile: bool = False  # 详情页是否用手机 UA 访问（京东等桌面页被拦截的站点）
    detail_limit: int = DEFAULT_DETAIL_LIMIT  # 每次任务最多补采多少条详情
    max_pages: int = DEFAULT_MAX_PAGES
    kind: str = "dom"
    id: str = ""
    name: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
    use_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "DomRecipe":
        """从存储/接口数据恢复 DOM 配方；结构不合法时抛 RecipeError。"""
        if isinstance(data, cls):
            return data
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                raise RecipeError("配方不是合法 JSON") from e
        if not isinstance(data, dict):
            raise RecipeError("配方结构不合法")

        domain = str(data.get("domain") or "").strip().lower()
        list_url = str(data.get("list_url") or "").strip()
        item_selector = str(data.get("item_selector") or "").strip()
        if not domain or not list_url.startswith("http") or not item_selector:
            raise RecipeError("DOM 配方缺少域名、列表地址或卡片选择器")

        field_selectors = _selector_map(data.get("field_selectors"))
        if not field_selectors:
            raise RecipeError("DOM 配方没有字段选择器")

        def _int(name: str, default: int) -> int:
            try:
                return int(data.get(name) or default)
            except (TypeError, ValueError):
                return default

        return cls(
            domain=domain,
            list_url=list_url,
            item_selector=item_selector,
            field_selectors=field_selectors,
            link_selector=str(data.get("link_selector") or "").strip(),
            next_selector=str(data.get("next_selector") or "").strip(),
            detail_selectors=_selector_map(data.get("detail_selectors")),
            detail_mobile=bool(data.get("detail_mobile")),
            detail_limit=max(0, _int("detail_limit", DEFAULT_DETAIL_LIMIT)),
            max_pages=max(1, _int("max_pages", DEFAULT_MAX_PAGES)),
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_used_at=str(data.get("last_used_at") or ""),
            use_count=_int("use_count", 0),
        )


_DOM_LIST_PROMPT = """你在为一个列表页生成 DOM 采集配方。

页面地址：{url}
需要提取的目标字段：{fields}
页面上重复出现的卡片（共 {count} 个），选择器候选：{selector}
第一个卡片的 HTML 片段：
{html}
{json}
只输出紧凑 JSON（单行、无缩进、无解释）：
{{"item_selector":"卡片选择器","fields":{{"目标字段":"卡片内的CSS选择器"}},"link_selector":"卡片内详情链接的CSS选择器"}}

要求：
- item_selector 应能选中页面上多张卡片，通常直接用上面的候选选择器。
- fields 的键只能是上面的目标字段；值是相对于卡片的 CSS 选择器；卡片里没有的字段不要写。
- 需要取属性而不是文本时写成 "css@属性名"，例如 "a@href"。
- 属性值是 JSON 时可在后面接字段路径，例如 "img@data-item#title"、"img@data-item#price"。
- link_selector 指向详情页链接；卡片里没有链接就填空字符串。
- 只写标准 CSS 选择器，不要用 :has-text、:contains 等 Playwright/jQuery 语法。"""


_DOM_DETAIL_PROMPT = """你在为一个详情页生成字段选择器。

页面地址：{url}
需要提取的目标字段：{fields}
页面 HTML 片段：
{html}
{data}
只输出紧凑 JSON（单行、无缩进、无解释）：
{{"fields":{{"目标字段":"CSS选择器"}}}}

要求：
- 键只能是上面的目标字段；选择器必须能在页面上定位到对应内容的元素。
- 需要取属性而不是文本时写成 "css@属性名"，例如 "img@src"。
- 内嵌数据里的字段直接写 "js:路径" 作为选择器，例如 "js:_itemOnly.item.upc"。
- 页面上确实没有的字段不要写，不要编造选择器。
- 只写标准 CSS 选择器，不要用 :has-text、:contains 等 Playwright/jQuery 语法。"""


# 详情页内嵌数据里值得提供给模型的键名（如京东图书的 upc 就是 ISBN）
_JS_VALUE_KEYS = [
    "isbn",
    "upc",
    "author",
    "writer",
    "publisher",
    "press",
    "bookname",
    "title",
    "price",
    "pubdate",
    "publish",
]

# 京东移动端详情页的稳定取值路径：优先内嵌数据，其次页面 DOM
_JD_DETAIL_RULES: list[tuple[tuple[str, ...], list[str]]] = [
    (("作者", "author", "writer"), ["js:_itemOnly.item.Author", "#actTitleDesc a:first-of-type"]),
    (("出版社", "publisher", "press"), ["js:_itemOnly.item.Publishers", "#actTitleDesc a:nth-of-type(2)"]),
    (("isbn",), ["js:_itemInfo.product.upc", "js:_itemInfo.upc", "js:_itemOnly.item.upc"]),
    (
        ("出版日期", "出版时间", "publication", "pubdate"),
        ["js:_itemInfo.product.pubDate", "js:_itemOnly.item.pubDate"],
    ),
    (("书名", "title"), ["js:_itemOnly.item.skuName"]),
]


def _jd_detail_candidates(fields: list[Field]) -> dict[str, list[str]]:
    """按字段名/描述匹配京东详情页的候选选择器（一个字段多个兜底）。"""
    found: dict[str, list[str]] = {}
    for item in fields:
        text = f"{item.name} {item.description or ''}".lower()
        for keywords, selectors in _JD_DETAIL_RULES:
            if any(keyword in text for keyword in keywords):
                found[item.name] = selectors
                break
    return found


def _resolve_selectors(
    tools, url: str, candidates: dict[str, list[str]], mobile: bool
) -> dict[str, str]:
    """一次打开详情页，为每个字段挑出第一个真正取到值的候选选择器。"""
    probe: dict[str, str] = {}
    for name, selectors in candidates.items():
        for index, selector in enumerate(selectors):
            probe[f"{name}::{index}"] = selector
    if not probe:
        return {}
    found = tools.extract_detail(url, probe, mobile=mobile) or {}
    resolved: dict[str, str] = {}
    for name, selectors in candidates.items():
        for index, selector in enumerate(selectors):
            if str(found.get(f"{name}::{index}") or "").strip():
                resolved[name] = selector
                break
    return resolved


def parse_dom_recipe(
    data: Any,
    domain: str,
    list_url: str,
    field_names: list[str],
) -> DomRecipe | None:
    """严格校验模型输出的列表选择器；任何一处不合法即返回 None。"""
    if not isinstance(data, dict):
        return None
    item_selector = str(data.get("item_selector") or "").strip()
    if not item_selector:
        return None
    selectors = _selector_map(
        data.get("fields") or data.get("field_selectors"), set(field_names)
    )
    if not selectors:
        return None
    return DomRecipe(
        domain=domain.strip().lower(),
        list_url=list_url,
        item_selector=item_selector,
        field_selectors=selectors,
        link_selector=str(data.get("link_selector") or "").strip(),
        name=f"{domain} DOM 配方",
    )


def build_dom_recipe(
    llm,
    tools,
    domain: str,
    list_url: str,
    fields: list[Field],
    timeout: float = 90.0,
) -> DomRecipe | None:
    """基于当前列表页生成并验证 DOM 配方。

    先让模型为一组重复卡片给出字段选择器（最多两次尝试），再用真实页面
    跑一遍校验；列表页拿不到的字段，用第一条详情页样例再生成一次选择器。
    """
    if not domain or not list_url or not fields:
        return None
    names = [f.name for f in fields]
    try:
        hints = tools.list_hints(limit=2) or {}
    except Exception:  # noqa: BLE001
        return None
    hint_items = hints.get("items") or []
    if not hint_items:
        return None

    recipe: DomRecipe | None = None
    rows: list[dict] = []
    for hint in hint_items[:2]:
        json_block = ""
        if hint.get("json_attr") and hint.get("json_fields"):
            lines = [
                f"{key} = {value}"
                for key, value in list(hint["json_fields"].items())[:12]
            ]
            json_block = (
                f'卡片内嵌 JSON（选择器写成 "{hint["json_attr"]}#字段名" 可直接取值）：\n'
                + "\n".join(lines)
                + "\n"
            )
        prompt = _DOM_LIST_PROMPT.format(
            url=list_url,
            fields="、".join(names),
            count=hint.get("count", 0),
            selector=hint.get("selector", ""),
            html=str(hint.get("sample_html") or "")[:2500],
            json=json_block,
        )
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": "你是网页采集配方生成器，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                timeout=timeout,
            )
        except Exception:  # noqa: BLE001 - 生成失败回退智能体采集
            return None
        candidate = parse_dom_recipe(llm.extract_json(raw), domain, list_url, names)
        if candidate is None:
            continue
        sample = tools.extract_dom(
            candidate.item_selector,
            candidate.field_selectors,
            candidate.link_selector,
            limit=6,
        )
        if not sample or len(sample) < 2:
            continue
        # 至少一半的已映射字段要在半数样例卡片里取到值，避免模型编选择器
        good = [
            name
            for name in candidate.field_selectors
            if sum(1 for row in sample if str(row.get(name) or "").strip()) * 2 >= len(sample)
        ]
        if good and len(good) * 2 >= len(candidate.field_selectors):
            recipe, rows = candidate, sample
            break

    if recipe is None:
        return None
    recipe.next_selector = str(hints.get("next_selector") or "")

    # 列表页拿不到的字段：先用站点规则（如京东）确定，再用详情页样例让模型补
    pending = [name for name in names if name not in recipe.field_selectors]
    detail_url = next((str(row.get("_url") or "") for row in rows if row.get("_url")), "")
    if pending and detail_url and domain.lower().endswith("jd.com"):
        candidates = _jd_detail_candidates([f for f in fields if f.name in pending])
        if candidates:
            resolved = _resolve_selectors(tools, detail_url, candidates, mobile=True)
            if resolved:
                recipe.detail_selectors.update(resolved)
                recipe.detail_mobile = True
                pending = [name for name in pending if name not in resolved]

    if pending and detail_url:
        sample = tools.fetch_detail_sample(detail_url, 8000, js_keys=_JS_VALUE_KEYS)
        html = str(sample.get("html") or "")
        mobile = False
        if not html:
            # 桌面详情页被拦截（如京东）时换手机 UA 再取一次
            sample = tools.fetch_detail_sample(
                detail_url, 8000, mobile=True, js_keys=_JS_VALUE_KEYS
            )
            html = str(sample.get("html") or "")
            mobile = bool(html)
        if html:
            lines = [
                f"js:{item.get('path')} = {item.get('value')}"
                for item in (sample.get("js_values") or [])
                if item.get("path")
            ]
            data_hint = ""
            if lines:
                data_hint = "\n页面内嵌数据（选择器可直接写 js:路径）：\n" + "\n".join(lines[:30]) + "\n"
            prompt = _DOM_DETAIL_PROMPT.format(
                url=detail_url, fields="、".join(pending), html=html, data=data_hint
            )
            try:
                raw = llm.chat(
                    [
                        {"role": "system", "content": "你是网页采集配方生成器，只输出 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    timeout=timeout,
                )
                data = llm.extract_json(raw)
            except Exception:  # noqa: BLE001 - 详情选择器缺失不影响列表采集
                data = None
            selectors = _selector_map((data or {}).get("fields"), set(pending))
            if selectors:
                # 只保留在真实详情页上取到值的选择器
                found = tools.extract_detail(detail_url, selectors, mobile=mobile) or {}
                keep = {
                    name: selector
                    for name, selector in selectors.items()
                    if str(found.get(name) or "").strip()
                }
                if keep:
                    recipe.detail_selectors.update(keep)
                    recipe.detail_mobile = mobile or recipe.detail_mobile
    return recipe


def execute_dom_recipe(
    tools,
    recipe: DomRecipe,
    fields: list[Field],
    filters: list[Filter] | None = None,
    target: int | None = None,
    max_pages: int | None = None,
) -> tuple[list[dict], int, int]:
    """按 DOM 配方翻页取数，返回（条目, 被筛选丢弃数, 实际页数）。

    列表页打不开或卡片选择器失效时抛 RecipeError，由调用方回退智能体循环。
    """
    if not recipe.field_selectors:
        raise RecipeError("DOM 配方没有字段选择器")
    pages_limit = max(1, min(max_pages or recipe.max_pages, 50))

    tools.navigate(recipe.list_url)
    collected: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    pages = 0
    details = 0

    for _ in range(pages_limit):
        if target and len(collected) >= target:
            break
        rows = tools.extract_dom(
            recipe.item_selector, recipe.field_selectors, recipe.link_selector
        )
        if not rows:
            if pages == 0:
                raise RecipeError(f"页面上找不到卡片：{recipe.item_selector}")
            break

        # 详情页补齐：只对确实缺字段、且有链接的卡片按上限补采
        if recipe.detail_selectors and details < recipe.detail_limit:
            for row in rows:
                if details >= recipe.detail_limit:
                    break
                url = str(row.get("_url") or "")
                pending = {
                    name: selector
                    for name, selector in recipe.detail_selectors.items()
                    if not str(row.get(name) or "").strip()
                }
                if not url or not pending:
                    continue
                details += 1
                found = tools.extract_detail(url, pending, mobile=recipe.detail_mobile) or {}
                for name, value in found.items():
                    if value and not str(row.get(name) or "").strip():
                        row[name] = value

        candidates = validate_items(rows, fields)
        for item, row in zip(candidates, rows):
            if row.get("_url"):
                item["_url"] = row["_url"]
        kept, drop = apply_filters(candidates, filters or [])
        dropped += drop

        new_count = 0
        for item in kept:
            values = {k: v for k, v in item.items() if not k.startswith("_")}
            if not any(v is not None for v in values.values()):
                continue
            key = json.dumps(values, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            collected.append(item)
            new_count += 1
        pages += 1
        if new_count == 0:
            break
        if target and len(collected) >= target:
            break

        # 先按配方里的选择器翻页，失效时用通用「下一页」兜底
        if recipe.next_selector and tools.click_selector(recipe.next_selector):
            continue
        if tools.click_next_page():
            continue
        break

    if target:
        collected = collected[:target]
    return collected, dropped, pages
