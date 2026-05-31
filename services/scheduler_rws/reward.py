"""RWS reward loop (P1 + P6).

Closes the feedback loop the bandit never had: each fire/skip decision is
enqueued with a snapshot, and after a settlement window the group's reaction is
measured into a scalar reward fed back to ``RWSBandit.observe``.

P6 (anti reward-hacking / dark-pattern) is **structural, written into the
reward formula here**, not an after-the-fact KPI gate: "被理睬" alone can never
dominate; 致冷/显式负反馈/被禁言 are hard negative terms. This encodes the
Pang et al. (EACL 2024) lesson — optimizing "did they reply" breeds a
provocative bot — directly into the weights' signs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RewardWeights:
    """Signs encode P6: positive only for genuine engagement, hard negatives
    for coldness / explicit rejection so the bandit can't be hacked toward
    attention-seeking."""

    ack: float = 1.0  # 被理睬：被@回/被引用/后续正情感
    cold: float = 0.8  # 致冷：发言后群沉默 / 话题被切走（负）
    neg: float = 1.0  # 强负：显式制止 / 被禁言（负）


_DEFAULT_WEIGHTS = RewardWeights()


@dataclass(slots=True)
class PendingDecision:
    """One fire/skip decision awaiting reward settlement."""

    group_id: str
    decision: bool  # True=fired (replied), False=skipped
    t0: float  # monotonic time of decision
    turn_baseline: int  # timeline turn count at decision
    rws_score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReactionSignals:
    """Observed group reaction within the settlement window."""

    acknowledged: bool = False  # someone @-ed/replied bot or positive follow-up
    went_cold: bool = False  # silence / topic switched away after bot spoke
    explicit_negative: bool = False  # "别说话" / banned


def compute_reward(signals: ReactionSignals, *, weights: RewardWeights = _DEFAULT_WEIGHTS) -> float:
    """Combine reaction signals into reward ∈ [-1, 1].

    NEVER use "被理睬" alone (Pang et al.): ack is one positive term, but cold
    and explicit-negative are hard negatives that can drag reward below zero
    regardless of ack — so the bandit cannot learn to provoke replies.
    """
    r = 0.0
    if signals.acknowledged:
        r += weights.ack
    if signals.went_cold:
        r -= weights.cold
    if signals.explicit_negative:
        r -= weights.neg
    return max(-1.0, min(1.0, r))


class RWSRewardQueue:
    """In-memory pending-settlement queue. Pure logic; the scheduler drives the
    settle loop and supplies the reaction-measuring callback + observe sink."""

    def __init__(self, *, window_s: float = 300.0) -> None:
        self._window_s = max(1.0, float(window_s))
        self._pending: list[PendingDecision] = []

    def enqueue(self, item: PendingDecision) -> None:
        self._pending.append(item)

    def pending_count(self) -> int:
        return len(self._pending)

    def due(self, *, now: float | None = None) -> list[PendingDecision]:
        """Pop and return all decisions whose settlement window has elapsed."""
        moment = float(now if now is not None else time.monotonic())
        ready: list[PendingDecision] = []
        keep: list[PendingDecision] = []
        for item in self._pending:
            if moment - item.t0 >= self._window_s:
                ready.append(item)
            else:
                keep.append(item)
        self._pending = keep
        return ready

    def settle_due(
        self,
        *,
        measure: Callable[[PendingDecision], ReactionSignals],
        observe: Callable[[bool, float], object],
        weights: RewardWeights = _DEFAULT_WEIGHTS,
        now: float | None = None,
    ) -> int:
        """Settle all due decisions: measure reaction → reward → observe.

        ``measure`` reads the group's post-decision reaction (scheduler-side,
        uses timeline/interaction signals). ``observe`` is the bandit sink
        (``observe_rws_bandit``-style). Returns the number settled.
        """
        settled = 0
        for item in self.due(now=now):
            try:
                signals = measure(item)
                reward = compute_reward(signals, weights=weights)
                observe(item.decision, reward)
                settled += 1
            except Exception:
                # A single bad settlement must not stall the loop.
                continue
        return settled
