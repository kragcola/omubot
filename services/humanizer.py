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

    async def delay(self, text: str = "") -> None:
        """Sleep for a random interval, longer for longer messages."""
        if not self.enabled:
            return
        base = random.uniform(self.min_delay, self.max_delay)
        extra = len(text) * self.char_delay * random.uniform(0.8, 1.2)
        total = base + extra
        await asyncio.sleep(total)


# Module-level singleton — set by chat plugin at startup, imported by tools
_humanizer: Humanizer | None = None


def set_humanizer(h: Humanizer) -> None:
    global _humanizer
    _humanizer = h


def get_humanizer() -> Humanizer | None:
    return _humanizer
