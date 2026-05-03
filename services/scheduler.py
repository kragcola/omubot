"""群聊统一调度器：debounce/batch/@ 触发模型调用，统一队列。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
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
    __slots__ = (
        "consecutive_skip", "debounce_task", "force_reply", "last_fire_time",
        "last_user_id", "msg_count", "pending_at", "running_task", "video_hint",
    )

    def __init__(self) -> None:
        self.consecutive_skip: int = 0
        self.debounce_task: asyncio.Task[None] | None = None
        self.running_task: asyncio.Task[None] | None = None
        self.msg_count: int = 0
        self.pending_at: bool = False
        self.force_reply: bool = False
        self.last_fire_time: float = 0.0
        self.last_user_id: str = ""
        self.video_hint: dict[str, object] | None = None


class GroupChatScheduler:
    """群聊统一调度器：debounce/batch/@触发模型调用。"""

    def __init__(
        self,
        llm: LLMClient,
        timeline: GroupTimeline,
        identity_mgr: IdentityManager,
        group_config: GroupConfig,
        humanizer: Any = None,
        mood_getter: Callable[[], Any] | None = None,
        talk_schedule: Any = None,
    ) -> None:
        self._llm = llm
        self._timeline = timeline
        self._identity_mgr = identity_mgr
        self._group_config = group_config
        self._humanizer = humanizer
        self._mood_getter = mood_getter
        self._talk_schedule = talk_schedule
        self._slots: dict[str, _GroupSlot] = {}
        self._bot: Bot | None = None
        self._muted_groups: set[str] = set()

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    # ------------------------------------------------------------------
    # Mute management
    # ------------------------------------------------------------------

    def mute(self, group_id: str) -> None:
        """Mark group as muted — cancel running task, block future fires."""
        self._muted_groups.add(group_id)
        slot = self._slots.get(group_id)
        if slot:
            if slot.running_task and not slot.running_task.done():
                slot.running_task.cancel()
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

    def notify(self, group_id: str, *, is_at: bool = False, user_id: str = "",
               video_hint: dict[str, object] | None = None) -> None:
        """Called on every group message. Manages debounce/batch."""
        if group_id in self._muted_groups:
            return
        identity = self._identity_mgr.resolve()
        if identity.proactive is None and not is_at and not (
            video_hint is not None and video_hint.get("mode") == "always"
        ):
            # Skip non-@ messages when there are no proactive interjection rules.
            # @ mentions and video always-reply bypass this check.
            _L.info("scheduler | group={} proactive=None, skip (non-@)", group_id)
            return

        resolved = self._group_config.resolve(int(group_id))

        slot = self._slots.setdefault(group_id, _GroupSlot())
        slot.msg_count += 1
        if user_id:
            slot.last_user_id = user_id
        if video_hint is not None:
            slot.video_hint = video_hint  # always update so latest video takes priority

        if is_at:
            if slot.running_task and not slot.running_task.done():
                slot.pending_at = True
                _L.debug("scheduler | group={} @ queued (task running)", group_id)
                return
            slot.force_reply = True
            _L.info("scheduler | group={} @ -> fire", group_id)
            self._fire(group_id)
            return

        # Video always-reply: force fire for B站 video shares
        if video_hint is not None and video_hint.get("mode") == "always":
            if slot.running_task and not slot.running_task.done():
                slot.pending_at = True
                _L.debug("scheduler | group={} bilibili always queued (task running)", group_id)
                return
            slot.force_reply = True
            _L.info("scheduler | group={} bilibili always -> fire", group_id)
            self._fire(group_id)
            return

        # at_only mode: only respond to @ messages
        if resolved.at_only:
            _L.info("scheduler | group={} at_only, skip (msgs={})", group_id, slot.msg_count)
            return

        if slot.running_task and not slot.running_task.done():
            _L.info("scheduler | group={} busy, skip (msgs={})", group_id, slot.msg_count)
            return

        # Probability-based dispatch with minimum interval (replaces debounce).
        now = time.monotonic()
        if now - slot.last_fire_time < resolved.planner_smooth:
            _L.info("scheduler | group={} interval too short, skip (msgs={})", group_id, slot.msg_count)
            return

        # Dynamic threshold: become more responsive after prolonged silence.
        # Use bilibili-specific talk_value when a video hint is present.
        if video_hint is not None and video_hint.get("mode") in ("dedicated", "autonomous"):
            base_talk_value = float(video_hint.get("bilibili_talk_value", resolved.talk_value))  # type: ignore[arg-type]
        else:
            base_talk_value = resolved.talk_value

        threshold = base_talk_value
        if slot.consecutive_skip >= 5:
            threshold = 1.0
        elif slot.consecutive_skip >= 3:
            threshold = min(1.0, base_talk_value * 2)

        # Autonomous mode: apply interest score multiplier.
        # Skip when consecutive_skip >= 5 so the forced-reply guarantee holds.
        if video_hint is not None and video_hint.get("mode") == "autonomous" and slot.consecutive_skip < 5:
            interest_score = float(video_hint.get("interest_score", 0.3))  # type: ignore[arg-type]
            threshold *= interest_score

        # Mood-adjusted probability — good mood boosts, bad mood suppresses.
        mood_mult = self._get_mood_multiplier()
        # Time-slot multiplier — configurable per time range.
        time_mult = self._talk_schedule.get_time_multiplier() if self._talk_schedule else 1.0
        threshold = min(1.0, threshold * mood_mult * time_mult)

        mode_label = video_hint.get("mode", "none") if video_hint else "none"

        if random.random() < threshold:
            _L.info(
                "scheduler | group={} prob fire (threshold={:.2f} mood={:.2f} time={:.2f} msgs={} skips={} mode={})",
                group_id, threshold, mood_mult, time_mult, slot.msg_count, slot.consecutive_skip, mode_label,
            )
            slot.consecutive_skip = 0
            slot.last_fire_time = now
            self._fire(group_id)
        else:
            slot.consecutive_skip += 1
            _L.info(
                "scheduler | group={} prob skip (threshold={:.2f} mood={:.2f} time={:.2f} msgs={} skips={} mode={})",
                group_id, threshold, mood_mult, time_mult, slot.msg_count, slot.consecutive_skip, mode_label,
            )

    def cancel_debounce(self, group_id: str) -> None:
        """Reset message counter. Called after echo handles the batch. (no-op after prob dispatch)"""
        slot = self._slots.get(group_id)
        if slot is None:
            return
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
        """Cancel all running tasks on shutdown."""
        tasks: list[asyncio.Task[None]] = []
        for slot in self._slots.values():
            if slot.running_task and not slot.running_task.done():
                slot.running_task.cancel()
                tasks.append(slot.running_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_mood_multiplier(self) -> float:
        """Compute a mood multiplier for talk_value.

        Good mood (high energy, positive valence, high openness) → >1.0 boost.
        Bad mood (low energy, negative valence, low openness) → <1.0 suppression.
        Range: [0.25, 2.0]. Returns 1.0 when no mood_getter is configured.
        """
        if self._mood_getter is None:
            return 1.0
        profile = self._mood_getter()
        if profile is None:
            return 1.0
        happy = (getattr(profile, "valence", 0.0) + 1.0) / 2.0
        mood_factor = (
            0.4 * getattr(profile, "openness", 0.5)
            + 0.3 * getattr(profile, "energy", 0.5)
            + 0.3 * happy
        )
        return 0.25 + 1.75 * mood_factor

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
        # Snapshot and clear video_hint so it's used exactly once.
        video_hint = slot.video_hint if slot else None
        if slot:
            slot.video_hint = None
        try:
            for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
                try:
                    identity = self._identity_mgr.resolve()
                    session_id = f"group_{group_id}"
                    uid = slot.last_user_id if slot else ""
                    ctx = ToolContext(bot=self._bot, user_id=uid, group_id=group_id)

                    async def on_segment(text: str) -> None:
                        await self._send_to_group(group_id, text)

                    # Build user_content: include video title so the LLM knows
                    # which video triggered the reply when multiple videos are in
                    # the timeline.
                    user_content = ""
                    if video_hint is not None:
                        mode = video_hint.get("mode", "")
                        video_title = video_hint.get("video_title", "")
                        if mode == "always":
                            user_content = f"（看到你分享了视频《{video_title}》，回应一下）"
                        elif mode in ("dedicated", "autonomous"):
                            user_content = f"（看到你分享了视频《{video_title}》，聊聊你的看法）"

                    resolved = self._group_config.resolve(int(group_id))
                    reply = await self._llm.chat(
                        session_id=session_id,
                        user_id=uid,
                        user_content=user_content,
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
