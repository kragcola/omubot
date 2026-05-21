from __future__ import annotations

from services.private_conversation import PrivateConversationManager


async def test_private_actor_wait_then_resume_complete() -> None:
    manager = PrivateConversationManager()
    actor = manager.get("private_123")

    async with actor.turn(event_id="1", user_id="100", text="继续说呢") as turn:
        assert turn.phase_before == "idle"
        assert turn.interrupted_wait is False
        wait_transition = turn.mark_wait("thinker_wait")
        assert wait_transition.action == "wait"
        assert wait_transition.phase_after == "waiting"

    snapshot = actor.snapshot()
    assert snapshot["phase"] == "waiting"
    assert snapshot["last_reason"] == "thinker_wait"

    async with actor.turn(event_id="2", user_id="100", text="再说一点") as turn2:
        assert turn2.phase_before == "waiting"
        assert turn2.interrupted_wait is True
        complete_transition = turn2.mark_complete("assistant_reply_sent", reply_text="好呀")
        assert complete_transition.action == "complete"
        assert complete_transition.phase_after == "complete"

    snapshot = actor.snapshot()
    assert snapshot["phase"] == "complete"
    assert snapshot["last_reason"] == "assistant_reply_sent"

    async with actor.turn(event_id="3", user_id="100", text="还有呢") as turn3:
        assert turn3.phase_before == "complete"
        assert turn3.resumed_from_complete is True
        resumed_wait = turn3.mark_wait("thinker_wait")
        assert resumed_wait.interrupted_wait is False

    snapshot = actor.snapshot()
    assert snapshot["phase"] == "waiting"
    assert snapshot["last_reason"] == "thinker_wait"


async def test_private_actor_abandoned_turn_falls_back_to_idle() -> None:
    manager = PrivateConversationManager()
    actor = manager.get("private_456")

    async with actor.turn(event_id="1", user_id="200", text="hello") as turn:
        assert turn.phase_before == "idle"
        assert actor.state.phase == "processing"

    assert actor.snapshot()["phase"] == "idle"

