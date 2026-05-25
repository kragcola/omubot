"""防检测人性化模块：随机延迟模拟人类打字节奏。

在每次发送消息前插入随机延迟，避免因即时回复被腾讯风控识别为机器人。
"""

from __future__ import annotations

import asyncio
import random
import re

from loguru import logger

_L = logger.bind(channel="system")

EMOJI_BASE_DELAY = 1.0
THINKING_FALLBACK = 10.0
_THINKING_FALLBACK_DELAY = 1.0
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")
_MOOD_DELAY_FACTOR = {
    "cold": 1.3,
    "tired": 1.15,
    "neutral": 1.0,
    "playful": 0.8,
    "high": 0.85,
}


class Humanizer:
    """Add human-like random delays before message sends."""

    def __init__(
        self,
        enabled: bool = True,
        min_delay: float = 0.5,
        max_delay: float = 3.0,
        char_delay: float = 0.02,
        emoji_base_s: float = EMOJI_BASE_DELAY,
    ) -> None:
        self.enabled = enabled
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.char_delay = char_delay
        self.emoji_base_s = emoji_base_s
        if enabled:
            _L.info(
                "humanizer enabled | delay={:.1f}s–{:.1f}s char_delay={:.0f}ms",
                min_delay, max_delay, char_delay * 1000,
            )

    async def delay(
        self,
        text: str = "",
        *,
        group_id: str | int | None = None,
        register: object | None = None,
        slot: object | None = None,
        mood: object | None = None,
        thinking_elapsed_s: float | None = None,
    ) -> None:
        """Sleep for a random interval, longer for longer messages."""
        if not self.enabled:
            return
        base = random.uniform(self.min_delay, self.max_delay)
        extra = self._typing_extra(text)
        total = (base + extra) * self._runtime_multiplier(
            group_id=group_id,
            register=register,
            slot=slot,
            mood=mood,
        )
        if _thinking_elapsed(thinking_elapsed_s) >= THINKING_FALLBACK:
            total = min(total, _THINKING_FALLBACK_DELAY)
        await asyncio.sleep(total)

    def _typing_extra(self, text: str) -> float:
        extra = len(text) * self.char_delay * random.uniform(0.8, 1.2)
        if _has_emoji(text):
            extra = max(extra, self.emoji_base_s)
        return extra

    @staticmethod
    def _runtime_multiplier(
        *,
        group_id: str | int | None = None,
        register: object | None = None,
        slot: object | None = None,
        mood: object | None = None,
    ) -> float:
        _ = group_id
        register_label = _register_label(register)
        if register_label == "playful":
            return 0.7 * _mood_factor(mood)
        if register_label == "quiet" and _energy(slot) < 0.3 and _energy(mood) < 0.4:
            return 1.5 * _mood_factor(mood)
        return _mood_factor(mood)


def _register_label(register: object | None) -> str:
    if register is None:
        return ""
    if isinstance(register, str):
        return register.strip().lower()
    if isinstance(register, dict):
        for key in ("label", "register", "name"):
            value = register.get(key)
            if value:
                return str(value).strip().lower()
    return str(getattr(register, "label", "") or getattr(register, "register", "")).strip().lower()


def _energy(value: object | None) -> float:
    if value is None:
        return 1.0
    raw = value.get("energy", 1.0) if isinstance(value, dict) else getattr(value, "energy", 1.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def _mood_factor(value: object | None) -> float:
    label = _mood_label(value)
    return _MOOD_DELAY_FACTOR.get(label, 1.0)


def _mood_label(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        return str(value.get("label") or value.get("mood") or "").strip().lower()
    return str(getattr(value, "label", "") or getattr(value, "mood", "")).strip().lower()


def _has_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(text) or "[CQ:face" in text or "[CQ:mface" in text)


def _thinking_elapsed(value: float | None) -> float:
    try:
        return 0.0 if value is None else max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


# Module-level singleton — set by chat plugin at startup, imported by tools
_humanizer: Humanizer | None = None


def set_humanizer(h: Humanizer) -> None:
    global _humanizer
    _humanizer = h


def get_humanizer() -> Humanizer | None:
    return _humanizer
