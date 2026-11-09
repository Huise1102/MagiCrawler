"""ReAct 决策循环：智能体爬虫核心调度。

流程：navigate 起始页 → 每轮让 LLM 决定动作 → 执行工具 → 反馈 → 判断终止。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .agent_prompt import ACTION_PROTOCOL, build_system_prompt
from .browser import BrowserTools
from .config import CrawlerConfig
from .intent import Filter, apply_filters, describe_filters, detect_filters, filters_to_payload
from .llm import LLMClient
from .recipe import Recipe, execute_recipe
from .schema import Field, parse_schema, schema_fields_to_text, validate_items


def _elapsed_ms(start: float) -> float:
    """从 start（perf_counter）到现在的毫秒数。"""
    return round((time.perf_counter() - start) * 1000, 1)


@dataclass
class RunResult:
    """一次采集的最终结果。"""

    items: list[dict]
    note: str = ""
    trajectory: list = None
    timings: dict = field(default_factory=dict)
    target: int | None = None  # 本次任务的期望条数（用于判定部分完成）
    filters: list[dict] = field(default_factory=list)  # 本次生效的筛选条件
    dropped: int = 0  # 因不满足筛选条件被丢弃的条数
    mode: str = "agent"  # agent（页面智能体）| recipe（接口直采）
    candidates: list = field(default_factory=list)  # 本次捕获到的列表接口候选

    def to_payload(self) -> dict:
        return {"items": self.items, "_note": self.note}


class Agent:
    """LLM 驱动的网页采集智能体。"""

    def __init__(self, llm: LLMClient, config: CrawlerConfig, on_step=None):
        self.llm = llm
        self.config = config
        self._llm_calls = 0
        self._llm_errors = 0
        self._deadline = 0.0
        # 本次任务的显式约束（来自采集表单，优先于从描述中推断）
        self._target: int | None = None
        self._max_pages: int | None = None
        # 从目标描述识别出的筛选条件（由代码判定，不交给模型）
        self._filters: list[Filter] = []
        self._filter_display: dict[str, str] = {}
        self._dropped = 0
        # 每一步完成后回调（供后端实时推送采集链路）
        self._on_step = on_step

    # ---------------- 主入口 ----------------

    def run(
        self,
        task_desc: str,
        start_url: str,
        schema_text: str,
        site_profile: str | None = None,
        max_items: int | None = None,
        max_pages: int | None = None,
        recipe: dict | None = None,
    ) -> RunResult:
        fields = parse_schema(schema_text)
        self._target = max_items if max_items and max_items > 0 else None
        self._max_pages = max_pages if max_pages and max_pages > 0 else None
        tools = BrowserTools(
            timeout_ms=self.config.timeout_ms,
            user_agent=self.config.user_agent,
            headless=self.config.headless,
            channel=self.config.browser_channel or None,
            block_resources=self.config.block_resources,
            reuse=self.config.browser_reuse,
            idle_timeout_s=self.config.browser_idle_timeout_s,
            profile_dir=site_profile,
        )
        trajectory: list[dict] = []
        timings: dict[str, Any] = {}
        self._llm_calls = 0
        self._llm_errors = 0
        self._dropped = 0
        self._deadline = time.monotonic() + max(1, self.config.max_run_seconds)
        start = time.perf_counter()

        # 先做一次意图识别：把「50 元以内」这类描述变成可判定的约束
        t_intent = time.perf_counter()
        self._filters = detect_filters(self.llm, task_desc, [f.name for f in fields])
        self._filter_display = {f.name: (f.description or f.name) for f in fields}
        timings["intent_ms"] = _elapsed_ms(t_intent)

        try:
            t0 = time.perf_counter()
            tools.start()
            timings["browser_ms"] = _elapsed_ms(t0)
            self._emit(
                {
                    "step": 0,
                    "tool": "launch",
                    "args": {"url": start_url},
                    "time": time.strftime("%H:%M:%S"),
                    "llm_ms": 0.0,
                    "exec_ms": timings["browser_ms"],
                    "total_ms": timings["browser_ms"],
                }
            )
            # 命中已保存的采集配方时先走接口直采；拿不到数据再回退到智能体循环
            result = None
            seed: list[dict] = []
            wanted = self._target or self._target_count(task_desc)
            if recipe:
                result = self._run_recipe(tools, fields, recipe, start_url, trajectory, timings, wanted)
                # 接口只拿到一部分时保留已有结果，再让智能体补采剩余数量
                if result is not None and wanted and len(result.items) < wanted:
                    seed = result.items
                    result = None
            if result is None:
                result = self._loop(
                    tools, fields, task_desc, start_url, trajectory, timings, seed
                )
            result.candidates = tools.network_candidates(limit=3)
            result.target = self._target or self._target_count(task_desc)
            result.filters = filters_to_payload(self._filters, self._filter_display)
            result.dropped = self._dropped
        finally:
            tools.close()

        timings["llm_calls"] = self._llm_calls
        timings["steps"] = len(trajectory)
        timings["total_ms"] = _elapsed_ms(start)
        result.timings = timings
        return result

    # ---------------- 决策循环 ----------------

    def _loop(
        self,
        tools: BrowserTools,
        fields: list[Field],
        task_desc: str,
        start_url: str,
        trajectory: list,
        timings: dict,
        seed: list[dict] | None = None,
    ) -> RunResult:
        schema_fields_text = schema_fields_to_text(fields)
        constraints_text = describe_filters(self._filters, self._filter_display) or "无"
        system_prompt = build_system_prompt(task_desc, start_url, schema_fields_text, constraints_text)

        collected: list[dict] = self._merge([], list(seed or []))
        target = self._target or self._target_count(task_desc)
        pages_opened = 1 if start_url else 0
        no_progress = 0
        last_snapshot = ""

        if self._remaining() <= 1:
            return RunResult(
                items=collected,
                note=self._timeout_note(collected, task_desc),
                trajectory=trajectory,
            )

        # 起始导航
        t0 = time.perf_counter()
        snapshot = tools.snapshot(self.config.max_snapshot_chars) if not start_url else tools.navigate(start_url)
        timings["navigate_ms"] = _elapsed_ms(t0)
        self._emit(
            {
                "step": 0,
                "tool": "navigate",
                "args": {"url": start_url},
                "time": time.strftime("%H:%M:%S"),
                "llm_ms": 0.0,
                "exec_ms": timings["navigate_ms"],
                "total_ms": timings["navigate_ms"],
            }
        )
        last_snapshot = snapshot

        for step in range(self.config.max_steps):
            # 硬性截止：超过最长运行时间立即收尾，避免无限爬取
            if self._remaining() <= 1:
                note = self._timeout_note(collected, task_desc)
                return RunResult(items=collected, note=note, trajectory=trajectory)

            user_msg = self._build_user_msg(snapshot, collected, trajectory, target, pages_opened)
            t_dec = time.perf_counter()
            action = self._decide(system_prompt, user_msg)
            if action is None:
                # 决策解析失败，重试一次
                action = self._decide(system_prompt, user_msg + "\n（请严格只输出一个 JSON 动作对象）")
            dec_ms = _elapsed_ms(t_dec)
            timings["decision_ms"] = round(timings.get("decision_ms", 0) + dec_ms, 1)

            if action is None:
                # LLM 连续失败则终止，避免空转
                if self._llm_errors >= 3:
                    note = f"LLM 连续 {self._llm_errors} 次调用或解析失败，已终止。{self._build_note(collected, task_desc)}"
                    return RunResult(items=collected, note=note, trajectory=trajectory)
            else:
                self._llm_errors = 0

            tool = (action or {}).get("tool", "")
            args = (action or {}).get("args", {}) or {}
            t_exec = time.perf_counter()

            # 1) 结束
            if tool == "finish":
                data = args.get("data", {}) if isinstance(args, dict) else {}
                items = validate_items(data, fields)
                items, dropped = apply_filters(items, self._filters)
                self._dropped += dropped
                collected = self._merge(collected, items)
                if target:
                    collected = collected[:target]
                note = data.get("_note", "") if isinstance(data, dict) else ""
                if self._filters or not note:
                    note = self._build_note(collected, task_desc)
                self._log_step(trajectory, step, tool, args, dec_ms, 0.0, len(collected))
                return RunResult(items=collected, note=note, trajectory=trajectory)

            # 2) 提取候选
            if tool == "extract":
                remain = (target - len(collected)) if target else None
                candidates = self._extract(tools, fields, remain)
                exec_ms = _elapsed_ms(t_exec)
                timings["extract_ms"] = round(timings.get("extract_ms", 0) + exec_ms, 1)
                before = len(collected)
                collected = self._merge(collected, candidates)
                if target:
                    collected = collected[:target]
                self._log_step(trajectory, step, tool, args, dec_ms, exec_ms, len(collected))
                # 已达到目标数量，直接结束，避免多余请求
                if target and len(collected) >= target:
                    note = self._build_note(collected, task_desc)
                    return RunResult(items=collected, note=note, trajectory=trajectory)
                if len(collected) > before:
                    no_progress = 0
                else:
                    no_progress += 1
                    # 连续提取无新增：内容已重复或页面无可提取项，及时收尾而不是耗到超时
                    if no_progress >= 2:
                        note = f"连续 {no_progress} 次提取没有新增数据，已终止。{self._build_note(collected, task_desc)}"
                        return RunResult(items=collected, note=note, trajectory=trajectory)
                continue

            # 3) 其它浏览器动作
            if tool == "navigate":
                # 已达到翻页上限：不再打开新页面，直接收尾
                if self._max_pages and pages_opened >= self._max_pages:
                    note = (
                        f"已达到最多翻页 {self._max_pages} 页，提前结束。"
                        + self._build_note(collected, task_desc)
                    )
                    return RunResult(items=collected, note=note, trajectory=trajectory)
                pages_opened += 1
            snapshot = self._execute(tools, tool, args)
            exec_ms = _elapsed_ms(t_exec)
            timings["action_ms"] = round(timings.get("action_ms", 0) + exec_ms, 1)
            self._log_step(trajectory, step, tool, args, dec_ms, exec_ms)
            if snapshot and snapshot != last_snapshot:
                no_progress = 0
            else:
                no_progress += 1
            last_snapshot = snapshot or last_snapshot

            # 终止条件：连续 2 次无进展
            if no_progress >= 2 and step >= 1:
                note = self._build_note(collected, task_desc)
                return RunResult(items=collected, note=note, trajectory=trajectory)

        # 达到最大步数
        note = self._build_note(collected, task_desc)
        return RunResult(items=collected, note=note, trajectory=trajectory)

    # ---------------- 接口直采 ----------------

    def _run_recipe(
        self,
        tools: BrowserTools,
        fields: list[Field],
        recipe: dict,
        start_url: str,
        trajectory: list,
        timings: dict,
        target: int | None = None,
    ) -> RunResult | None:
        """按已保存的配方直接请求接口；失败返回 None 交给智能体循环。"""
        if self._remaining() <= 1:
            return None
        try:
            rec = Recipe.from_dict(recipe)
        except Exception:  # noqa: BLE001 - 失效配方直接忽略
            return None

        # 先打开起始页：获取匿名 Cookie / 登录态，并为接口请求带上 Referer
        t_nav = time.perf_counter()
        try:
            tools.navigate(start_url)
        except Exception:  # noqa: BLE001 - 接口请求仍可继续尝试
            pass
        nav_ms = _elapsed_ms(t_nav)
        timings["navigate_ms"] = nav_ms
        self._emit(
            {
                "step": 0,
                "tool": "navigate",
                "args": {"url": start_url},
                "time": time.strftime("%H:%M:%S"),
                "llm_ms": 0.0,
                "exec_ms": nav_ms,
                "total_ms": nav_ms,
            }
        )

        t0 = time.perf_counter()
        try:
            items, dropped, pages = execute_recipe(
                tools,
                rec,
                fields,
                filters=self._filters,
                target=target,
                max_pages=self._max_pages,
            )
        except Exception:  # noqa: BLE001 - 接口失效时回退到页面采集
            return None
        recipe_ms = _elapsed_ms(t0)
        timings["recipe_ms"] = recipe_ms
        if not items:
            return None

        self._dropped = dropped
        entry = {
            "step": 1,
            "tool": "recipe",
            "args": {"url": rec.url, "items_path": rec.items_path},
            "time": time.strftime("%H:%M:%S"),
            "llm_ms": 0.0,
            "exec_ms": recipe_ms,
            "total_ms": round(nav_ms + recipe_ms, 1),
            "items": len(items),
        }
        trajectory.append(entry)
        self._emit(entry)

        note = f"接口直采命中已保存的配方（{rec.items_path}，共 {pages} 页），采集到 {len(items)} 条"
        filter_text = describe_filters(self._filters, self._filter_display)
        if filter_text:
            note += f"；筛选条件：{filter_text}"
            if dropped:
                note += f"，另有 {dropped} 条不满足条件已丢弃"
        return RunResult(
            items=items,
            note=note + "。",
            trajectory=trajectory,
            mode="recipe",
        )

    # ---------------- 子步骤 ----------------

    def _log_step(
        self,
        trajectory: list,
        step: int,
        tool: str,
        args: dict,
        dec_ms: float,
        exec_ms: float,
        items: int | None = None,
    ) -> None:
        """记录单步动作与耗时（决策 / 执行 / 合计），并通知链路回调。"""
        entry = {
            "step": step + 1,
            "tool": tool,
            "args": args,
            "time": time.strftime("%H:%M:%S"),
            "llm_ms": dec_ms,
            "exec_ms": exec_ms,
            "total_ms": round(dec_ms + exec_ms, 1),
        }
        if items is not None:
            entry["items"] = int(items)
        trajectory.append(entry)
        self._emit(entry)

    def _emit(self, entry: dict) -> None:
        """把单步事件交给调用方（后端），回调异常不影响采集。"""
        if not self._on_step:
            return
        try:
            self._on_step(dict(entry))
        except Exception:  # noqa: BLE001 - 回调仅用于展示，不能中断采集
            pass

    def _decide(self, system_prompt: str, user_msg: str) -> dict | None:
        if self._remaining() <= 1:
            return None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg + "\n\n" + ACTION_PROTOCOL},
        ]
        try:
            raw = self.llm.chat(messages, temperature=0, timeout=self._remaining())
        except Exception:  # noqa: BLE001
            self._llm_errors += 1
            return None
        self._llm_calls += 1
        data = self.llm.extract_json(raw)
        if isinstance(data, dict):
            return data
        return None

    def _build_user_msg(
        self,
        snapshot: str,
        collected: list,
        trajectory: list,
        target: int | None = None,
        pages_opened: int = 0,
    ) -> str:
        recent = trajectory[-5:]
        hist = "\n".join([f"- {t['tool']}({json.dumps(t['args'], ensure_ascii=False)})" for t in recent]) or "（无）"
        if target:
            goal = (
                f"目标数量：{target} 条；已收集 {len(collected)} 条，"
                f"还需 {max(0, target - len(collected))} 条。达到目标数量立即 finish，不要多采。\n"
            )
        else:
            goal = f"已收集条目数：{len(collected)}\n"
        if self._filters:
            goal += (
                f"筛选条件：{describe_filters(self._filters, self._filter_display)}；"
                "只有符合条件的条目才计入已收集数量。\n"
            )
        page_hint = ""
        if self._max_pages:
            page_hint = (
                f"翻页上限：最多浏览 {self._max_pages} 页；当前已打开第 {max(1, pages_opened)} 页，"
                "到达上限后不要再 navigate 到新的列表页。\n"
            )
        return (
            f"当前页面快照：\n{snapshot[: self.config.max_snapshot_chars]}\n\n"
            f"{goal}"
            f"{page_hint}"
            f"最近动作：\n{hist}\n\n"
            "请决定下一步动作。"
        )

    def _execute(self, tools: BrowserTools, tool: str, args: dict) -> str:
        try:
            if tool == "navigate":
                return tools.navigate(args.get("url", ""))
            if tool == "fetch":
                return tools.fetch()
            if tool == "click":
                return tools.click(args.get("selector", ""))
            if tool == "scroll":
                return tools.scroll(int(args.get("times", 1)))
        except Exception as e:
            return f"[{tool}] 执行出错：{e}"
        return ""

    def _extract(self, tools: BrowserTools, fields: list[Field], remain: int | None = None) -> list[dict]:
        """让 LLM 从当前快照中提取结构化候选条目。

        要求输出紧凑单行 JSON 并限制条数：Qwen 等本地模型逐 token 生成，
        缩进与多余条目会成倍拉长耗时。
        """
        if self._remaining() <= 1:
            return []
        snapshot = tools.snapshot(self.config.max_snapshot_chars)
        names = ", ".join(f.name for f in fields)
        # 单次提取的条数上限：Qwen 等本地模型逐 token 生成，条数过多会超出单次调用超时
        cap = min(remain, 20) if remain else 20
        limit = f"最多 {cap} 条。"
        constraint = ""
        if self._filters:
            constraint = f"硬性筛选条件：{describe_filters(self._filters, self._filter_display)}；不满足条件的条目一律不要输出。\n"
        prompt = (
            f"从页面中提取满足条件的条目，{limit}"
            + constraint
            + '只输出紧凑 JSON（单行、无缩进、无多余空白），格式 {"items":[{...}]}。'
            f"每个对象只包含这些字段：{names}。缺失字段设为 null，不要编造，不要任何说明文字。\n"
            "页面中的时长（如 03:06）、播放量/点赞数（如 1.7万）等按原文照抄，"
            "不要自行换算或因为格式特殊而丢弃；只有页面上确实找不到对应信息时才填 null。\n"
            "链接/ID 必须是该条目自身的地址：不要把导航、频道、首页等公共链接当成条目链接，"
            "找不到条目独立链接或 ID 时填 null，不要猜测或编造。\n\n"
            f"页面内容：\n{snapshot}"
        )
        messages = [
            {"role": "system", "content": "你是网页数据提取助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = self.llm.chat(messages, temperature=0, timeout=self._remaining())
        except Exception:  # noqa: BLE001
            self._llm_errors += 1
            return []
        self._llm_calls += 1
        data = self.llm.extract_json(raw)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            candidates = validate_items(data["items"], fields)
            kept, dropped = apply_filters(candidates, self._filters)
            self._dropped += dropped
            return kept
        return []

    # ---------------- 时间预算 ----------------

    def _remaining(self) -> float:
        """距离本次任务截止还剩多少秒。"""
        return self._deadline - time.monotonic()

    def _timeout_note(self, collected: list, task_desc: str) -> str:
        """超时收尾时的说明。"""
        return (
            f"执行超时：超过 {self.config.max_run_seconds} 秒，已提前终止；"
            f"本次采集到 {len(collected)} 条数据。"
        )

    @staticmethod
    def _merge(collected: list[dict], new_items: list[dict]) -> list[dict]:
        """合并并去重（按完整条目内容判重）。"""
        seen = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in collected}
        for item in new_items:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen or not any(v is not None for v in item.values()):
                continue
            seen.add(key)
            collected.append(item)
        return collected

    @staticmethod
    def _cn_number(text: str) -> int | None:
        """中文数字转整数（支持「五」「十五」「二十五」「一百二十」）。"""
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        units = {"十": 10, "百": 100, "千": 1000}
        total = 0
        current = 0
        seen = False
        for ch in text:
            if ch in digits:
                current = digits[ch]
                seen = True
            elif ch in units:
                total += (current or 1) * units[ch]
                current = 0
                seen = True
            else:
                return None
        return total + current if seen else None

    @classmethod
    def _target_count(cls, task_desc: str) -> int | None:
        """从目标描述里粗略提取期望数量（如"前 10 本"、"前五个视频"）。"""
        import re

        m = re.search(
            r"(?:前|首个|top\s*)?([0-9]+|[零一二两三四五六七八九十百千]+)\s*"
            r"(?:本|条|个|项|台|款|件|册|篇|名|家|种|套|张|部|只|盒|瓶|箱)",
            task_desc or "",
            re.IGNORECASE,
        )
        if not m:
            return None
        raw = m.group(1)
        return int(raw) if raw.isdigit() else cls._cn_number(raw)

    def _build_note(self, collected: list, task_desc: str) -> str:
        target = self._target or self._target_count(task_desc)
        if target is None:
            note = f"本次共采集到 {len(collected)} 条数据"
        elif len(collected) >= target:
            note = f"已采集到目标数量（{target} 条），实际 {len(collected)} 条"
        else:
            note = f"目标数量 {target} 条，实际采集到 {len(collected)} 条（可能因翻页结束或无更多符合条件的条目）"
        filter_text = describe_filters(self._filters, self._filter_display)
        if filter_text:
            dropped_text = f"，另有 {self._dropped} 条不满足条件已丢弃" if self._dropped else ""
            return f"{note}；筛选条件：{filter_text}{dropped_text}。"
        return f"{note}。"
