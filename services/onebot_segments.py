"""Bounded rendering for raw OneBot rich-message segments.

The renderer owns no network or model clients. Callers may inject resolvers for
active paths; history and suppressed-group ingestion can remain local-only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from kernel.qq_face import face_to_text
from services.json_card import extract_json_card_text
from services.memory.types import ImageRefBlock

MessageResolver = Callable[[str], Awaitable[object | None]]
ForwardRenderer = Callable[[str], Awaitable[str]]
ImageRenderer = Callable[[Mapping[str, Any], int | None], Awaitable[tuple[str, ImageRefBlock | None]]]

_TRUNCATED = "«内容已截断»"
_UNAVAILABLE = "无法获取"


@dataclass(frozen=True)
class RichRenderLimits:
    max_depth: int = 4
    max_nodes: int = 100
    max_segments: int = 1000
    max_chars: int = 2000
    max_fetches: int = 8
    resolve_timeout_s: float = 3.0
    image_timeout_s: float | None = None
    max_images: int = 5


@dataclass
class RichRenderResult:
    text: str
    images: list[ImageRefBlock] = field(default_factory=list)
    truncated: bool = False


@dataclass
class _RenderState:
    limits: RichRenderLimits
    chunks: list[str] = field(default_factory=list)
    images: list[ImageRefBlock] = field(default_factory=list)
    char_count: int = 0
    node_count: int = 0
    segment_count: int = 0
    fetch_count: int = 0
    image_count: int = 0
    reserved_chars: int = 0
    stopped: bool = False
    active_reply_ids: set[str] = field(default_factory=set)
    active_forward_ids: set[str] = field(default_factory=set)
    reply_cache: dict[str, object | None] = field(default_factory=dict)
    forward_cache: dict[str, str] = field(default_factory=dict)

    def append(self, value: object) -> None:
        if self.stopped:
            return
        text = str(value or "")
        if not text:
            return
        remaining = self.limits.max_chars - self.char_count - self.reserved_chars
        if len(text) <= remaining:
            self.chunks.append(text)
            self.char_count += len(text)
            return
        marker_room = min(len(_TRUNCATED), max(0, remaining))
        body_room = max(0, remaining - marker_room)
        if body_room:
            self.chunks.append(text[:body_room])
        if marker_room:
            self.chunks.append(_TRUNCATED[:marker_room])
        self.char_count = self.limits.max_chars
        self.stopped = True

    def stop_with_truncation(self) -> None:
        self.append(_TRUNCATED)
        self.stopped = True

    def reserve(self, value: str) -> bool:
        available = self.limits.max_chars - self.char_count - self.reserved_chars
        if len(value) > available:
            return False
        self.reserved_chars += len(value)
        return True

    def append_reserved(self, value: str) -> None:
        self.reserved_chars = max(0, self.reserved_chars - len(value))
        self.chunks.append(value)
        self.char_count += len(value)

    def can_fit(self, *values: str) -> bool:
        available = self.limits.max_chars - self.char_count - self.reserved_chars
        return sum(len(value) for value in values) <= available


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        data = value.get("data")
        if name in {"message", "message_id", "sender"} and isinstance(data, Mapping) and name in data:
            return data.get(name, default)
        return value.get(name, default)
    return getattr(value, name, default)


def _segment_parts(segment: object) -> tuple[str, Mapping[str, Any]]:
    if isinstance(segment, Mapping):
        seg_type = str(segment.get("type", "") or "")
        data = segment.get("data", {})
    else:
        seg_type = str(getattr(segment, "type", "") or "")
        data = getattr(segment, "data", {})
    return seg_type, data if isinstance(data, Mapping) else {}


def _message_segments(message: object) -> Sequence[object]:
    if isinstance(message, Sequence) and not isinstance(message, (str, bytes, bytearray)):
        return message
    try:
        return list(message)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ()


def _sender_parts(sender: object, self_id: str) -> tuple[str, str]:
    uid = str(_field(sender, "user_id", "") or "")
    nickname = str(_field(sender, "nickname", "") or _field(sender, "card", "") or uid)
    return uid, "我" if self_id and uid == str(self_id) else nickname


def _image_summary(data: Mapping[str, Any]) -> str:
    summary = str(data.get("summary", "") or "").strip("[]")
    if summary:
        return f"«{summary}»"
    return "«动画表情»" if str(data.get("sub_type", "0")) == "1" else "«图片»"


class OneBotSegmentRenderer:
    def __init__(
        self,
        *,
        self_id: str = "",
        reply_resolver: MessageResolver | None = None,
        forward_renderer: ForwardRenderer | None = None,
        image_renderer: ImageRenderer | None = None,
        limits: RichRenderLimits | None = None,
    ) -> None:
        self._self_id = str(self_id)
        self._reply_resolver = reply_resolver
        self._forward_renderer = forward_renderer
        self._image_renderer = image_renderer
        self._state = _RenderState(limits=limits or RichRenderLimits())

    async def render(
        self,
        segments: Sequence[object],
        *,
        reply: object | None = None,
    ) -> RichRenderResult:
        if reply is not None:
            reply_id = str(_field(reply, "message_id", "") or "")
            if reply_id:
                self._state.active_reply_ids.add(reply_id)
            try:
                await self._render_quote(reply, depth=0)
            finally:
                if reply_id:
                    self._state.active_reply_ids.discard(reply_id)
        await self._render_segments(segments, depth=0, source_message_id=None)
        return RichRenderResult(
            text="".join(self._state.chunks),
            images=list(self._state.images),
            truncated=self._state.stopped,
        )

    async def _render_segments(
        self,
        segments: Sequence[object],
        *,
        depth: int,
        source_message_id: int | None,
    ) -> None:
        for segment in segments:
            if self._state.stopped:
                return
            if self._state.segment_count >= self._state.limits.max_segments:
                self._state.stop_with_truncation()
                return
            self._state.segment_count += 1
            try:
                seg_type, data = _segment_parts(segment)
                await self._render_segment(
                    seg_type,
                    data,
                    depth=depth,
                    source_message_id=source_message_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._state.append("«富消息不可用»")

    async def _render_segment(
        self,
        seg_type: str,
        data: Mapping[str, Any],
        *,
        depth: int,
        source_message_id: int | None,
    ) -> None:
        if seg_type == "text":
            self._state.append(data.get("text", ""))
            return
        if seg_type == "at":
            qq = str(data.get("qq", "") or "")
            self._state.append("@我" if self._self_id and qq == self._self_id else f"@{qq}")
            return
        if seg_type == "face":
            try:
                self._state.append(face_to_text(int(data.get("id", ""))))
            except (TypeError, ValueError):
                self._state.append("«表情»")
            return
        if seg_type == "image":
            await self._render_image(data, source_message_id)
            return
        if seg_type == "json":
            card = extract_json_card_text(str(data.get("data", "") or ""))
            self._state.append(f"[卡片: {card}]" if card else "«卡片»")
            return
        if seg_type == "file":
            self._state.append(f"«文件: {data.get('name', '未知文件')}»")
            return
        if seg_type == "reply":
            await self._render_reply_id(str(data.get("id", "") or ""), depth=depth)
            return
        if seg_type == "forward":
            await self._render_forward(data, depth=depth)
            return
        if seg_type:
            self._state.append(f"«{seg_type}»")

    async def _render_image(self, data: Mapping[str, Any], source_message_id: int | None) -> None:
        if self._image_renderer is None or self._state.image_count >= self._state.limits.max_images:
            self._state.append(_image_summary(data))
            return
        self._state.image_count += 1
        try:
            timeout_s = self._state.limits.image_timeout_s
            if timeout_s is None:
                timeout_s = self._state.limits.resolve_timeout_s
            text, image_ref = await asyncio.wait_for(
                self._image_renderer(data, source_message_id),
                timeout=timeout_s,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._state.append(_image_summary(data))
            return
        self._state.append(text or _image_summary(data))
        if image_ref is not None:
            self._state.images.append(image_ref)

    async def _resolve_reply(self, message_id: str) -> object | None:
        if message_id in self._state.reply_cache:
            return self._state.reply_cache[message_id]
        if self._reply_resolver is None or self._state.fetch_count >= self._state.limits.max_fetches:
            return None
        self._state.fetch_count += 1
        try:
            result = await asyncio.wait_for(
                self._reply_resolver(message_id),
                timeout=self._state.limits.resolve_timeout_s,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = None
        self._state.reply_cache[message_id] = result
        return result

    async def _render_reply_id(self, message_id: str, *, depth: int) -> None:
        if not message_id:
            self._state.append("«引用消息»")
            return
        if message_id in self._state.active_reply_ids:
            self._state.append(f"«引用消息 #{message_id}（循环）»")
            return
        if depth >= self._state.limits.max_depth:
            self._state.append(f"«引用消息 #{message_id}（已截断）»")
            return
        resolved = await self._resolve_reply(message_id)
        if resolved is None:
            suffix = _UNAVAILABLE if self._reply_resolver is not None else "未展开"
            self._state.append(f"«引用消息 #{message_id}（{suffix}）»")
            return
        self._state.active_reply_ids.add(message_id)
        try:
            await self._render_quote(resolved, depth=depth + 1)
        finally:
            self._state.active_reply_ids.discard(message_id)

    async def _render_quote(self, message_record: object, *, depth: int) -> None:
        if self._state.node_count >= self._state.limits.max_nodes:
            self._state.stop_with_truncation()
            return
        if depth > self._state.limits.max_depth:
            self._state.stop_with_truncation()
            return
        self._state.node_count += 1
        sender = _field(message_record, "sender", {})
        uid, name = _sender_parts(sender, self._self_id)
        raw_message_id = _field(message_record, "message_id", None)
        try:
            source_message_id = int(str(raw_message_id)) if raw_message_id is not None else None
        except (TypeError, ValueError):
            source_message_id = None
        message = _field(message_record, "message", ())
        segments = _message_segments(message)
        opening = f"[QUOTED_MSG sender_id={uid} sender_name={name}]\n"
        closing = "\n[/QUOTED_MSG] "
        if not self._state.can_fit(opening, closing) or not self._state.reserve(closing):
            self._state.stop_with_truncation()
            return
        self._state.append(opening)
        try:
            await self._render_segments(
                segments,
                depth=depth,
                source_message_id=source_message_id,
            )
        finally:
            self._state.append_reserved(closing)

    async def _render_forward(self, data: Mapping[str, Any], *, depth: int) -> None:
        if depth >= self._state.limits.max_depth:
            self._state.append("«合并转发消息（已截断）»")
            return
        if "content" in data:
            content = data["content"]
            if not isinstance(content, list) or not content:
                self._state.append("«合并转发消息（空）»")
                return
            self._state.append("«合并转发消息»\n")
            await self._render_forward_nodes(content, depth=depth + 1)
            return
        forward_id = str(data.get("id", "") or "")
        if not forward_id:
            self._state.append("«合并转发消息（无法获取内容）»")
            return
        if forward_id in self._state.active_forward_ids:
            self._state.append(f"«合并转发消息 #{forward_id}（循环）»")
            return
        if self._forward_renderer is None:
            self._state.append(f"«合并转发消息 #{forward_id}（未展开）»")
            return
        if forward_id in self._state.forward_cache:
            self._state.append(self._state.forward_cache[forward_id])
            return
        if self._state.fetch_count >= self._state.limits.max_fetches:
            self._state.append(f"«合并转发消息 #{forward_id}（已截断）»")
            return
        self._state.fetch_count += 1
        self._state.active_forward_ids.add(forward_id)
        try:
            try:
                rendered = await asyncio.wait_for(
                    self._forward_renderer(forward_id),
                    timeout=self._state.limits.resolve_timeout_s,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                rendered = "«合并转发消息（无法获取内容）»"
            self._state.forward_cache[forward_id] = rendered
            self._state.append(rendered)
        finally:
            self._state.active_forward_ids.discard(forward_id)

    async def _render_forward_nodes(self, nodes: list[object], *, depth: int) -> None:
        for node in nodes:
            if self._state.stopped:
                return
            if self._state.node_count >= self._state.limits.max_nodes:
                self._state.stop_with_truncation()
                return
            self._state.node_count += 1
            if not isinstance(node, Mapping):
                continue
            uid, name = _sender_parts(node.get("sender", {}), self._self_id)
            indent = "  " * max(0, depth - 1)
            self._state.append(f"{indent}{name}({uid}): ")
            content = node.get("message", node.get("content", ""))
            if isinstance(content, list):
                await self._render_segments(content, depth=depth, source_message_id=None)
            else:
                self._state.append(content)
            self._state.append("\n")


async def render_onebot_segments(
    segments: Sequence[object],
    *,
    reply: object | None = None,
    self_id: str = "",
    reply_resolver: MessageResolver | None = None,
    forward_renderer: ForwardRenderer | None = None,
    image_renderer: ImageRenderer | None = None,
    limits: RichRenderLimits | None = None,
) -> RichRenderResult:
    renderer = OneBotSegmentRenderer(
        self_id=self_id,
        reply_resolver=reply_resolver,
        forward_renderer=forward_renderer,
        image_renderer=image_renderer,
        limits=limits,
    )
    return await renderer.render(segments, reply=reply)
