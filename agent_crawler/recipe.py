"""接口优先的「采集配方」。

思路：站点列表通常由前端调用 JSON 接口渲染。把接口地址、列表路径、
字段映射与翻页参数记下来，下次同一域名直接请求接口、确定性取值，
不再让模型逐条抄页面文本。

LLM 只在「生成配方」时参与一次；执行阶段完全确定，失败则由调用方
回退到原有 ReAct 循环。
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
