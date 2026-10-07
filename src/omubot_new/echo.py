"""Pure, bounded group echo decisions; all admission and sending stay with callers."""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .rich_messages import AtSegment, FaceSegment, ImageSegment, TextSegment
from .store import request_digest
from .types import (
    ConversationKey,
    ConversationScope,
    Event,
    OperationError,
    QQWriteGrant,
    QQWriteSpec,
    ReplyTarget,
    Scope,
    SendReceipt,
    StickerImage,
    VisualOwner,
)
from .visual_transport import ImageBytes

_WINDOW_SECONDS = 300.0
_THRESHOLD = 3
_BREAK_CHANCE = 0.05
_BREAK_TEXT = "打断复读！"


@dataclass(frozen=True, slots=True)
class EchoImage:
    owner: VisualOwner
    image: StickerImage
    is_sticker: bool


type EchoSegment = TextSegment | FaceSegment | AtSegment | EchoImage


@dataclass(frozen=True, slots=True)
class RichEchoPayload:
    """Original typed segments and request-local pixels, never a media URL."""

    event: Event
    source_digest: str
    segments: tuple[EchoSegment, ...]
    identity: str

    @property
    def includes_images(self) -> bool:
        return any(isinstance(segment, EchoImage) for segment in self.segments)

    @property
    def includes_mentions(self) -> bool:
        return any(isinstance(segment, AtSegment) for segment in self.segments)

    def assert_source(self, event: Event) -> None:
        if self.event != event or self.source_digest != request_digest(event):
            raise OperationError("echo_source_changed")


@runtime_checkable
class EchoSenderPort(Protocol):
    """Optional typed echo on the existing message sender, with one receipt."""

    is_external: bool

    async def send_echo(
        self, scope: ConversationScope, payload: RichEchoPayload,
        *, target: ReplyTarget | None = None,
    ) -> SendReceipt: ...

    async def close(self) -> None: ...


@runtime_checkable
class QQEchoSenderPort(EchoSenderPort, Protocol):
    """Optional live QQ descriptor on the same echo receipt boundary."""

    @property
    def connection_generation(self) -> int: ...

    def describe_echo(
        self, scope: ConversationScope, payload: RichEchoPayload,
        *, target: ReplyTarget | None = None,
    ) -> QQWriteSpec: ...

    async def send_echo(
        self, scope: ConversationScope, payload: RichEchoPayload,
        *, target: ReplyTarget | None = None, grant: QQWriteGrant | None = None,
    ) -> SendReceipt: ...


