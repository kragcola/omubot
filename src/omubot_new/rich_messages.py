"""Bounded local rendering contract for N5 rich message segments.

This module intentionally has no network, filesystem, model, or resolver
dependency.  The implementation is introduced after the RED tests establish
the local structure and budget contract.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")


@dataclass(frozen=True, slots=True)
class ImageRef:
    """Opaque request-local image reference; this module never dereferences it."""

    value: str
    media_type: str = "image/*"

    def __post_init__(self) -> None:
        _require_text(self.value, "image ref")
        _require_text(self.media_type, "image media type")


@dataclass(frozen=True, slots=True)
class TextSegment:
    text: str

    def __post_init__(self) -> None:
        _require_text(self.text, "text")


@dataclass(frozen=True, slots=True)
class FaceSegment:
    face_id: str

    def __post_init__(self) -> None:
        _require_text(self.face_id, "face id")


@dataclass(frozen=True, slots=True)
class AtSegment:
    """Exact authenticated mention target, retained separately from its text projection."""

    target_id: str

    def __post_init__(self) -> None:
        _require_text(self.target_id, "at target")


@dataclass(frozen=True, slots=True)
class ImageSegment:
    marker: str = "图片"
    ref: ImageRef | None = None
    is_sticker: bool = False
    is_flash: bool = False

    def __post_init__(self) -> None:
        _require_text(self.marker, "image marker")
        if type(self.is_sticker) is not bool or type(self.is_flash) is not bool:
            raise TypeError("image classification must be boolean")


@dataclass(frozen=True, slots=True)
class VideoRef:
    """Untrusted finite card metadata, never a fetch or upload authority."""

    platform: Literal["bilibili", "youtube"]
    video_id: str
    url: str
    title: str
    reference_kind: Literal["video", "episode", "season", "short", "qqdoc", "card_title"] = "video"


@dataclass(frozen=True, slots=True)
class JsonSegment:
    summary: str = ""
    video_refs: tuple[VideoRef, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _require_text(self.summary, "JSON summary")


@dataclass(frozen=True, slots=True)
class RichMessage:
    sender_id: str
    sender_name: str
    segments: tuple[Segment, ...]

    def __post_init__(self) -> None:
        _require_text(self.sender_id, "sender id")
        _require_text(self.sender_name, "sender name")
        if type(self.segments) is not tuple:
            raise TypeError("message segments must be a tuple")


@dataclass(frozen=True, slots=True)
class ReplySegment:
    message_id: str
    message: RichMessage | None = None

    def __post_init__(self) -> None:
        _require_text(self.message_id, "reply message id")


@dataclass(frozen=True, slots=True)
class ForwardNode:
    sender_id: str
    sender_name: str
    segments: tuple[Segment, ...]

    def __post_init__(self) -> None:
        _require_text(self.sender_id, "forward sender id")
        _require_text(self.sender_name, "forward sender name")
        if type(self.segments) is not tuple:
            raise TypeError("forward segments must be a tuple")


@dataclass(frozen=True, slots=True)
class ForwardSegment:
    forward_id: str = ""
    nodes: tuple[ForwardNode, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.forward_id, "forward id")
        if type(self.nodes) is not tuple:
            raise TypeError("forward nodes must be a tuple")


type Segment = (
    TextSegment
    | FaceSegment
    | AtSegment
    | ImageSegment
    | JsonSegment
    | ReplySegment
    | ForwardSegment
)

# Defaults match the first legacy bounded-rendering contract; callers may make
# any budget smaller, but never bypass these process-local hard ceilings.
HARD_MAX_DEPTH = 16
HARD_MAX_NODES = 512
HARD_MAX_SEGMENTS = 4096
HARD_MAX_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class RenderLimits:
    max_depth: int = 4
    max_nodes: int = 100
    max_segments: int = 1000
    max_chars: int = 2000

    def __post_init__(self) -> None:
        ceilings = {
            "max_depth": HARD_MAX_DEPTH,
            "max_nodes": HARD_MAX_NODES,
            "max_segments": HARD_MAX_SEGMENTS,
            "max_chars": HARD_MAX_CHARS,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or value < 0 or value > ceiling:
                raise ValueError(f"{name} must be between zero and {ceiling}")


@dataclass(frozen=True, slots=True)
class RenderedRichMessage:
    text: str
    image_refs: tuple[ImageRef, ...] = ()
    truncated: bool = False


_TRUNCATED = "«内容已截断»"
_BROKEN = "«富消息不可用»"


@dataclass
class _RenderState:
    limits: RenderLimits
    chunks: list[str]
    image_refs: list[ImageRef]
    chars: int = 0
    nodes: int = 0
    segments: int = 0
    truncated: bool = False
    stopped: bool = False
    reply_path: set[str] | None = None
    forward_path: set[str] | None = None

    def __post_init__(self) -> None:
        if self.reply_path is None:
            self.reply_path = set()
        if self.forward_path is None:
            self.forward_path = set()

    def append(self, value: str) -> bool:
        if self.stopped or not value:
            return False
        remaining = self.limits.max_chars - self.chars
        if remaining <= 0:
            self.truncated = True
            self.stopped = True
            return False
        if len(value) <= remaining:
            self.chunks.append(value)
            self.chars += len(value)
            return True
        if remaining <= len(_TRUNCATED):
            self.chunks.append(_TRUNCATED[:remaining])
        else:
            self.chunks.append(value[: remaining - len(_TRUNCATED)] + _TRUNCATED)
        self.chars = self.limits.max_chars
        self.truncated = True
        self.stopped = True
        return False

    def limit(self, marker: str = _TRUNCATED) -> None:
        self.truncated = True
        self.append(marker)

    def take_node(self) -> bool:
        if self.nodes >= self.limits.max_nodes:
            self.limit("«内容已截断（节点）»")
            return False
        self.nodes += 1
        return True

    def take_segment(self) -> bool:
        if self.segments >= self.limits.max_segments:
            self.limit("«内容已截断（段数）»")
            return False
        self.segments += 1
        return True


def render(
    segments: tuple[Segment, ...],
    *,
    limits: RenderLimits | None = None,
) -> RenderedRichMessage:
    """Render local structure without resolving, downloading, or sending anything."""

    if type(segments) is not tuple:
        raise TypeError("segments must be a tuple")
    state = _RenderState(
        limits=limits or RenderLimits(),
        chunks=[],
        image_refs=[],
    )
    _render_segments(state, segments, depth=0)
    return RenderedRichMessage(
        text="".join(state.chunks),
        image_refs=tuple(state.image_refs),
        truncated=state.truncated,
    )


def _render_segments(state: _RenderState, segments: tuple[Segment, ...], *, depth: int) -> None:
    for segment in segments:
        if state.stopped:
            return
        if not state.take_segment():
            return
        try:
            _render_segment(state, segment, depth=depth)
        except asyncio.CancelledError:
            raise
        except Exception:
            state.append(_BROKEN)


def _render_segment(state: _RenderState, segment: Segment, *, depth: int) -> None:
    if isinstance(segment, TextSegment):
        state.append(segment.text)
    elif isinstance(segment, FaceSegment):
        state.append(f"«表情 {segment.face_id}»")
    elif isinstance(segment, AtSegment):
        state.append("@" + segment.target_id)
    elif isinstance(segment, ImageSegment):
        rendered = state.append(f"«{segment.marker or '图片'}»")
        # Only direct segments belong to this render call's current owner.
        # Nested quoted/forwarded images stay readable but cannot become the
        # caller's current visual evidence.
        if rendered and depth == 0 and segment.ref is not None:
            state.image_refs.append(segment.ref)
    elif isinstance(segment, JsonSegment):
        if segment.summary:
            state.append(f"[卡片: {segment.summary}]")
        else:
            state.append("«卡片»")
    elif isinstance(segment, ReplySegment):
        _render_reply(state, segment, depth=depth)
    else:
        _render_forward(state, segment, depth=depth)


def _render_reply(state: _RenderState, segment: ReplySegment, *, depth: int) -> None:
    if not segment.message_id:
        state.append("«引用消息»")
        return
    if segment.message is None:
        state.append(f"«引用消息 #{segment.message_id}（未展开）»")
        return
    if depth >= state.limits.max_depth:
        state.limit("«引用消息（深度已截断）»")
        return
    assert state.reply_path is not None
    if segment.message_id in state.reply_path:
        state.append(f"«引用消息 #{segment.message_id}（循环）»")
        return
    if not state.take_node():
        return
    state.reply_path.add(segment.message_id)
    try:
        message = segment.message
        state.append(
            f"[引用消息 sender_id={message.sender_id} sender_name={message.sender_name}]\n"
        )
        _render_segments(state, message.segments, depth=depth + 1)
        state.append("\n[/引用消息] ")
    finally:
        state.reply_path.discard(segment.message_id)


def _render_forward(state: _RenderState, segment: ForwardSegment, *, depth: int) -> None:
    if depth >= state.limits.max_depth:
        state.limit("«合并转发消息（深度已截断）»")
        return
    if not segment.nodes:
        if segment.forward_id:
            state.append(f"«合并转发消息 #{segment.forward_id}（未展开）»")
        else:
            state.append("«合并转发消息（未展开）»")
        return
    assert state.forward_path is not None
    if segment.forward_id and segment.forward_id in state.forward_path:
        state.append(f"«合并转发消息 #{segment.forward_id}（循环）»")
        return
    if not state.take_node():
        return
    if segment.forward_id:
        state.forward_path.add(segment.forward_id)
    try:
        state.append("«合并转发消息»\n")
        for node in segment.nodes:
            if state.stopped:
                return
            try:
                if not state.take_node():
                    continue
                state.append(f"{node.sender_name}({node.sender_id}): ")
                _render_segments(state, node.segments, depth=depth + 1)
                state.append("\n")
            except asyncio.CancelledError:
                raise
            except Exception:
                state.append(_BROKEN)
    finally:
        if segment.forward_id:
            state.forward_path.discard(segment.forward_id)
