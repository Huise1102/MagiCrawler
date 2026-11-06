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
from .llm import LLMClient
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

    def to_payload(self) -> dict:
        return {"items": self.items, "_note": self.note}


class Agent:
    """LLM 驱动的网页采集智能体。"""

    def __init__(self, llm: LLMClient, config: CrawlerConfig):
        self.llm = llm
        self.config = config
        self._llm_calls = 0
        self._llm_errors = 0
        self._deadline = 0.0

    # ---------------- 主入口 ----------------

    def run(self, task_desc: str, start_url: str, schema_text: str) -> RunResult:
        fields = parse_schema(schema_text)
        tools = BrowserTools(
            timeout_ms=self.config.timeout_ms,
            user_agent=self.config.user_agent,
            headless=self.config.headless,
            channel=self.config.browser_channel or None,
            block_resources=self.config.block_resources,
            reuse=self.config.browser_reuse,
            idle_timeout_s=self.config.browser_idle_timeout_s,
        )
        trajectory: list[dict] = []
        timings: dict[str, Any] = {}
        self._llm_calls = 0
        self._llm_errors = 0
        self._deadline = time.monotonic() + max(1, self.config.max_run_seconds)
        start = time.perf_counter()
        try:
            t0 = time.perf_counter()
            tools.start()
            timings["browser_ms"] = _elapsed_ms(t0)
            result = self._loop(tools, fields, task_desc, start_url, trajectory, timings)
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
    ) -> RunResult:
        schema_fields_text = schema_fields_to_text(fields)
        system_prompt = build_system_prompt(task_desc, start_url, schema_fields_text)

        collected: list[dict] = []
        target = self._target_count(task_desc)
        no_progress = 0
        last_snapshot = ""

        if self._remaining() <= 1:
            return RunResult(items=[], note=self._timeout_note([], task_desc), trajectory=trajectory)

        # 起始导航
        t0 = time.perf_counter()
        snapshot = tools.snapshot(self.config.max_snapshot_chars) if not start_url else tools.navigate(start_url)
        timings["navigate_ms"] = _elapsed_ms(t0)
        last_snapshot = snapshot

        for step in range(self.config.max_steps):
            # 硬性截止：超过最长运行时间立即收尾，避免无限爬取
            if self._remaining() <= 1:
                note = self._timeout_note(collected, task_desc)
                return RunResult(items=collected, note=note, trajectory=trajectory)

            user_msg = self._build_user_msg(snapshot, collected, trajectory)
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
                collected = self._merge(collected, items)
                if target:
                    collected = collected[:target]
                note = data.get("_note", "") if isinstance(data, dict) else ""
                if not note:
                    note = self._build_note(collected, task_desc)
                self._log_step(trajectory, step, tool, args, dec_ms, 0.0)
                return RunResult(items=collected, note=note, trajectory=trajectory)

            # 2) 提取候选
            if tool == "extract":
                remain = (target - len(collected)) if target else None
                candidates = self._extract(tools, fields, remain)
                exec_ms = _elapsed_ms(t_exec)
                timings["extract_ms"] = round(timings.get("extract_ms", 0) + exec_ms, 1)
                self._log_step(trajectory, step, tool, args, dec_ms, exec_ms)
                before = len(collected)
                collected = self._merge(collected, candidates)
                if target:
                    collected = collected[:target]
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

    # ---------------- 子步骤 ----------------

    @staticmethod
    def _log_step(
        trajectory: list,
        step: int,
        tool: str,
        args: dict,
        dec_ms: float,
        exec_ms: float,
    ) -> None:
        """记录单步动作与耗时（决策 / 执行 / 合计）。"""
        trajectory.append(
            {
                "step": step + 1,
                "tool": tool,
                "args": args,
                "time": time.strftime("%H:%M:%S"),
                "llm_ms": dec_ms,
                "exec_ms": exec_ms,
                "total_ms": round(dec_ms + exec_ms, 1),
            }
        )

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

    def _build_user_msg(self, snapshot: str, collected: list, trajectory: list) -> str:
        recent = trajectory[-5:]
        hist = "\n".join([f"- {t['tool']}({json.dumps(t['args'], ensure_ascii=False)})" for t in recent]) or "（无）"
        return (
            f"当前页面快照：\n{snapshot[: self.config.max_snapshot_chars]}\n\n"
            f"已收集条目数：{len(collected)}\n"
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
        prompt = (
            f"从页面中提取满足条件的条目，{limit}"
            '只输出紧凑 JSON（单行、无缩进、无多余空白），格式 {"items":[{...}]}。'
            f"每个对象只包含这些字段：{names}。缺失字段设为 null，不要编造，不要任何说明文字。\n\n"
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
            return validate_items(data["items"], fields)
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
    def _target_count(task_desc: str) -> int | None:
        """从目标描述里粗略提取期望数量（如"前 10 本"、"前 20 台"）。"""
        import re

        m = re.search(
            r"(?:前|首个|top\s*)?(\d+)\s*"
            r"(?:本|条|个|项|台|款|件|册|篇|名|家|种|套|张|页|次|部|只|盒|瓶|箱)",
            task_desc or "",
            re.IGNORECASE,
        )
        return int(m.group(1)) if m else None

    @classmethod
    def _build_note(cls, collected: list, task_desc: str) -> str:
        target = cls._target_count(task_desc)
        if target is None:
            return f"本次共采集到 {len(collected)} 条数据。"
        if len(collected) >= target:
            return f"已采集到目标数量（{target} 条），实际 {len(collected)} 条。"
        return f"目标数量 {target} 条，实际采集到 {len(collected)} 条（可能因翻页结束或无更多数据）。"
