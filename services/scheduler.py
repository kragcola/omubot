"""群聊统一调度器：debounce/batch/@ 触发模型调用，统一队列。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import time
import time as _time_mod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from loguru import logger

from kernel.config import GroupConfig
from kernel.types import ResponseClass, TriggerContext
from services.group.topic_block import TopicBlockTracker
from services.humanization import CLOCK_CURRENT_SLOT, REGISTER_LABEL_SLOT
from services.llm.arbiter import ArbiterClient, InterruptionResult, PendingMessage
from services.llm.client import RATE_LIMIT_BASE_DELAY, RATE_LIMIT_MAX_RETRIES, RateLimitError
from services.memory.timeline import GroupTimeline
from services.runtime_clock import now_cst
from services.scheduler_eot import EOTCache, EOTClassifier
from services.scheduler_hawkes import HawkesCache, estimate_rho_from_times
from services.scheduler_rws import DEFAULT_RWS_WEIGHTS, RWSBandit, RWSExplanation, RWSFeatures, compute_rws
from services.scheduler_rws.reward import PendingDecision, ReactionSignals, RWSRewardQueue
from services.scheduler_rws.rws import dual_decision
from services.system_module import Scope
from services.tools.context import ToolContext

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot

    from services.llm.client import LLMClient
    from services.persona import PersonaRuntime

_L = logger.bind(channel="scheduler")
_CHAT_LOCK_LLM_TIMEOUT_S = 120.0
# Arbiter B (interruption) timeout layering. MUST stay above the inner arbiter
# LLM timeout (arbiter.timeout_ms, config.json) which itself must cover the
# deepseek-flash p90. Measured 2026-06-10: deepseek-v4-flash arbiter payload
# p50≈1.45s / p90≈1.95s. With the old 0.8s the outer monitor wait_for cancelled
# the live inner call on EVERY message → 3 consecutive timeouts → circuit open →
# Arbiter B never once succeeded (0 arbiter_b_abort in all history). The inner
# LLM timeout is now 2500ms (config.json); this outer wrap must be ≥ that so the
# inner call's own timeout/fallback fires first instead of being cancelled here.
_MONITOR_JUDGE_TIMEOUT_S: float = 3.0
# Separate budget: how long a single segment stalls waiting for an in-flight
# verdict between emissions. Kept short so a healthy reply doesn't visibly lag
# per segment — if the verdict isn't ready we fail-open and emit. This is NOT the
# judge call timeout (that's _MONITOR_JUDGE_TIMEOUT_S); the monitor keeps polling
# in the background and a late abort still lands on a later segment.
_GATE_TIMEOUT_S: float = 0.8
_MAX_ABORTS_PER_FIRE: int = 2
_CB_THRESHOLD: int = 3
_CB_HALF_OPEN_S: float = 30.0
# Weak-reply (closing) P0 — anti-spam knobs.
_LIGHT_COOLDOWN_S: float = 30.0  # min gap between two light replies in one group
_CLOSING_RESET_S: float = 1800.0  # after this quiet gap, a new closing is allowed again
_RATIFIED_FLOOR_COOLDOWN_S: float = 15.0  # min gap between bot's last fire and next ratified-floor fire


@dataclass(slots=True)
class _MuteRecord:
    source: str
    since_unix: float
    until_unix: float | None = None


def _action_failed_retcode(error: Exception) -> int | None:
    retcode = getattr(error, "retcode", None)
    if isinstance(retcode, int):
        return retcode
    payload = _action_failed_payload(error)
    if isinstance(payload, dict):
        for key in ("retcode", "code", "status"):
            raw = payload.get(key)
            if not isinstance(raw, (int, str)):
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
    info = getattr(error, "info", None)
    if isinstance(info, dict):
        for key in ("retcode", "code", "status"):
            raw = info.get(key)
            if not isinstance(raw, (int, str)):
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
    return None


def _action_failed_payload(error: Exception) -> dict[str, Any]:
    info = getattr(error, "info", None)
    if not isinstance(info, dict):
        return {}
    nested = info.get("info")
    if isinstance(nested, dict):
        return nested
    return info


def _should_force_reply(trigger: TriggerContext | None) -> bool:
    if trigger is None:
        return False
    obligation = getattr(trigger, "obligation", None)
    if obligation is not None and getattr(obligation, "level", "") == "must":
        return True
    if trigger.mode in {"video_always", "directed_followup", "qq_interaction", "correction"}:
        return True
    # A deferred addressed-wait re-fire must reply (its quiet window elapsed).
    if bool(trigger.extra.get("force_after_wait", False)):
        return True
    if trigger.mode != "at_mention":
        return False
    return bool(trigger.extra.get("addressee_self", True))


class _GroupSlot:
    __slots__ = (
        "arbiter_task",
        "block_fire_queue",
        "burst_pending",
        "chat_lock",
        "closing_done",
        "consecutive_skip",
        "debounce_task",
        "firing_block_id",
        "firing_user_id",
        "first_segment_sent",
        "last_fire_time",
        "last_light_time",
        "last_reply_content",
        "last_reply_time",
        "last_response_class",
        "last_role",
        "last_rws",
        "last_skip_time",
        "last_user_id",
        "msg_count",
        "pending_during_generation",
        "running_task",
        "trigger",
        "wait_defer_task",
        "wait_deferrals",
    )

    def __init__(self) -> None:
        self.consecutive_skip: int = 0
        self.burst_pending: list[PendingMessage] = []
        self.arbiter_task: asyncio.Task[None] | None = None
        self.debounce_task: asyncio.Task[None] | None = None
        self.running_task: asyncio.Task[None] | None = None
        self.msg_count: int = 0
        self.pending_during_generation: list[PendingMessage] = []
        self.last_fire_time: float = 0.0
        self.last_reply_time: float = 0.0
        self.last_reply_content: str = ""
        self.last_skip_time: float = 0.0
        self.last_user_id: str = ""
        self.last_response_class: str = ResponseClass.SILENCE.value
        # Unified receiver role (Goffman) decided at fire time, read by chat()'s
        # necessity gate so "要不要说话" is judged with ONE definition of被寻址.
        self.last_role: str = "addressed"
        self.last_rws: dict[str, Any] | None = None
        self.trigger: TriggerContext | None = None
        # Path Y (multi-addressee): when a burst spans multiple topic blocks,
        # fire serially. `block_fire_queue` holds per-block merged
        # TriggerContexts; the _do_chat finally-block drains it with NO
        # inter-block gap. burst_pending is the source of truth for which @
        # messages exist (each PendingMessage carries its own block_id /
        # target_message_id), so we do NOT keep a parallel trigger list that
        # could desync with the pending_during_generation path.
        self.block_fire_queue: list[TriggerContext] = []
        self.chat_lock = asyncio.Lock()
        # Weak-reply (closing) state — P0.
        self.last_light_time: float = 0.0  # cooldown across light replies
        self.closing_done: bool = False  # terminal exchange completed; dedup repeats
        # Addressed-wait deferral: an @ turn whose thinker chose wait re-fires
        # after a quiet window so the @ obligation isn't dropped (bounded).
        self.wait_defer_task: asyncio.Task[None] | None = None
        self.wait_deferrals: int = 0
        # Deterministic cancel-and-remerge (interrupt-merge): identity of the
        # in-flight fire. When a same-block / same-user @ arrives mid-generation
        # AND the first visible segment has NOT been sent yet, we cancel the
        # running fire so the finally-block re-merges pending_during_generation
        # into burst_pending and Arbiter-A fires ONE unified reply. Once the
        # first segment is out ("已发段不撤回"), we no longer cancel — the
        # post-emission case falls to Arbiter-B (abort unsent segments only).
        self.firing_block_id: str = ""
        self.firing_user_id: str = ""
        self.first_segment_sent: bool = False


class _EmissionGate:
    """Segment emission gate with deadline, budget, and circuit-breaker protection."""

    __slots__ = (
        "_abort_count",
        "_circuit_open_until",
        "_consecutive_timeouts",
        "_event",
        "_segment_index",
        "_state",
        "_verdict",
    )

    def __init__(self) -> None:
        self._state: Literal["open", "pending", "abort"] = "open"
        self._event = asyncio.Event()
        self._event.set()
        self._verdict: InterruptionResult | None = None
        self._abort_count = 0
        self._consecutive_timeouts = 0
        self._circuit_open_until = 0.0
        self._segment_index = 0

    def arm(self) -> None:
        if self._state == "open":
            self._state = "pending"
            self._event.clear()

    def resolve(self, verdict: InterruptionResult, *, timed_out: bool = False) -> None:
        if timed_out:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= _CB_THRESHOLD:
                self._circuit_open_until = _time_mod.monotonic() + _CB_HALF_OPEN_S
                _L.warning("arbiter_b_circuit_open | will retry after {}s", _CB_HALF_OPEN_S)
            self._state = "open"
            self._verdict = None
            self._event.set()
            return
        self._consecutive_timeouts = 0
        self._verdict = verdict
        if verdict.action == "continue":
            self._state = "open"
            self._event.set()
            return
        self._abort_count += 1
        if self._abort_count > _MAX_ABORTS_PER_FIRE:
            self._state = "open"
            _L.warning("arbiter_b_budget_exhausted | forcing emit")
        else:
            self._state = "abort"
        self._event.set()

    @property
    def circuit_open(self) -> bool:
        return _time_mod.monotonic() < self._circuit_open_until

    async def check(self) -> bool:
        self._segment_index += 1
        if self._segment_index == 1:
            return True
        if self.circuit_open or self._state == "open":
            return True
        if self._state == "abort":
            return False
        try:
            await asyncio.wait_for(self._event.wait(), timeout=_GATE_TIMEOUT_S)
        except TimeoutError:
            # Verdict not ready for THIS segment within the short stall budget.
            # Fail-open (emit) — but do NOT count this toward the circuit breaker:
            # the judge call legitimately takes ~1.5-2s (deepseek-flash p50≈1.45s),
            # longer than this per-segment stall, so a not-ready verdict is the
            # normal case, not an arbiter failure. The monitor keeps the call
            # in-flight and a late abort lands on a later segment. Only genuine
            # judge-call timeouts/errors (monitor side, resolve(timed_out=True))
            # trip the breaker.
            self._state = "open"
            _L.debug("gate_check_verdict_not_ready | emitting, monitor still polling")
            return True
        return self._state != "abort"

    @property
    def verdict(self) -> InterruptionResult | None:
        return self._verdict

    @property
    def abort_count(self) -> int:
        return self._abort_count


class GroupChatScheduler:
    """群聊统一调度器：debounce/batch/@触发模型调用。"""

    def __init__(
        self,
        llm: LLMClient,
        timeline: GroupTimeline,
        persona_runtime: PersonaRuntime,
        group_config: GroupConfig,
        arbiter_config: Any | None = None,
        humanizer: Any = None,
        mood_getter: Callable[..., Any] | None = None,
        talk_schedule: Any = None,
        runtime_state: Any = None,
        humanization_config: Any = None,
        memory_signal_getter: Callable[[str, str], dict[str, Any] | None] | None = None,
        hawkes_cache: HawkesCache | None = None,
        eot_cache: EOTCache | None = None,
        eot_classifier: EOTClassifier | None = None,
        rws_bandit: RWSBandit | None = None,
        bot_pair_guard: Any = None,
        block_trace_store: Any = None,
        self_mute_config: Any = None,
        group_inventory_getter: Callable[[], dict[str, Any] | None] | None = None,
        topic_block_config: Any = None,
        thinker_config: Any = None,
    ) -> None:
        self._llm = llm
        self._timeline = timeline
        self._persona_runtime = persona_runtime
        self._group_config = group_config
        self._humanizer = humanizer
        self._mood_getter = mood_getter
        self._talk_schedule = talk_schedule
        self._runtime_state = runtime_state
        self._humanization_config = humanization_config
        self._memory_signal_getter = memory_signal_getter
        self._hawkes_cache = hawkes_cache if self._hflag_global("rws_hawkes") else None
        if self._hflag_global("rws_hawkes") and self._hawkes_cache is None:
            self._hawkes_cache = HawkesCache()
        self._eot_cache = eot_cache if self._hflag_global("rws_eot") else None
        self._eot_classifier = eot_classifier if self._hflag_global("rws_eot") else None
        if self._hflag_global("rws_eot"):
            self._eot_cache = self._eot_cache or EOTCache()
            self._eot_classifier = self._eot_classifier or EOTClassifier()
        self._rws_bandit = rws_bandit
        if self._hflag_global("rws_bandit") and self._rws_bandit is None:
            self._rws_bandit = RWSBandit(
                theta=self._hfloat_global("rws_threshold", 0.5),
                frozen=self._hflag_global("rws_bandit_freeze", default=True),
                algo=self._hstr_global("rws_bandit_algo", "thompson"),
                min_obs=self._hint_global("rws_bandit_min_obs", 50),
                decay_per_obs=self._hfloat_global("rws_bandit_decay_per_obs", 0.99),
            )
        # P1: RWS reward loop — pending-settlement queue + background settle loop.
        self._rws_reward_enabled = self._hflag_global("rws_reward")
        self._rws_reward_queue: RWSRewardQueue | None = (
            RWSRewardQueue(window_s=self._hfloat_global("rws_reward_window_s", 300.0))
            if self._rws_reward_enabled
            else None
        )
        self._rws_reward_task: asyncio.Task[None] | None = None
        self._slots: dict[str, _GroupSlot] = {}
        self._bot: Bot | None = None
        self._muted_groups: set[str] = set()
        self._mute_records: dict[str, _MuteRecord] = {}
        self._bot_pair_guard = bot_pair_guard
        self._block_trace_store = block_trace_store
        self._self_mute_config = self_mute_config
        self._group_inventory_getter = group_inventory_getter
        self._reconcile_task: asyncio.Task[None] | None = None
        self._self_id: str = ""
        self._arbiter_config = arbiter_config
        self._arbiter: ArbiterClient | None = None
        # B1 topic-block attribution (parallel-topic understanding).
        self._topic_block_config = topic_block_config
        self._thinker_config = thinker_config
        self._topic_tracker: TopicBlockTracker | None = None
        if bool(getattr(topic_block_config, "enabled", False)):
            self._topic_tracker = TopicBlockTracker()
            self._topic_tracker.configure(
                stale_seconds=getattr(topic_block_config, "stale_seconds", None),
                attrib_recent_seconds=getattr(topic_block_config, "attrib_recent_seconds", None),
                sim_threshold=getattr(topic_block_config, "sim_threshold", None),
                max_blocks=getattr(topic_block_config, "max_blocks", None),
            )

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot
        self._self_id = str(getattr(bot, "self_id", "") or "")
        if self._bot_pair_guard is not None:
            with contextlib.suppress(Exception):
                self._bot_pair_guard.bind_self_id(str(getattr(bot, "self_id", "") or ""))
        if bool(getattr(self._self_mute_config, "reconcile_enabled", False)):
            task = self._reconcile_task
            if task is None or task.done():
                self._reconcile_task = asyncio.create_task(self._reconcile_self_mute_loop())
                self._reconcile_task.add_done_callback(lambda _: None)
        if self._rws_reward_queue is not None:
            task = self._rws_reward_task
            if task is None or task.done():
                self._rws_reward_task = asyncio.create_task(self._rws_reward_loop())
                self._rws_reward_task.add_done_callback(lambda _: None)

    # ------------------------------------------------------------------
    # Mute management
    # ------------------------------------------------------------------

    def mute(
        self,
        group_id: str,
        *,
        source: str = "manual",
        since_unix: float | None = None,
        until_unix: float | None = None,
    ) -> None:
        """Mark group as muted — cancel running task, block future fires."""
        self._muted_groups.add(group_id)
        existing = self._mute_records.get(group_id)
        record_until = (
            until_unix
            if until_unix is not None
            else (existing.until_unix if existing is not None else None)
        )
        self._mute_records[group_id] = _MuteRecord(
            source=source,
            since_unix=existing.since_unix if existing is not None else (since_unix or time.time()),
            until_unix=record_until,
        )
        slot = self._slots.get(group_id)
        if slot:
            if slot.running_task and not slot.running_task.done():
                slot.running_task.cancel()
            slot.running_task = None
            slot.msg_count = 0
            slot.pending_during_generation = []
            slot.burst_pending = []
        _L.info("scheduler | group={} muted source={} tasks cancelled", group_id, source)

    def unmute(self, group_id: str) -> None:
        """Unmark group as muted — resume normal scheduling."""
        self._muted_groups.discard(group_id)
        self._mute_records.pop(group_id, None)
        _L.info("scheduler | group={} unmuted", group_id)

    def is_muted(self, group_id: str) -> bool:
        return group_id in self._muted_groups

    def get_mute_state(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for group_id in sorted(self._muted_groups):
            record = self._mute_records.get(group_id)
            result[group_id] = {
                "muted": True,
                "source": record.source if record is not None else "unknown",
                "since_unix": record.since_unix if record is not None else None,
                "until_unix": record.until_unix if record is not None else None,
            }
        return result

    def get_rws_bandit_state(self) -> dict[str, object]:
        if self._rws_bandit is None:
            return {"available": False}
        return {
            "available": True,
            "theta": self._rws_bandit.theta,
            "epsilon": self._rws_bandit.epsilon,
            "learning_rate": self._rws_bandit.learning_rate,
            "min_theta": self._rws_bandit.min_theta,
            "max_theta": self._rws_bandit.max_theta,
            "frozen": self._rws_bandit.frozen,
            "observations": self._rws_bandit.observations,
            "last_reward": self._rws_bandit.last_reward,
            "history": list(self._rws_bandit.history),
        }

    def observe_rws_bandit(self, *, decision: bool, reward: float) -> dict[str, object]:
        if self._rws_bandit is None:
            return {"ok": False, "error": "RWS bandit not configured"}
        theta = self._rws_bandit.observe(decision=decision, reward=reward)
        return {"ok": True, "theta": theta, "state": self.get_rws_bandit_state()}

    async def get_rws_reward_summary(self, *, limit: int = 200) -> dict[str, object]:
        """P6 admin readout: RWS activation flags + reward-loop health.

        Reads the ``rws_reward`` runtime metrics (persisted by the settle loop)
        and summarises recent reward, so the console can see whether the loop is
        actually closing — without standing up a new store/table."""
        flags = {
            "shadow": self._hflag_global("rws_shadow"),
            "primary": self._hflag_global("rws_primary"),
            "reward": self._hflag_global("rws_reward"),
            "eot": self._hflag_global("rws_eot"),
            "hawkes": self._hflag_global("rws_hawkes"),
            "bandit": self._hflag_global("rws_bandit"),
            "bandit_freeze": self._hflag_global("rws_bandit_freeze", default=True),
            "dual_threshold": self._hflag_global("rws_dual_threshold"),
        }
        pending = self._rws_reward_queue.pending_count() if self._rws_reward_queue is not None else 0
        rewards: list[dict[str, Any]] = []
        store = self._block_trace_store
        if store is not None and hasattr(store, "list_runtime_metrics"):
            with contextlib.suppress(Exception):
                rewards = await store.list_runtime_metrics(metric_key="rws_reward", limit=max(1, int(limit)))
        values: list[float] = []
        fired = skipped = 0
        for row in rewards:
            meta = row.get("metadata") if isinstance(row, dict) else None
            if not isinstance(meta, dict):
                continue
            with contextlib.suppress(TypeError, ValueError):
                values.append(float(meta.get("reward", 0.0)))
            if bool(meta.get("decision")):
                fired += 1
            else:
                skipped += 1
        settled = len(values)
        return {
            "available": self._rws_bandit is not None or flags["reward"] or flags["primary"],
            "flags": flags,
            "bandit": self.get_rws_bandit_state(),
            "reward": {
                "enabled": self._rws_reward_enabled,
                "window_s": self._hfloat_global("rws_reward_window_s", 300.0),
                "pending": pending,
                "settled": settled,
                "fired": fired,
                "skipped": skipped,
                "avg_reward": round(sum(values) / settled, 4) if settled else None,
                "positive_rate": round(sum(1 for v in values if v > 0) / settled, 4) if settled else None,
                "recent": rewards[:50],
            },
        }

    def get_all_slots(self) -> dict[str, dict[str, object]]:
        """Return public state for all group slots (admin API)."""
        result: dict[str, dict[str, object]] = {}
        for gid, slot in self._slots.items():
            result[gid] = {
                "consecutive_skip": slot.consecutive_skip,
                "msg_count": slot.msg_count,
                "pending_during_generation": len(slot.pending_during_generation),
                "last_fire_time": slot.last_fire_time,
                "last_skip_time": slot.last_skip_time,
                "last_user_id": slot.last_user_id,
                "last_response_class": slot.last_response_class,
                "last_rws": slot.last_rws,
                "has_trigger": slot.trigger is not None,
                "is_muted": gid in self._muted_groups,
                "has_running_task": slot.running_task is not None and not slot.running_task.done(),
                "burst_pending_count": len(slot.burst_pending),
                "has_arbiter_task": slot.arbiter_task is not None and not slot.arbiter_task.done(),
            }
        return result

    def set_arbiter(self, arbiter: ArbiterClient | None) -> None:
        self._arbiter = arbiter

    def get_slot(self, group_id: str) -> Any | None:
        return self._slots.get(group_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify(
        self,
        group_id: str,
        *,
        trigger: TriggerContext | None = None,
        user_id: str = "",
        message_text: str = "",
        message_id: int | None = None,
        reply_to_sender_id: str = "",
        reply_to_self: bool = False,
        at_targets: tuple[str, ...] = (),
        at_self: bool = False,
        is_addressed: bool = False,
    ) -> None:
        """Called on every group message. Manages probability-based dispatch."""
        if group_id in self._muted_groups:
            return
        # B1: feed every observed group message into topic-block attribution
        # before any gating, so the block map reflects the full stream.
        observed_block = None
        if self._topic_tracker is not None and message_text:
            try:
                observed_block = self._topic_tracker.observe(
                    group_id,
                    message_id=message_id,
                    speaker=user_id,
                    text=message_text,
                    reply_to_sender_id=reply_to_sender_id,
                    reply_to_self=reply_to_self,
                    at_targets=at_targets,
                    at_self=at_self,
                )
            except Exception as exc:
                _L.debug("topic_block observe failed | group={} err={}", group_id, exc)
        identity = self._persona_runtime.identity_snapshot()
        is_at = trigger is not None and trigger.mode == "at_mention"
        is_video_always = trigger is not None and trigger.mode == "video_always"
        is_directed_followup = trigger is not None and trigger.mode == "directed_followup"
        is_correction = trigger is not None and trigger.mode == "correction"
        is_closing = trigger is not None and trigger.mode == "closing"
        is_greeting = trigger is not None and trigger.mode == "greeting"
        if (
            identity.proactive is None
            and not is_at
            and not is_video_always
            and not is_directed_followup
            and not is_correction
            and not is_closing
            and not is_greeting
        ):
            # Skip non-@ messages when there are no proactive interjection rules.
            # @ mentions, directed followups, correction turns, closing farewells,
            # and video always-reply bypass this check.
            _L.info("scheduler | group={} proactive=None, skip (non-@)", group_id)
            return

        resolved = self._group_config.resolve(int(group_id))

        slot = self._slots.setdefault(group_id, _GroupSlot())
        # msg_count is incremented only when the message reaches the probability /
        # gray-zone scoring path below.  Rule-layer fire/skip paths (@, closing,
        # followup, correction, video_always, at_only, busy, interval) either fire
        # immediately (→ msg_count reset in _fire) or skip definitively — neither
        # wants to bump the counter that the _do_chat finally block uses to re-fire.
        slot.last_role = "addressed"
        if user_id:
            slot.last_user_id = user_id
        if trigger is not None:
            # Scalar shim: the non-arbiter @ path (single message → immediate
            # _fire) and all non-@ paths read this. The multi-@ covering-write
            # defect only manifests in the arbiter burst path, where the real
            # carrier is burst_pending (each PendingMessage now carries its own
            # block_id/target_message_id) — see _build_block_triggers.
            slot.trigger = trigger

        block_id = observed_block.block_id if observed_block is not None else ""
        pending_message = PendingMessage(
            content=message_text or (trigger.reason if trigger is not None else "") or "@我",
            user_id=user_id,
            timestamp=time.time(),
            target_message_id=(trigger.target_message_id if trigger is not None else None),
            block_id=block_id,
            evidence=(trigger.mode if trigger is not None else ""),
            obligation_level=(str(trigger.obligation) if trigger is not None else ""),
        )

        # ════════════════════════════════════════════════════════════════════
        # P7 — RULE LAYER (deterministic, runs before any scoring).
        # Strong, cheap, certain signals decide here and `_fire()`+`return`
        # immediately; RWS/role scoring never runs for them. Upstream of notify
        # the rule layer also covers blocked_users (router) and the bot↔bot loop
        # guard (S1, router). The gray-zone scoring path (RWS, role gating,
        # probability) begins only AFTER every rule-layer branch below has
        # declined — see the "GRAY ZONE" marker. Do NOT add "should I speak"
        # scoring above that marker, and do NOT let scoring override a
        # rule-layer decision (the分裂点 A anti-regression boundary).
        # ════════════════════════════════════════════════════════════════════
        if is_at:
            if slot.running_task and not slot.running_task.done():
                slot.pending_during_generation.append(pending_message)
                # Deterministic interrupt-merge: a same-block OR same-user @
                # arriving mid-generation is a continuation of the in-flight
                # reply (the user is still addressing us about the same thing).
                # If the first visible segment has NOT been sent yet, cancel the
                # running fire — the _do_chat finally-block re-merges
                # pending_during_generation into burst_pending and Arbiter-A
                # fires ONE unified reply (no debounce, purely event-driven).
                # Once the first segment is out we must NOT retract it
                # ("已发段不撤回"); that post-emission case is left to Arbiter-B
                # (abort unsent segments only).
                same_addressee = bool(
                    not slot.first_segment_sent
                    and (
                        (block_id and block_id == slot.firing_block_id)
                        or (user_id and user_id == slot.firing_user_id)
                    )
                )
                if same_addressee:
                    _L.info(
                        "scheduler | group={} same-addressee burst (block={} user={}) "
                        "-> cancel & remerge (n={})",
                        group_id, block_id or "_nob", user_id,
                        len(slot.pending_during_generation),
                    )
                    slot.running_task.cancel()
                else:
                    _L.debug(
                        "scheduler | group={} @ queued during generation (n={})",
                        group_id,
                        len(slot.pending_during_generation),
                    )
                return
            if self._arbiter_enabled(group_id):
                slot.burst_pending.append(pending_message)
                if slot.arbiter_task is None or slot.arbiter_task.done():
                    slot.arbiter_task = asyncio.create_task(self._arbiter_completeness_loop(group_id))
                    slot.arbiter_task.add_done_callback(lambda _: None)
                _L.info("scheduler | group={} @ -> arbiter wait", group_id)
                return
            _L.info("scheduler | group={} @ -> fire", group_id)
            self._fire(group_id)
            return

        if is_directed_followup:
            if slot.running_task and not slot.running_task.done():
                slot.pending_during_generation.append(pending_message)
                _L.debug(
                    "scheduler | group={} directed_followup queued during generation (n={})",
                    group_id,
                    len(slot.pending_during_generation),
                )
                return
            _L.info("scheduler | group={} directed_followup -> fire", group_id)
            self._fire(group_id)
            return

        if is_correction:
            if slot.running_task and not slot.running_task.done():
                slot.pending_during_generation.append(pending_message)
                _L.debug(
                    "scheduler | group={} correction queued during generation (n={})",
                    group_id,
                    len(slot.pending_during_generation),
                )
                return
            _L.info("scheduler | group={} correction -> fire", group_id)
            self._fire(group_id)
            return

        if is_closing:
            # Weak-reply P0: a farewell ("晚安") demands a symmetric terminal
            # token (Schegloff & Sacks). Bypass the probability gate like
            # directed_followup, but gate on dedup + cooldown so we don't spam.
            now_wall = time.time()
            # closing_done resets after a long quiet gap (new conversation).
            if slot.closing_done and (now_wall - slot.last_light_time) >= _CLOSING_RESET_S:
                slot.closing_done = False
            if slot.closing_done:
                _L.info("scheduler | group={} closing already done, skip", group_id)
                slot.trigger = None
                return
            if (now_wall - slot.last_light_time) < _LIGHT_COOLDOWN_S:
                _L.info("scheduler | group={} closing within light cooldown, skip", group_id)
                slot.trigger = None
                return
            if slot.running_task and not slot.running_task.done():
                slot.pending_during_generation.append(pending_message)
                _L.debug(
                    "scheduler | group={} closing queued during generation (n={})",
                    group_id,
                    len(slot.pending_during_generation),
                )
                return
            slot.closing_done = True
            slot.last_light_time = now_wall
            _L.info("scheduler | group={} closing -> fire", group_id)
            self._fire(group_id)
            return

        if is_greeting:
            # Weak-reply: a greeting ("早安") invites a symmetric hello. Bypass
            # the probability gate like closing, but gate on the shared light
            # cooldown only (greeting is NOT terminal, so it does not set/honor
            # closing_done — back-to-back 早安/晚安 in the same window stay
            # cooldown-limited, not permanently deduped).
            now_wall = time.time()
            if (now_wall - slot.last_light_time) < _LIGHT_COOLDOWN_S:
                _L.info("scheduler | group={} greeting within light cooldown, skip", group_id)
                slot.trigger = None
                return
            if slot.running_task and not slot.running_task.done():
                slot.pending_during_generation.append(pending_message)
                _L.debug(
                    "scheduler | group={} greeting queued during generation (n={})",
                    group_id,
                    len(slot.pending_during_generation),
                )
                return
            slot.last_light_time = now_wall
            _L.info("scheduler | group={} greeting -> fire", group_id)
            self._fire(group_id)
            return

        # Video always-reply: force fire for B站 video shares
        if is_video_always:
            if slot.running_task and not slot.running_task.done():
                slot.pending_during_generation.append(pending_message)
                _L.debug(
                    "scheduler | group={} bilibili always queued during generation (n={})",
                    group_id,
                    len(slot.pending_during_generation),
                )
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

        # ════════════════════════════════════════════════════════════════════
        # P7 — GRAY ZONE (probabilistic / RWS scoring begins here).
        # Reached only when no rule-layer branch above fired or skipped. This is
        # the ONLY place "should the bot proactively speak" is scored. RWS, the
        # receiver-role gate, and the probability roll all live below; none of
        # them can resurrect a rule-layer skip nor veto a rule-layer fire.
        # ════════════════════════════════════════════════════════════════════
        # Probability-based dispatch with minimum interval (replaces debounce).
        now = time.monotonic()
        if now - slot.last_fire_time < resolved.planner_smooth:
            _L.info("scheduler | group={} interval too short, skip (msgs={})", group_id, slot.msg_count)
            slot.trigger = None  # clear trigger on skip — prevent leak
            return

        slot.msg_count += 1

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
        if (
            slot.consecutive_skip >= resolved.consecutive_skip_force_threshold
            and now - getattr(slot, "last_skip_time", 0.0) < 1800.0
        ):
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

        # B2: receiver-role gating (Goffman). When the bot is merely an
        # overhearer of this topic block — not addressed and not a participant
        # — it has no response obligation. Suppress the probability interjection
        # so the bot does not insert itself into a conversation it only watches.
        role = self._receiver_role(
            group_id,
            slot,
            is_addressed=is_addressed,
            reply_to_self=reply_to_self,
            at_self=at_self,
            has_trigger=trigger is not None,
        )
        slot.last_role = role  # C1: single source of truth for "被寻址", read by chat()
        if role == "overhearer":
            mode = str(getattr(self._topic_block_config, "overhearer_mode", "shadow") or "shadow")
            if mode == "silent":
                _L.info("scheduler | group={} overhearer -> silent (no fire)", group_id)
                slot.consecutive_skip += 1
                slot.last_skip_time = time.monotonic()
                slot.last_response_class = ResponseClass.SILENCE.value
                slot.trigger = None
                return
            if mode == "threshold":
                boost = float(getattr(self._topic_block_config, "overhearer_threshold_boost", 0.0) or 0.0)
                threshold = max(0.0, threshold - boost)
            else:  # shadow — observe only, do not change behavior
                _L.info("scheduler | group={} overhearer (would suppress, mode=shadow)", group_id)

        # B2 continuation floor: when the user follows up in a block the bot is
        # already part of (role=ratified), this is a live back-and-forth. A low
        # time-of-day multiplier or low base score should not silently kill it.
        # The floor is applied ONCE, after the probability decision (§C3): it is
        # independent of which channel (old roll vs RWS) is authoritative, so we
        # don't tweak `threshold` here (that only fed the now-shadow old_decision
        # under rws_primary, forcing a double-write). See the post-decision OR.
        ratified_floor = 0.0
        if role == "ratified":
            ratified_floor = float(
                getattr(self._topic_block_config, "ratified_continuation_floor", 0.0) or 0.0,
            )

        mode_label = trigger.mode if trigger else "none"

        roll = random.random()
        old_decision = roll < threshold
        decision = old_decision
        rws = self._maybe_compute_rws(
            group_id,
            slot=slot,
            trigger=trigger,
            threshold=threshold,
            mood_mult=mood_mult,
            time_mult=time_mult,
            old_decision=old_decision,
            roll=roll,
        )
        if rws is not None and self._rws_primary(group_id):
            decision = rws.decision
            # P5: when dual-threshold is on, fire only if BOTH "worth saying"
            # (im) and "good moment" (interrupt) pass. Proactive (non-addressed)
            # interjection uses a higher interrupt bar than an addressed reply.
            if self._hflag_global("rws_dual_threshold"):
                proactive = role not in {"addressed", "ratified"}
                interrupt_threshold = self._hfloat_global(
                    "rws_interrupt_threshold_proactive" if proactive else "rws_interrupt_threshold",
                    0.65 if proactive else 0.5,
                )
                decision = dual_decision(
                    rws,
                    im_threshold=self._hfloat_global("rws_im_threshold", 0.5),
                    interrupt_threshold=interrupt_threshold,
                )
                _L.info(
                    "scheduler_rws_dual | group={} im={:.3f} interrupt={:.3f} proactive={} -> {}",
                    group_id, rws.im_score, rws.interrupt_score, proactive, decision,
                )

        # Single ratified-continuation floor (§C3): a live back-and-forth fires
        # if EITHER the probability decision (roll/RWS) said yes OR the floor
        # roll passes. One write, channel-independent.
        if ratified_floor > 0.0 and not decision and roll < ratified_floor:
            decision = True
            _L.info("scheduler | group={} ratified continuation floor -> fire (floor={:.2f})", group_id, ratified_floor)

        if decision:
            _L.info(
                "scheduler | group={} prob fire "
                "(threshold={:.2f} mood={:.2f} time={:.2f} msgs={} skips={} mode={} role={} rws={})",
                group_id,
                threshold,
                mood_mult,
                time_mult,
                slot.msg_count,
                slot.consecutive_skip,
                mode_label,
                role,
                f"{rws.score:.2f}" if rws is not None else "--",
            )
            slot.consecutive_skip = 0
            slot.last_fire_time = now
            slot.last_response_class = ResponseClass.FULL_REPLY.value
            self._maybe_anchor_topic_block(group_id, slot)
            self._enqueue_reward(group_id, decision=True, rws=rws, threshold=threshold)
            self._fire(group_id)
        else:
            # Companion rescue (weak-reply §2.5-2d): a ratified continuation —
            # the user follows up in a block the bot is already part of (the
            # "宝宝" case) — is a message that should be SEEN. Rather than letting
            # a probability miss drop it to SILENCE, degrade to a companion weak
            # reply (thinker picks light_kind=companion → short ack/sticker).
            # Rate-limited by the shared light cooldown so it does not turn into
            # "reply to every follow-up". Pure overhearers still go SILENCE.
            now_wall = time.time()
            if (
                role == "ratified"
                and (now_wall - slot.last_light_time) >= _LIGHT_COOLDOWN_S
                and not (slot.running_task and not slot.running_task.done())
            ):
                _L.info(
                    "scheduler | group={} prob skip -> companion rescue (ratified, role={})",
                    group_id, role,
                )
                slot.consecutive_skip = 0
                slot.last_light_time = now_wall
                slot.last_response_class = ResponseClass.LIGHT_ACK.value
                companion_trigger = TriggerContext(
                    reason="对方在和你的对话里续话，该被看见——轻轻应一声",
                    mode="companion",
                    target_message_id=(trigger.target_message_id if trigger is not None else None),
                    target_user_id=(slot.last_user_id or ""),
                )
                self._enqueue_reward(group_id, decision=True, rws=rws, threshold=threshold)
                self._fire(group_id, block_trigger=companion_trigger)
                return
            slot.consecutive_skip += 1
            slot.last_skip_time = now
            slot.last_response_class = ResponseClass.SILENCE.value
            _L.info(
                "scheduler | group={} prob skip "
                "(threshold={:.2f} mood={:.2f} time={:.2f} msgs={} skips={} mode={} role={} rws={})",
                group_id,
                threshold,
                mood_mult,
                time_mult,
                slot.msg_count,
                slot.consecutive_skip,
                mode_label,
                role,
                f"{rws.score:.2f}" if rws is not None else "--",
            )
            slot.trigger = None  # clear trigger on skip — prevent leak
            self._enqueue_reward(group_id, decision=False, rws=rws, threshold=threshold)

    # B1-addressed: focus an addressed reply on the @-ed message + its topic
    # block, instead of replying to the whole stale multi-topic timeline.
    _FOCUS_TRIGGER_MODES = frozenset({"at_mention", "directed_followup", "correction", "qq_interaction"})

    def _build_block_triggers(
        self, group_id: str, pending: list[PendingMessage]
    ) -> list[TriggerContext]:
        """Group a burst of @ messages by topic block → one trigger per block.

        Fixes the scalar covering-write defect: when several people @ the bot
        in the same burst across different topics, each block gets its own
        TriggerContext anchored to that block's @ message (representative),
        instead of the last-arriving @ clobbering everyone else's reply target.
        Same-topic @s merge into one trigger (combined addressees). Order
        follows first-arrival so the earliest addressing fires first.
        """
        if not pending:
            return []
        # Preserve first-seen block order; "" (no block) falls back to a single
        # synthetic group so the legacy single-trigger behavior is unchanged.
        order: list[str] = []
        groups: dict[str, list[PendingMessage]] = {}
        for msg in pending:
            key = msg.block_id or "_nob"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(msg)

        base = self._slots.get(group_id)
        base_trigger = base.trigger if base is not None else None
        triggers: list[TriggerContext] = []
        for key in order:
            members = groups[key]
            # Anchor to the topic block's representative (@ message preferred),
            # else the last-arriving @ in this block.
            anchor_mid: int | None = None
            if key != "_nob" and self._topic_tracker is not None:
                block = self._topic_tracker.pick_block_by_id(group_id, key)
                if block is not None:
                    anchor_mid = block.representative_message_id()
            if anchor_mid is None:
                for msg in reversed(members):
                    if msg.target_message_id is not None:
                        anchor_mid = msg.target_message_id
                        break
            # Strong-signal addressees only (Q3): everyone in this burst block
            # who carried an @-style target. Lexical-fallback co-members never
            # appear in burst_pending, so membership here is already strong.
            addressees: list[str] = []
            for msg in members:
                if msg.user_id and msg.user_id not in addressees:
                    addressees.append(msg.user_id)
            anchor_uid = ""
            for msg in reversed(members):
                if msg.target_message_id is not None and msg.user_id:
                    anchor_uid = msg.user_id
                    break
            extra: dict[str, Any] = dict(base_trigger.extra) if base_trigger is not None else {}
            if len(addressees) > 1:
                extra["block_addressees"] = list(addressees)
            extra["block_id"] = "" if key == "_nob" else key
            triggers.append(
                TriggerContext(
                    reason=(base_trigger.reason if base_trigger is not None else "有人@了你"),
                    mode="at_mention",
                    target_message_id=anchor_mid,
                    target_user_id=anchor_uid or (base_trigger.target_user_id if base_trigger is not None else ""),
                    obligation=(base_trigger.obligation if base_trigger is not None else None),
                    extra=extra,
                )
            )
        return triggers


    def _focused_trigger_reason(self, trigger: TriggerContext) -> str:
        """Append a topic-focus directive to an addressed trigger's reason.

        Without this, an @-mention makes the bot reply to every lingering
        topic in the timeline (it sees ``user_content=""`` + the full stream).
        The directive tells it to answer only the addressed message's current
        topic and not rake up older topic blocks. Gated on
        ``topic_block.enabled``; returns the original reason unchanged when
        disabled or for non-addressed modes.
        """
        reason = str(getattr(trigger, "reason", "") or "")
        if self._topic_tracker is None:
            return reason
        mode = str(getattr(trigger, "mode", "") or "")
        if mode not in self._FOCUS_TRIGGER_MODES:
            return reason
        directive = "（只回应对方这条消息当下的话题，不要把上文里别的、已经过去的话题也一并翻出来回答）"
        return f"{reason}{directive}" if reason else directive.strip("（）")

    def _receiver_role(
        self,
        group_id: str,
        slot: _GroupSlot,
        *,
        is_addressed: bool,
        reply_to_self: bool,
        at_self: bool,
        has_trigger: bool,
    ) -> str:
        """B2: classify the bot's reception role in the current topic block.

        Goffman participation framework — only an addressed recipient carries
        a response obligation. ``addressed``: explicitly @-ed / replied-to /
        triggered. ``ratified``: the bot already participates in an active
        block (it may join low-key). ``overhearer``: neither → default silence.
        Returns ``"addressed"`` when topic tracking is disabled (no behavior
        change without B1).
        """
        if is_addressed or reply_to_self or at_self or has_trigger:
            return "addressed"
        if self._topic_tracker is None:
            return "addressed"  # tracker off → no role gating
        block = self._topic_tracker.pick_anchor_block(group_id, require_bot_involved=True)
        if block is not None:
            return "ratified"
        return "overhearer"

    def _maybe_anchor_topic_block(self, group_id: str, slot: _GroupSlot) -> None:
        """B1: anchor a probability-fire reply to the bot's topic block.

        Only fires for non-explicit triggers (``slot.trigger is None``);
        at/followup/closing already carry their own anchor. Writes via the
        existing ``add_pending_trigger`` (closing-P0 same API) so the anchor
        lands in pending → last message → outside the cache prefix.
        """
        if self._topic_tracker is None or slot.trigger is not None:
            return
        try:
            block = self._topic_tracker.pick_anchor_block(group_id)
            if block is None:
                return
            rep_id = block.representative_message_id()
            if rep_id is None:
                return
            preview = (block.last_text or "").strip().replace("\n", " ")[:40]
            reason = (
                f"群里在聊「{preview}」，你接这条所在的话题"
                "（可以只回个表情或短短一句，不必长篇）"
            )
            self._timeline.add_pending_trigger(
                group_id,
                reason=reason,
                message_id=rep_id,
                target_user_id=block.representative_speaker(),
            )
            _L.info("scheduler | group={} topic-block anchor -> msg={}", group_id, rep_id)
        except Exception as exc:
            _L.debug("topic_block anchor failed | group={} err={}", group_id, exc)

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
        slot.pending_during_generation = []
        slot.block_fire_queue = []
        if cancel_running and slot.running_task and not slot.running_task.done():
            slot.running_task.cancel()
            slot.running_task = None
        slot.burst_pending = []
        if slot.arbiter_task and not slot.arbiter_task.done():
            slot.arbiter_task.cancel()
        slot.arbiter_task = None
        _L.info(
            "scheduler | group={} pending cleared cancel_running={}",
            group_id,
            cancel_running,
        )

    def trigger(self, group_id: str) -> None:
        """Immediately fire a chat for this group (no debounce). Used at startup."""
        if group_id in self._muted_groups:
            return
        identity = self._persona_runtime.identity_snapshot()
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
            if slot.arbiter_task and not slot.arbiter_task.done():
                slot.arbiter_task.cancel()
                tasks.append(slot.arbiter_task)
            if slot.wait_defer_task and not slot.wait_defer_task.done():
                slot.wait_defer_task.cancel()
                tasks.append(slot.wait_defer_task)
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
            tasks.append(self._reconcile_task)
        if self._rws_reward_task and not self._rws_reward_task.done():
            self._rws_reward_task.cancel()
            tasks.append(self._rws_reward_task)
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

    def _active_groups(self) -> list[str]:
        groups = set(self._slots)
        groups.update(self._muted_groups)
        if callable(self._group_inventory_getter):
            try:
                inventory = self._group_inventory_getter() or {}
            except Exception as exc:
                _L.debug("scheduler group inventory read failed | err={}", exc)
            else:
                if isinstance(inventory, dict):
                    groups.update(str(group_id).strip() for group_id in inventory if str(group_id).strip())
        return sorted(groups)

    def _pending_messages_since(self, group_id: str, baseline_count: int) -> list[PendingMessage]:
        pending = self._timeline.get_pending(group_id)
        fresh = pending[baseline_count:]
        results: list[PendingMessage] = []
        for row in fresh:
            if row.get("role") != "user":
                continue
            if row.get("trigger_reason"):
                continue
            content = row.get("content", "")
            text = content if isinstance(content, str) else self._timeline._content_to_text(content)  # type: ignore[attr-defined]
            if not str(text).strip():
                continue
            speaker = str(row.get("speaker", "") or "")
            user_id = ""
            if speaker.endswith(")") and "(" in speaker:
                user_id = speaker.rsplit("(", 1)[-1].rstrip(")")
            results.append(
                PendingMessage(
                    content=str(text),
                    user_id=user_id,
                    timestamp=time.time(),
                )
            )
        return results

    def _arbiter_enabled(self, group_id: str) -> bool:
        config = self._arbiter_config
        if config is None or not bool(getattr(config, "enabled", False)):
            return False
        groups = {
            str(gid).strip()
            for gid in (getattr(config, "runtime_groups", None) or [])
            if str(gid).strip()
        }
        return not groups or str(group_id).strip() in groups

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return str(content or "")

    def _latest_assistant_reply_after(self, group_id: str, baseline_turn_count: int) -> str:
        turns = list(self._timeline.get_turns(group_id))
        if len(turns) <= baseline_turn_count:
            return ""
        for turn in reversed(turns[baseline_turn_count:]):
            if turn.get("role") != "assistant":
                continue
            return self._content_to_text(turn.get("content", "")).strip()
        return ""

    async def _arbiter_completeness_loop(self, group_id: str) -> None:
        slot = self._slots.get(group_id)
        config = self._arbiter_config
        arbiter = self._arbiter
        if slot is None or config is None or arbiter is None:
            return
        elapsed = 0.0
        poll_s = max(0.05, float(getattr(config, "completeness_poll_interval_s", 0.3) or 0.3))
        max_wait_s = max(0.05, float(getattr(config, "completeness_max_wait_s", 5.0) or 5.0))
        threshold = max(
            0.0,
            min(1.0, float(getattr(config, "completeness_confidence_threshold", 0.8) or 0.8)),
        )
        try:
            while elapsed < max_wait_s:
                await asyncio.sleep(poll_s)
                elapsed += poll_s
                if not slot.burst_pending:
                    return
                result = await arbiter.judge_completeness(
                    list(slot.burst_pending),
                    user_id=slot.burst_pending[-1].user_id if slot.burst_pending else "",
                    group_id=group_id,
                )
                if result.complete and result.confidence >= threshold:
                    _L.info(
                        "arbiter_a_fire | group={} pending={} confidence={:.2f} fallback={}",
                        group_id,
                        len(slot.burst_pending),
                        result.confidence,
                        result.fallback,
                    )
                    break
            if slot.running_task and not slot.running_task.done():
                slot.pending_during_generation.extend(slot.burst_pending)
                return
            # Path Y: split the burst by topic block. Same-topic @s merge into
            # one reply; different-topic @s each get their own fire, anchored to
            # their own block's representative message (no more last-@ clobber).
            block_triggers = self._build_block_triggers(group_id, list(slot.burst_pending))
            if len(block_triggers) <= 1:
                self._fire(group_id, block_trigger=block_triggers[0] if block_triggers else None)
            else:
                _L.info(
                    "arbiter_a_fire | group={} burst spans {} topic blocks -> serial fire",
                    group_id, len(block_triggers),
                )
                # Fire the first block now; the rest drain back-to-back in the
                # _do_chat finally-block (no inter-block gap).
                slot.block_fire_queue = block_triggers[1:]
                self._fire(group_id, block_trigger=block_triggers[0])
        except asyncio.CancelledError:
            raise
        finally:
            slot.burst_pending = []
            slot.arbiter_task = None

    async def _arbiter_b_monitor(
        self,
        group_id: str,
        gate: _EmissionGate,
        baseline: int,
        sent_texts: list[str],
        user_id: str,
    ) -> None:
        """Feed interruption verdicts into the emission gate.

        Reads ``slot.pending_during_generation`` directly (the canonical
        during-generation queue filled by notify) rather than scanning the
        timeline by a baseline snapshot. The old baseline approach had a race:
        an @ that arrived between "Arbiter-A decides to fire" and "_do_chat
        snapshots generation_pending_baseline" landed BELOW the baseline, so
        ``_pending_messages_since`` never returned it and the monitor never
        armed (observed: 0 Arbiter-B activations in production). The slot queue
        has no such window — every mid-generation @ is visible here.
        """
        if self._arbiter is None:
            return
        slot = self._slots.get(group_id)
        if slot is None:
            return
        poll_interval = 0.15
        seen_keys: set[tuple[str, str]] = set()
        while True:
            await asyncio.sleep(poll_interval)
            if gate.circuit_open:
                continue
            new_pending = [
                msg for msg in list(slot.pending_during_generation)
                if (msg.content, msg.user_id) not in seen_keys
            ]
            if not new_pending:
                continue
            gate.arm()
            try:
                result = await asyncio.wait_for(
                    self._arbiter.judge_interruption(
                        already_sent=list(sent_texts),
                        unsent=[],
                        new_messages=[msg.content for msg in new_pending],
                        user_id=user_id,
                        group_id=group_id,
                    ),
                    timeout=_MONITOR_JUDGE_TIMEOUT_S,
                )
                gate.resolve(result)
            except TimeoutError:
                gate.resolve(
                    InterruptionResult(action="continue", reason="timeout", fallback=True),
                    timed_out=True,
                )
                _L.warning("arbiter_b_timeout | group={}", group_id)
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                gate.resolve(
                    InterruptionResult(action="continue", reason="error", fallback=True),
                    timed_out=True,
                )
                _L.exception("arbiter_b_monitor_error | group={}", group_id)
                continue
            if result.action == "continue":
                seen_keys.update((msg.content, msg.user_id) for msg in new_pending)
                continue
            _L.info(
                "arbiter_b_abort | group={} action={} reason={}",
                group_id,
                result.action,
                result.reason,
            )
            return

    def _hflag_global(self, name: str, *, default: bool = False) -> bool:
        return bool(getattr(self._humanization_config, name, default))

    def _hfloat_global(self, name: str, default: float) -> float:
        try:
            return float(getattr(self._humanization_config, name, default))
        except (TypeError, ValueError):
            return default

    def _hint_global(self, name: str, default: int) -> int:
        try:
            return int(getattr(self._humanization_config, name, default))
        except (TypeError, ValueError):
            return default

    def _hstr_global(self, name: str, default: str) -> str:
        value = getattr(self._humanization_config, name, default)
        return str(value).strip() if value is not None else default

    def _humanization_group_allowed(self, group_id: str) -> bool:
        groups = {
            str(gid).strip()
            for gid in (getattr(self._humanization_config, "runtime_groups", None) or [])
            if str(gid).strip()
        }
        return not groups or str(group_id).strip() in groups

    def _rws_enabled(self, group_id: str) -> bool:
        return self._humanization_group_allowed(group_id) and (
            self._hflag_global("rws_shadow") or self._hflag_global("rws_primary")
        )

    def _rws_primary(self, group_id: str) -> bool:
        return self._humanization_group_allowed(group_id) and self._hflag_global("rws_primary")

    def _rws_theta(self) -> float:
        if self._rws_bandit is not None and self._hflag_global("rws_bandit"):
            self._rws_bandit.frozen = self._hflag_global("rws_bandit_freeze", default=True)
            return self._rws_bandit.current_theta()
        return max(0.0, min(1.0, self._hfloat_global("rws_threshold", 0.5)))

    @staticmethod
    def _memory_coupling_enabled() -> bool:
        raw = os.getenv("RWS_MEMORY_COUPLING", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _rws_weights(self):
        if self._memory_coupling_enabled():
            return DEFAULT_RWS_WEIGHTS
        return replace(
            DEFAULT_RWS_WEIGHTS,
            outcome=0.0,
            familiarity=0.0,
            willingness=0.0,
        )

    def _memory_signal_snapshot(self, group_id: str) -> dict[str, Any] | None:
        if self._memory_signal_getter is None:
            return None
        slot = self._slots.get(group_id)
        user_id = str(slot.last_user_id or "") if slot is not None else ""
        if not user_id:
            return None
        try:
            snapshot = self._memory_signal_getter(group_id, user_id)
        except Exception as exc:
            _L.debug("memory signal getter failed | group={} user={} err={}", group_id, user_id, exc)
            return None
        return snapshot if isinstance(snapshot, dict) else None

    def _maybe_compute_rws(
        self,
        group_id: str,
        *,
        slot: _GroupSlot,
        trigger: TriggerContext | None,
        threshold: float,
        mood_mult: float,
        time_mult: float,
        old_decision: bool,
        roll: float,
    ) -> RWSExplanation | None:
        if not self._rws_enabled(group_id):
            slot.last_rws = None
            return None
        addressee_self = bool(trigger.extra.get("addressee_self", True)) if trigger is not None else True
        eot_probability = self._eot_probability(group_id) if self._hflag_global("rws_eot") else 0.5
        signal_snapshot = self._memory_signal_snapshot(group_id) or {}
        mood_signal = signal_snapshot.get("mood_trend") if self._memory_coupling_enabled() else None
        features = RWSFeatures(
            mode=trigger.mode if trigger else "none",
            addressee_self=addressee_self,
            old_threshold=threshold,
            mood_mult=mood_mult,
            mood_trend=float(mood_signal) if mood_signal is not None else None,
            time_mult=time_mult,
            consecutive_skip=slot.consecutive_skip,
            force_threshold=self._group_config.resolve(int(group_id)).consecutive_skip_force_threshold,
            double_threshold=self._group_config.resolve(int(group_id)).consecutive_skip_double_threshold,
            hawkes_rho=self._hawkes_rho(group_id) if self._hflag_global("rws_hawkes") else 0.0,
            eot_probability=eot_probability,
            outcome_ratio=float(signal_snapshot.get("outcome_ratio", 0.5)),
            familiarity=float(signal_snapshot.get("familiarity", 0.0)),
            willingness_phase=float(signal_snapshot.get("willingness_phase", 0.5)),
        )
        explanation = replace(
            compute_rws(features, theta=self._rws_theta(), weights=self._rws_weights()),
            old_decision=old_decision,
        )
        probabilistic_decision = roll < explanation.score
        payload = explanation.to_dict()
        payload["probabilistic_decision"] = probabilistic_decision
        payload["roll"] = round(roll, 4)
        slot.last_rws = payload
        _L.info(
            "scheduler_rws | group={} score={:.3f} theta={:.3f} old={} prob={} primary={}",
            group_id,
            explanation.score,
            explanation.theta,
            old_decision,
            probabilistic_decision,
            self._rws_primary(group_id),
        )
        return explanation

    def _hawkes_rho(self, group_id: str) -> float:
        if self._hawkes_cache is not None:
            snapshot = self._hawkes_cache.load(group_id)
            if snapshot is not None:
                return snapshot.rho
        turns = self._timeline.get_turns(group_id)
        times = [
            self._timeline.get_turn_time(group_id, idx)
            for idx in range(len(turns))
            if self._timeline.get_turn_time(group_id, idx) > 0
        ]
        return estimate_rho_from_times(times, window_s=1800.0)

    def _eot_probability(self, group_id: str) -> float:
        if self._eot_cache is None:
            return 0.5
        cached = self._eot_cache.get(group_id)
        if cached is not None:
            return cached.probability
        if self._eot_classifier is not None and self._eot_cache.can_call(group_id):
            messages = self._recent_message_rows(group_id, limit=5)
            if messages:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return 0.5
                if self._eot_cache.reserve_call(group_id):
                    task = loop.create_task(self._refresh_eot(group_id, messages))
                    task.add_done_callback(lambda _: None)
        return 0.5

    async def _refresh_eot(self, group_id: str, messages: list[dict[str, Any]]) -> None:
        if self._eot_cache is None or self._eot_classifier is None:
            return
        api_call = getattr(self._llm, "_call", None)
        if not callable(api_call):
            return
        typed_api_call = cast(Callable[[Any], Awaitable[dict[str, Any]]], api_call)
        decision = await self._eot_classifier.classify(messages, group_id=group_id, api_call=typed_api_call)
        self._eot_cache.put(group_id, decision)

    def _recent_message_rows(self, group_id: str, *, limit: int) -> list[dict[str, Any]]:
        turns = list(self._timeline.get_turns(group_id))[-limit:]
        pending = [
            {"role": row.get("role", "user"), "content": row.get("content", "")}
            for row in self._timeline.get_pending(group_id)[-limit:]
        ]
        rows = [
            {"role": row.get("role", ""), "content": row.get("content", "")}
            for row in turns
        ]
        return (rows + pending)[-limit:]

    def _fire(self, group_id: str, *, block_trigger: TriggerContext | None = None) -> None:
        slot = self._slots.get(group_id)
        if not slot:
            return
        # A real fire supersedes any pending addressed-wait deferral.
        if slot.wait_defer_task and not slot.wait_defer_task.done():
            slot.wait_defer_task.cancel()
            slot.wait_defer_task = None
        # Path Y: a per-block trigger (multi-addressee burst) takes precedence
        # over the scalar slot.trigger; otherwise consume slot.trigger once.
        if block_trigger is not None:
            trigger = block_trigger
            slot.trigger = None
        else:
            trigger = slot.trigger
            slot.trigger = None
        slot.msg_count = 0
        slot.last_response_class = ResponseClass.FULL_REPLY.value
        # Record the in-flight fire's addressee identity so a same-block /
        # same-user @ arriving mid-generation can be recognized as a continuation
        # of THIS reply and trigger cancel-and-remerge (see the is_at branch in
        # notify). Reset the first-segment guard for the new fire.
        slot.firing_block_id = str(trigger.extra.get("block_id", "") or "") if trigger is not None else ""
        slot.firing_user_id = str(trigger.target_user_id or "") if trigger is not None else ""
        slot.first_segment_sent = False
        slot.running_task = asyncio.create_task(self._do_chat(group_id, trigger=trigger))
        slot.running_task.add_done_callback(lambda _: None)

    _ADDRESSED_WAIT_MODES = frozenset({"at_mention"})

    def _maybe_defer_addressed_wait(self, group_id: str, trigger: TriggerContext | None) -> None:
        """Re-fire an addressed (@) turn after a quiet window if its thinker chose
        wait. Prevents an @ from being silently dropped when the bot decides the
        user might not be finished. Bounded by ``wait_max_deferrals``.
        """
        if trigger is None or trigger.mode not in self._ADDRESSED_WAIT_MODES:
            return
        # Only when the thinker actually chose to wait (not a genuine no-op).
        if str(getattr(self._llm, "_last_thinker_action", "") or "") != "wait":
            return
        delay = float(getattr(self._thinker_config, "wait_deferral_seconds", 0.0) or 0.0)
        max_def = int(getattr(self._thinker_config, "wait_max_deferrals", 0) or 0)
        if delay <= 0.0 or max_def <= 0:
            return
        slot = self._slots.get(group_id)
        if slot is None or slot.wait_deferrals >= max_def:
            if slot is not None and slot.wait_deferrals >= max_def:
                _L.info("scheduler | group={} addressed-wait deferral cap reached", group_id)
            return
        slot.wait_deferrals += 1
        if slot.wait_defer_task and not slot.wait_defer_task.done():
            slot.wait_defer_task.cancel()
        _L.info(
            "scheduler | group={} addressed-wait -> defer {}/{} ({:.0f}s)",
            group_id, slot.wait_deferrals, max_def, delay,
        )
        slot.wait_defer_task = asyncio.create_task(self._deferred_addressed_fire(group_id, trigger, delay))
        slot.wait_defer_task.add_done_callback(lambda _: None)

    async def _deferred_addressed_fire(
        self, group_id: str, trigger: TriggerContext, delay: float,
    ) -> None:
        """After a quiet window, honor a deferred @ unless a new turn superseded it."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        slot = self._slots.get(group_id)
        if slot is None:
            return
        # Superseded: a new trigger queued, or a chat is already running → that
        # path will handle the conversation; drop the stale deferral.
        if slot.trigger is not None or (slot.running_task and not slot.running_task.done()):
            _L.info("scheduler | group={} deferred @ superseded, skip", group_id)
            return
        if group_id in self._muted_groups:
            return
        # Force a reply this time (the quiet window elapsed → user finished).
        extra = dict(trigger.extra)
        extra["force_after_wait"] = True
        fire_trigger = replace(
            trigger,
            extra=extra,
            obligation=replace(trigger.obligation, level="must") if trigger.obligation is not None else None,
        )
        _L.info("scheduler | group={} deferred @ -> fire (force)", group_id)
        slot.last_response_class = ResponseClass.FULL_REPLY.value
        slot.running_task = asyncio.create_task(self._do_chat(group_id, trigger=fire_trigger))
        slot.running_task.add_done_callback(lambda _: None)

    async def _record_runtime_metric(
        self,
        *,
        metric_key: str,
        group_id: str,
        amount: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        store = self._block_trace_store
        if store is None or not hasattr(store, "record_runtime_metric"):
            return
        try:
            await store.record_runtime_metric(
                metric_key=metric_key,
                group_id=group_id,
                amount=amount,
                metadata=metadata,
            )
        except Exception:
            _L.debug("scheduler runtime metric skipped | key={} group={}", metric_key, group_id)

    async def _reconcile_self_mute_once(self) -> None:
        bot = self._bot
        if bot is None or not self._self_id:
            return
        now = time.time()
        for group_id in self._active_groups():
            try:
                info = await bot.get_group_member_info(
                    group_id=int(group_id),
                    user_id=int(self._self_id),
                    no_cache=True,
                )
                raw = info.get("shut_up_timestamp") or 0
                shut_up_ts = int(str(raw))
                is_muted_server = shut_up_ts > now
                is_muted_local = group_id in self._muted_groups
                if is_muted_server and not is_muted_local:
                    self.mute(group_id, source="reconcile", until_unix=float(shut_up_ts))
                    _L.warning(
                        "reconcile | group={} server says muted (until {}), marking",
                        group_id,
                        shut_up_ts,
                    )
                elif not is_muted_server and is_muted_local:
                    self.unmute(group_id)
                    _L.info("reconcile | group={} server says unmuted, clearing", group_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                _L.debug("reconcile | group={} query failed, skip", group_id)

    async def _reconcile_self_mute_loop(self) -> None:
        interval = max(1, int(getattr(self._self_mute_config, "reconcile_interval_seconds", 300) or 300))
        while True:
            await asyncio.sleep(interval)
            await self._reconcile_self_mute_once()

    # ------------------------------------------------------------------
    # P1: RWS reward loop
    # ------------------------------------------------------------------
    def _enqueue_reward(
        self, group_id: str, *, decision: bool, rws: RWSExplanation | None, threshold: float,
    ) -> None:
        """Record a fire/skip decision for delayed reward settlement (P1)."""
        if self._rws_reward_queue is None:
            return
        try:
            baseline = len(self._timeline.get_turns(group_id)) if self._timeline is not None else 0
            self._rws_reward_queue.enqueue(
                PendingDecision(
                    group_id=group_id,
                    decision=decision,
                    t0=time.monotonic(),
                    turn_baseline=baseline,
                    rws_score=float(rws.score) if rws is not None else float(threshold),
                ),
            )
        except Exception as exc:
            _L.debug("rws reward enqueue failed | group={} err={}", group_id, exc)

    def _measure_reaction(self, item: PendingDecision) -> ReactionSignals:
        """Read the group's post-decision reaction within the settlement window.

        Pure read of timeline turns since the decision baseline. Acknowledged =
        new turns appeared and bot stayed in the exchange; went_cold = bot fired
        but no follow-up turn appeared (silence). explicit_negative reserved for
        ban/制止 hooks (not yet wired → False).
        """
        if self._timeline is None:
            return ReactionSignals()
        turns = list(self._timeline.get_turns(item.group_id))
        new_turns = max(0, len(turns) - item.turn_baseline)
        if item.decision:
            # bot fired: ack if a human turn followed; cold if nothing followed.
            acknowledged = new_turns >= 1
            return ReactionSignals(acknowledged=acknowledged, went_cold=new_turns == 0)
        # bot skipped: a skip was "right" if conversation continued naturally
        # (new turns) without anyone waiting on the bot → treat as mild ack;
        # "wrong" (cold) if the group went silent after the skip.
        return ReactionSignals(acknowledged=new_turns >= 1, went_cold=new_turns == 0)

    async def _rws_reward_loop(self) -> None:
        queue = self._rws_reward_queue
        if queue is None:
            return
        window = self._hfloat_global("rws_reward_window_s", 300.0)
        poll = max(5.0, window / 4.0)
        while True:
            await asyncio.sleep(poll)
            try:
                settled = queue.settle_due(
                    measure=self._measure_reaction,
                    observe=self._observe_reward,
                )
                if settled:
                    _L.info("rws reward | settled={} pending={}", settled, queue.pending_count())
            except asyncio.CancelledError:
                raise
            except Exception:
                _L.debug("rws reward settle loop error")

    def _observe_reward(self, decision: bool, reward: float) -> None:
        """Feed a settled reward to the bandit + persist a metric (P1/P6)."""
        if self._rws_bandit is not None:
            try:
                self._rws_bandit.observe(decision=decision, reward=reward)
            except Exception as exc:
                _L.debug("rws bandit observe failed | err={}", exc)
        if self._block_trace_store is not None:
            with contextlib.suppress(Exception):
                asyncio.create_task(  # noqa: RUF006 — fire-and-forget metric
                    self._record_runtime_metric(
                        metric_key="rws_reward",
                        group_id="",
                        metadata={"decision": decision, "reward": round(reward, 3)},
                    ),
                )


    async def _send_to_group(
        self,
        group_id: str,
        text: str,
        *,
        humanize: str = "normal",
        target_user_id: str = "",
    ) -> float:
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
                if self._bot_pair_guard is not None:
                    try:
                        recorded = self._bot_pair_guard.record_outbound(group_id, target_user_id)
                    except Exception as exc:
                        _L.debug(
                            "scheduler pair guard outbound skipped | group={} target={} err={}",
                            group_id,
                            target_user_id,
                            exc,
                        )
                    else:
                        if recorded:
                            await self._record_runtime_metric(
                                metric_key="pair_guard_outbound_recorded",
                                group_id=group_id,
                                metadata={"target_user_id": target_user_id},
                            )
                return elapsed
            except ActionFailed as e:
                retcode = _action_failed_retcode(e)
                retcodes = {
                    int(code)
                    for code in (getattr(self._self_mute_config, "action_failed_retcodes", []) or [])
                    if str(code).strip()
                }
                if (
                    retcode is not None
                    and retcode in retcodes
                    and bool(getattr(self._self_mute_config, "action_failed_reverse_mark", False))
                    and group_id not in self._muted_groups
                ):
                    self.mute(group_id, source="action_failed")
                    _L.warning(
                        "scheduler | group={} ActionFailed retcode={} -> reverse-mark muted",
                        group_id,
                        retcode,
                    )
                _L.warning(
                    "scheduler | group={} send failed: {} | retry in {}s",
                    group_id, e.info.get("wording") or e.info.get("message", str(e)), delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    async def _do_chat(self, group_id: str, *, trigger: TriggerContext | None = None) -> None:
        slot = self._slots.get(group_id)
        try:
            if slot is None:
                return
            slot_ref = slot
            async with slot_ref.chat_lock:
                monitor_task: asyncio.Task[None] | None = None
                for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
                    monitor_task = None
                    try:
                        identity = self._persona_runtime.identity_snapshot()
                        session_id = f"group_{group_id}"
                        uid = slot.last_user_id if slot else ""
                        ctx = ToolContext(
                            bot=self._bot,
                            user_id=uid,
                            group_id=group_id,
                            session_id=session_id,
                        )
                        # C1: hand the unified receiver role to chat() so its
                        # necessity gate uses the SAME "被寻址" definition as the
                        # scheduler (addressed/ratified never get suppressed).
                        if slot is not None:
                            ctx.extra["receiver_role"] = slot.last_role

                        # Write trigger reason into the timeline so the LLM sees it
                        # in the pending buffer, not as transient user_content.
                        if trigger is not None:
                            trace_request_id = str(trigger.extra.get("u13_double_haiku_request_id", "") or "")
                            if trace_request_id:
                                ctx.extra["u13_double_haiku_request_id"] = trace_request_id
                            self._timeline.add_pending_trigger(
                                group_id, reason=self._focused_trigger_reason(trigger),
                                message_id=trigger.target_message_id,
                                target_user_id=trigger.target_user_id,
                            )

                        force_reply = _should_force_reply(trigger)
                        must_emit = bool(
                            trigger is not None
                            and getattr(getattr(trigger, "obligation", None), "level", "") == "must"
                        )

                        # @mention: prepend [CQ:reply] to the first streamed segment only.
                        # Quote-reply already identifies the target — no need for [CQ:at].
                        first_segment = True
                        sent_segments = 0
                        send_total_elapsed = 0.0
                        sent_texts: list[str] = []
                        generation_pending_baseline = len(self._timeline.get_pending(group_id))
                        generation_turn_baseline = len(self._timeline.get_turns(group_id))
                        reply_prefix = ""
                        if (
                            trigger is not None
                            and trigger.mode == "at_mention"
                            and trigger.target_message_id is not None
                        ):
                            reply_prefix = f"[CQ:reply,id={trigger.target_message_id}]"
                            _L.info("scheduler | group={} @mention prefix={}", group_id, reply_prefix)
                        elif (
                            trigger is not None
                            and trigger.mode == "qq_interaction"
                            and trigger.target_user_id
                        ):
                            reply_prefix = f"[CQ:at,qq={trigger.target_user_id}] "

                        gate: _EmissionGate | None = None
                        if (
                            self._arbiter is not None
                            and self._arbiter_enabled(group_id)
                            and bool(getattr(self._arbiter_config, "interruption_enabled", False))
                        ):
                            gate = _EmissionGate()
                            monitor_task = asyncio.create_task(
                                self._arbiter_b_monitor(
                                    group_id=group_id,
                                    gate=gate,
                                    baseline=generation_pending_baseline,
                                    sent_texts=sent_texts,
                                    user_id=uid,
                                )
                            )
                            monitor_task.add_done_callback(lambda _: None)

                        async def on_segment(
                            text: str,
                            _prefix: str = reply_prefix,
                            _target_user_id: str = uid,
                            _baseline: int = generation_pending_baseline,
                            _sent_texts: list[str] = sent_texts,
                            _gate: _EmissionGate | None = gate,
                        ) -> bool:
                            nonlocal first_segment, sent_segments, send_total_elapsed

                            if _gate is not None and not await _gate.check():
                                if _gate.verdict and _gate.verdict.action == "revise":
                                    new_pending = self._pending_messages_since(group_id, _baseline)
                                    existing_keys = {
                                        (item.content, item.user_id)
                                        for item in slot_ref.pending_during_generation
                                    }
                                    for item in new_pending:
                                        key = (item.content, item.user_id)
                                        if key in existing_keys:
                                            continue
                                        slot_ref.pending_during_generation.append(item)
                                        existing_keys.add(key)
                                _L.info(
                                    "on_segment_aborted | group={} action={}",
                                    group_id,
                                    _gate.verdict.action if _gate.verdict else "abort",
                                )
                                return False

                            is_first = first_segment
                            if first_segment:
                                if _prefix:
                                    text = _prefix + text
                                first_segment = False
                            send_total_elapsed += await self._send_to_group(
                                group_id,
                                text,
                                humanize="skip" if is_first else "normal",
                                target_user_id=_target_user_id,
                            )
                            sent_segments += 1
                            _sent_texts.append(text)
                            # First visible segment is now out — past this point
                            # cancel-and-remerge must not retract it. Subsequent
                            # same-block @s fall to Arbiter-B (unsent only).
                            slot_ref.first_segment_sent = True
                            return True

                        resolved = self._group_config.resolve(int(group_id))
                        reply = await asyncio.wait_for(
                            self._llm.chat(
                                session_id=session_id,
                                user_id=uid,
                                user_content="",
                                identity=identity,
                                group_id=group_id,
                                ctx=ctx,
                                on_segment=on_segment if self._bot else None,
                                privacy_mask=resolved.privacy_mask,
                                force_reply=force_reply,
                                must_emit=must_emit,
                                trigger=trigger,
                            ),
                            timeout=_CHAT_LOCK_LLM_TIMEOUT_S,
                        )
                        latest_reply = self._latest_assistant_reply_after(group_id, generation_turn_baseline)

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
                                target_user_id=uid,
                            )
                            sent_segments += 1
                            slot_ref.first_segment_sent = True
                            _L.info(
                                "scheduler reply send complete | group={} segments={} send_total={:.1f}s",
                                group_id, sent_segments, send_total_elapsed,
                            )
                        if latest_reply:
                            slot_ref.last_reply_time = time.time()
                            slot_ref.last_reply_content = latest_reply
                        # B2 fix: mark the active topic block as bot-involved so a
                        # user's follow-up in the same block is judged "ratified"
                        # (a continuation of our exchange) rather than suppressed
                        # as overhearer. Without this the bot replies once, then
                        # goes silent on the user's very next line.
                        if sent_segments > 0 and self._topic_tracker is not None:
                            try:
                                self._topic_tracker.mark_bot_involved(group_id)
                            except Exception as exc:
                                _L.debug("mark_bot_involved failed | group={} err={}", group_id, exc)
                        if sent_segments > 0:
                            slot_ref.wait_deferrals = 0  # honored → reset deferral budget
                        # Addressed-wait deferral: an @ turn produced no reply
                        # because the thinker chose to wait (user may not be
                        # finished). Don't drop the @ obligation — re-fire after a
                        # quiet window so the bot honors the @ if nothing more
                        # came. Bounded by wait_max_deferrals to avoid waiting
                        # forever. A new message meanwhile supersedes via notify.
                        if sent_segments == 0 and not latest_reply:
                            self._maybe_defer_addressed_wait(group_id, trigger)
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
                    except TimeoutError:
                        _L.warning(
                            "scheduler | group={} llm chat timed out after {:.1f}s",
                            group_id,
                            _CHAT_LOCK_LLM_TIMEOUT_S,
                        )
                        return
                    finally:
                        if monitor_task is not None and not monitor_task.done():
                            monitor_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await monitor_task

        except asyncio.CancelledError:
            _L.debug("scheduler | group={} chat cancelled", group_id)
            # D2 cancel-path: a cancelled fire (shutdown / clear_pending) must
            # NOT spawn the next block — clear the queue so it cannot pollute a
            # later run. (The finally still runs; with an empty queue it falls
            # back to the pre-existing pending/msg_count re-fire behavior.)
            if slot:
                slot.block_fire_queue = []
        except Exception:
            _L.exception("scheduler | group={} chat error", group_id)
        finally:
            if slot:
                slot.running_task = None
                # Path Y: drain the next topic block back-to-back (no gap) so a
                # multi-addressee burst across topics gets one reply per block.
                if slot.block_fire_queue:
                    next_trigger = slot.block_fire_queue.pop(0)
                    self._fire(group_id, block_trigger=next_trigger)
                elif slot.pending_during_generation:
                    slot.burst_pending.extend(slot.pending_during_generation)
                    slot.pending_during_generation = []
                    if self._arbiter_enabled(group_id):
                        slot.arbiter_task = asyncio.create_task(self._arbiter_completeness_loop(group_id))
                        slot.arbiter_task.add_done_callback(lambda _: None)
                    else:
                        self._fire(group_id)
                elif slot.msg_count > 0:
                    self._fire(group_id)
