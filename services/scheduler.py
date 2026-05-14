"""群聊统一调度器：debounce/batch/@ 触发模型调用，统一队列。"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from kernel.config import GroupConfig, ReplySegmentationConfig, ReplyWorkflowConfig
from kernel.types import TriggerContext
from services.llm.client import (
    RATE_LIMIT_BASE_DELAY,
    RATE_LIMIT_MAX_RETRIES,
    CollectedReply,
    RateLimitError,
)
from services.memory.timeline import GroupTimeline
from services.send_queue import BatchSendHandle, GroupSendQueue, ReplySegmentBatch
from services.tools.context import ToolContext

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot

    from services.identity import IdentityManager
    from services.llm.client import LLMClient
    from services.reply_workflow import ReplyWorkflowAction

_L = logger.bind(channel="scheduler")


_TRIGGER_PRIORITIES: dict[str, int] = {
    "at_mention": 10,
    "video_always": 20,
    "directed_followup": 30,
}
_DEFAULT_TRIGGER_PRIORITY = 100

_FORCE_REPLY_FOCUS_DIRECTIVE = (
    "请优先直接回应本轮最后一条触发消息；"
    "不要因为历史对话、知识库资料或群记忆自行切换话题。"
)


@dataclass(order=True)
class _QueuedTrigger:
    priority: int
    sequence: int
    trigger: TriggerContext = field(compare=False)


class _GroupSlot:
    __slots__ = (
        "_pending_triggers", "_trigger_sequence", "consecutive_skip",
        "debounce_task", "last_fire_time", "last_user_id", "msg_count",
        "running_task", "trigger",
    )

    def __init__(self) -> None:
        self.consecutive_skip: int = 0
        self.debounce_task: asyncio.Task[None] | None = None
        self.running_task: asyncio.Task[None] | None = None
        self.msg_count: int = 0
        self._pending_triggers: list[_QueuedTrigger] = []
        self.last_fire_time: float = 0.0
        self.last_user_id: str = ""
        self.trigger: TriggerContext | None = None
        self._trigger_sequence = itertools.count()

    @property
    def pending_at(self) -> bool:
        """Backward-compatible view: whether any strong trigger is queued."""
        return bool(self._pending_triggers)

    @pending_at.setter
    def pending_at(self, value: bool) -> None:
        # Existing tests and reset paths assign False. Assigning True without a
        # TriggerContext would be ambiguous, so it is intentionally a no-op.
        if not value:
            self._pending_triggers.clear()

    @property
    def pending_trigger_count(self) -> int:
        return len(self._pending_triggers)

    def enqueue_trigger(self, trigger: TriggerContext) -> None:
        priority = _TRIGGER_PRIORITIES.get(trigger.mode, _DEFAULT_TRIGGER_PRIORITY)
        heapq.heappush(
            self._pending_triggers,
            _QueuedTrigger(priority=priority, sequence=next(self._trigger_sequence), trigger=trigger),
        )
        self.trigger = trigger

    def pop_trigger(self) -> TriggerContext | None:
        if not self._pending_triggers:
            self.trigger = None
            return None
        queued = heapq.heappop(self._pending_triggers)
        self.trigger = self._pending_triggers[0].trigger if self._pending_triggers else None
        return queued.trigger


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
        reply_segmentation: ReplySegmentationConfig | None = None,
        reply_workflow: ReplyWorkflowConfig | None = None,
        global_llm_limit: int = 2,
        first_segment_release: bool = False,
    ) -> None:
        self._llm = llm
        self._timeline = timeline
        self._identity_mgr = identity_mgr
        self._group_config = group_config
        self._humanizer = humanizer
        self._mood_getter = mood_getter
        self._talk_schedule = talk_schedule
        self._reply_segmentation = reply_segmentation or ReplySegmentationConfig()
        self._reply_workflow = reply_workflow or ReplyWorkflowConfig()
        self._first_segment_release = bool(first_segment_release)
        if first_segment_release:
            _L.warning("scheduler | first_segment_release enabled (experimental)")
        self._slots: dict[str, _GroupSlot] = {}
        self._tail_send_tasks: set[asyncio.Task[None]] = set()
        self._bot: Bot | None = None
        self._muted_groups: set[str] = set()
        self._llm_semaphore = asyncio.Semaphore(max(1, int(global_llm_limit)))
        self._send_queue = GroupSendQueue(
            humanizer=self._humanizer,
            muted_checker=lambda gid: gid in self._muted_groups,
            send_allowed_checker=self._can_send_to_group,
        )

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot
        self._send_queue.set_bot(bot)

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

    def _can_send_to_group(self, group_id: str) -> bool:
        checker = getattr(self._group_config, "allows_active_group", None)
        if callable(checker):
            try:
                return bool(checker(group_id))
            except Exception:
                return True
        try:
            resolved = self._group_config.resolve(int(group_id))
            access_allowed = bool(getattr(resolved, "access_allowed", True))
            presence_mode = str(getattr(resolved, "presence_mode", "active") or "active")
            return access_allowed and presence_mode == "active"
        except Exception:
            return True

    def get_all_slots(self) -> dict[str, dict[str, object]]:
        """Return public state for all group slots (admin API)."""
        result: dict[str, dict[str, object]] = {}
        for gid, slot in self._slots.items():
            result[gid] = {
                "consecutive_skip": slot.consecutive_skip,
                "msg_count": slot.msg_count,
                "pending_at": slot.pending_at,
                "pending_trigger_count": slot.pending_trigger_count,
                "last_fire_time": slot.last_fire_time,
                "last_user_id": slot.last_user_id,
                "has_trigger": slot.trigger is not None,
                "is_muted": gid in self._muted_groups,
                "has_running_task": slot.running_task is not None and not slot.running_task.done(),
            }
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _log_workflow_shadow(
        self,
        group_id: str,
        *,
        action: ReplyWorkflowAction,
        reason: str,
        threshold: float | None = None,
        mood_mult: float | None = None,
        time_mult: float | None = None,
        msg_count: int = 0,
        skips: int = 0,
        trigger_mode: str = "none",
    ) -> None:
        if getattr(self._reply_workflow, "mode", "shadow") != "shadow":
            return
        from services.reply_workflow import log_shadow_decision, scheduler_shadow_decision

        decision = scheduler_shadow_decision(
            action=action,
            reason=reason,
            threshold=threshold,
            mood_mult=mood_mult,
            time_mult=time_mult,
            msg_count=msg_count,
            skips=skips,
            trigger_mode=trigger_mode,
        )
        log_shadow_decision(
            decision,
            conversation=f"group_{group_id}",
            mode="group_scheduler_shadow",
        )

    def notify(self, group_id: str, *, trigger: TriggerContext | None = None,
               user_id: str = "") -> None:
        """Called on every group message. Manages probability-based dispatch."""
        if group_id in self._muted_groups:
            return
        if not self._can_send_to_group(group_id):
            _L.info("scheduler | group={} speaking disabled by group policy, skip", group_id)
            return
        identity = self._identity_mgr.resolve()
        is_at = trigger is not None and trigger.mode == "at_mention"
        is_directed = trigger is not None and trigger.mode == "directed_followup"
        is_video_always = trigger is not None and trigger.mode == "video_always"
        if identity.proactive is None and not is_at and not is_directed and not is_video_always:
            # Skip non-@ messages when there are no proactive interjection rules.
            # @ mentions and video always-reply bypass this check.
            _L.info("scheduler | group={} proactive=None, skip (non-@)", group_id)
            self._log_workflow_shadow(
                group_id,
                action="pass",
                reason="proactive_none_non_addressed",
                trigger_mode=trigger.mode if trigger is not None else "none",
            )
            return

        resolved = self._group_config.resolve(int(group_id))

        slot = self._slots.setdefault(group_id, _GroupSlot())
        slot.msg_count += 1
        if user_id:
            slot.last_user_id = user_id

        if is_at or is_directed:
            if slot.running_task and not slot.running_task.done():
                assert trigger is not None
                slot.enqueue_trigger(trigger)
                _L.debug(
                    "scheduler | group={} {} queued (task running, queue_len={})",
                    group_id, trigger.mode, slot.pending_trigger_count,
                )
                self._log_workflow_shadow(
                    group_id,
                    action="force_reply",
                    reason=f"{trigger.mode}_queued_task_running",
                    msg_count=slot.msg_count,
                    skips=slot.consecutive_skip,
                    trigger_mode=trigger.mode,
                )
                return
            _L.info("scheduler | group={} {} -> fire", group_id, trigger.mode if trigger else "unknown")
            self._log_workflow_shadow(
                group_id,
                action="force_reply",
                reason=f"{trigger.mode if trigger else 'unknown'}_force_fire",
                msg_count=slot.msg_count,
                skips=slot.consecutive_skip,
                trigger_mode=trigger.mode if trigger else "unknown",
            )
            self._fire(group_id, trigger=trigger)
            return

        # Video always-reply: force fire for B站 video shares
        if is_video_always:
            if slot.running_task and not slot.running_task.done():
                assert trigger is not None
                slot.enqueue_trigger(trigger)
                _L.debug(
                    "scheduler | group={} bilibili always queued (task running, queue_len={})",
                    group_id, slot.pending_trigger_count,
                )
                self._log_workflow_shadow(
                    group_id,
                    action="force_reply",
                    reason="video_always_queued_task_running",
                    msg_count=slot.msg_count,
                    skips=slot.consecutive_skip,
                    trigger_mode=trigger.mode,
                )
                return
            _L.info("scheduler | group={} bilibili always -> fire", group_id)
            self._log_workflow_shadow(
                group_id,
                action="force_reply",
                reason="video_always_force_fire",
                msg_count=slot.msg_count,
                skips=slot.consecutive_skip,
                trigger_mode=trigger.mode if trigger else "video_always",
            )
            self._fire(group_id, trigger=trigger)
            return

        # at_only mode: only respond to @ messages
        if resolved.at_only:
            _L.info("scheduler | group={} at_only, skip (msgs={})", group_id, slot.msg_count)
            self._timeline.deactivate_pending(group_id, "at_only")
            slot.trigger = None  # clear trigger on skip — prevent leak
            self._log_workflow_shadow(
                group_id,
                action="suppress",
                reason="at_only",
                msg_count=slot.msg_count,
                skips=slot.consecutive_skip,
                trigger_mode=trigger.mode if trigger is not None else "none",
            )
            slot.msg_count = 0
            return

        if slot.running_task and not slot.running_task.done():
            _L.info("scheduler | group={} busy, skip (msgs={})", group_id, slot.msg_count)
            self._timeline.deactivate_latest_pending(group_id, "scheduler_busy")
            slot.trigger = None
            self._log_workflow_shadow(
                group_id,
                action="suppress",
                reason="scheduler_busy",
                msg_count=slot.msg_count,
                skips=slot.consecutive_skip,
                trigger_mode=trigger.mode if trigger is not None else "none",
            )
            slot.msg_count = 0
            return

        # Probability-based dispatch with minimum interval (replaces debounce).
        now = time.monotonic()
        if now - slot.last_fire_time < resolved.planner_smooth:
            _L.info("scheduler | group={} interval too short, skip (msgs={})", group_id, slot.msg_count)
            self._timeline.deactivate_pending(group_id, "interval_too_short")
            slot.trigger = None  # clear trigger on skip — prevent leak
            self._log_workflow_shadow(
                group_id,
                action="suppress",
                reason="interval_too_short",
                msg_count=slot.msg_count,
                skips=slot.consecutive_skip,
                trigger_mode=trigger.mode if trigger is not None else "none",
            )
            slot.msg_count = 0
            return

        # Dynamic threshold: become more responsive after prolonged silence.
        # Use bilibili-specific talk_value when a video trigger is present.
        is_video_prob = trigger is not None and trigger.mode in ("video_dedicated", "video_autonomous")
        if is_video_prob:
            assert trigger is not None
            base_talk_value = float(trigger.extra.get("bilibili_talk_value", resolved.talk_value))
        else:
            base_talk_value = resolved.talk_value

        threshold = base_talk_value
        if slot.consecutive_skip >= 5:
            threshold = 1.0
        elif slot.consecutive_skip >= 3:
            threshold = min(1.0, base_talk_value * 2)

        # Autonomous mode: apply interest score as a floor booster.
        # High-interest videos (e.g. pjsk, 25时) should fire reliably even
        # during low-activity time slots — the bot cares about the content.
        # Skip when consecutive_skip >= 5 so the forced-reply guarantee holds.
        is_autonomous = trigger is not None and trigger.mode == "video_autonomous"
        if is_autonomous and slot.consecutive_skip < 5:
            assert trigger is not None
            interest_score = float(trigger.extra.get("interest_score", 0.3))
            # Blend so interest acts as a floor: 0.1 + 0.9×interest maps
            # 0.05→0.145, 0.55→0.595, 0.85→0.865, 1.0→1.0
            threshold = base_talk_value * (0.1 + 0.9 * interest_score)

        # Mood-adjusted probability — good mood boosts, bad mood suppresses.
        mood_mult = self._get_mood_multiplier()
        # Time-slot multiplier — capped at 0.7 globally.
        # High-interest videos (score >= 0.6) get a floor of 0.7 so the
        # bot can still reply during off hours, but never exceed the cap.
        time_mult = self._talk_schedule.get_time_multiplier() if self._talk_schedule else 1.0
        if is_autonomous:
            assert trigger is not None
            interest_score = float(trigger.extra.get("interest_score", 0.3))
            if interest_score >= 0.6:
                time_mult = max(time_mult, 0.7)
        threshold = min(1.0, threshold * mood_mult * time_mult)

        mode_label = trigger.mode if trigger else "none"

        if random.random() < threshold:
            _L.info(
                "scheduler | group={} prob fire (threshold={:.2f} mood={:.2f} time={:.2f} msgs={} skips={} mode={})",
                group_id, threshold, mood_mult, time_mult, slot.msg_count, slot.consecutive_skip, mode_label,
            )
            slot.consecutive_skip = 0
            slot.last_fire_time = now
            self._log_workflow_shadow(
                group_id,
                action="force_reply",
                reason="probability_fire",
                threshold=threshold,
                mood_mult=mood_mult,
                time_mult=time_mult,
                msg_count=slot.msg_count,
                skips=slot.consecutive_skip,
                trigger_mode=mode_label,
            )
            self._fire(group_id, trigger=trigger)
        else:
            slot.consecutive_skip += 1
            _L.info(
                "scheduler | group={} prob skip (threshold={:.2f} mood={:.2f} time={:.2f} msgs={} skips={} mode={})",
                group_id, threshold, mood_mult, time_mult, slot.msg_count, slot.consecutive_skip, mode_label,
            )
            self._timeline.deactivate_pending(group_id, f"prob_skip:{mode_label}")
            slot.trigger = None  # clear trigger on skip — prevent leak
            self._log_workflow_shadow(
                group_id,
                action="pass",
                reason="probability_skip",
                threshold=threshold,
                mood_mult=mood_mult,
                time_mult=time_mult,
                msg_count=slot.msg_count,
                skips=slot.consecutive_skip,
                trigger_mode=mode_label,
            )
            slot.msg_count = 0

    def cancel_debounce(self, group_id: str) -> None:
        """Reset message counter. Called after echo handles the batch. (no-op after prob dispatch)"""
        slot = self._slots.get(group_id)
        if slot is None:
            return
        slot.msg_count = 0

    def clear_pending(self, group_id: str, *, cancel_running: bool = False) -> None:
        """Clear queued scheduler state for interactive command flows."""
        slot = self._slots.get(group_id)
        if slot is None:
            return
        slot.msg_count = 0
        slot.trigger = None
        slot.pending_at = False
        if cancel_running and slot.running_task and not slot.running_task.done():
            slot.running_task.cancel()
            slot.running_task = None
        _L.info(
            "scheduler | group={} pending cleared cancel_running={}",
            group_id,
            cancel_running,
        )

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
        tail_tasks = list(self._tail_send_tasks)
        for task in tail_tasks:
            if not task.done():
                task.cancel()
        if tail_tasks:
            await asyncio.gather(*tail_tasks, return_exceptions=True)
        self._tail_send_tasks.clear()
        await self._send_queue.close()

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

    def _fire(self, group_id: str, *, trigger: TriggerContext | None = None) -> None:
        slot = self._slots.get(group_id)
        if not slot:
            return
        if slot.running_task and not slot.running_task.done():
            if trigger is not None:
                slot.enqueue_trigger(trigger)
            return
        # Snapshot and clear trigger so it's consumed exactly once.
        if trigger is None:
            trigger = slot.trigger
        slot.trigger = None
        slot.msg_count = 0
        slot.running_task = asyncio.create_task(self._do_chat(group_id, trigger=trigger))
        slot.running_task.add_done_callback(lambda _: None)

    async def send_group_text(self, group_id: str, text: str, *, humanize: str = "normal") -> float:
        """Send visible group text through the per-group send queue."""
        if not self._bot:
            return 0.0
        if not self._can_send_to_group(group_id):
            _L.info("scheduler | group={} speaking disabled, drop text send", group_id)
            return 0.0

        # Detect [CQ:reply,id=X] prefix for quote-reply targeting.
        # OneBot v11 Message handles CQ codes natively — we just log it.
        if text.startswith("[CQ:reply,id="):
            import re
            if m := re.match(r"\[CQ:reply,id=(-?\d+)\]", text):
                _L.info("scheduler | group={} reply targets msg_id={}", group_id, m.group(1))

        return await self._send_queue.send_group_text(group_id, text, humanize=humanize)

    async def enqueue_group_text(
        self,
        group_id: str,
        text: str,
        *,
        humanize: str = "normal",
        description: str = "scheduler_text",
    ) -> asyncio.Future[float]:
        """Enqueue visible group text and return a completion future."""
        if not self._bot:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[float] = loop.create_future()
            future.set_result(0.0)
            return future
        if not self._can_send_to_group(group_id):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.set_result(0.0)
            _L.info("scheduler | group={} speaking disabled, drop queued text send", group_id)
            return future

        return await self._send_queue.enqueue_group_text(
            group_id,
            text,
            humanize=humanize,
            description=description,
        )

    async def _send_to_group(self, group_id: str, text: str, *, humanize: str = "normal") -> float:
        """Backward-compatible scheduler text send wrapper."""
        return await self.send_group_text(group_id, text, humanize=humanize)

    async def _send_reply_batch(
        self,
        group_id: str,
        segments: list[str],
        *,
        reply_prefix: str = "",
        assistant_turn_id: str | None = None,
        release_after_first: bool = False,
    ) -> tuple[int, float, bool]:
        """Send a collected reply as one contiguous per-group batch."""
        if not self._can_send_to_group(group_id):
            _L.info("scheduler | group={} speaking disabled, drop reply batch", group_id)
            return 0, 0.0, False
        visible_segments = [
            f"{reply_prefix}{segment}" if idx == 0 and reply_prefix else segment
            for idx, segment in enumerate(segments)
        ]
        handle = await self._send_queue.enqueue_reply_batch(
            ReplySegmentBatch(
                group_id=group_id,
                segments=visible_segments,
                first_segment_humanize=self._reply_segmentation.first_segment_humanize,
                later_segment_humanize=self._reply_segmentation.later_segment_humanize,
                allow_interleaved_between_segments=True,
                inter_segment_delay_s=self._reply_segmentation.inter_segment_delay_s,
            )
        )
        queue_wait = await handle.started
        first_elapsed = await handle.first_segment_sent
        if release_after_first:
            self._timeline.mark_assistant_visible_state(group_id, assistant_turn_id, "first_segment_sent")
            task = asyncio.create_task(
                self._finish_reply_batch_tail(
                    group_id=group_id,
                    handle=handle,
                    first_elapsed=first_elapsed,
                    queue_wait=queue_wait,
                    segment_count=len(visible_segments),
                    assistant_turn_id=assistant_turn_id,
                )
            )
            self._tail_send_tasks.add(task)
            task.add_done_callback(self._tail_send_tasks.discard)
            _L.info(
                "scheduler reply batch first segment sent | group={} segments={} "
                "reply_batch_queue_wait_s={:.3f} first_segment_elapsed_s={:.3f}",
                group_id, len(visible_segments), queue_wait, first_elapsed,
            )
            return 1, first_elapsed, True
        elapsed = await handle.done
        interleave_count = await handle.interleave_count
        tail_elapsed = max(0.0, elapsed - first_elapsed)
        _L.info(
            "scheduler reply batch metrics | group={} segments={} "
            "reply_batch_queue_wait_s={:.3f} first_segment_elapsed_s={:.3f} "
            "tail_send_elapsed_s={:.3f} total_send_elapsed_s={:.3f} interleave_count={}",
            group_id, len(visible_segments), queue_wait, first_elapsed, tail_elapsed, elapsed, interleave_count,
        )
        return len(visible_segments), elapsed, False

    async def _finish_reply_batch_tail(
        self,
        *,
        group_id: str,
        handle: BatchSendHandle,
        first_elapsed: float,
        queue_wait: float,
        segment_count: int,
        assistant_turn_id: str | None,
    ) -> None:
        try:
            elapsed = await handle.done
            interleave_count = await handle.interleave_count
        except Exception:
            self._timeline.mark_assistant_visible_state(group_id, assistant_turn_id, "failed")
            _L.exception("scheduler reply batch tail failed | group={} segments={}", group_id, segment_count)
            return
        tail_elapsed = max(0.0, elapsed - first_elapsed)
        self._timeline.mark_assistant_visible_state(group_id, assistant_turn_id, "complete")
        _L.info(
            "scheduler reply batch tail complete | group={} segments={} "
            "reply_batch_queue_wait_s={:.3f} first_segment_elapsed_s={:.3f} "
            "tail_send_elapsed_s={:.3f} total_send_elapsed_s={:.3f} interleave_count={}",
            group_id, segment_count, queue_wait, first_elapsed, tail_elapsed, elapsed, interleave_count,
        )

    async def _do_chat(self, group_id: str, *, trigger: TriggerContext | None = None) -> None:
        slot = self._slots.get(group_id)
        try:
            for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
                try:
                    identity = self._identity_mgr.resolve()
                    session_id = f"group_{group_id}"
                    uid = slot.last_user_id if slot else ""
                    ctx = ToolContext(
                        bot=self._bot,
                        user_id=uid,
                        group_id=group_id,
                        extra={"send_queue": self._send_queue, "group_policy": self._group_config},
                    )

                    # Write trigger reason into the timeline so the LLM sees it
                    # in the pending buffer, not as transient user_content.
                    if trigger is not None:
                        self._timeline.add_pending_trigger(
                            group_id, reason=trigger.reason,
                            message_id=trigger.target_message_id,
                            target_user_id=trigger.target_user_id,
                        )

                    force_reply = trigger is not None and trigger.mode in (
                        "at_mention", "directed_followup", "video_always",
                    )

                    # @mention: prepend [CQ:reply] to the first visible segment only.
                    # Quote-reply already identifies the target — no need for [CQ:at].
                    sent_segments = 0
                    send_total_elapsed = 0.0
                    reply_prefix = ""
                    if (
                        trigger is not None
                        and trigger.mode in ("at_mention", "directed_followup")
                        and trigger.target_message_id is not None
                    ):
                        reply_prefix = f"[CQ:reply,id={trigger.target_message_id}]"
                        _L.info("scheduler | group={} {} prefix={}", group_id, trigger.mode, reply_prefix)

                    async def send_reply_segments(
                        segments: list[str],
                        *,
                        assistant_turn_id: str | None = None,
                        release_after_first: bool = False,
                        _prefix: str = reply_prefix,
                    ) -> bool:
                        nonlocal sent_segments, send_total_elapsed
                        count, elapsed, released = await self._send_reply_batch(
                            group_id,
                            segments,
                            reply_prefix=_prefix,
                            assistant_turn_id=assistant_turn_id,
                            release_after_first=release_after_first,
                        )
                        sent_segments += count
                        send_total_elapsed += elapsed
                        return released

                    resolved = self._group_config.resolve(int(group_id))
                    llm_wait_start = time.monotonic()
                    async with self._llm_semaphore:
                        llm_wait_elapsed = time.monotonic() - llm_wait_start
                        if llm_wait_elapsed >= 0.05:
                            _L.info(
                                "scheduler llm wait | group={} wait={:.2f}s queued_triggers={}",
                                group_id, llm_wait_elapsed, slot.pending_trigger_count if slot else 0,
                            )
                        llm_call_start = time.monotonic()
                        reply = await self._llm.chat(
                            session_id=session_id,
                            user_id=uid,
                            user_content=_FORCE_REPLY_FOCUS_DIRECTIVE if force_reply else "",
                            identity=identity,
                            group_id=group_id,
                            ctx=ctx,
                            privacy_mask=resolved.privacy_mask,
                            force_reply=force_reply,
                            allow_empty_fallback=trigger is None or trigger.mode != "directed_followup",
                            collect_segments=True,
                        )
                        llm_elapsed = time.monotonic() - llm_call_start
                        if llm_elapsed >= 8.0:
                            _L.warning(
                                "scheduler llm slow | group={} elapsed={:.1f}s trigger={}",
                                group_id, llm_elapsed, trigger.mode if trigger else "none",
                            )

                    if isinstance(reply, CollectedReply) or reply:
                        try:
                            segments_to_send = reply.segments if isinstance(reply, CollectedReply) else [reply]
                            assistant_turn_id = reply.assistant_turn_id if isinstance(reply, CollectedReply) else None
                            released = await send_reply_segments(
                                segments_to_send,
                                assistant_turn_id=assistant_turn_id,
                                release_after_first=(
                                    self._first_segment_release and isinstance(reply, CollectedReply)
                                ),
                            )
                        except Exception:
                            assistant_turn_id = reply.assistant_turn_id if isinstance(reply, CollectedReply) else None
                            self._timeline.mark_assistant_visible_state(group_id, assistant_turn_id, "failed")
                            _L.exception(
                                "scheduler reply send failed | group={} segments_sent={} send_total={:.1f}s",
                                group_id, sent_segments, send_total_elapsed,
                            )
                            return
                        if isinstance(reply, CollectedReply):
                            if released:
                                _L.info(
                                    "scheduler reply batch released after first segment | group={} "
                                    "segments_sent={} send_elapsed={:.1f}s",
                                    group_id, sent_segments, send_total_elapsed,
                                )
                                return
                            _L.info(
                                "scheduler reply batch send complete | group={} segments={} "
                                "send_total={:.1f}s first_release={}",
                                group_id, sent_segments, send_total_elapsed,
                                self._first_segment_release,
                            )
                            self._timeline.mark_assistant_visible_state(
                                group_id, reply.assistant_turn_id, "complete"
                            )
                        else:
                            _L.info(
                                "scheduler reply send complete | group={} segments={} send_total={:.1f}s",
                                group_id, sent_segments, send_total_elapsed,
                            )
                            self._timeline.mark_latest_assistant_visible_state(group_id, "complete")
                    elif sent_segments == 0:
                        if slot and slot.pending_at:
                            self._timeline.deactivate_pending_except_latest_active(group_id, "no_visible_reply")
                        else:
                            self._timeline.deactivate_pending(group_id, "no_visible_reply")
                    else:
                        self._timeline.mark_latest_assistant_visible_state(group_id, "complete")
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
                if slot.pending_at:
                    next_trigger = slot.pop_trigger()
                    _L.info(
                        "scheduler | group={} queued trigger -> fire (remaining={})",
                        group_id, slot.pending_trigger_count,
                    )
                    self._fire(group_id, trigger=next_trigger)