def original_echo_payload(event: Event, images: tuple[ImageBytes, ...] = ()) -> RichEchoPayload | None:
    """Bind admitted direct pixels to their original positions before counting."""
    if event.reply_to or len(images) > 2:
        return None
    original = event.rich_segments or (TextSegment(event.text),)
    if len(original) > 32:
        return None
    by_index: dict[int, ImageBytes] = {}
    total_bytes = 0
    for image in images:
        owner = image.owner
        if (owner.scope != event.scope or owner.event_id != event.event_id
                or owner.message_id != event.message_id or owner.source_kind != "direct"
                or owner.segment_index in by_index):
            raise OperationError("invalid_echo_image_owner")
        by_index[owner.segment_index] = image
        total_bytes += len(image.data)
    if total_bytes > 8 * 1024 * 1024:
        return None
    segments: list[EchoSegment] = []
    identities: list[list[str]] = []
    chars = 0
    for index, segment in enumerate(original):
        if isinstance(segment, TextSegment):
            chars += len(segment.text)
            segments.append(segment)
            identities.append(["text", segment.text])
        elif isinstance(segment, FaceSegment):
            if not segment.face_id.isdecimal() or len(segment.face_id) > 6:
                return None
            segments.append(segment)
            identities.append(["face", segment.face_id])
        elif isinstance(segment, AtSegment):
            if not segment.target_id.isdecimal() or len(segment.target_id) > 20:
                return None  # @all never rides a normal reply grant.
            segments.append(segment)
            identities.append(["at", segment.target_id])
        elif isinstance(segment, ImageSegment):
            image = by_index.pop(index, None)
            if image is None or segment.is_flash:
                return None
            segments.append(EchoImage(image.owner, StickerImage(image.data, image.media_type),
                                      segment.is_sticker))
            identities.append(["image", image.media_type, hashlib.sha256(image.data).hexdigest(),
                               "sticker" if segment.is_sticker else "normal"])
        else:
            return None  # A quoted/forward/JSON display projection is not sendable.
    if by_index:
        raise OperationError("invalid_echo_image_owner")
    if chars > 2000 or not identities or all(value[0] == "text" and not value[1].strip()
                                           for value in identities):
        return None
    identity = hashlib.sha256(json.dumps(identities, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()
    return RichEchoPayload(event, request_digest(event), tuple(segments), identity)


@dataclass(frozen=True, slots=True)
class EchoDecision:
    """One locally chosen output bound to the exact source event, without authority."""

    event: Event
    kind: Literal["repeat", "break"]
    text: str
    payload: RichEchoPayload | None = None


@dataclass(slots=True)
class _EchoState:
    last_seen: float
    text: str = ""
    identity: str = ""
    count: int = 0
    first_seen: float = 0.0
    echoed: bool = False
    interrupt_chain: int = 0


class EchoOwner:
    """Own only transient repetition state for admitted group messages.

    Call before nickname/addressing cleanup. The caller supplies its existing
    session capacity, admits the scope and uses the single Actions send outlet.
    Unsupported rich content clears the previous round: display projections
    cannot stand in for original media, mentions or reply segments.
    """

    def __init__(self, *, max_groups: int, clock: Callable[[], float] = time.monotonic,
                 rng: Callable[[], float] = random.random) -> None:
        if type(max_groups) is not int or max_groups < 1:
            raise ValueError("echo group capacity must be a positive integer")
        self._max_groups = max_groups
        self._clock = clock
        self._rng = rng
        self._states: OrderedDict[ConversationKey, _EchoState] = OrderedDict()
        self._closed = False

    @staticmethod
    def _original_text(event: Event) -> str | None:
        if event.mention_targets or event.reply_to:
            return None
        if not event.rich_segments:
            return event.text
        parts: list[str] = []
        for segment in event.rich_segments:
            if not isinstance(segment, TextSegment):
                return None
            parts.append(segment.text)
        return "".join(parts)

    def _prune(self, now: float) -> None:
        for key, state in tuple(self._states.items()):
            if now - state.last_seen > _WINDOW_SECONDS:
                del self._states[key]

    def process(self, event: Event, *, is_command: bool = False,
                images: tuple[ImageBytes, ...] = ()) -> EchoDecision | None:
        """Choose at most one output; do not read policy, send, or launch a task.

        Commands/private messages do not participate or reset a group round.
        A repeat is consecutive and occurs on the third matching original text
        within 300 seconds. A normal repeat round restarts on its next input;
        incoming break text continues the legacy immediate interrupt chain.
        """
        if self._closed:
            raise OperationError("echo_closed")
        scope = event.scope
        if not isinstance(scope, Scope) or is_command:
            return None
        now = self._clock()
        self._prune(now)
        text = self._original_text(event)
        if text is not None and (not text.strip() or text.startswith("/")):
            return None
        payload = original_echo_payload(event, images)
        if payload is None:
            self.remove_scope(scope)
            return None
        key = scope.key
        state = self._states.get(key)
        if state is None:
            if len(self._states) >= self._max_groups:
                self._states.popitem(last=False)
            state = _EchoState(last_seen=now)
            self._states[key] = state
        else:
            state.last_seen = now
            self._states.move_to_end(key)

        if text == _BREAK_TEXT:
            state.interrupt_chain += 1
            return EchoDecision(event, "break", "打断" * state.interrupt_chain + "复读！")

        if (payload.identity != state.identity or now - state.first_seen > _WINDOW_SECONDS or state.echoed):
            state.text = text or event.text
            state.identity = payload.identity
            state.count = 1
            state.first_seen = now
            state.echoed = False
            state.interrupt_chain = 0
            return None

        state.count += 1
        if state.count < _THRESHOLD:
            return None
        state.echoed = True
        if self._rng() < _BREAK_CHANCE:
            state.interrupt_chain = 0
            return EchoDecision(event, "break", _BREAK_TEXT)
        return EchoDecision(event, "repeat", text or event.text, payload)

    def remove_scope(self, scope: Scope) -> None:
        """Forget an exact group when its application session is removed/revoked."""
        self._states.pop(scope.key, None)

    def clear(self) -> None:
        """Discard counters when the caller invalidates authorized observations."""
        self._states.clear()

    def close(self) -> None:
        self._closed = True
        self._states.clear()
