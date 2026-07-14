"""Regression tests for FoodPlugin memory-card side effects."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from plugins.food.plugin import FoodPlugin
from services.memory.card_store import CardStore


@pytest.mark.asyncio
async def test_food_handle_eat_records_served_series_on_success(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        await _mark_tutorial_shown(store, "123")
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["热汤面"]
        series = await store.get_series_by_key("food_served:123")
        assert series is not None
        cards = await store.get_series_cards(series.series_id)
        assert len(cards) == 1
        assert cards[0].category == "event"
        assert "推荐了热汤面" in cards[0].content
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_handle_eat_uses_local_library_when_llm_fails(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(store, _FailingLLM())
        await _mark_tutorial_shown(store, "123")
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent
        assert sent[-1] in {"热汤面", "鸡蛋羹"}
        series = await store.get_series_by_key("food_served:123")
        assert series is not None
        cards = await store.get_series_cards(series.series_id)
        assert len(cards) == 1
        assert sent[-1] in cards[0].content
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_tutorial_is_durable_across_plugin_restart(tmp_path) -> None:
    db_path = tmp_path / "memory.db"
    first_store = CardStore(str(db_path))
    await first_store.init()
    try:
        first = _food_plugin(first_store, _FakeLLM("热汤面"))
        first_sent: list[str] = []
        first._send_reply = _capture_reply(first_sent)  # type: ignore[method-assign]

        await first._handle_eat(_cmd_ctx("123"))

        assert first_sent == [
            "你还没设置过口味偏好呢~\n"
            "发送 /food help 查看如何设置，之后推荐会更准哦\n"
            "（本消息只显示一次）",
            "热汤面",
        ]
        assert await first_store.get_series_by_key("food_tutorial:123") is not None
    finally:
        await first_store.close()

    restarted_store = CardStore(str(db_path))
    await restarted_store.init()
    try:
        restarted = _food_plugin(restarted_store, _FakeLLM("鸡蛋羹"))
        restarted_sent: list[str] = []
        restarted._send_reply = _capture_reply(restarted_sent)  # type: ignore[method-assign]

        await restarted._handle_eat(_cmd_ctx("123"))

        assert restarted_sent == ["鸡蛋羹"]
    finally:
        await restarted_store.close()


@pytest.mark.asyncio
async def test_food_existing_history_migrates_without_showing_tutorial(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.get_or_create_series(
            "food_served:123",
            scope="user",
            scope_id="123",
            label="食物推荐记录",
            source="food_plugin",
        )
        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["热汤面"]
        assert await store.get_series_by_key("food_tutorial:123") is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_concurrent_plugin_instances_only_send_one_tutorial(tmp_path) -> None:
    db_path = tmp_path / "memory.db"
    first_store = CardStore(str(db_path))
    second_store = CardStore(str(db_path))
    await first_store.init()
    await second_store.init()
    second_read_missing = asyncio.Event()
    release_second = asyncio.Event()
    first_llm_started = asyncio.Event()
    release_first_llm = asyncio.Event()
    try:
        first = _food_plugin(
            first_store,
            _BlockingLLM("热汤面", started=first_llm_started, release=release_first_llm),
        )
        second = _food_plugin(
            _PauseAfterMissingTutorialStore(
                second_store,
                saw_missing=second_read_missing,
                release=release_second,
            ),
            _FakeLLM("鸡蛋羹"),
        )
        first_sent: list[str] = []
        second_sent: list[str] = []
        first._send_reply = _capture_reply(first_sent)  # type: ignore[method-assign]
        second._send_reply = _capture_reply(second_sent)  # type: ignore[method-assign]

        second_task = asyncio.create_task(second._handle_eat(_cmd_ctx("123")))
        await asyncio.wait_for(second_read_missing.wait(), timeout=1)
        first_task = asyncio.create_task(first._handle_eat(_cmd_ctx("123")))
        await asyncio.wait_for(first_llm_started.wait(), timeout=1)

        release_second.set()
        await asyncio.wait_for(second_task, timeout=1)
        release_first_llm.set()
        await asyncio.wait_for(first_task, timeout=1)

        tutorial_count = sum(
            text.endswith("（本消息只显示一次）")
            for text in [*first_sent, *second_sent]
        )
        assert tutorial_count == 1
        assert await first_store.get_series_by_key("food_tutorial:123") is not None
    finally:
        release_second.set()
        release_first_llm.set()
        await second_store.close()
        await first_store.close()


def _food_plugin(store: Any, llm_client: Any) -> FoodPlugin:
    plugin = FoodPlugin()
    cast(Any, plugin)._ctx = SimpleNamespace(
        card_store=store,
        llm_client=llm_client,
        tool_registry=None,
    )
    plugin._search_enabled = False
    plugin._food_library_max_items = 40
    plugin._food_library = [
        _food("热汤面"),
        _food("鸡蛋羹"),
    ]
    return plugin


def _food(name: str) -> dict[str, str]:
    return {
        "name": name,
        "brand": "",
        "taste": "清淡",
        "category": "主食",
        "staple": "面",
        "cooking_method": "煮",
        "temperature": "热",
        "available_time": "不限",
    }


def _cmd_ctx(user_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        args="",
        is_private=False,
        group_id="456",
        event=SimpleNamespace(message_id=1),
        bot=SimpleNamespace(),
    )


def _capture_reply(sent: list[str]):
    async def _send_reply(cmd_ctx: Any, text: str) -> None:
        del cmd_ctx
        sent.append(text)

    return _send_reply


async def _mark_tutorial_shown(store: CardStore, user_id: str) -> None:
    await store.get_or_create_series(
        f"food_tutorial:{user_id}",
        scope="user",
        scope_id=user_id,
        label="食物推荐教程",
        source="food_plugin",
    )


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def _call(self, *args, **kwargs) -> dict[str, str]:
        del args, kwargs
        return {"text": self._text}


class _BlockingLLM(_FakeLLM):
    def __init__(self, text: str, *, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(text)
        self._started = started
        self._release = release

    async def _call(self, *args, **kwargs) -> dict[str, str]:
        self._started.set()
        await self._release.wait()
        return await super()._call(*args, **kwargs)


class _PauseAfterMissingTutorialStore:
    def __init__(
        self,
        store: CardStore,
        *,
        saw_missing: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._store = store
        self._saw_missing = saw_missing
        self._release = release
        self._paused = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    async def get_series_by_key(self, series_key: str) -> Any:
        series = await self._store.get_series_by_key(series_key)
        if series_key == "food_tutorial:123" and series is None and not self._paused:
            self._paused = True
            self._saw_missing.set()
            await self._release.wait()
        return series


class _FailingLLM:
    async def _call(self, *args, **kwargs) -> dict[str, str]:
        del args, kwargs
        raise RuntimeError("service busy")
