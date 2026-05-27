from __future__ import annotations

from kernel.bot_pair_guard import BotPairLoopGuard


def _guard() -> BotPairLoopGuard:
    return BotPairLoopGuard(
        self_id="1",
        known_other_bots={
            "100": ["2", "3"],
            "200": ["2"],
        },
        max_per_minute=3,
        cooldown_seconds=60,
    )


def test_pair_guard_triggers_cooldown_after_fourth_hit() -> None:
    guard = _guard()

    for idx in range(4):
        assert guard.record_inbound("100", "2", now=float(idx)) is True

    assert guard.is_suppressed("100", "2", now=4.0) is True
    assert guard.record_outbound("100", "2", now=5.0) is True


def test_pair_guard_self_pair_and_human_user_are_ignored() -> None:
    guard = _guard()

    assert guard.record_inbound("100", "1", now=1.0) is False
    assert guard.is_suppressed("100", "1", now=2.0) is False
    assert guard.record_inbound("100", "9999", now=3.0) is False
    assert guard.is_suppressed("100", "9999", now=4.0) is False


def test_pair_guard_isolated_per_group() -> None:
    guard = _guard()

    for idx in range(4):
        assert guard.record_inbound("100", "2", now=float(idx)) is True

    assert guard.is_suppressed("100", "2", now=4.0) is True
    assert guard.is_suppressed("200", "2", now=4.0) is False


def test_pair_guard_ttl_self_heals_after_window_and_cooldown() -> None:
    guard = _guard()

    for idx in range(4):
        guard.record_inbound("100", "2", now=float(idx))

    assert guard.is_suppressed("100", "2", now=10.0) is True
    assert guard.is_suppressed("100", "2", now=64.0) is False
    assert guard.record_inbound("100", "2", now=65.0) is True
    assert guard.is_suppressed("100", "2", now=65.1) is False


def test_pair_guard_pair_key_is_symmetric_for_inbound_and_outbound() -> None:
    guard = _guard()

    assert guard.record_inbound("100", "2", now=1.0) is True
    assert guard.record_outbound("100", "2", now=2.0) is True

    assert len(guard._events) == 1
    [(group_id, pair)] = list(guard._events)
    assert group_id == "100"
    assert pair == ("1", "2")


def test_pair_guard_bind_self_id_late_still_works() -> None:
    guard = BotPairLoopGuard(
        known_other_bots={"100": ["2"]},
        max_per_minute=3,
        cooldown_seconds=60,
    )

    assert guard.record_inbound("100", "2", now=1.0) is False
    guard.bind_self_id("1")
    assert guard.record_inbound("100", "2", now=2.0) is True
