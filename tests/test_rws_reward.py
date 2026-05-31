"""Tests for the RWS reward loop (P1 + P6)."""

from __future__ import annotations

from services.scheduler_rws.reward import (
    PendingDecision,
    ReactionSignals,
    RewardWeights,
    RWSRewardQueue,
    compute_reward,
)

# --- P6: reward formula safety (the core anti-hacking guarantee) -----------

def test_acknowledged_alone_is_positive() -> None:
    assert compute_reward(ReactionSignals(acknowledged=True)) > 0


def test_cold_is_negative() -> None:
    assert compute_reward(ReactionSignals(went_cold=True)) < 0


def test_explicit_negative_is_negative() -> None:
    assert compute_reward(ReactionSignals(explicit_negative=True)) < 0


def test_ack_cannot_outweigh_explicit_negative() -> None:
    """P6 core: being replied-to must NOT redeem an explicitly-rejected turn —
    otherwise the bandit could learn to provoke replies (Pang et al.)."""
    r = compute_reward(ReactionSignals(acknowledged=True, explicit_negative=True))
    assert r <= 0  # ack(1.0) - neg(1.0) = 0, never positive


def test_ack_plus_cold_not_strongly_positive() -> None:
    """Replied-but-then-cold should not be a strong win."""
    r = compute_reward(ReactionSignals(acknowledged=True, went_cold=True))
    assert r <= 0.2  # ack 1.0 - cold 0.8 = 0.2


def test_reward_clamped() -> None:
    w = RewardWeights(ack=5.0, cold=5.0, neg=5.0)
    assert compute_reward(ReactionSignals(acknowledged=True), weights=w) == 1.0
    assert compute_reward(ReactionSignals(went_cold=True, explicit_negative=True), weights=w) == -1.0


def test_no_signal_is_neutral() -> None:
    assert compute_reward(ReactionSignals()) == 0.0


# --- P1: pending-settlement queue ------------------------------------------

def _decision(group: str = "100", *, decision: bool = True, t0: float = 0.0) -> PendingDecision:
    return PendingDecision(group_id=group, decision=decision, t0=t0, turn_baseline=0)


def test_queue_due_respects_window() -> None:
    q = RWSRewardQueue(window_s=300.0)
    q.enqueue(_decision(t0=0.0))
    assert q.due(now=100.0) == []  # window not elapsed
    assert q.pending_count() == 1
    due = q.due(now=300.0)
    assert len(due) == 1
    assert q.pending_count() == 0  # popped


def test_settle_due_measures_and_observes() -> None:
    q = RWSRewardQueue(window_s=10.0)
    q.enqueue(_decision(decision=True, t0=0.0))
    observed: list[tuple[bool, float]] = []
    settled = q.settle_due(
        measure=lambda _item: ReactionSignals(acknowledged=True),
        observe=lambda d, r: observed.append((d, r)),
        now=20.0,
    )
    assert settled == 1
    assert observed == [(True, 1.0)]


def test_settle_due_skips_not_yet_due() -> None:
    q = RWSRewardQueue(window_s=300.0)
    q.enqueue(_decision(t0=0.0))
    settled = q.settle_due(
        measure=lambda _item: ReactionSignals(acknowledged=True),
        observe=lambda _d, _r: None,
        now=100.0,
    )
    assert settled == 0
    assert q.pending_count() == 1


def test_settle_due_tolerates_measure_exception() -> None:
    """A bad settlement must not stall the loop (skips that item)."""
    q = RWSRewardQueue(window_s=1.0)
    q.enqueue(_decision(group="A", t0=0.0))
    q.enqueue(_decision(group="B", t0=0.0))

    def measure(item: PendingDecision) -> ReactionSignals:
        if item.group_id == "A":
            raise RuntimeError("boom")
        return ReactionSignals(acknowledged=True)

    observed: list[tuple[bool, float]] = []
    settled = q.settle_due(measure=measure, observe=lambda d, r: observed.append((d, r)), now=10.0)
    assert settled == 1  # B settled, A skipped
    assert observed == [(True, 1.0)]


# --- D2: cancel-path — settlement interrupted by shutdown must not pollute ----

def test_settle_due_propagates_cancellation_without_double_observe() -> None:
    """If the observe sink is cancelled mid-settle (shutdown), CancelledError
    must propagate (settle_due's per-item `except Exception` must NOT swallow a
    BaseException) and no item may be observed twice. due() pops all ready items
    before settling, so a partial settle drops the rest — it must never
    re-observe what already landed nor pollute the next run."""
    import asyncio

    q = RWSRewardQueue(window_s=1.0)
    q.enqueue(_decision(group="A", t0=0.0))
    q.enqueue(_decision(group="B", t0=0.0))
    q.enqueue(_decision(group="C", t0=0.0))

    calls: list[str] = []

    def measure(item: PendingDecision) -> ReactionSignals:
        calls.append(f"measure:{item.group_id}")
        return ReactionSignals(acknowledged=True)

    def observe(_d: bool, _r: float) -> None:
        idx = sum(1 for c in calls if c.startswith("observe"))
        calls.append(f"observe:{idx}")
        if idx == 1:  # second observe → simulate shutdown cancel
            raise asyncio.CancelledError

    try:
        q.settle_due(measure=measure, observe=observe, now=10.0)
        raised = False
    except asyncio.CancelledError:
        raised = True

    assert raised, "CancelledError must propagate (not be swallowed as a normal Exception)"
    # Exactly two observe attempts happened (the 2nd cancelled mid-flight); the
    # 3rd item is never observed → no double-observe, no phantom reward.
    observe_calls = [c for c in calls if c.startswith("observe")]
    assert observe_calls == ["observe:0", "observe:1"]
    # due() drained the queue before settling — re-running settle must not
    # replay the already-attempted items (no pollution of the next run).
    assert q.pending_count() == 0
    assert q.settle_due(measure=measure, observe=lambda _d, _r: None, now=20.0) == 0

