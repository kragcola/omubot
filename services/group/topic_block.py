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

import math
import time
from dataclasses import dataclass, field

from services.similarity import (
    NgramSimilarityProvider,
    SimilarityProvider,
)

# Tunables (module constants; overridable via configure() → config fields).
_MAX_BLOCKS = 6
_ATTRIB_RECENT_S = 120.0
# L1 linear-scoring weights (same-speaker demoted to soft feature per audit §7.3(ii)).
_L1_W_SPK = 0.25
_L1_W_TIME = 0.25
_L1_W_SIM = 0.50
_L1_SCORE_FLOOR = 0.30
# L2 activity-decay (EDMStream, lazy-evaluated; no timer).
_DECAY_A = 0.998
_DECAY_LAMBDA = 1.0
_ACTIVITY_FLOOR = 0.5
_RESERVOIR_MAX = 12


@dataclass
class TopicBlock:
    """One concurrent conversation thread within a group."""

    block_id: str
    message_ids: list[int] = field(default_factory=list)
    participants: dict[str, float] = field(default_factory=dict)  # L1: QQ→last-speak timestamp
    last_active: float = 0.0
    activity: float = 0.0  # L2: EDMStream activity (lazy-evaluated)
    last_access: float = 0.0  # L2: last time activity was evaluated
    last_text: str = ""
    bot_involved: bool = False
    at_message_id: int | None = None  # last message that @-addressed someone
    anchor_speaker: str = ""  # L0: speaker of the message that started this block (edge source)
    centroid: str | None = None  # L3: aggregated block text (c-TF-IDF; NOT maintained yet — see §5)

    def representative_message_id(self) -> int | None:
        """Block's anchor message: the @-addressed one, else the latest."""
        if self.at_message_id is not None:
            return self.at_message_id
        return self.message_ids[-1] if self.message_ids else None

    def representative_speaker(self) -> str:
        """Best-effort speaker QQ for the anchor (edge source, or latest participant)."""
        if self.anchor_speaker:
            return self.anchor_speaker
        if self.participants:
            return max(self.participants, key=lambda qq: self.participants[qq])
        return ""


