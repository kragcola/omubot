"""Core turn identity and cooperative cancellation contract; no resource I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Annotated, Any, Literal, cast

from pydantic import Field, ValidationError

from .types import OperationError, StrictModel


class ThinkerDecision(StrictModel):
    action: Literal["queue", "replace"]


class DecisionBinding(StrictModel):
    """The immutable turn and input snapshot a judgment is allowed to affect."""

    turn_id: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=0)
    topic_id: str | None = None
    topic_revision: int = Field(ge=0)
    input_revision: int = Field(ge=0)
    persona_version: str = Field(min_length=1, max_length=128)
    configuration_version: str = Field(min_length=1, max_length=128)
    context_version: str = Field(min_length=1, max_length=128)


class RegisterObservation(StrictModel):
    label: Literal["neutral", "quiet", "playful", "affectionate", "serious", "distant"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class StageADecision(StrictModel):
    """Stage A completes/holds and may add bounded, non-authoritative advice."""

    stage: Literal["A"]
    outcome: Literal["complete", "hold"]
    response_shape: Literal["text", "sticker_only"] = "text"
    topic_intent_label: Literal[
        "闲聊",
        "关心",
        "安抚",
        "打趣",
        "吐槽",
        "询问",
        "提议",
        "信息同步",
        "技术讨论",
        "反对",
    ] = "闲聊"
    response_direction: Literal[
        "respond_normally",
        "answer",
        "clarify",
        "comfort",
        "acknowledge",
        "suggest",
        "playful",
        "explain",
        "correct",
    ] = "respond_normally"
    retrieve_mode: Literal["skip", "doc", "fact", "hybrid"] = "skip"
    rewritten_query: str = Field(default="", max_length=160)
    register_observation: RegisterObservation | None = None


_TOPIC_INTENT_LABELS = frozenset(
    {
        "闲聊",
        "关心",
        "安抚",
        "打趣",
        "吐槽",
        "询问",
        "提议",
        "信息同步",
        "技术讨论",
        "反对",
    }
)
_RESPONSE_DIRECTIONS = frozenset(
    {
        "respond_normally",
        "answer",
        "clarify",
        "comfort",
        "acknowledge",
        "suggest",
        "playful",
        "explain",
        "correct",
    }
)
_RETRIEVE_MODES = frozenset({"skip", "doc", "fact", "hybrid"})
_RESPONSE_SHAPES = frozenset({"text", "sticker_only"})
_UNRESOLVED_QUERY_PREFIXES = (
    "这个",
    "那个",
    "它",
    "他",
    "她",
    "他们",
    "她们",
    "上述",
    "前面提到",
    "刚才那个",
    "用户在问",
    "用户想知道",
    "this ",
    "that ",
    "it ",
    "they ",
)


def parse_stage_a_decision(payload: object) -> StageADecision:
    """Validate A's required result while safely defaulting optional advice.

    Old A responses contain only ``stage`` and ``outcome``. Invalid optional
    advice must not convert a valid completion into a hold or failure.
    """
    if not isinstance(payload, dict):
        return StageADecision.model_validate(payload)
    raw = cast(dict[str, Any], payload)

    topic = raw.get("topic_intent_label", "闲聊")
    if type(topic) is not str or topic not in _TOPIC_INTENT_LABELS:
        topic = "闲聊"

    direction = raw.get("response_direction", "respond_normally")
    if type(direction) is not str or direction not in _RESPONSE_DIRECTIONS:
        direction = "respond_normally"

    mode = raw.get("retrieve_mode", "skip")
    if type(mode) is not str or mode not in _RETRIEVE_MODES:
        mode = "skip"

    response_shape = raw.get("response_shape", "text")
    if type(response_shape) is not str or response_shape not in _RESPONSE_SHAPES:
        response_shape = "text"

    query_value = raw.get("rewritten_query", "")
    query = ""
    if type(query_value) is str:
        query = " ".join(query_value.split())
        has_searchable_text = any(char.isalnum() or "\u3400" <= char <= "\u9fff" for char in query)
        starts_with_unresolved_reference = query.casefold().startswith(
            _UNRESOLVED_QUERY_PREFIXES
        )
        if (
            len(query) < 4
            or len(query) > 160
            or not has_searchable_text
            or starts_with_unresolved_reference
        ):
            query = ""
    if mode == "skip" or query == "" or raw.get("outcome") == "hold":
        mode, query = "skip", ""

    register = None
    if raw.get("register_observation") is not None:
        try:
            register = RegisterObservation.model_validate(raw["register_observation"])
        except ValidationError:
            # Optional observed advice may be absent; never invent a confidence.
            pass

    return StageADecision.model_validate(
        {
            "stage": raw.get("stage"),
            "outcome": raw.get("outcome"),
            "response_shape": response_shape if raw.get("outcome") != "hold" else "text",
            "topic_intent_label": topic,
            "response_direction": direction,
            "retrieve_mode": mode,
            "rewritten_query": query,
            "register_observation": register,
        }
    )


class StageBDecision(StrictModel):
    """Stage B may continue, abort unsent output, or revise it."""

    stage: Literal["B"]
    outcome: Literal["continue", "abort_unsent", "revise"]


DecisionResult = Annotated[
    StageADecision | StageBDecision, Field(discriminator="stage")
]


class BoundDecision(StrictModel):
    """A validated A/B result tied to one exact scheduler-owned snapshot."""

    binding: DecisionBinding
    decision: DecisionResult

    def targets(self, current: DecisionBinding) -> bool:
        return self.binding == current


@dataclass
class Turn:
    id: str
    generation: int
    deadline: float = float("inf")
    started: bool = False
    valid: bool = True
    reason: str = ""
    segments_total: int = 1
    segments_sent: int = 0
    dispatching_segment_index: int | None = None
    sent_receipts: list[str] = field(default_factory=list[str])
    emission: Literal["pending", "dispatching", "partial", "sent", "unknown"] = "pending"
    # A streaming reply can fill every reserved send slot before its model
    # finishes. Those receipts alone do not prove the final text was valid.
    stream_finalized: bool = True
    # Captured at actual successful Sender completion; installed with its durable receipt.
    last_segment_completed_at: float | None = None
    # Reply phases share this Turn. Queue/hold age stays in the fixed overall
    # cap; only actual preparation spends its finite generation budget.
    reply_overall_deadline: float | None = None
    reply_generation_remaining: float = 0.0
    reply_generation_started_at: float | None = None
    reply_admission_seconds: float = 0.0
    reply_delivery_seconds: float = 0.0
    reply_admission_deadline: float | None = None
    reply_dispatch_started_at: float | None = None
    reply_delivery_deadline: float | None = None

    def configure_reply(
        self, *, overall_deadline: float, generation_seconds: float,
        admission_seconds: float, delivery_seconds: float,
    ) -> None:
        # Lookahead hands off the original Turn, including spent preparation
        # and any first dispatch. Taking that handoff never renews a clock.
        if self.reply_overall_deadline is not None:
            return
        self.reply_overall_deadline = min(self.deadline, overall_deadline)
        self.reply_generation_remaining = generation_seconds
        self.reply_admission_seconds = admission_seconds
        self.reply_delivery_seconds = delivery_seconds
        self._reply_deadline()

    def _reply_deadline(self) -> None:
        assert self.reply_overall_deadline is not None
        deadline = self.reply_overall_deadline
        if self.reply_generation_started_at is not None:
            deadline = min(deadline, self.reply_generation_started_at + self.reply_generation_remaining)
        if self.reply_delivery_deadline is not None:
            deadline = min(deadline, self.reply_delivery_deadline)
        elif self.reply_admission_deadline is not None:
            deadline = min(deadline, self.reply_admission_deadline)
        self.deadline = deadline

    def begin_reply_generation(self) -> None:
        self.check()
        assert self.reply_generation_started_at is None
        self.reply_generation_started_at = monotonic()
        self._reply_deadline()
        self.check()

    def finish_reply_generation(self) -> None:
        assert self.reply_generation_started_at is not None
        self.reply_generation_remaining = max(
            0.0, self.reply_generation_remaining - (monotonic() - self.reply_generation_started_at),
        )
        self.reply_generation_started_at = None
        self._reply_deadline()

    def begin_reply_admission(self) -> None:
        if self.reply_admission_deadline is None and self.reply_dispatch_started_at is None:
            self.reply_admission_deadline = monotonic() + self.reply_admission_seconds
        self._reply_deadline()
        self.check()

    def mark_message_dispatch(self) -> None:
        # A generic Turn also guards standalone tool/model Actions. Only a
        # configured reply has a message-group clock to start.
        if self.reply_overall_deadline is None:
            return
        self.check()
        if self.reply_dispatch_started_at is None:
            self.reply_dispatch_started_at = monotonic()
            self.reply_delivery_deadline = self.reply_dispatch_started_at + self.reply_delivery_seconds
            self._reply_deadline()

    def send_admission_deadline(self, timeout: float) -> float:
        # First admission ends at actual dispatch, so its ACK timeout belongs
        # to the new group phase. Later writes must leave room for their ACK.
        return self.deadline if self.reply_overall_deadline is not None and (
            self.reply_dispatch_started_at is None
        ) else self.deadline - timeout

    def send_completion_deadline(self) -> float:
        if self.reply_overall_deadline is not None and self.reply_dispatch_started_at is None:
            return min(self.reply_overall_deadline, monotonic() + self.reply_delivery_seconds)
        return self.deadline

    def check(self) -> None:
        if monotonic() >= self.deadline:
            raise OperationError("deadline")
        if not self.valid:
            raise OperationError(self.reason or "superseded")

    def invalidate(self, reason: str) -> None:
        self.valid = False
        self.reason = reason
