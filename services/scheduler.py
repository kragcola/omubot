"""群聊统一调度器：debounce/batch/@ 触发模型调用，统一队列。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from kernel.config import GroupConfig
from kernel.types import TriggerContext
from services.humanization import CLOCK_CURRENT_SLOT, REGISTER_LABEL_SLOT
from services.llm.client import RATE_LIMIT_BASE_DELAY, RATE_LIMIT_MAX_RETRIES, RateLimitError
from services.memory.timeline import GroupTimeline
from services.runtime_clock import now_cst
from services.system_module import Scope
from services.tools.context import ToolContext

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot

    from services.identity import IdentityManager
    from services.llm.client import LLMClient

_L = logger.bind(channel="scheduler")


def _should_force_reply(trigger: TriggerContext | None) -> bool:
    if trigger is None:
        return False
    if trigger.mode in {"video_always", "directed_followup"}:
        return True
    if trigger.mode != "at_mention":
        return False
    return bool(trigger.extra.get("addressee_self", True))


class _GroupSlot:
    __slots__ = (
        "consecutive_skip", "debounce_task", "last_fire_time",
        "last_user_id", "msg_count", "pending_at", "running_task", "trigger",
    )

    def __init__(self) -> None:
        self.consecutive_skip: int = 0
        self.debounce_task: asyncio.Task[None] | None = None
        self.running_task: asyncio.Task[None] | None = None
        self.msg_count: int = 0
        self.pending_at: bool = False
        self.last_fire_time: float = 0.0
        self.last_user_id: str = ""
        self.trigger: TriggerContext | None = None


class GroupChatScheduler:
    """群聊统一调度器：debounce/batch/@触发模型调用。"""

    def __init__(
        self,
        llm: LLMClient,
        timeline: GroupTimeline,
        identity_mgr: IdentityManager,
        group_config: GroupConfig,
        humanizer: Any = None,
        mood_getter: Callable[..., Any] | None = None,
        talk_schedule: Any = None,
        runtime_state: Any = None,
    ) -> None:
        self._llm = llm
        self._timeline = timeline
        self._identity_mgr = identity_mgr
        self._group_config = group_config
        self._humanizer = humanizer
        self._mood_getter = mood_getter
        self._talk_schedule = talk_schedule
        self._runtime_state = runtime_state
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

    def get_all_slots(self) -> dict[str, dict[str, object]]:
        """Return public state for all group slots (admin API)."""
        result: dict[str, dict[str, object]] = {}
        for gid, slot in self._slots.items():
            result[gid] = {
                "consecutive_skip": slot.consecutive_skip,
                "msg_count": slot.msg_count,
                "pending_at": slot.pending_at,
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

    def notify(self, group_id: str, *, trigger: TriggerContext | None = None,
               user_id: str = "") -> None:
        """Called on every group message. Manages probability-based dispatch."""
        if group_id in self._muted_groups:
            return
        identity = self._identity_mgr.resolve()
        is_at = trigger is not None and trigger.mode == "at_mention"
        is_video_always = trigger is not None and trigger.mode == "video_always"
        is_directed_followup = trigger is not None and trigger.mode == "directed_followup"
        if identity.proactive is None and not is_at and not is_video_always and not is_directed_followup:
            # Skip non-@ messages when there are no proactive interjection rules.
            # @ mentions, directed followups, and video always-reply bypass this check.
            _L.info("scheduler | group={} proactive=None, skip (non-@)", group_id)
            return

        resolved = self._group_config.resolve(int(group_id))

        slot = self._slots.setdefault(group_id, _GroupSlot())
        slot.msg_count += 1
        if user_id:
            slot.last_user_id = user_id
        if trigger is not None:
            slot.trigger = trigger  # always update so latest trigger takes priority

        if is_at:
            if slot.running_task and not slot.running_task.done():
                slot.pending_at = True
                _L.debug("scheduler | group={} @ queued (task running)", group_id)
                return
            _L.info("scheduler | group={} @ -> fire", group_id)
            self._fire(group_id)
            return

        if is_directed_followup:
            if slot.running_task and not slot.running_task.done():
                slot.pending_at = True
                _L.debug("scheduler | group={} directed_followup queued (task running)", group_id)
                return
            _L.info("scheduler | group={} directed_followup -> fire", group_id)
            self._fire(group_id)
            return

        # Video always-reply: force fire for B站 video shares
        if is_video_always:
            if slot.running_task and not slot.running_task.done():
                slot.pending_at = True
                _L.debug("scheduler | group={} bilibili always queued (task running)", group_id)
                return
            _L.info("scheduler | group={} bilibili always -> fire", group_id)
            self._fire(group_id)
            return

        # at_only mode: only respond to @ messages
        if resolved.at_only:
            _L.info("scheduler | group={} at_only, skip (msgs={})", group_id, slot.msg_count)
            slot.trigger = None  # clear trigger on skip — prevent leak
            return

        if slot.running_task and not slot.running_task.done():
            _L.info("scheduler | group={} busy, skip (msgs={})", group_id, slot.msg_count)
            return

        # Probability-based dispatch with minimum interval (replaces debounce).
        now = time.monotonic()
        if now - slot.last_fire_time < resolved.planner_smooth:
            _L.info("scheduler | group={} interval too short, skip (msgs={})", group_id, slot.msg_count)
            slot.trigger = None  # clear trigger on skip — prevent leak
            return

        # Dynamic threshold: become more responsive after prolonged silence.
        # Use bilibili-specific talk_value when a video trigger is present.
        is_video_prob = trigger is not None and trigger.mode in ("video_dedicated", "video_autonomous")
        if is_video_prob:
            trigger_extra = trigger.extra if trigger is not None else {}
            base_talk_value = float(trigger_extra.get("bilibili_talk_value", resolved.talk_value))
        else:
            trigger_extra = {}
            base_talk_value = resolved.talk_value

        threshold = base_talk_value
        if slot.consecutive_skip >= resolved.consecutive_skip_force_threshold:
            threshold = 1.0
        elif slot.consecutive_skip >= resolved.consecutive_skip_double_threshold:
            threshold = min(1.0, base_talk_value * 2)

        # Autonomous mode: apply interest score as a floor booster.
        # High-interest videos (e.g. pjsk, 25时) should fire reliably even
        # during low-activity time slots — the bot cares about the content.
        # Skip the interest floor once the force threshold is reached.
        is_autonomous = trigger is not None and trigger.mode == "video_autonomous"
        if is_autonomous and slot.consecutive_skip < resolved.consecutive_skip_force_threshold:
            interest_score = float(trigger_extra.get("interest_score", 0.3))
            # Blend so interest acts as a floor: 0.1 + 0.9×interest maps
            # 0.05→0.145, 0.55→0.595, 0.85→0.865, 1.0→1.0
            threshold = base_talk_value * (0.1 + 0.9 * interest_score)

        # Mood-adjusted probability — good mood boosts, bad mood suppresses.
        mood_mult = self._get_mood_multiplier(group_id)
        # Time-slot multiplier — capped at 0.7 globally.
        # High-interest videos (score >= 0.6) get a floor of 0.7 so the
        # bot can still reply during off hours, but never exceed the cap.
        time_mult = self._talk_schedule.get_time_multiplier() if self._talk_schedule else 1.0
        if is_autonomous:
            interest_score = float(trigger_extra.get("interest_score", 0.3))
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
            self._fire(group_id)
        else:
            slot.consecutive_skip += 1
            _L.info(
                "scheduler | group={} prob skip (threshold={:.2f} mood={:.2f} time={:.2f} msgs={} skips={} mode={})",
                group_id, threshold, mood_mult, time_mult, slot.msg_count, slot.consecutive_skip, mode_label,
            )
            slot.trigger = None  # clear trigger on skip — prevent leak

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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_mood_multiplier(self, group_id: str) -> float:
        """Compute a mood multiplier for talk_value.

        Good mood (high energy, positive valence, high openness) → >1.0 boost.
        Bad mood (low energy, negative valence, low openness) → <1.0 suppression.
        Range: [0.25, 2.0]. Returns 1.0 when no mood_getter is configured.
        """
        profile = self._get_current_mood(group_id)
        if profile is None:
            return 1.0
        happy = (getattr(profile, "valence", 0.0) + 1.0) / 2.0
        mood_factor = (
            0.4 * getattr(profile, "openness", 0.5)
            + 0.3 * getattr(profile, "energy", 0.5)
            + 0.3 * happy
        )
        return 0.25 + 1.75 * mood_factor

    def _get_current_mood(self, group_id: str) -> Any:
        if self._mood_getter is None:
            return None
        try:
            return self._mood_getter(group_id=group_id, session_id=f"group_{group_id}")
        except TypeError:
            return self._mood_getter()

    def _runtime_scope(self, group_id: str, *, turn_id: str = "") -> Scope:
        slot = self._slots.get(group_id)
        return Scope(
            session_id=f"group_{group_id}",
            group_id=group_id,
            user_id=slot.last_user_id if slot else "",
            turn_id=turn_id,
        )

    def _runtime_state_value(self, slot_id: str, scope: Scope) -> Any:
        if self._runtime_state is None:
            return None
        try:
            snapshot = self._runtime_state.get(slot_id, scope=scope)
        except Exception as exc:
            _L.debug("scheduler runtime_state read failed | slot={} err={}", slot_id, exc)
            return None
        return snapshot.value if snapshot is not None else None

    def _current_register(self, group_id: str) -> Any:
        return self._runtime_state_value(
            REGISTER_LABEL_SLOT,
            self._runtime_scope(group_id),
        )

    def _current_slot_payload(self, group_id: str) -> Any:
        value = self._runtime_state_value(
            CLOCK_CURRENT_SLOT,
            self._runtime_scope(group_id),
        )
        if value is not None:
            return value
        current_slot = getattr(self._talk_schedule, "current_slot", None)
        if not callable(current_slot):
            return None
        try:
            slot = current_slot(now_cst())
        except Exception as exc:
            _L.debug("scheduler talk_schedule slot read failed | group={} err={}", group_id, exc)
            return None
        if slot is None:
            return None
        return {
            "slot_time": str(getattr(slot, "time", "") or ""),
            "slot_activity": str(getattr(slot, "activity", "") or ""),
            "slot_mood_hint": str(getattr(slot, "mood_hint", "") or ""),
            "energy": getattr(slot, "energy", 1.0),
        }

    def _humanizer_runtime(self, group_id: str) -> dict[str, Any]:
        return {
            "group_id": group_id,
            "register": self._current_register(group_id),
            "slot": self._current_slot_payload(group_id),
            "mood": self._get_current_mood(group_id),
        }

    def _fire(self, group_id: str) -> None:
        slot = self._slots.get(group_id)
        if not slot:
            return
        # Snapshot and clear trigger so it's consumed exactly once.
        trigger = slot.trigger
        slot.trigger = None
        slot.msg_count = 0
        slot.running_task = asyncio.create_task(self._do_chat(group_id, trigger=trigger))
        slot.running_task.add_done_callback(lambda _: None)

    async def _send_to_group(self, group_id: str, text: str, *, humanize: str = "normal") -> float:
        """Send a text message to a group with retry on failure."""
        if not self._bot:
            return 0.0
        from nonebot.adapters.onebot.v11 import Message
        from nonebot.adapters.onebot.v11.exception import ActionFailed

        # Detect [CQ:reply,id=X] prefix for quote-reply targeting.
        # OneBot v11 Message handles CQ codes natively — we just log it.
        if text.startswith("[CQ:reply,id="):
            import re
            if m := re.match(r"\[CQ:reply,id=(-?\d+)\]", text):
                _L.info("scheduler | group={} reply targets msg_id={}", group_id, m.group(1))

        delay = 2.0
        max_delay = 60.0
        while True:
            if group_id in self._muted_groups:
                _L.warning("scheduler | group={} muted, dropping message", group_id)
                return 0.0
            try:
                t_send = time.monotonic()
                if self._humanizer is not None and humanize != "skip":
                    await self._humanizer.delay(text, **self._humanizer_runtime(group_id))
                await self._bot.send_group_msg(group_id=int(group_id), message=Message(text))
                elapsed = time.monotonic() - t_send
                if elapsed >= 8.0:
                    _L.warning(
                        "scheduler send slow | group={} humanize={} len={} elapsed={:.1f}s",
                        group_id, humanize, len(text), elapsed,
                    )
                else:
                    _L.debug(
                        "scheduler send ok | group={} humanize={} len={} elapsed={:.1f}s",
                        group_id, humanize, len(text), elapsed,
                    )
                return elapsed
            except ActionFailed as e:
                _L.warning(
                    "scheduler | group={} send failed: {} | retry in {}s",
                    group_id, e.info.get("wording") or e.info.get("message", str(e)), delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

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
                        session_id=session_id,
                    )

                    # Write trigger reason into the timeline so the LLM sees it
                    # in the pending buffer, not as transient user_content.
                    if trigger is not None:
                        trace_request_id = str(trigger.extra.get("u13_double_haiku_request_id", "") or "")
                        if trace_request_id:
                            ctx.extra["u13_double_haiku_request_id"] = trace_request_id
                        self._timeline.add_pending_trigger(
                            group_id, reason=trigger.reason,
                            message_id=trigger.target_message_id,
                            target_user_id=trigger.target_user_id,
                        )

                    force_reply = _should_force_reply(trigger)

                    # @mention: prepend [CQ:reply] to the first streamed segment only.
                    # Quote-reply already identifies the target — no need for [CQ:at].
                    first_segment = True
                    sent_segments = 0
                    send_total_elapsed = 0.0
                    reply_prefix = ""
                    if trigger is not None and trigger.mode == "at_mention" and trigger.target_message_id is not None:
                        reply_prefix = f"[CQ:reply,id={trigger.target_message_id}]"
                        _L.info("scheduler | group={} @mention prefix={}", group_id, reply_prefix)

                    async def on_segment(text: str, _prefix: str = reply_prefix) -> None:
                        nonlocal first_segment, sent_segments, send_total_elapsed
                        is_first = first_segment
                        if first_segment:
                            if _prefix:
                                text = _prefix + text
                            first_segment = False
                        send_total_elapsed += await self._send_to_group(
                            group_id,
                            text,
                            humanize="skip" if is_first else "normal",
                        )
                        sent_segments += 1

                    resolved = self._group_config.resolve(int(group_id))
                    reply = await self._llm.chat(
                        session_id=session_id,
                        user_id=uid,
                        user_content="",
                        identity=identity,
                        group_id=group_id,
                        ctx=ctx,
                        on_segment=on_segment if self._bot else None,
                        privacy_mask=resolved.privacy_mask,
                        force_reply=force_reply,
                    )

                    if reply:
                        # Non-streaming fallback: prepend [CQ:reply] if on_segment was never called
                        is_first = first_segment
                        if first_segment and reply_prefix:
                            reply = reply_prefix + reply
                            first_segment = False
                        send_total_elapsed += await self._send_to_group(
                            group_id,
                            reply,
                            humanize="skip" if is_first else "normal",
                        )
                        sent_segments += 1
                        _L.info(
                            "scheduler reply send complete | group={} segments={} send_total={:.1f}s",
                            group_id, sent_segments, send_total_elapsed,
                        )
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
