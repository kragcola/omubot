"""群聊统一调度器：debounce/batch/@ 触发模型调用，统一队列。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from kernel.config import GroupConfig
from services.llm.client import RATE_LIMIT_BASE_DELAY, RATE_LIMIT_MAX_RETRIES, RateLimitError
from services.memory.timeline import GroupTimeline
from services.tools.context import ToolContext

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot

    from services.identity import IdentityManager
    from services.llm.client import LLMClient

_L = logger.bind(channel="scheduler")


class _GroupSlot:
    __slots__ = ("debounce_task", "force_reply", "msg_count", "pending_at", "running_task")

    def __init__(self) -> None:
        self.debounce_task: asyncio.Task[None] | None = None
        self.running_task: asyncio.Task[None] | None = None
        self.msg_count: int = 0
        self.pending_at: bool = False
        self.force_reply: bool = False


class GroupChatScheduler:
    """群聊统一调度器：debounce/batch/@触发模型调用。"""

    def __init__(
        self,
        llm: LLMClient,
        timeline: GroupTimeline,
        identity_mgr: IdentityManager,
        group_config: GroupConfig,
        humanizer: Any = None,
    ) -> None:
        self._llm = llm
        self._timeline = timeline
        self._identity_mgr = identity_mgr
        self._group_config = group_config
        self._humanizer = humanizer
        self._slots: dict[str, _GroupSlot] = {}
        self._bot: Bot | None = None
        self._muted_groups: set[str] = set()

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    # ------------------------------------------------------------------
    # Mute management
    # ------------------------------------------------------------------

    def mute(self, group_id: str) -> None:
        """Mark group as muted — cancel pending tasks, block future fires."""
        self._muted_groups.add(group_id)
        slot = self._slots.get(group_id)
        if slot:
            for task in (slot.debounce_task, slot.running_task):
                if task and not task.done():
                    task.cancel()
            slot.debounce_task = None
            slot.running_task = None
            slot.msg_count = 0
            slot.pending_at = False
        _L.info("scheduler | group={} muted, tasks cancelled", group_id)

    def unmute(self, group_id: str) -> None:
        """Unmark group as muted — resume normal scheduling."""
        self._muted_groups.discard(group_id)
        _L.info("scheduler | group={} unmuted", group_id)

    def is_muted(self, group_id: str) -> bool:
        return group_id in self._muted_groups

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify(self, group_id: str, *, is_at: bool = False) -> None:
        """Called on every group message. Manages debounce/batch."""
        if group_id in self._muted_groups:
            return
        identity = self._identity_mgr.resolve()
        if identity.proactive is None:
            return

        resolved = self._group_config.resolve(int(group_id))

        slot = self._slots.setdefault(group_id, _GroupSlot())
        slot.msg_count += 1

        if is_at:
            if slot.running_task and not slot.running_task.done():
                slot.pending_at = True
                _L.debug("scheduler | group={} @ queued (task running)", group_id)
                return
            if slot.debounce_task and not slot.debounce_task.done():
                slot.debounce_task.cancel()
            slot.force_reply = True
            _L.info("scheduler | group={} @ -> fire", group_id)
            self._fire(group_id)
            return

        # at_only mode: only respond to @ messages
        if resolved.at_only:
            _L.debug("scheduler | group={} at_only, skip (msgs={})", group_id, slot.msg_count)
            return

        if slot.running_task and not slot.running_task.done():
            _L.debug("scheduler | group={} busy, skip (msgs={})", group_id, slot.msg_count)
            return

        if slot.debounce_task and not slot.debounce_task.done():
            slot.debounce_task.cancel()

        if slot.msg_count >= resolved.batch_size:
            _L.info("scheduler | group={} batch full ({} msgs) -> fire", group_id, slot.msg_count)
            self._fire(group_id)
        else:
            _L.debug("scheduler | group={} debounce start (msgs={})", group_id, slot.msg_count)
            slot.debounce_task = asyncio.create_task(
                self._debounce(group_id, resolved.debounce_seconds)
            )

    def cancel_debounce(self, group_id: str) -> None:
        """Cancel pending debounce and reset counter. Called after echo handles the batch."""
        slot = self._slots.get(group_id)
        if slot is None:
            return
        if slot.debounce_task and not slot.debounce_task.done():
            slot.debounce_task.cancel()
        slot.msg_count = 0

    def trigger(self, group_id: str) -> None:
        """Immediately fire a chat for this group (no debounce). Used at startup."""
        if group_id in self._muted_groups:
            return
        identity = self._identity_mgr.resolve()
        if identity.proactive is None:
            return
        slot = self._slots.setdefault(group_id, _GroupSlot())
        if slot.running_task and not slot.running_task.done():
            return
        _L.info("scheduler | group={} trigger (startup)", group_id)
        self._fire(group_id)

    async def close(self) -> None:
        """Cancel all pending tasks on shutdown."""
        tasks: list[asyncio.Task[None]] = []
        for slot in self._slots.values():
            for task in (slot.debounce_task, slot.running_task):
                if task and not task.done():
                    task.cancel()
                    tasks.append(task)
        # Let cancelled tasks finish their CancelledError handling
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _debounce(self, group_id: str, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            slot = self._slots.get(group_id)
            if slot and slot.msg_count > 0:
                _L.info("scheduler | group={} debounce fired ({} msgs)", group_id, slot.msg_count)
                self._fire(group_id)
        except asyncio.CancelledError:
            pass

    def _fire(self, group_id: str) -> None:
        slot = self._slots.get(group_id)
        if not slot:
            return
        force = slot.force_reply
        slot.msg_count = 0
        slot.force_reply = False
        slot.running_task = asyncio.create_task(self._do_chat(group_id, force_reply=force))
        slot.running_task.add_done_callback(lambda _: None)

    async def _send_to_group(self, group_id: str, text: str) -> None:
        """Send a text message to a group with retry on failure."""
        if not self._bot:
            return
        from nonebot.adapters.onebot.v11 import Message
        from nonebot.adapters.onebot.v11.exception import ActionFailed

        delay = 2.0
        max_delay = 60.0
        while True:
            if group_id in self._muted_groups:
                _L.warning("scheduler | group={} muted, dropping message", group_id)
                return
            try:
                if self._humanizer is not None:
                    await self._humanizer.delay(text)
                await self._bot.send_group_msg(group_id=int(group_id), message=Message(text))
                return
            except ActionFailed as e:
                _L.warning(
                    "scheduler | group={} send failed: {} | retry in {}s",
                    group_id, e.info.get("wording") or e.info.get("message", str(e)), delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    async def _do_chat(self, group_id: str, *, force_reply: bool = False) -> None:
        slot = self._slots.get(group_id)
        try:
            for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
                try:
                    identity = self._identity_mgr.resolve()
                    session_id = f"group_{group_id}"
                    ctx = ToolContext(bot=self._bot, user_id="", group_id=group_id)

                    async def on_segment(text: str) -> None:
                        await self._send_to_group(group_id, text)

                    resolved = self._group_config.resolve(int(group_id))
                    reply = await self._llm.chat(
                        session_id=session_id,
                        user_id="",
                        user_content="",
                        identity=identity,
                        group_id=group_id,
                        ctx=ctx,
                        on_segment=on_segment if self._bot else None,
                        privacy_mask=resolved.privacy_mask,
                        force_reply=force_reply,
                    )

                    if reply:
                        await self._send_to_group(group_id, reply)
                    return

                except RateLimitError:
                    if attempt >= RATE_LIMIT_MAX_RETRIES:
                        _L.error(
                            "scheduler | group={} rate limit exhausted after {} retries",
                            group_id, RATE_LIMIT_MAX_RETRIES,
                        )
                        return
                    delay = RATE_LIMIT_BASE_DELAY * (2 ** attempt)
                    _L.warning(
                        "scheduler | group={} rate limited, retry {}/{} in {:.0f}s (will include new messages)",
                        group_id, attempt + 1, RATE_LIMIT_MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)

        except asyncio.CancelledError:
            _L.debug("scheduler | group={} chat cancelled", group_id)
        except Exception:
            _L.exception("scheduler | group={} chat error", group_id)
        finally:
            if slot:
                slot.running_task = None
                if slot.pending_at or slot.msg_count > 0:
                    slot.pending_at = False
                    self._fire(group_id)
