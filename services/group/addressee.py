"""Addressee detection for group messages."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AddresseeResult:
    target_id: str | None
    confidence: float
    source: str


class AddresseeDetector:
    """Stateless four-layer detector: adapter -> regex -> quote -> at."""

    _AT_PATTERNS = (
        re.compile(r"\[CQ:at,qq=(\d+)\]"),
        re.compile(r"<@!?(\d+)>"),
        re.compile(r"(?<!\w)@(\d{5,})(?!\w)"),
    )
    _QUOTE_KEYS = ("reply_sender_id", "quote_user_id", "quoted_user_id", "source_user_id")
    _TARGET_KEYS = ("target_id", "addressee_id", "mentioned_user_id", "to_user_id")

    def __init__(self, *, bot_ids: Sequence[str] = (), bot_names: Sequence[str] = ()) -> None:
        self.bot_ids = tuple(str(value) for value in bot_ids if str(value).strip())
        self.bot_names = tuple(str(value).strip() for value in bot_names if str(value).strip())

    async def detect(
        self,
        message: Mapping[str, Any] | object,
        *,
        bot_ids: Sequence[str] | None = None,
        bot_names: Sequence[str] | None = None,
    ) -> AddresseeResult:
        ids = tuple(str(value) for value in (bot_ids if bot_ids is not None else self.bot_ids))
        names = tuple(str(value).strip() for value in (bot_names if bot_names is not None else self.bot_names))
        for layer in (
            self._adapter_layer,
            self._regex_layer,
            self._quote_layer,
            self._at_layer,
        ):
            result = await layer(message, ids, names)
            if result.target_id:
                return result
        return AddresseeResult(target_id=None, confidence=0.0, source="none")

    async def _adapter_layer(
        self,
        message: Mapping[str, Any] | object,
        bot_ids: Sequence[str],
        _bot_names: Sequence[str],
    ) -> AddresseeResult:
        for key in self._TARGET_KEYS:
            value = _field(message, key) or _field(_field(message, "additional_config"), key)
            if value:
                return AddresseeResult(str(value), 0.98, "adapter")
        additional = _field(message, "additional_config")
        if bot_ids and (_truthy(_field(message, "mentioned_bot")) or _truthy(_field(additional, "mentioned_bot"))):
            return AddresseeResult(bot_ids[0], 0.96, "adapter")
        if bot_ids and (_truthy(_field(message, "at")) or _truthy(_field(additional, "at"))):
            return AddresseeResult(bot_ids[0], 0.93, "adapter")
        return AddresseeResult(None, 0.0, "adapter")

    async def _regex_layer(
        self,
        message: Mapping[str, Any] | object,
        bot_ids: Sequence[str],
        bot_names: Sequence[str],
    ) -> AddresseeResult:
        text = _text(message).lstrip()
        for name in bot_names:
            if re.match(rf"^@?{re.escape(name)}(?:\b|[\s,，:：])", text):
                return AddresseeResult(bot_ids[0] if bot_ids else name, 0.82, "regex")
        return AddresseeResult(None, 0.0, "regex")

    async def _quote_layer(
        self,
        message: Mapping[str, Any] | object,
        _bot_ids: Sequence[str],
        _bot_names: Sequence[str],
    ) -> AddresseeResult:
        for key in self._QUOTE_KEYS:
            value = _field(message, key) or _field(_field(message, "additional_config"), key)
            if value:
                return AddresseeResult(str(value), 0.72, "quote")
        return AddresseeResult(None, 0.0, "quote")

    async def _at_layer(
        self,
        message: Mapping[str, Any] | object,
        _bot_ids: Sequence[str],
        _bot_names: Sequence[str],
    ) -> AddresseeResult:
        text = _text(message)
        for pattern in self._AT_PATTERNS:
            match = pattern.search(text)
            if match:
                return AddresseeResult(match.group(1), 0.9, "at")
        return AddresseeResult(None, 0.0, "at")


def _field(payload: Any, key: str) -> Any:
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        return payload.get(key)
    return getattr(payload, key, None)


def _text(message: Mapping[str, Any] | object) -> str:
    for key in ("content_text", "text", "raw_message", "message"):
        value = _field(message, key)
        if value:
            return str(value)
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def addressee_gate(
    result: AddresseeResult,
    *,
    bot_ids: Sequence[str] = (),
    mood_label: Any | None = None,
    reply_to_bot: bool = False,
) -> bool:
    ids = {str(value) for value in bot_ids if str(value).strip()}
    target = str(result.target_id or "")
    addressed_to_self = bool(reply_to_bot or (target and target in ids))
    return _label(mood_label) == "cold" and not addressed_to_self


def _label(value: Any | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return str(value.get("label") or value.get("mood") or "").strip().lower()
    return str(getattr(value, "label", "") or getattr(value, "mood", "") or value).strip().lower()
