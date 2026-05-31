from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from nonebot.adapters.onebot.v11 import NoticeEvent, PokeNotifyEvent

from kernel.types import PluginContext, TriggerContext
from services.humanization.qq_interactions import (
    QQInteractionSignal,
    dispatch_qq_interaction_signal,
    parse_qq_interaction_signal,
    reset_qq_interaction_rate_guard,
)


class _GroupConfig:
    def resolve(self, group_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            access_allowed=True,
            presence_mode="active",
            blocked_users=set(),
        )


class _Timeline:
    def __init__(self) -> None:
        self.triggers: list[dict[str, object]] = []

    def add_pending_trigger(
        self,
        group_id: str,
        *,
        reason: str,
        message_id: int | None = None,
        target_user_id: str = "",
    ) -> None:
        self.triggers.append({
            "group_id": group_id,
            "reason": reason,
            "message_id": message_id,
            "target_user_id": target_user_id,
        })


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def is_muted(self, group_id: str) -> bool:
        return False

    def notify(
        self,
        group_id: str,
        *,
        trigger: object | None = None,
        user_id: str = "",
        message_text: str = "",
    ) -> None:
        del message_text
        self.calls.append({
            "group_id": group_id,
            "trigger": trigger,
            "user_id": user_id,
        })


def _ctx(*, poke_enabled: bool = True, reaction_enabled: bool = True) -> PluginContext:
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            group=_GroupConfig(),
            humanization=SimpleNamespace(
                qq_interactions=SimpleNamespace(
                    poke_inbound_response_enabled=poke_enabled,
                    reaction_inbound_response_enabled=reaction_enabled,
                ),
            ),
        ),
        timeline=_Timeline(),
        scheduler=_Scheduler(),
    )
    return cast(PluginContext, ctx)


def setup_function() -> None:
    reset_qq_interaction_rate_guard()


def test_parse_poke_notice_to_bot() -> None:
    event = PokeNotifyEvent(
        time=1,
        self_id=42,
        post_type="notice",
        notice_type="notify",
        sub_type="poke",
        user_id=10001,
        target_id=42,
        group_id=123456,
    )

    signal = parse_qq_interaction_signal(event, self_id="42")

    assert signal == QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )


def test_parse_napcat_raw_reaction_notice() -> None:
    event = NoticeEvent.model_validate({
        "time": 1,
        "self_id": 42,
        "post_type": "notice",
        "notice_type": "message_reactions_updated",
        "group_id": 123456,
        "user_id": 10001,
        "target_id": 42,
        "message_id": 9988,
        "emoji_id": "66",
    })

    signal = parse_qq_interaction_signal(event, self_id="42")

    assert signal == QQInteractionSignal(
        kind="message_reaction",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        raw_message_id=9988,
        emoji_code="66",
        is_tome=True,
    )


def test_dispatch_enabled_poke_adds_trigger_and_notifies_scheduler() -> None:
    ctx = _ctx()
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    assert dispatch_qq_interaction_signal(ctx, signal, now=100.0) is True

    assert ctx.timeline.triggers == [{
        "group_id": "123456",
        "reason": "QQ 戳一戳",
        "message_id": None,
        "target_user_id": "10001",
    }]
    call = ctx.scheduler.calls[0]
    trigger = call["trigger"]
    assert isinstance(trigger, TriggerContext)
    assert call["group_id"] == "123456"
    assert call["user_id"] == "10001"
    assert trigger.mode == "qq_interaction"
    assert trigger.extra["kind"] == "poke"


def test_dispatch_disabled_or_not_tome_does_not_mutate_runtime() -> None:
    ctx = _ctx(poke_enabled=False)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    assert dispatch_qq_interaction_signal(ctx, signal, now=100.0) is False
    assert ctx.timeline.triggers == []
    assert ctx.scheduler.calls == []

    signal = QQInteractionSignal(
        kind="message_reaction",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="999",
        raw_message_id=9988,
        emoji_code="66",
        is_tome=False,
    )
    ctx = _ctx()

    assert dispatch_qq_interaction_signal(ctx, signal, now=100.0) is False
    assert ctx.timeline.triggers == []
    assert ctx.scheduler.calls == []


def test_poke_rate_guard_mutes_fifth_poke_for_same_user() -> None:
    ctx = _ctx()
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    results = [
        dispatch_qq_interaction_signal(ctx, signal, now=100.0 + offset)
        for offset in range(6)
    ]

    assert results == [True, True, True, True, False, False]
    assert len(ctx.timeline.triggers) == 4
    assert len(ctx.scheduler.calls) == 4
