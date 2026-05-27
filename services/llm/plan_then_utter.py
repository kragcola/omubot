"""Plan-then-utter pilot for proactive group replies."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from services.llm.llm_request import LLMRequest

_LINE_PREFIX_RE = re.compile(r"^\s*(?:[-*]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*")
_QUOTE_RE = re.compile(r"^[\"'“”‘’]+|[\"'“”‘’]+$")

PLAN_SYSTEM_HINT = (
    "【plan_then_utter】\n"
    "先为这次主动群聊回复写 2-3 个短段落大纲。只输出 JSON："
    "{\"utterances\":[\"大纲1\",\"大纲2\"]}。"
    "每个大纲不超过 18 个中文字符，不要直接写完整回复。"
)

UTTER_SYSTEM_HINT = (
    "【plan_then_utter】\n"
    "你正在按短计划分段发言。只输出当前这一段可直接发送到群聊的自然文本；"
    "不要编号，不要解释计划，不要使用工具，不要重复已发段落。"
)


@dataclass(frozen=True)
class PlanThenUtterResult:
    parent_span_id: str
    plan_text: str
    outlines: tuple[str, ...]
    utterances: tuple[str, ...]
    plan_usage: dict[str, Any]
    utter_usages: tuple[dict[str, Any], ...]


class PlanThenUtter:
    """Build and run a short plan call followed by 2-3 utter calls."""

    def __init__(self, *, plan_max_tokens: int = 80, utter_max_tokens: int = 150) -> None:
        self.plan_max_tokens = plan_max_tokens
        self.utter_max_tokens = utter_max_tokens

    def build_plan_request(
        self,
        *,
        system_blocks: list[dict[str, Any]],
        messages: list[Any],
        user_id: str,
        group_id: str,
    ) -> LLMRequest:
        return LLMRequest(
            task="main",
            user_id=user_id,
            group_id=group_id,
            static_blocks=[*system_blocks, {"type": "text", "text": PLAN_SYSTEM_HINT}],
            user_messages=list(messages),
            max_tokens=self.plan_max_tokens,
            auto_record_usage=False,
            requires_capabilities=("chat",),
        )

    def build_utter_request(
        self,
        *,
        system_blocks: list[dict[str, Any]],
        messages: list[Any],
        user_id: str,
        group_id: str,
        plan_text: str,
        outline: str,
        utter_index: int,
        total_utters: int,
        previous_utterances: tuple[str, ...] = (),
    ) -> LLMRequest:
        previous = "\n".join(f"- {item}" for item in previous_utterances) or "无"
        prompt = (
            f"完整短计划：{plan_text.strip()}\n"
            f"当前段落：{utter_index + 1}/{total_utters}\n"
            f"当前大纲：{outline.strip()}\n"
            f"已发送段落：\n{previous}"
        )
        return LLMRequest(
            task="main",
            user_id=user_id,
            group_id=group_id,
            static_blocks=[*system_blocks, {"type": "text", "text": UTTER_SYSTEM_HINT}],
            user_messages=[*list(messages), {"role": "user", "content": prompt}],
            max_tokens=self.utter_max_tokens,
            auto_record_usage=False,
            requires_capabilities=("chat",),
        )

    def parse_plan(self, text: str) -> tuple[str, ...]:
        """Extract 2-3 clean outline items from JSON, bullets, or short lines."""
        raw = (text or "").strip()
        if not raw:
            return ()
        items = self._parse_json(raw) or self._parse_lines(raw)
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = self._clean_item(str(item))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(normalized)
            if len(cleaned) >= 3:
                break
        if len(cleaned) < 2:
            return ()
        return tuple(cleaned)

    async def run(
        self,
        *,
        call: Callable[[LLMRequest], Awaitable[dict[str, Any]]],
        system_blocks: list[dict[str, Any]],
        messages: list[Any],
        user_id: str,
        group_id: str,
        parent_span_id: str,
    ) -> PlanThenUtterResult | None:
        plan_result = await call(self.build_plan_request(
            system_blocks=system_blocks,
            messages=messages,
            user_id=user_id,
            group_id=group_id,
        ))
        plan_text = str(plan_result.get("text") or "")
        outlines = self.parse_plan(plan_text)
        if not outlines:
            return None

        utterances: list[str] = []
        utter_usages: list[dict[str, Any]] = []
        for index, outline in enumerate(outlines):
            utter_result = await call(self.build_utter_request(
                system_blocks=system_blocks,
                messages=messages,
                user_id=user_id,
                group_id=group_id,
                plan_text=plan_text,
                outline=outline,
                utter_index=index,
                total_utters=len(outlines),
                previous_utterances=tuple(utterances),
            ))
            utterances.append(str(utter_result.get("text") or ""))
            utter_usages.append(dict(utter_result))

        return PlanThenUtterResult(
            parent_span_id=parent_span_id,
            plan_text=plan_text,
            outlines=outlines,
            utterances=tuple(utterances),
            plan_usage=dict(plan_result),
            utter_usages=tuple(utter_usages),
        )

    def _parse_json(self, raw: str) -> list[str]:
        with_braces = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw and "}" in raw else raw
        try:
            payload = json.loads(with_braces)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            value = payload.get("utterances") or payload.get("plan") or payload.get("outline")
            if isinstance(value, list):
                return [str(item) for item in value]
        if isinstance(payload, list):
            return [str(item) for item in payload]
        return []

    def _parse_lines(self, raw: str) -> list[str]:
        parts: list[str] = []
        for line in raw.replace("；", "\n").replace(";", "\n").splitlines():
            line = _LINE_PREFIX_RE.sub("", line).strip()
            if line:
                parts.append(line)
        return parts

    def _clean_item(self, item: str) -> str:
        item = _QUOTE_RE.sub("", item.strip())
        item = item.replace("\n", " ").strip(" -，。,.；;")
        return item[:48].strip()
