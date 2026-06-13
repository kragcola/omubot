"""Topic-block attribution for parallel group conversations (B1).

Groups recent group messages into a small set of concurrent topic blocks
so the scheduler can anchor a probability-fire reply to the block the bot
should actually join — instead of letting the LLM freely latch onto a
stale older topic (the "回旧话题" failure, see
docs/tracking/fix-prob-fire-stale-topic-sticker-2026-05-30.md).

Attribution uses signals already present in the repo, strongest first:
reply-to edge (QQ ground-truth, implements skip-connecting) > @-mention >
same-speaker continuation > lexical similarity fallback > new block.

Pure-CPU, no I/O, no await. Held at scheduler instance level (one tracker
per process, keyed by group_id), NOT inside ``_GroupSlot`` — so the slot
contract is untouched. Disabled by default behind ``topic_block.enabled``.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from services.similarity import NgramSimilarityProvider, SimilarityProvider

# Tunables (overridable via config later; defaults match the design doc §3).
_MAX_BLOCKS = 6  # retain at most N blocks per group (2-4 active in practice)
_STALE_S = 300.0  # a block inactive longer than this is archived (not picked)
_ATTRIB_RECENT_S = 120.0  # window for same-speaker / @-continuation attribution
_SIM_THRESHOLD = 0.4  # lexical-similarity fallback floor for same-block


@dataclass
class TopicBlock:
    """One concurrent conversation thread within a group."""

    block_id: str
    message_ids: list[int] = field(default_factory=list)
    participants: set[str] = field(default_factory=set)
    last_active: float = 0.0
    last_text: str = ""
    bot_involved: bool = False
    at_message_id: int | None = None  # last message that @-addressed someone

    def representative_message_id(self) -> int | None:
        """Block's anchor message: the @-addressed one, else the latest."""
        if self.at_message_id is not None:
            return self.at_message_id
        return self.message_ids[-1] if self.message_ids else None

    def representative_speaker(self) -> str:
        """Best-effort speaker QQ for the anchor (latest participant)."""
        return next(reversed(list(self.participants)), "") if self.participants else ""


