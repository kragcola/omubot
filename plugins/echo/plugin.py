"""EchoPlugin: group echo detection — priority 200, pipeline interceptor.

Detects repeated identical messages within a 5-minute window.
On 3rd occurrence: either echoes the message or randomly (5%) breaks the chain.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from loguru import logger

from kernel.types import AmadeusPlugin, MessageContext, PluginContext

_ECHO_WINDOW_S = 300.0
_ECHO_THRESHOLD = 3
_BREAK_TEXT = "打断复读！"
_BREAK_CHANCE = 0.05

_log = logger.bind(channel="echo")


def build_echo_key(segments: Iterable[object]) -> str:
    """Build a stable key from OneBot message segments for echo detection."""
    parts: list[str] = []
    for seg in segments:
        t: str = getattr(seg, "type", "")
        d: dict = getattr(seg, "data", {}) or {}
        if t == "text":
            parts.append(d.get("text", ""))
        elif t == "image":
            sub = str(d.get("sub_type", "0"))
            file_hash = d.get("file", "")
            parts.append(f"[image:{sub}:{file_hash}]")
        elif t == "face":
            parts.append(f"[face:{d.get('id', '')}]")
        elif t == "at":
            parts.append(f"[at:{d.get('qq', '')}]")
        else:
            parts.append(f"[{t}]")
    return "".join(parts).strip()


@dataclass
class _EchoState:
    text: str = ""
    count: int = 0
    first_seen: float = 0.0
    echoed: bool = False
    interrupt_chain: int = 0


class EchoTracker:
    """Per-group repeat detection and interrupt chain."""

    def __init__(self, rand: Callable[[], float] | None = None) -> None:
        self._states: dict[str, _EchoState] = {}
        self._rand = rand or random.random

    def process(self, group_id: str, text: str, now: float) -> str | None:
        """Process a message. Returns a string to send, or None."""
        state = self._states.get(group_id)
        if state is None:
            state = _EchoState()
            self._states[group_id] = state

        if text == _BREAK_TEXT:
            state.interrupt_chain += 1
            prefix = "打断" * state.interrupt_chain
            return f"{prefix}复读！"

        if text != state.text:
            state.text = text
            state.count = 1
            state.first_seen = now
            state.echoed = False
            state.interrupt_chain = 0
            return None

        if now - state.first_seen > _ECHO_WINDOW_S or state.echoed:
            return None

        state.count += 1
        if state.count < _ECHO_THRESHOLD:
            return None

        state.echoed = True

        if self._rand() < _BREAK_CHANCE:
            state.interrupt_chain = 0
            return _BREAK_TEXT

        return state.text


class EchoPlugin(AmadeusPlugin):
    name = "echo"
    description = "群聊复读检测：5分钟内同消息3次触发复读，5%概率打断"
    version = "1.0.0"
    priority = 200

    async def on_startup(self, ctx: PluginContext) -> None:
        self._tracker = EchoTracker()
        self._humanizer = ctx.humanizer
        self._scheduler = ctx.scheduler
        self._timeline = ctx.timeline

    async def on_message(self, ctx: MessageContext) -> bool:
        if ctx.is_private:
            return False

        echo_key = ctx.raw_message.get("echo_key", "")
        if not echo_key:
            return False

        echo_reply = self._tracker.process(ctx.group_id or "", echo_key, time.monotonic())
        if echo_reply is None:
            return False

        group_id = ctx.group_id
        if group_id is None:
            return False

        self._scheduler.cancel_debounce(group_id)
        self._timeline.add(
            group_id,
            role="user",
            speaker=f"{ctx.nickname}({ctx.user_id})",
            content=ctx.raw_message.get("plain_text", echo_key),
            message_id=ctx.message_id or 0,
        )

        from nonebot.adapters.onebot.v11 import Message

        if echo_reply.startswith("打断"):
            await self._humanizer.delay(echo_reply)
            await ctx.bot.send_group_msg(group_id=int(group_id), message=Message(echo_reply))
        else:
            segments = ctx.raw_message.get("segments")
            await self._humanizer.delay(echo_key)
            await ctx.bot.send_group_msg(group_id=int(group_id), message=segments)

        self._timeline.add(
            group_id,
            role="assistant",
            speaker="",
            content=echo_reply,
            message_id=0,
        )
        _log.info("echo | group={} key={!r}", group_id, echo_reply)
        return True
