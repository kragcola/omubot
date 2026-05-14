"""Group chat unified timeline: append-only turns + pending buffer model."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, overload

from services.memory.types import Content, ContentBlock, TextBlock

if TYPE_CHECKING:
    from services.memory.message_log import MessageLog

_MAX_GROUPS = 200

AssistantVisibleState = Literal["pending", "first_segment_sent", "complete", "failed"]


class TimelineMessage(TypedDict):
    role: Literal["user", "assistant"]
    speaker: str | None  # user → "昵称(QQ号)", assistant → None
    content: Content
    message_id: NotRequired[int | None]  # QQ message_id for reply references
    trigger_reason: NotRequired[str]  # 触发原因标记（如 "有人@了你"），合并时包裹为 «触发原因: ...»
    trigger_target: NotRequired[str]  # 触发者 QQ 号，合并时渲染为 «来自 QQ=xxx»
    pending_state: NotRequired[Literal["active", "background"]]
    skip_reason: NotRequired[str]
    skipped_at: NotRequired[float]


# ------------------------------------------------------------------
# Module-level merge helpers (used by other modules)
# ------------------------------------------------------------------


def merge_user_contents(batch: list[TimelineMessage]) -> Content:
    """Merge consecutive user messages into a single content value.

    Returns str if all messages are plain text (backward compatible).
    Returns list[ContentBlock] if any message contains image blocks.
    """
    has_blocks = any(isinstance(m["content"], list) for m in batch)

    if not has_blocks:
        lines: list[str] = []
        for m in batch:
            assert isinstance(m["content"], str)
            background_prefix = _background_prefix(m)
            trigger = m.get("trigger_reason")
            mid = m.get("message_id")
            if trigger:
                # Trigger marker: metadata only, no «msg:mid» tag
                parts = [f"触发原因: {trigger}"]
                target = m.get("trigger_target", "")
                if target:
                    parts.append(f"来自 QQ={target}")
                if mid is not None:
                    parts.append(f"消息ID={mid}")
                lines.append(f"{background_prefix}«{' | '.join(parts)}»")
            else:
                # Regular message
                tag = f"«msg:{mid}» " if mid is not None else ""
                if m["speaker"] is not None:
                    lines.append(f"{background_prefix}{tag}{m['speaker']}: {m['content']}")
                else:
                    line = f"{background_prefix}{tag}{m['content']}" if tag else f"{background_prefix}{m['content']}"
                    lines.append(line)
        return "\n".join(lines)

    merged: list[ContentBlock] = []
    for m in batch:
        background_prefix = _background_prefix(m)
        trigger = m.get("trigger_reason")
        mid = m.get("message_id")
        if trigger:
            # Trigger marker: metadata only, no «msg:mid» tag
            parts = [f"触发原因: {trigger}"]
            target = m.get("trigger_target", "")
            if target:
                parts.append(f"来自 QQ={target}")
            if mid is not None:
                parts.append(f"消息ID={mid}")
            merged.append(TextBlock(type="text", text=f"{background_prefix}«{' | '.join(parts)}»"))
        else:
            # Regular message
            tag = f"«msg:{mid}» " if mid is not None else ""
            if m["speaker"] is not None:
                prefix = f"{background_prefix}{tag}{m['speaker']}: "
            else:
                prefix = f"{background_prefix}{tag}"
            if isinstance(m["content"], str):
                merged.append(TextBlock(type="text", text=f"{prefix}{m['content']}"))
            else:
                if prefix and (not m["content"] or m["content"][0]["type"] != "text"):
                    merged.append(TextBlock(type="text", text=prefix.rstrip()))
                for j, block in enumerate(m["content"]):
                    if j == 0 and block["type"] == "text" and prefix:
                        merged.append(TextBlock(type="text", text=f"{prefix}{block['text']}"))
                    else:
                        merged.append(block)
    return merged


def _is_active_pending(msg: TimelineMessage) -> bool:
    return msg.get("pending_state", "active") == "active"


def _background_prefix(msg: TimelineMessage) -> str:
    if _is_active_pending(msg):
        return ""
    reason = msg.get("skip_reason", "").strip()
    if reason:
        return f"«已跳过，仅作历史背景: {reason}» "
    return "«已跳过，仅作历史背景» "


# ------------------------------------------------------------------
# TurnLog — append-only Sequence[dict[str, Any]]
# ------------------------------------------------------------------


class TurnLog(Sequence[dict[str, Any]]):
    """Append-only container for finalized Anthropic message turns."""

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: list[dict[str, Any]] = []

    # -- Sequence protocol --

    @overload
    def __getitem__(self, index: int) -> dict[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> list[dict[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        return self._data[index]

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._data)

    # -- Mutators (append-only, no arbitrary mutation) --

    def append(self, turn: dict[str, Any]) -> None:
        self._data.append(turn)

    def compact_truncate(self, count: int) -> None:
        """Remove the first `count` turns."""
        self._data = self._data[count:]

    def reset(self) -> None:
        """Clear all turns."""
        self._data.clear()


# ------------------------------------------------------------------
# _GroupState
# ------------------------------------------------------------------


class _GroupState:
    __slots__ = (
        "last_cached_msg_index",
        "last_input_tokens",
        "next_assistant_turn_id",
        "pending",
        "summary",
        "turn_times",
        "turns",
    )

    def __init__(self) -> None:
        self.turns = TurnLog()
        self.turn_times: list[float] = []
        self.pending: list[TimelineMessage] = []
        self.summary: str = ""
        self.last_input_tokens: int = 0
        self.last_cached_msg_index: int = 0
        self.next_assistant_turn_id: int = 0


# ------------------------------------------------------------------
# GroupTimeline — public API
# ------------------------------------------------------------------


class GroupTimeline:
    """Group chat unified timeline with append-only turns and pending buffer."""

    def __init__(self, message_log: MessageLog | None = None) -> None:
        self._store: dict[str, _GroupState] = {}
        self._message_log = message_log

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(self, group_id: str) -> _GroupState:
        if group_id not in self._store:
            if len(self._store) >= _MAX_GROUPS:
                oldest = next(iter(self._store))
                del self._store[oldest]
            self._store[group_id] = _GroupState()
        return self._store[group_id]

    # ------------------------------------------------------------------
    # Content extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _content_to_text(content: Content) -> str:
        """Extract plain text from Content for SQLite content_text column."""
        if isinstance(content, str):
            return content
        return " ".join(b["text"] for b in content if b["type"] == "text")

    @staticmethod
    def _content_to_json(content: Content) -> str:
        """Serialize Content to JSON for SQLite content_json column."""
        return json.dumps(content, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Message ingestion
    # ------------------------------------------------------------------

    def add(
        self,
        group_id: str,
        *,
        role: Literal["user", "assistant"],
        content: Content,
        speaker: str | None = None,
        message_id: int | None = None,
        flush_pending_count: int | None = None,
        assistant_visible_state: AssistantVisibleState = "complete",
    ) -> str | None:
        """Add a message to the timeline.

        role="user"  → appends to pending buffer.
        role="assistant" → flushes pending into a merged user turn,
                           then appends both user and assistant turns to turns.
        """
        state = self._get_or_create(group_id)
        assistant_turn_id: str | None = None

        if role == "user":
            msg = TimelineMessage(role="user", speaker=speaker, content=content)
            if message_id is not None:
                msg["message_id"] = message_id
            state.pending.append(msg)
        else:
            # Flush pending user messages into a merged user turn
            now = time.time()
            pending_to_flush = state.pending
            if flush_pending_count is not None:
                pending_to_flush = state.pending[:max(0, flush_pending_count)]
            if pending_to_flush:
                user_content = merge_user_contents(pending_to_flush)
                state.turns.append({"role": "user", "content": user_content})
                state.turn_times.append(now)
                if flush_pending_count is None:
                    state.pending.clear()
                else:
                    del state.pending[:len(pending_to_flush)]

            # Append assistant turn
            state.next_assistant_turn_id += 1
            assistant_turn_id = f"{group_id}:{state.next_assistant_turn_id}"
            state.turns.append({
                "role": "assistant",
                "content": content,
                "turn_id": assistant_turn_id,
                "visible_state": assistant_visible_state,
                "visible_updated_at": now,
            })
            state.turn_times.append(now)

        # Fire-and-forget SQLite write
        if self._message_log:
            asyncio.create_task(self._message_log.record(  # noqa: RUF006
                group_id=group_id,
                role=role,
                speaker=speaker,
                content_text=self._content_to_text(content),
                content_json=self._content_to_json(content),
                message_id=message_id,
            ))
        return assistant_turn_id

    def add_pending_trigger(
        self, group_id: str, *, reason: str,
        message_id: int | None = None,
        target_user_id: str = "",
    ) -> None:
        """Append a trigger-reason marker to the pending buffer.

        This marker is merged into the user turn when the assistant replies,
        making the trigger reason visible to the LLM and persistent across
        compactions.  «触发原因: ...»  tags distinguish it from user text.
        """
        state = self._get_or_create(group_id)
        msg: TimelineMessage = {
            "role": "user",
            "speaker": None,
            "content": "",
            "trigger_reason": reason,
        }
        if message_id is not None:
            msg["message_id"] = message_id
        if target_user_id:
            msg["trigger_target"] = target_user_id
        state.pending.insert(0, msg)

    # ------------------------------------------------------------------
    # Read accessors
    # ------------------------------------------------------------------

    def get_turns(self, group_id: str) -> Sequence[dict[str, Any]]:
        """Return read-only view of finalized turns."""
        if group_id not in self._store:
            return TurnLog()
        return self._store[group_id].turns

    def get_turns_for_prompt(self, group_id: str) -> list[dict[str, Any]]:
        """Return Anthropic-compatible turns, stripping timeline metadata.

        Incomplete assistant turns are represented as a short state marker
        rather than exposing a full response that may not be visible in chat yet.
        """
        turns = self.get_turns(group_id)
        prompt_turns: list[dict[str, Any]] = []
        for turn in turns:
            role = turn.get("role")
            content = turn.get("content", "")
            if role == "assistant":
                visible_state = turn.get("visible_state", "complete")
                if visible_state != "complete":
                    content = f"«上一轮回复仍在发送中，visible_state={visible_state}，暂不作为完整上下文。»"
            prompt_turns.append({"role": role, "content": content})
        return prompt_turns

    def mark_latest_assistant_visible_state(
        self,
        group_id: str,
        visible_state: AssistantVisibleState,
    ) -> bool:
        """Update the newest assistant turn's visible state."""
        return self.mark_assistant_visible_state(group_id, None, visible_state)

    def mark_assistant_visible_state(
        self,
        group_id: str,
        turn_id: str | None,
        visible_state: AssistantVisibleState,
    ) -> bool:
        """Update an assistant turn's visible state.

        When ``turn_id`` is None, the newest assistant turn is updated for
        backward compatibility.
        """
        state = self._store.get(group_id)
        if state is None:
            return False
        for turn in reversed(list(state.turns)):
            if turn.get("role") == "assistant" and (turn_id is None or turn.get("turn_id") == turn_id):
                turn["visible_state"] = visible_state
                turn["visible_updated_at"] = time.time()
                return True
        return False

    def clamp_compact_split_to_visible(self, group_id: str, split: int) -> int:
        """Avoid compacting assistant turns that are not fully visible yet."""
        state = self._store.get(group_id)
        if state is None or split <= 0:
            return 0
        split = min(split, len(state.turns))
        for idx, turn in enumerate(state.turns[:split]):
            if turn.get("role") == "assistant" and turn.get("visible_state", "complete") != "complete":
                if idx > 0 and state.turns[idx - 1].get("role") == "user":
                    return idx - 1
                return idx
        return split

    def get_pending(self, group_id: str) -> list[TimelineMessage]:
        """Return a copy of the pending user message buffer."""
        if group_id not in self._store:
            return []
        return list(self._store[group_id].pending)

    def pending_len(self, group_id: str) -> int:
        """Return the number of pending messages currently buffered."""
        if group_id not in self._store:
            return 0
        return len(self._store[group_id].pending)

    def get_active_pending(self, group_id: str) -> list[TimelineMessage]:
        """Return pending messages that should participate in the next reply."""
        if group_id not in self._store:
            return []
        return [msg for msg in self._store[group_id].pending if _is_active_pending(msg)]

    def deactivate_pending(self, group_id: str, reason: str) -> int:
        """Mark active pending messages as background history.

        The messages remain in the pending buffer and are flushed into turns
        when the assistant next replies, but they are not used as the current
        reply target.
        """
        state = self._store.get(group_id)
        if state is None:
            return 0
        changed = 0
        now = time.time()
        for msg in state.pending:
            if not _is_active_pending(msg):
                continue
            msg["pending_state"] = "background"
            msg["skip_reason"] = reason
            msg["skipped_at"] = now
            changed += 1
        return changed

    def deactivate_latest_pending(self, group_id: str, reason: str, *, count: int = 1) -> int:
        """Mark the newest active pending messages as background history."""
        state = self._store.get(group_id)
        if state is None or count <= 0:
            return 0
        changed = 0
        now = time.time()
        for msg in reversed(state.pending):
            if not _is_active_pending(msg):
                continue
            msg["pending_state"] = "background"
            msg["skip_reason"] = reason
            msg["skipped_at"] = now
            changed += 1
            if changed >= count:
                break
        return changed

    def deactivate_pending_except_latest_active(self, group_id: str, reason: str) -> int:
        """Mark active pending messages before the latest active one as background."""
        state = self._store.get(group_id)
        if state is None:
            return 0
        latest_active_index: int | None = None
        for idx in range(len(state.pending) - 1, -1, -1):
            if _is_active_pending(state.pending[idx]):
                latest_active_index = idx
                break
        if latest_active_index is None:
            return 0

        changed = 0
        now = time.time()
        for msg in state.pending[:latest_active_index]:
            if not _is_active_pending(msg):
                continue
            msg["pending_state"] = "background"
            msg["skip_reason"] = reason
            msg["skipped_at"] = now
            changed += 1
        return changed

    def get_turn_time(self, group_id: str, index: int) -> float:
        """Return the timestamp of the turn at the given index."""
        if group_id not in self._store:
            return 0.0
        state = self._store[group_id]
        if 0 <= index < len(state.turn_times):
            return state.turn_times[index]
        return 0.0

    def get_recent_text(self, group_id: str, last_n: int = 3) -> str:
        """Return concatenated text from the most recent N turns."""
        turns = self.get_turns(group_id)
        recent = list(turns)[-last_n:] if len(turns) > last_n else list(turns)
        parts: list[str] = []
        for t in recent:
            c = t.get("content", "")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(b.get("text", ""))
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Summary & token management
    # ------------------------------------------------------------------

    def get_summary(self, group_id: str) -> str:
        if group_id not in self._store:
            return ""
        return self._store[group_id].summary

    def set_input_tokens(self, group_id: str, tokens: int) -> None:
        """Record input token count from the latest API call."""
        state = self._get_or_create(group_id)
        state.last_input_tokens = tokens

    def get_input_tokens(self, group_id: str) -> int:
        if group_id not in self._store:
            return 0
        return self._store[group_id].last_input_tokens

    def get_cached_msg_index(self, group_id: str) -> int:
        """Return the Anthropic messages index cached by the previous API call."""
        if group_id not in self._store:
            return 0
        return self._store[group_id].last_cached_msg_index

    def set_cached_msg_index(self, group_id: str, index: int) -> None:
        """Store which Anthropic messages index to use as cache breakpoint next call."""
        state = self._get_or_create(group_id)
        state.last_cached_msg_index = index

    def needs_compact(self, group_id: str, max_tokens: int, ratio: float) -> bool:
        """Check if compaction is needed: input tokens exceed threshold."""
        return self.get_input_tokens(group_id) > max_tokens * ratio

    # ------------------------------------------------------------------
    # Compaction & cleanup
    # ------------------------------------------------------------------

    def compact(self, group_id: str, split: int, new_summary: str) -> None:
        """Truncate the first `split` turns and update summary."""
        if group_id not in self._store:
            return
        state = self._store[group_id]
        state.turns.compact_truncate(split)
        state.turn_times = state.turn_times[split:]
        state.summary = new_summary
        state.last_input_tokens = 0
        state.last_cached_msg_index = 0

    def drop_oldest(self, group_id: str, count: int) -> None:
        """Drop the oldest `count` turns. For micro compact."""
        state = self._store.get(group_id)
        if state is None:
            return
        state.turns.compact_truncate(count)
        state.turn_times = state.turn_times[count:]

    def clear(self, group_id: str) -> None:
        """Clear turns + pending + turn_times, but preserve summary."""
        if group_id not in self._store:
            return
        state = self._store[group_id]
        state.turns.reset()
        state.turn_times.clear()
        state.pending.clear()
