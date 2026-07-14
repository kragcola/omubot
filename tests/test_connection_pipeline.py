"""First-connect contract for the production Router connection pipeline."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any, cast

import pytest


class _Bot:
    self_id = 123456

    async def get_group_list(self) -> list[dict[str, object]]:
        return []


class _ReconnectBot(_Bot):
    def __init__(self, groups: list[dict[str, object]], muted_until: int) -> None:
        self.groups = groups
        self.muted_until = muted_until
        self.member_info_calls: list[dict[str, object]] = []

    async def get_group_list(self) -> list[dict[str, object]]:
        return self.groups

    async def get_group_member_info(
        self,
        *,
        group_id: int,
        user_id: int,
    ) -> dict[str, int]:
        self.member_info_calls.append(
            {
                "group_id": group_id,
                "user_id": user_id,
            }
        )
        if group_id != 100:
            raise AssertionError("non-learning group member info must not be queried")
        return {"shut_up_timestamp": self.muted_until}


class _SequentialGroupListBot(_Bot):
    def __init__(self, group_lists: list[list[dict[str, object]]]) -> None:
        self._group_lists = group_lists
        self.group_list_call_count = 0

    async def get_group_list(self) -> list[dict[str, object]]:
        call_index = self.group_list_call_count
        self.group_list_call_count += 1
        if call_index >= len(self._group_lists):
            raise AssertionError("get_group_list called more than expected")
        return self._group_lists[call_index]


class _UsageAlertBot(_Bot):
    def __init__(self, failing_admin_id: int) -> None:
        self._failing_admin_id = failing_admin_id
        self.private_messages: list[tuple[int, str]] = []

    async def send_private_msg(self, *, user_id: int, message: str) -> None:
        self.private_messages.append((user_id, message))
        if user_id == self._failing_admin_id:
            raise RuntimeError("private message failed")


class _FailingInventoryBot(_Bot):
    def __init__(self) -> None:
        self.member_info_call_count = 0

    async def get_group_list(self) -> list[dict[str, object]]:
        raise RuntimeError("group list unavailable")

    async def get_group_member_info(self, **kwargs: object) -> dict[str, object]:
        self.member_info_call_count += 1
        return {}


class _ProtocolConnections:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls
        self.connected_bots: list[object] = []
        self.disconnected_bots: list[object] = []

    def record_connected(self, bot: object) -> None:
        self.connected_bots.append(bot)
        self._calls.append(("protocol_connections.record_connected", bot))

    def record_disconnected(self, bot: object) -> None:
        self.disconnected_bots.append(bot)


class _BotWrapper:
    def __init__(self, calls: list[tuple[str, object]], name: str) -> None:
        self._calls = calls
        self._name = name

    def wrap_bot(self, bot: object) -> None:
        self._calls.append((f"{self._name}.wrap_bot", bot))


class _LLMClient:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls
        self.__bot_self_id: object = None

    @property
    def _bot_self_id(self) -> object:
        return self.__bot_self_id

    @_bot_self_id.setter
    def _bot_self_id(self, value: object) -> None:
        self.__bot_self_id = value
        self._calls.append(("llm_client._bot_self_id", value))


class _StateBoard:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls
        self._bot_self_id: object = None

    @property
    def bot_self_id(self) -> object:
        return self._bot_self_id

    @bot_self_id.setter
    def bot_self_id(self, value: object) -> None:
        self._bot_self_id = value
        self._calls.append(("state_board.bot_self_id", value))


class _PersonaRuntime:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    def bind_bot_self_id(self, self_id: str) -> None:
        self._calls.append(("persona_runtime.bind_bot_self_id", self_id))


class _Scheduler:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls
        self.mute_calls: list[tuple[str, str, float]] = []

    def set_bot(self, bot: object) -> None:
        self._calls.append(("scheduler.set_bot", bot))

    def mute(self, group_id: str, *, source: str, until_unix: float) -> None:
        self.mute_calls.append((group_id, source, until_unix))


class _GroupConfig:
    def __init__(self, learning_group_id: str) -> None:
        self._learning_group_id = learning_group_id
        self.checked_group_ids: list[str] = []

    def allows_learning_group(self, group_id: str) -> bool:
        self.checked_group_ids.append(group_id)
        return group_id == self._learning_group_id


class _NameVariationRegistry:
    def __init__(
        self,
        calls: list[tuple[str, object]],
        *,
        failing_group_id: str,
    ) -> None:
        self._calls = calls
        self._failing_group_id = failing_group_id

    async def refresh(self, bot: object, group_id: str) -> None:
        self._calls.append(("name_registry.refresh", (bot, group_id)))
        if group_id == self._failing_group_id:
            raise RuntimeError("refresh failed")


class _UsageTracker:
    def __init__(self) -> None:
        self.alert_registrations: list[dict[str, object]] = []

    def set_alert(
        self,
        *,
        alert_fn: object,
        cache_hit_warn: object,
        slow_threshold_s: object,
        cache_alert_window_m: object,
        cache_alert_cooldown_m: object,
    ) -> None:
        self.alert_registrations.append(
            {
                "alert_fn": alert_fn,
                "cache_hit_warn": cache_hit_warn,
                "slow_threshold_s": slow_threshold_s,
                "cache_alert_window_m": cache_alert_window_m,
                "cache_alert_cooldown_m": cache_alert_cooldown_m,
            }
        )


class _Context:
    def __init__(
        self,
        calls: list[tuple[str, object]],
        *,
        startup_triggered: bool = False,
        group_config: _GroupConfig | None = None,
    ) -> None:
        self._calls = calls
        self._bot: object = None
        self._startup_triggered = startup_triggered
        self.protocol_connections = _ProtocolConnections(calls)
        self.outbound_group_access_guard = _BotWrapper(calls, "outbound_guard")
        self.protocol_trace = _BotWrapper(calls, "protocol_trace")
        self.llm_client = _LLMClient(calls)
        self.state_board = _StateBoard(calls)
        self.persona_runtime = _PersonaRuntime(calls)
        self.scheduler = _Scheduler(calls)
        self.group_inventory: dict[str, dict[str, object]] = {}
        self.name_registry: Any = None
        self.usage_tracker: Any = None
        self.config = SimpleNamespace(group=group_config)
        self.admins: dict[str, object] = {}

    @property
    def bot(self) -> object:
        return self._bot

    @bot.setter
    def bot(self, value: object) -> None:
        self._bot = value
        self._calls.append(("ctx.bot", value))

    @property
    def startup_triggered(self) -> bool:
        return self._startup_triggered

    @startup_triggered.setter
    def startup_triggered(self, value: bool) -> None:
        self._startup_triggered = value
        self._calls.append(("ctx.startup_triggered", value))


class _Bus:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls

    async def fire_on_bot_connect(self, ctx: _Context, bot: object) -> None:
        assert ctx.startup_triggered is True
        self._calls.append(("bus.fire_on_bot_connect", (ctx, bot)))

    def start_tick_loop(self, ctx: _Context) -> None:
        self._calls.append(("bus.start_tick_loop", ctx))


class _CancellingBus(_Bus):
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        super().__init__(calls)
        self.connect_attempts = 0

    async def fire_on_bot_connect(self, ctx: _Context, bot: object) -> None:
        self.connect_attempts += 1
        if self.connect_attempts == 1:
            raise asyncio.CancelledError
        await super().fire_on_bot_connect(ctx, bot)


def _pipeline_type() -> Any:
    try:
        module = importlib.import_module("services.routing.connection_pipeline")
    except ImportError:
        raise AssertionError(
            "services.routing.connection_pipeline must be implemented"
        ) from None

    pipeline_type = getattr(module, "RuntimeConnectionPipeline", None)
    assert callable(pipeline_type), "RuntimeConnectionPipeline must be implemented"
    return pipeline_type


@pytest.mark.asyncio
async def test_first_connect_publishes_and_binds_before_hooks_then_starts_tick_once() -> None:
    calls: list[tuple[str, object]] = []
    ctx = _Context(calls)
    bus = _Bus(calls)
    bot = _Bot()
    pipeline = _pipeline_type()(ctx, bus)

    await pipeline.on_connect(bot)

    assert calls == [
        ("ctx.bot", bot),
        ("protocol_connections.record_connected", bot),
        ("outbound_guard.wrap_bot", bot),
        ("protocol_trace.wrap_bot", bot),
        ("llm_client._bot_self_id", bot.self_id),
        ("state_board.bot_self_id", bot.self_id),
        ("persona_runtime.bind_bot_self_id", str(bot.self_id)),
        ("scheduler.set_bot", bot),
        ("ctx.startup_triggered", True),
        ("bus.fire_on_bot_connect", (ctx, bot)),
        ("bus.start_tick_loop", ctx),
    ]


@pytest.mark.asyncio
async def test_reconnect_refreshes_full_inventory_and_reconciles_only_learning_groups() -> None:
    calls: list[tuple[str, object]] = []
    muted_until = 4_102_444_800
    groups = [
        {"group_id": 100, "group_name": "learning"},
        {"group_id": 200, "group_name": "non-learning"},
    ]
    group_config = _GroupConfig(learning_group_id="100")
    ctx = _Context(calls, startup_triggered=True, group_config=group_config)
    bus = _Bus(calls)
    bot = _ReconnectBot(groups, muted_until)
    pipeline = _pipeline_type()(ctx, bus)

    await pipeline.on_connect(bot)

    assert ctx.group_inventory == {
        "100": groups[0],
        "200": groups[1],
    }
    assert ctx.group_inventory["100"] is not groups[0]
    assert ctx.group_inventory["200"] is not groups[1]
    assert group_config.checked_group_ids == ["100", "200"]
    assert bot.member_info_calls == [
        {
            "group_id": 100,
            "user_id": bot.self_id,
        }
    ]
    assert ctx.scheduler.mute_calls == [
        ("100", "reconcile", float(muted_until)),
    ]
    assert calls.count(("bus.fire_on_bot_connect", (ctx, bot))) == 1
    assert ("bus.start_tick_loop", ctx) not in calls


@pytest.mark.parametrize(
    ("current_mode", "should_clear"),
    [
        pytest.param("same-object", True, id="same-object"),
        pytest.param("same-self-id", False, id="same-self-id"),
        pytest.param("different-self-id", False, id="different-self-id"),
    ],
)
@pytest.mark.asyncio
async def test_disconnect_clears_only_the_current_bot_owner(
    current_mode: str,
    should_clear: bool,
) -> None:
    calls: list[tuple[str, object]] = []
    ctx = _Context(calls)
    bus = _Bus(calls)
    disconnected_bot = SimpleNamespace(self_id=123456)
    if current_mode == "same-object":
        current_bot = disconnected_bot
    elif current_mode == "same-self-id":
        current_bot = SimpleNamespace(self_id="123456")
    else:
        current_bot = SimpleNamespace(self_id="654321")
    ctx.bot = current_bot
    calls.clear()
    pipeline = _pipeline_type()(ctx, bus)
    on_disconnect = getattr(pipeline, "on_disconnect", None)
    assert callable(on_disconnect), "RuntimeConnectionPipeline.on_disconnect must be implemented"

    await cast(Any, on_disconnect)(disconnected_bot)

    assert ctx.protocol_connections.disconnected_bots == [disconnected_bot]
    if should_clear:
        assert ctx.bot is None
    else:
        assert ctx.bot is current_bot


@pytest.mark.asyncio
async def test_name_registry_preload_skips_empty_groups_and_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    history_calls: list[tuple[object, object, tuple[str, ...] | None]] = []
    preload_groups = [
        {"group_id": 100, "group_name": "first"},
        {"group_id": "", "group_name": "missing-id"},
        {"group_id": "200", "group_name": "second"},
    ]
    bot = _SequentialGroupListBot([preload_groups, []])
    ctx = _Context(calls)
    registry = _NameVariationRegistry(calls, failing_group_id="100")
    ctx.name_registry = registry
    bus = _Bus(calls)
    pipeline_type = _pipeline_type()
    pipeline_module = importlib.import_module("services.routing.connection_pipeline")
    history_module = importlib.import_module("services.history_backfill")

    async def history_backfill_stage(
        stage_ctx: object,
        stage_bot: object,
        group_ids: tuple[str, ...] | None = None,
    ) -> None:
        history_calls.append((stage_ctx, stage_bot, group_ids))

    monkeypatch.setattr(
        pipeline_module,
        "NameVariationRegistry",
        _NameVariationRegistry,
        raising=False,
    )
    monkeypatch.setattr(
        history_module,
        "run_history_backfill",
        history_backfill_stage,
    )
    pipeline = pipeline_type(ctx, bus)

    await pipeline.on_connect(bot)

    assert bot.group_list_call_count == 2
    assert history_calls == [(ctx, bot, ("100", "200"))]
    assert pipeline.history_backfill_status == {
        "status": "success",
        "runs": 1,
        "last_error": "",
    }
    assert [
        call
        for call in calls
        if call[0] in {"name_registry.refresh", "bus.fire_on_bot_connect"}
    ] == [
        ("name_registry.refresh", (bot, "100")),
        ("name_registry.refresh", (bot, "200")),
        ("bus.fire_on_bot_connect", (ctx, bot)),
    ]


@pytest.mark.asyncio
async def test_cancelled_history_handoff_uses_fresh_snapshot_on_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    snapshots: list[tuple[str, ...] | None] = []
    bot = _SequentialGroupListBot(
        [
            [{"group_id": 100, "group_name": "first-attempt"}],
            [{"group_id": 200, "group_name": "retry"}],
            [],
        ]
    )
    ctx = _Context(calls)
    ctx.name_registry = _NameVariationRegistry(calls, failing_group_id="")
    bus = _Bus(calls)
    pipeline_type = _pipeline_type()
    pipeline_module = importlib.import_module("services.routing.connection_pipeline")
    history_module = importlib.import_module("services.history_backfill")

    async def history_backfill_stage(
        stage_ctx: object,
        stage_bot: object,
        group_ids: tuple[str, ...] | None = None,
    ) -> None:
        assert stage_ctx is ctx
        assert stage_bot is bot
        snapshots.append(group_ids)
        if len(snapshots) == 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        pipeline_module,
        "NameVariationRegistry",
        _NameVariationRegistry,
        raising=False,
    )
    monkeypatch.setattr(
        history_module,
        "run_history_backfill",
        history_backfill_stage,
    )
    pipeline = pipeline_type(ctx, bus)

    with pytest.raises(asyncio.CancelledError):
        await pipeline.on_connect(bot)

    assert pipeline.history_backfill_status == {
        "status": "idle",
        "runs": 0,
        "last_error": "",
    }

    await pipeline.on_connect(bot)

    assert snapshots == [("100",), ("200",)]
    assert bot.group_list_call_count == 3
    assert pipeline.history_backfill_status == {
        "status": "success",
        "runs": 1,
        "last_error": "",
    }


@pytest.mark.asyncio
async def test_usage_alert_wires_thresholds_and_notifies_every_admin_best_effort() -> None:
    calls: list[tuple[str, object]] = []
    cache_hit_warn = object()
    cache_alert_window_m = object()
    cache_alert_cooldown_m = object()
    slow_threshold_s = object()
    ctx = _Context(calls)
    ctx.admins = {"1001": object(), "1002": object()}
    ctx.config.llm = SimpleNamespace(
        usage=SimpleNamespace(
            enabled=True,
            slow_threshold_s=slow_threshold_s,
        )
    )
    ctx.config.compact = SimpleNamespace(
        cache_hit_warn=cache_hit_warn,
        cache_alert_window_m=cache_alert_window_m,
        cache_alert_cooldown_m=cache_alert_cooldown_m,
    )
    usage_tracker = _UsageTracker()
    ctx.usage_tracker = usage_tracker
    bus = _Bus(calls)
    bot = _UsageAlertBot(failing_admin_id=1001)
    pipeline = _pipeline_type()(ctx, bus)

    await pipeline.on_connect(bot)

    assert len(usage_tracker.alert_registrations) == 1
    registration = usage_tracker.alert_registrations[0]
    assert registration["cache_hit_warn"] is cache_hit_warn
    assert registration["cache_alert_window_m"] is cache_alert_window_m
    assert registration["cache_alert_cooldown_m"] is cache_alert_cooldown_m
    assert registration["slow_threshold_s"] is slow_threshold_s
    alert_fn = registration["alert_fn"]
    assert callable(alert_fn)
    message = "usage threshold breached"
    try:
        await cast(Any, alert_fn)(message)
    except RuntimeError as exc:
        raise AssertionError(
            "usage alert must continue after one admin notification fails"
        ) from exc

    assert bot.private_messages == [
        (1001, message),
        (1002, message),
    ]


@pytest.mark.asyncio
async def test_inventory_failure_preserves_existing_state_after_core_connect_completes() -> None:
    calls: list[tuple[str, object]] = []
    ctx = _Context(calls)
    existing_inventory: dict[str, dict[str, object]] = {
        "old": {"group_id": "old", "group_name": "existing"},
    }
    ctx.group_inventory = existing_inventory
    ctx.name_registry = None
    ctx.admins = {}
    bus = _Bus(calls)
    bot = _FailingInventoryBot()
    pipeline = _pipeline_type()(ctx, bus)

    try:
        await pipeline.on_connect(bot)
    except RuntimeError as exc:
        raise AssertionError("ordinary inventory failure must be swallowed") from exc

    assert ctx.bot is bot
    assert ctx.protocol_connections.connected_bots == [bot]
    assert calls.count(("outbound_guard.wrap_bot", bot)) == 1
    assert calls.count(("protocol_trace.wrap_bot", bot)) == 1
    assert ctx.llm_client._bot_self_id == bot.self_id
    assert ctx.state_board.bot_self_id == bot.self_id
    assert calls.count(("persona_runtime.bind_bot_self_id", str(bot.self_id))) == 1
    assert calls.count(("scheduler.set_bot", bot)) == 1
    assert ctx.startup_triggered is True
    assert calls.count(("bus.fire_on_bot_connect", (ctx, bot))) == 1
    assert calls.count(("bus.start_tick_loop", ctx)) == 1
    assert ctx.group_inventory is existing_inventory
    assert ctx.group_inventory == {
        "old": {"group_id": "old", "group_name": "existing"},
    }
    assert bot.member_info_call_count == 0
    assert ctx.scheduler.mute_calls == []


@pytest.mark.asyncio
async def test_cancelled_first_connect_rolls_back_startup_claim_for_retry() -> None:
    calls: list[tuple[str, object]] = []
    ctx = _Context(calls)
    bus = _CancellingBus(calls)
    bot = _Bot()
    pipeline = _pipeline_type()(ctx, bus)

    with pytest.raises(asyncio.CancelledError):
        await pipeline.on_connect(bot)

    assert ctx.startup_triggered is False
    assert ("bus.start_tick_loop", ctx) not in calls

    await pipeline.on_connect(bot)

    assert bus.connect_attempts == 2
    assert calls.count(("bus.start_tick_loop", ctx)) == 1
