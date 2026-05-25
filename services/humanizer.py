"""防检测人性化模块：随机延迟模拟人类打字节奏。

在每次发送消息前插入随机延迟，避免因即时回复被腾讯风控识别为机器人。
"""

from __future__ import annotations

import asyncio
import random

from loguru import logger

_L = logger.bind(channel="system")


class Humanizer:
    """Add human-like random delays before message sends."""

    def __init__(
        self,
        enabled: bool = True,
        min_delay: float = 0.5,
        max_delay: float = 3.0,
        char_delay: float = 0.02,
    ) -> None:
        self.enabled = enabled
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.char_delay = char_delay
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
    ) -> None:
        """Sleep for a random interval, longer for longer messages."""
        if not self.enabled:
            return
        base = random.uniform(self.min_delay, self.max_delay)
        extra = len(text) * self.char_delay * random.uniform(0.8, 1.2)
        total = (base + extra) * self._runtime_multiplier(
            group_id=group_id,
            register=register,
            slot=slot,
            mood=mood,
        )
        await asyncio.sleep(total)

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
            return 0.7
        if register_label == "quiet" and _energy(slot) < 0.3 and _energy(mood) < 0.4:
            return 1.5
        return 1.0


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


# Module-level singleton — set by chat plugin at startup, imported by tools
_humanizer: Humanizer | None = None


def set_humanizer(h: Humanizer) -> None:
    global _humanizer
    _humanizer = h


def get_humanizer() -> Humanizer | None:
    return _humanizer
