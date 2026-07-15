from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from nonebot.adapters.onebot.v11 import NoticeEvent, PokeNotifyEvent

from kernel.types import PluginContext, TriggerContext
from services.dialogue_climate.sensors import SensorInput
from services.humanization.qq_interactions import (
    QQInteractionSignal,
    dispatch_qq_interaction_signal,
    parse_qq_interaction_signal,
    register_climate_mention_irritation,
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


class _MoodEngine:
    def __init__(self) -> None:
        self.signals: list[dict[str, float]] = []
        self.sessions: list[str] = []

    def register_interaction_signal(
        self,
        *,
        valence_d: float = 0.0,
        openness_d: float = 0.0,
        tension_d: float = 0.0,
        group_id: str | int | None = None,
        session_id: str = "",
    ) -> None:
        del group_id
        self.signals.append({
            "valence_d": valence_d,
            "openness_d": openness_d,
            "tension_d": tension_d,
        })
        self.sessions.append(session_id)


class _ClimateHub:
    enabled = True

    def __init__(self) -> None:
        self.inputs: list[SensorInput] = []

    def collect(self, data: SensorInput) -> int:
        self.inputs.append(data)
        return 1


def _ctx(
    *,
    poke_enabled: bool = True,
    reaction_enabled: bool = True,
    mood_engine: object | None = None,
    climate_hub: object | None = None,
) -> PluginContext:
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
        mood_engine=mood_engine,
        climate_sensor_hub=climate_hub,
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

    assert signal is not None
    assert signal.kind == "poke"
    assert signal.group_id == "123456"
    assert signal.actor_user_id == "10001"
    assert signal.target_user_id == "42"
    assert signal.is_tome is True
    assert signal.event_id.startswith("poke:123456:10001:42:1:")


def test_parse_same_poke_event_instance_keeps_nonce() -> None:
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

    first = parse_qq_interaction_signal(event, self_id="42")
    second = parse_qq_interaction_signal(event, self_id="42")

    assert first is not None
    assert second is not None
    assert first.event_id == second.event_id


def test_poke_event_nonce_does_not_depend_on_reusable_object_id(monkeypatch) -> None:
    import services.humanization.qq_interactions as qq_interactions

    monkeypatch.setattr(qq_interactions, "id", lambda _event: 1, raising=False)
    events = [
        PokeNotifyEvent(
            time=1,
            self_id=42,
            post_type="notice",
            notice_type="notify",
            sub_type="poke",
            user_id=10001,
            target_id=42,
            group_id=123456,
        )
        for _ in range(2)
    ]

    signals = [parse_qq_interaction_signal(event, self_id="42") for event in events]

    assert signals[0] is not None
    assert signals[1] is not None
    assert signals[0].event_id != signals[1].event_id


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


def test_replayed_poke_notice_is_deduplicated_before_rate_mutation() -> None:
    hub = _ClimateHub()
    ctx = _ctx(mood_engine=_MoodEngine(), climate_hub=hub)
    event = PokeNotifyEvent(
        time=100,
        self_id=42,
        post_type="notice",
        notice_type="notify",
        sub_type="poke",
        user_id=10001,
        target_id=42,
        group_id=123456,
    )
    signal = parse_qq_interaction_signal(event, self_id="42")
    assert signal is not None

    first = dispatch_qq_interaction_signal(ctx, signal, now=100.0)
    replay = dispatch_qq_interaction_signal(ctx, signal, now=100.0)

    assert first is True
    assert replay is False
    assert len(hub.inputs) == 1
    assert len(ctx.scheduler.calls) == 1


def test_distinct_poke_notices_in_same_second_are_both_accepted() -> None:
    hub = _ClimateHub()
    ctx = _ctx(mood_engine=_MoodEngine(), climate_hub=hub)

    for _ in range(2):
        event = PokeNotifyEvent(
            time=100,
            self_id=42,
            post_type="notice",
            notice_type="notify",
            sub_type="poke",
            user_id=10001,
            target_id=42,
            group_id=123456,
        )
        signal = parse_qq_interaction_signal(event, self_id="42")
        assert signal is not None
        assert dispatch_qq_interaction_signal(ctx, signal, now=100.0) is True

    assert len(hub.inputs) == 2
    assert len(ctx.scheduler.calls) == 2


def test_dispatch_poke_nudges_tension() -> None:
    mood = _MoodEngine()
    ctx = _ctx(mood_engine=mood)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    dispatch_qq_interaction_signal(ctx, signal, now=100.0)

    assert len(mood.signals) == 1
    s = mood.signals[0]
    assert s["tension_d"] > 0
    assert s["valence_d"] == 0.0
    assert mood.sessions[0] == "group_123456"


def test_poke_without_climate_hub_keeps_part0_static_nudge() -> None:
    mood = _MoodEngine()
    ctx = _ctx(mood_engine=mood)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    for offset in range(3):
        dispatch_qq_interaction_signal(ctx, signal, now=100.0 + offset)

    assert [s["tension_d"] for s in mood.signals] == pytest.approx([0.04, 0.04, 0.04])
    assert all(s["valence_d"] == 0.0 for s in mood.signals)


def test_climate_poke_frequency_aggregates_without_double_writing_mood() -> None:
    mood = _MoodEngine()
    hub = _ClimateHub()
    ctx = _ctx(mood_engine=mood, climate_hub=hub)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    for offset in range(3):
        dispatch_qq_interaction_signal(ctx, signal, now=100.0 + offset)

    assert mood.signals == []
    assert [data.poke_count for data in hub.inputs] == [1, 1, 1]
    assert [data.burst_continuation for data in hub.inputs] == [False, True, True]


def test_distinct_pokes_feed_only_marginal_burst_increment() -> None:
    hub = _ClimateHub()
    ctx = _ctx(mood_engine=_MoodEngine(), climate_hub=hub)

    for event_time in (100, 101, 102):
        event = PokeNotifyEvent(
            time=event_time,
            self_id=42,
            post_type="notice",
            notice_type="notify",
            sub_type="poke",
            user_id=10001,
            target_id=42,
            group_id=123456,
        )
        signal = parse_qq_interaction_signal(event, self_id="42")
        assert signal is not None
        dispatch_qq_interaction_signal(ctx, signal, now=float(event_time))

    assert [data.poke_count for data in hub.inputs] == [1, 1, 1]
    assert [data.burst_continuation for data in hub.inputs] == [False, True, True]


def test_rate_muted_poke_still_feeds_climate_frequency() -> None:
    mood = _MoodEngine()
    hub = _ClimateHub()
    ctx = _ctx(mood_engine=mood, climate_hub=hub)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    for offset in range(6):
        dispatch_qq_interaction_signal(ctx, signal, now=100.0 + offset)

    assert mood.signals == []
    assert [data.poke_count for data in hub.inputs] == [1, 1, 1, 1, 1, 1]
    assert [data.burst_continuation for data in hub.inputs] == [
        False,
        True,
        True,
        True,
        True,
        True,
    ]
    assert len(ctx.scheduler.calls) == 4


def test_climate_mention_without_hub_is_noop() -> None:
    mood = _MoodEngine()
    ctx = _ctx(mood_engine=mood)

    changed = register_climate_mention_irritation(
        ctx,
        group_id="123456",
        actor_user_id="10001",
        now=100.0,
    )

    assert changed is False
    assert mood.signals == []


def test_climate_mention_frequency_aggregates_in_sensor_hub() -> None:
    mood = _MoodEngine()
    hub = _ClimateHub()
    ctx = _ctx(mood_engine=mood, climate_hub=hub)

    changed = [
        register_climate_mention_irritation(
            ctx,
            group_id="123456",
            actor_user_id="10001",
            now=100.0 + offset,
        )
        for offset in range(3)
    ]

    assert changed == [True, True, True]
    assert mood.signals == []
    assert [data.mention_count for data in hub.inputs] == [1, 1, 1]
    assert [data.burst_continuation for data in hub.inputs] == [False, True, True]


def test_climate_mention_replay_is_deduplicated_before_frequency_mutation() -> None:
    hub = _ClimateHub()
    ctx = _ctx(climate_hub=hub)

    for message_id in (10, 10, 11):
        register_climate_mention_irritation(
            ctx,
            group_id="123456",
            actor_user_id="10001",
            message_id=message_id,
            now=100.0 + message_id,
        )

    assert [data.mention_count for data in hub.inputs] == [1, 1]
    assert [data.burst_continuation for data in hub.inputs] == [False, True]


def test_climate_mention_and_poke_share_frequency_context() -> None:
    mood = _MoodEngine()
    hub = _ClimateHub()
    ctx = _ctx(mood_engine=mood, climate_hub=hub)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    assert register_climate_mention_irritation(
        ctx,
        group_id="123456",
        actor_user_id="10001",
        now=100.0,
    ) is True
    dispatch_qq_interaction_signal(ctx, signal, now=101.0)
    dispatch_qq_interaction_signal(ctx, signal, now=102.0)
    assert register_climate_mention_irritation(
        ctx,
        group_id="123456",
        actor_user_id="10001",
        now=103.0,
    ) is True

    assert mood.signals == []
    assert [
        (data.mention_count, data.poke_count)
        for data in hub.inputs
    ] == [(1, 0), (0, 1), (0, 1), (1, 0)]
    assert [data.burst_continuation for data in hub.inputs] == [
        False,
        True,
        True,
        True,
    ]


def test_dispatch_positive_reaction_nudges_valence() -> None:
    mood = _MoodEngine()
    ctx = _ctx(mood_engine=mood)
    signal = QQInteractionSignal(
        kind="message_reaction",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        raw_message_id=9988,
        emoji_code="171",  # 点赞 → positive
        is_tome=True,
    )

    dispatch_qq_interaction_signal(ctx, signal, now=100.0)

    assert len(mood.signals) == 1
    s = mood.signals[0]
    assert s["valence_d"] > 0
    assert s["tension_d"] == 0.0


def test_dispatch_negative_reaction_lowers_valence_raises_tension() -> None:
    mood = _MoodEngine()
    ctx = _ctx(mood_engine=mood)
    signal = QQInteractionSignal(
        kind="message_reaction",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        raw_message_id=9988,
        emoji_code="322",  # 翻白眼 → negative
        is_tome=True,
    )

    dispatch_qq_interaction_signal(ctx, signal, now=100.0)

    assert len(mood.signals) == 1
    s = mood.signals[0]
    assert s["valence_d"] < 0
    assert s["tension_d"] > 0


def test_dispatch_neutral_reaction_no_nudge() -> None:
    mood = _MoodEngine()
    ctx = _ctx(mood_engine=mood)
    signal = QQInteractionSignal(
        kind="message_reaction",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        raw_message_id=9988,
        emoji_code="32",  # 疑问 → neutral
        is_tome=True,
    )

    dispatch_qq_interaction_signal(ctx, signal, now=100.0)

    assert mood.signals == []


def test_dispatch_without_mood_engine_is_safe() -> None:
    ctx = _ctx(mood_engine=None)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    # Must not raise; dispatch still succeeds.
    assert dispatch_qq_interaction_signal(ctx, signal, now=100.0) is True


def test_rate_muted_poke_still_nudges_tension() -> None:
    mood = _MoodEngine()
    ctx = _ctx(mood_engine=mood)
    signal = QQInteractionSignal(
        kind="poke",
        group_id="123456",
        actor_user_id="10001",
        target_user_id="42",
        is_tome=True,
    )

    # 6 pokes: 5th+ are reply-muted, but tension should still accrue each time.
    for offset in range(6):
        dispatch_qq_interaction_signal(ctx, signal, now=100.0 + offset)

    assert len(mood.signals) == 6
    assert len(ctx.scheduler.calls) == 4