class TopicBlockTracker:
    """Per-process tracker; maintains active topic blocks per group."""

    def __init__(self, *, similarity: SimilarityProvider | None = None) -> None:
        self._similarity = similarity or NgramSimilarityProvider()
        self._blocks: dict[str, deque[TopicBlock]] = {}
        self._counter = 0
        self._max_blocks = _MAX_BLOCKS
        self._stale_s = _STALE_S
        self._attrib_recent_s = _ATTRIB_RECENT_S
        self._sim_threshold = _SIM_THRESHOLD

    def configure(
        self,
        *,
        stale_seconds: float | None = None,
        attrib_recent_seconds: float | None = None,
        sim_threshold: float | None = None,
        max_blocks: int | None = None,
    ) -> None:
        """Override tunables from config (no-op for None values)."""
        if stale_seconds is not None:
            self._stale_s = max(1.0, float(stale_seconds))
        if attrib_recent_seconds is not None:
            self._attrib_recent_s = max(1.0, float(attrib_recent_seconds))
        if sim_threshold is not None:
            self._sim_threshold = max(0.0, min(1.0, float(sim_threshold)))
        if max_blocks is not None:
            self._max_blocks = max(1, int(max_blocks))

    def _next_block_id(self) -> str:
        self._counter += 1
        return f"b{self._counter}"

    def _active(self, group_id: str, now: float) -> list[TopicBlock]:
        """Non-stale blocks for a group, newest-active first."""
        blocks = self._blocks.get(group_id)
        if not blocks:
            return []
        return [b for b in blocks if (now - b.last_active) <= self._stale_s]

    def observe(
        self,
        group_id: str,
        *,
        message_id: int | None,
        speaker: str,
        text: str,
        reply_to_sender_id: str = "",
        reply_to_self: bool = False,
        at_targets: tuple[str, ...] = (),
        at_self: bool = False,
        now: float | None = None,
    ) -> TopicBlock:
        """Attribute one message to a topic block (strongest signal first)."""
        now = time.monotonic() if now is None else now
        blocks = self._blocks.setdefault(group_id, deque(maxlen=self._max_blocks))
        active = [b for b in blocks if (now - b.last_active) <= self._stale_s]
        target = self._attribute(active, speaker, text, reply_to_sender_id, reply_to_self, at_targets, now)
        if target is None:
            target = TopicBlock(block_id=self._next_block_id())
            blocks.append(target)
        self._apply(target, message_id, speaker, text, at_targets, at_self, reply_to_self, now)
        return target

    def _attribute(
        self,
        active: list[TopicBlock],
        speaker: str,
        text: str,
        reply_to_sender_id: str,
        reply_to_self: bool,
        at_targets: tuple[str, ...],
        now: float,
    ) -> TopicBlock | None:
        """Find the block this message belongs to, or None to open a new one."""
        # 1. reply-to bot → the bot block (skip-connecting onto our own line).
        if reply_to_self:
            for b in active:
                if b.bot_involved:
                    return b
        # 2. reply-to a known speaker → their block (strongest non-bot edge).
        if reply_to_sender_id:
            for b in active:
                if reply_to_sender_id in b.participants:
                    return b
        # 3. @-mention a block participant, recently active → that block.
        if at_targets:
            for b in active:
                if (now - b.last_active) <= self._attrib_recent_s and any(t in b.participants for t in at_targets):
                    return b
        # 4. same-speaker continuation within the recent window.
        for b in active:
            if (now - b.last_active) <= self._attrib_recent_s and speaker and speaker in b.participants:
                return b
        # 5. lexical-similarity fallback: most similar block above threshold.
        best: TopicBlock | None = None
        best_sim = self._sim_threshold
        for b in active:
            sim = self._similarity.similarity(text, b.last_text)
            if sim >= best_sim:
                best, best_sim = b, sim
        return best  # 6. None → caller opens a new block

    def _apply(
        self,
        block: TopicBlock,
        message_id: int | None,
        speaker: str,
        text: str,
        at_targets: tuple[str, ...],
        at_self: bool,
        reply_to_self: bool,
        now: float,
    ) -> None:
        if message_id is not None:
            block.message_ids.append(message_id)
            if at_targets:
                block.at_message_id = message_id
        if speaker:
            block.participants.add(speaker)
        if text:
            block.last_text = text
        block.last_active = now
        if reply_to_self or at_self:
            block.bot_involved = True

    def mark_bot_involved(self, group_id: str, now: float | None = None) -> None:
        """Flag the most-active block as bot-involved (after bot speaks)."""
        now = time.monotonic() if now is None else now
        block = self.pick_anchor_block(group_id, now, require_bot_involved=False)
        if block is not None:
            block.bot_involved = True

    def pick_block_by_id(self, group_id: str, block_id: str) -> TopicBlock | None:
        """Return the block with this id (any age), or None. Used by the
        scheduler to anchor a per-block fire to its representative message."""
        if not block_id:
            return None
        for b in self._blocks.get(group_id, ()):  # type: ignore[arg-type]
            if b.block_id == block_id:
                return b
        return None

    def pick_anchor_block(
        self,
        group_id: str,
        now: float | None = None,
        *,
        require_bot_involved: bool = True,
    ) -> TopicBlock | None:
        """Pick the block the bot should join.

        With ``require_bot_involved=True`` (default), returns a block ONLY if
        the bot already participates in one (was @-ed / replied-to / spoke in
        it). When the bot is in none of the active blocks it returns ``None``
        — the scheduler then injects no anchor, so the bot does not get
        "placed" into a conversation block it is merely overhearing (F-α).
        Set ``False`` to fall back to the most-active block regardless.
        """
        now = time.monotonic() if now is None else now
        active = self._active(group_id, now)
        if not active:
            return None
        involved = [b for b in active if b.bot_involved]
        if not involved and require_bot_involved:
            return None
        pool = involved or active
        return max(pool, key=lambda b: (b.last_active, len(b.participants)))

    def reset(self, group_id: str) -> None:
        """Drop all blocks for a group (e.g. interactive command flows)."""
        self._blocks.pop(group_id, None)

