"""Inject transient persona anchors at semantic boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_SENTENCE_RE = re.compile(r"[。！？!?；;\n]+")
_NEGATIVE_EXAMPLE_RE = re.compile(r"反例：.*?->\s*(?P<reply>.+)$")
_POSITIVE_EXAMPLE_RE = re.compile(r"正例：.*?回复：(?P<reply>.+)$")
_AT_NAME_RE_TEMPLATE = r"@\s*{name}"


@dataclass(frozen=True, slots=True)
class AnchorConfig:
    enabled: bool = False
    min_turns_between_anchors: int = 5
    max_turns_without_anchor: int = 7
    anchor_token_budget: int = 80


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return str(message or "")
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "") or "")
        if block_type == "text":
            parts.append(str(block.get("text", "") or ""))
        elif block_type == "tool_result":
            parts.append(str(block.get("content", "") or ""))
    return "\n".join(part for part in parts if part)


def _user_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        if str(message.get("role", "")) != "user":
            continue
        text = _message_text(message).strip()
        if text:
            texts.append(text)
    return texts


def _token_set(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _first_sentence(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()]
    return parts[0] if parts else text


def _best_voice_demo(examples_text: str, voice_text: str) -> str:
    for raw in str(examples_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        negative = _NEGATIVE_EXAMPLE_RE.search(line)
        if negative:
            return negative.group("reply").strip()
    for raw in str(examples_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        positive = _POSITIVE_EXAMPLE_RE.search(line)
        if positive:
            return positive.group("reply").strip()
    for raw in str(voice_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "：" in line:
            _, value = line.split("：", 1)
            value = value.strip()
            if value:
                return value
        return line
    return ""


def _trim_for_budget(text: str, budget: int) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if budget <= 0 or not cleaned:
        return cleaned
    tokens = _TOKEN_RE.findall(cleaned)
    if len(tokens) <= budget:
        return cleaned
    approx_chars = max(32, budget * 3)
    return cleaned[:approx_chars].rstrip(" ，。！？!?；;")


class AnchorReinjector:
    def __init__(
        self,
        *,
        bot_name: str,
        personality: str = "",
        proactive: str | None = None,
        voice_text: str = "",
        examples_text: str = "",
        config: AnchorConfig | Any | None = None,
    ) -> None:
        self._config = AnchorConfig(
            enabled=bool(getattr(config, "enabled", False)),
            min_turns_between_anchors=max(1, int(getattr(config, "min_turns_between_anchors", 5) or 5)),
            max_turns_without_anchor=max(1, int(getattr(config, "max_turns_without_anchor", 7) or 7)),
            anchor_token_budget=max(16, int(getattr(config, "anchor_token_budget", 80) or 80)),
        )
        self._bot_name = str(bot_name or "").strip()
        personality_hint = _first_sentence(personality)
        proactive_hint = _first_sentence(proactive or "")
        voice_demo = _best_voice_demo(examples_text, voice_text)
        parts = [f"你是{self._bot_name}。"] if self._bot_name else []
        if personality_hint:
            parts.append(personality_hint)
        if proactive_hint:
            parts.append(f"保持：{proactive_hint}")
        if voice_demo:
            parts.append(f"示例语气：{voice_demo}")
        anchor_body = " ".join(part.strip() for part in parts if part.strip())
        self._anchor_text = _trim_for_budget(f"[ANCHOR] {anchor_body}".strip(), self._config.anchor_token_budget)

    @property
    def enabled(self) -> bool:
        return self._config.enabled and bool(self._anchor_text)

    def current_turn(self, messages: list[dict[str, Any]]) -> int:
        return len(_user_texts(messages))

    def _contains_tool_result_boundary(self, messages: list[dict[str, Any]]) -> bool:
        for message in reversed(messages):
            if str(message.get("role", "")) != "user":
                continue
            content = message.get("content", "")
            if not isinstance(content, list):
                return False
            return any(
                isinstance(block, dict) and str(block.get("type", "") or "") == "tool_result"
                for block in content
            )
        return False

    def _contains_new_mention(self, messages: list[dict[str, Any]]) -> bool:
        user_texts = _user_texts(messages)
        if not user_texts:
            return False
        latest = user_texts[-1]
        pattern = (
            re.compile(_AT_NAME_RE_TEMPLATE.format(name=re.escape(self._bot_name)))
            if self._bot_name
            else re.compile(r"@\S+")
        )
        if not pattern.search(latest):
            return False
        return not any(pattern.search(previous) for previous in user_texts[-4:-1])

    def _contains_topic_shift(self, messages: list[dict[str, Any]]) -> bool:
        user_texts = _user_texts(messages)
        if len(user_texts) < 3:
            return False
        recent = " ".join(user_texts[-2:])
        previous = " ".join(user_texts[:-2][-5:])
        previous_tokens = _token_set(previous)
        recent_tokens = _token_set(recent)
        if not previous_tokens or not recent_tokens:
            return False
        overlap = len(previous_tokens & recent_tokens) / max(1, len(previous_tokens | recent_tokens))
        return overlap < 0.2

    def should_inject(self, messages: list[dict[str, Any]], last_anchor_turn: int) -> bool:
        if not self.enabled:
            return False
        current_turn = self.current_turn(messages)
        turns_since_anchor = current_turn if last_anchor_turn <= 0 else max(0, current_turn - last_anchor_turn)
        if turns_since_anchor >= self._config.max_turns_without_anchor:
            return True
        if turns_since_anchor < self._config.min_turns_between_anchors:
            return False
        return (
            self._contains_tool_result_boundary(messages)
            or self._contains_new_mention(messages)
            or self._contains_topic_shift(messages)
        )

    def build_anchor_message(self) -> dict[str, str]:
        return {"role": "user", "content": self._anchor_text}
