from __future__ import annotations

from kernel.bot_pair_guard import BotPairLoopGuard


def _guard() -> BotPairLoopGuard:
    return BotPairLoopGuard(
        self_id="1",
        known_other_bots={
            "100": ["2", "3"],
            "200": ["2"],
        },
        cooldown_seconds=60,
        loop_alt_threshold=10,
        known_peer_alt_threshold=6,
    )


def _pingpong(guard: BotPairLoopGuard, group: str, peer: str, flips: int, *, start: float = 0.0) -> float:
    """Drive a back-and-forth out/in/out/... producing `flips` direction changes."""
    t = start
    # first event (no flip yet), then `flips` alternating events
    guard.record_outbound(group, peer, now=t)
    for i in range(flips):
        t += 1.0
        if i % 2 == 0:
            guard.record_inbound(group, peer, now=t)
        else:
            guard.record_outbound(group, peer, now=t)
    return t


def test_unknown_peer_pingpong_is_suppressed() -> None:
    """S1 core: a peer NOT in known_other_bots still gets suppressed once the
    self↔peer back-and-forth reaches loop_alt_threshold (regression for P0)."""
    guard = _guard()
    # peer "9999" is not registered → old design never counted it
    t = _pingpong(guard, "100", "9999", flips=10)
    assert guard.is_suppressed("100", "9999", now=t) is True


def test_unknown_peer_below_threshold_not_suppressed() -> None:
    guard = _guard()
    t = _pingpong(guard, "100", "9999", flips=9)  # one short of 10
    assert guard.is_suppressed("100", "9999", now=t) is False


def test_human_burst_same_direction_not_suppressed() -> None:
    """A human sending many messages (all inbound, zero direction flips) must
    never trip the guard, however long the burst."""
    guard = _guard()
    for idx in range(20):
        guard.record_inbound("100", "9999", now=float(idx))
    assert guard.is_suppressed("100", "9999", now=20.0) is False


def test_known_peer_suppressed_faster() -> None:
    """Registered bots use the stricter known_peer_alt_threshold (6)."""
    guard = _guard()
    t = _pingpong(guard, "100", "2", flips=6)  # known peer threshold = 6
    assert guard.is_suppressed("100", "2", now=t) is True
    # an unknown peer with the same 6 flips is NOT yet suppressed (threshold 10)
    t2 = _pingpong(guard, "100", "8888", flips=6)
    assert guard.is_suppressed("100", "8888", now=t2) is False


def test_self_pair_ignored() -> None:
    guard = _guard()
    assert guard.record_inbound("100", "1", now=1.0) is False
    assert guard.is_suppressed("100", "1", now=2.0) is False


def test_isolated_per_group() -> None:
    guard = _guard()
    t = _pingpong(guard, "100", "2", flips=6)
    assert guard.is_suppressed("100", "2", now=t) is True
    assert guard.is_suppressed("200", "2", now=t) is False


def test_ttl_self_heals_after_window_and_cooldown() -> None:
    guard = _guard()
    t = _pingpong(guard, "100", "2", flips=6)
    assert guard.is_suppressed("100", "2", now=t) is True
    # cooldown is 60s from the last record; before that still suppressed
    assert guard.is_suppressed("100", "2", now=t + 30.0) is True
    # after cooldown expires, healed
    assert guard.is_suppressed("100", "2", now=t + 61.0) is False


def test_pair_key_symmetric_inbound_outbound() -> None:
    guard = _guard()
    assert guard.record_inbound("100", "2", now=1.0) is True
    assert guard.record_outbound("100", "2", now=2.0) is True
    assert len(guard._events) == 1
    [(group_id, pair)] = list(guard._events)
    assert group_id == "100"
    assert pair == ("1", "2")


def test_prune_drops_events_outside_window() -> None:
    """Events older than 60s are pruned, so stale flips don't accumulate."""
    guard = _guard()
    # 5 flips, then a long gap, then more — old ones pruned, can't reach 10
    _pingpong(guard, "100", "9999", flips=5, start=0.0)
    t = _pingpong(guard, "100", "9999", flips=5, start=1000.0)
    assert guard.is_suppressed("100", "9999", now=t) is False


def test_bind_self_id_late_still_works() -> None:
    guard = BotPairLoopGuard(
        known_other_bots={"100": ["2"]},
        cooldown_seconds=60,
        loop_alt_threshold=10,
        known_peer_alt_threshold=6,
    )
    # no self_id yet → no key built
    assert guard.record_inbound("100", "2", now=1.0) is False
    guard.bind_self_id("1")
    # now known peer "2" alternation suppresses at 6 flips
    t = _pingpong(guard, "100", "2", flips=6, start=2.0)
    assert guard.is_suppressed("100", "2", now=t) is True