class TopicBlockTracker:
    """Per-process tracker; maintains active topic blocks per group."""

    def __init__(self, *, similarity: SimilarityProvider | None = None) -> None:
        self._similarity = similarity or NgramSimilarityProvider()
        self._blocks: dict[str, dict[str, TopicBlock]] = {}
        self._reservoir: dict[str, dict[str, TopicBlock]] = {}
        self._msg_to_block: dict[str, dict[int, str]] = {}
        self._counter = 0
        self._max_blocks = _MAX_BLOCKS
        self._attrib_recent_s = _ATTRIB_RECENT_S
        # L2 tunables (overridable via configure).
        self._decay_a = _DECAY_A
        self._decay_lambda = _DECAY_LAMBDA
        self._activity_floor = _ACTIVITY_FLOOR
        self._reservoir_max = _RESERVOIR_MAX

    def configure(
        self,
        *,
        attrib_recent_seconds: float | None = None,
        max_blocks: int | None = None,
        decay_a: float | None = None,
        decay_lambda: float | None = None,
        activity_floor: float | None = None,
        reservoir_max: int | None = None,
    ) -> None:
        """Override tunables from config (no-op for None values)."""
        if attrib_recent_seconds is not None:
            self._attrib_recent_s = max(1.0, float(attrib_recent_seconds))
        if max_blocks is not None:
            self._max_blocks = max(1, int(max_blocks))
        if decay_a is not None:
            self._decay_a = max(0.9, min(1.0, float(decay_a)))
        if decay_lambda is not None:
            self._decay_lambda = max(0.1, float(decay_lambda))
        if activity_floor is not None:
            self._activity_floor = max(0.0, float(activity_floor))
        if reservoir_max is not None:
            self._reservoir_max = max(1, int(reservoir_max))

    def _next_block_id(self) -> str:
        self._counter += 1
        return f"b{self._counter}"

    # ── L2 activity helpers ───────────────────────────────────────────

    def _decay_activity(self, block: TopicBlock, now: float) -> float:
        """Apply multiplicative decay since last access (no +1; bump handles arrivals)."""
        dt = max(0.0, now - block.last_access)
        block.activity *= math.pow(self._decay_a, self._decay_lambda * dt)
        block.last_access = now
        return block.activity

    @staticmethod
    def _block_text(block: TopicBlock) -> str:
        """L3: use aggregated centroid for similarity; fall back to last_text."""
        return block.centroid if block.centroid else block.last_text

    @staticmethod
    def _bump_activity(block: TopicBlock) -> None:
        """Quick activity bump without full decay (used on attribution)."""
        block.activity += 1.0
        block.last_access = max(block.last_access, block.last_active)

    # ── block pool management ─────────────────────────────────────────

    def _active(self, group_id: str, now: float) -> list[TopicBlock]:
        """Blocks with activity > floor (sorted descending by activity).
        Low-activity blocks are moved to reservoir inline (guardrail 3)."""
        blocks = self._blocks.get(group_id)
        if not blocks:
            return []
        active: list[TopicBlock] = []
        cold: list[TopicBlock] = []
        for b in blocks.values():
            self._decay_activity(b, now)
            if b.activity > self._activity_floor:
                active.append(b)
            else:
                cold.append(b)
        for b in cold:
            self._move_to_reservoir(group_id, b)
        active.sort(key=lambda b: b.activity, reverse=True)
        return active

    def _move_to_reservoir(self, group_id: str, block: TopicBlock) -> None:
        """Move a low-activity block to reservoir (guardrail 3: keep msgid index)."""
        active = self._blocks.get(group_id, {})
        active.pop(block.block_id, None)
        if not active:
            self._blocks.pop(group_id, None)
        res = self._reservoir.setdefault(group_id, {})
        res[block.block_id] = block
        # Evict oldest reservoir block if over capacity.
        while len(res) > self._reservoir_max:
            _, oldest = min(res.items(), key=lambda kv: kv[1].activity)
            res.pop(oldest.block_id, None)
            # Clean _msg_to_block entries owned by the evicted block
            # so the reverse index does not grow without bound.
            self._prune_msg_index(group_id, oldest.block_id)

    def _revive_from_reservoir(self, group_id: str, block: TopicBlock) -> None:
        """Move a reservoir block back to active (reply revival)."""
        res = self._reservoir.get(group_id, {})
        res.pop(block.block_id, None)
        if not res:
            self._reservoir.pop(group_id, None)
        active = self._blocks.setdefault(group_id, {})
        active[block.block_id] = block
        self._bump_activity(block)

    def _prune_msg_index(self, group_id: str, block_id: str) -> None:
        """Remove _msg_to_block entries pointing to a specific block."""
        group_idx = self._msg_to_block.get(group_id)
        if group_idx is None:
            return
        stale = [mid for mid, bid in group_idx.items() if bid == block_id]
        for mid in stale:
            del group_idx[mid]
        if not group_idx:
            self._msg_to_block.pop(group_id, None)

    # ── observe / attribute / apply ───────────────────────────────────

    def observe(
        self,
        group_id: str,
        *,
        message_id: int | None,
        speaker: str,
        text: str,
        reply_to_sender_id: str = "",
        reply_to_message_id: int | None = None,
        reply_to_self: bool = False,
        at_targets: tuple[str, ...] = (),
        at_self: bool = False,
        now: float | None = None,
    ) -> TopicBlock:
        """Attribute one message to a topic block (strongest signal first)."""
        now = time.monotonic() if now is None else now
        group_blocks = self._blocks.setdefault(group_id, {})
        active = self._active(group_id, now)
        target = self._attribute(
            group_id, active, speaker, text,
            reply_to_sender_id, reply_to_message_id, reply_to_self, at_targets, now,
        )
        if target is None:
            target = TopicBlock(block_id=self._next_block_id())
            group_blocks[target.block_id] = target
        reservoir = self._reservoir.get(group_id, {})
        if target.block_id in reservoir:
            self._revive_from_reservoir(group_id, target)
        active_now = self._blocks.get(group_id, {})
        while len(active_now) > self._max_blocks:
            _, coldest = min(active_now.items(), key=lambda kv: kv[1].activity)
            self._move_to_reservoir(group_id, coldest)
            active_now = self._blocks.get(group_id, {})
        self._apply(group_id, target, message_id, speaker, text, at_targets, at_self, reply_to_self, now)
        return target

    def _attribute(
        self,
        group_id: str,
        active: list[TopicBlock],
        speaker: str,
        text: str,
        reply_to_sender_id: str,
        reply_to_message_id: int | None,
        reply_to_self: bool,
        at_targets: tuple[str, ...],
        now: float,
    ) -> TopicBlock | None:
        """Find the block this message belongs to, or None to open a new one."""
        # L0-4: reply-to a specific message → O(1) reverse lookup (edge model).
        if reply_to_message_id is not None:
            group_idx = self._msg_to_block.get(group_id, {})
            found_block_id = group_idx.get(reply_to_message_id)
            if found_block_id is not None:
                b_active = next((b for b in active if b.block_id == found_block_id), None)
                if b_active is not None:
                    if reply_to_sender_id and reply_to_sender_id not in b_active.participants:
                        pass  # guardrail 2: inconsistent edge → fall through
                    else:
                        b_active.anchor_speaker = reply_to_sender_id or b_active.anchor_speaker
                        return b_active
                else:
                    all_blocks: dict[str, TopicBlock] = {}
                    all_blocks.update(self._blocks.get(group_id, {}))
                    all_blocks.update(self._reservoir.get(group_id, {}))
                    b = all_blocks.get(found_block_id)
                    if b is not None and reply_to_sender_id and reply_to_sender_id in b.participants:
                        b.anchor_speaker = reply_to_sender_id
                        return b
        # 1. reply-to bot → the bot block.
        if reply_to_self:
            for b in active:
                if b.bot_involved:
                    return b
        # 2. reply-to a known speaker → their block.
        if reply_to_sender_id:
            for b in active:
                if reply_to_sender_id in b.participants:
                    return b
        # 3. @-mention → target block.
        if at_targets:
            for b in active:
                if (now - b.last_active) <= self._attrib_recent_s and any(
                    t in b.participants for t in at_targets
                ):
                    return b
        # 4-5. L1 linear scoring over active ∪ reservoir (guardrail 1).
        best: TopicBlock | None = None
        best_score = _L1_SCORE_FLOOR
        candidates = active + list(self._reservoir.get(group_id, {}).values())
        for b in candidates:
            spk_c = 1.0 if speaker and speaker in b.participants else 0.0
            age = max(0.0, now - b.last_active)
            recency = max(0.0, 1.0 - age / self._attrib_recent_s)
            sim = self._similarity.similarity(text, self._block_text(b)) if self._block_text(b) else 0.0
            score = _L1_W_SPK * spk_c + _L1_W_TIME * recency + _L1_W_SIM * sim
            if score >= best_score:
                best, best_score = b, score
        return best

    def _apply(
        self,
        group_id: str,
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
            self._msg_to_block.setdefault(group_id, {})[message_id] = block.block_id
            if at_targets:
                block.at_message_id = message_id
        if speaker:
            block.participants[speaker] = now
        if text:
            block.last_text = text
        block.last_active = now
        self._bump_activity(block)
        if reply_to_self or at_self:
            block.bot_involved = True

    # ── public helpers ────────────────────────────────────────────────

    def mark_bot_involved(self, group_id: str, now: float | None = None, *, block_id: str = "") -> None:
        """Flag the bot's reply block as bot-involved."""
        now = time.monotonic() if now is None else now
        if block_id:
            block = self.pick_block_by_id(group_id, block_id)
        else:
            block = self.pick_anchor_block(group_id, now, require_bot_involved=False)
        if block is not None:
            block.bot_involved = True

    def pick_block_by_id(self, group_id: str, block_id: str) -> TopicBlock | None:
        """Return the block with this id (checks active + reservoir)."""
        if not block_id:
            return None
        b = self._blocks.get(group_id, {}).get(block_id)
        if b is not None:
            return b
        return self._reservoir.get(group_id, {}).get(block_id)

    def pick_anchor_block(
        self,
        group_id: str,
        now: float | None = None,
        *,
        require_bot_involved: bool = True,
    ) -> TopicBlock | None:
        """Pick the block the bot should join (F-α gate)."""
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
        """Drop all blocks for a group (including msgid reverse index)."""
        self._blocks.pop(group_id, None)
        self._reservoir.pop(group_id, None)
        self._msg_to_block.pop(group_id, None)
