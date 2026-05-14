"""Regression tests for FoodPlugin tutorial gating and memory-card side effects."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.food.plugin import FoodPlugin
from services.memory.card_store import CardStore, NewCard


@pytest.mark.asyncio
async def test_food_handle_eat_shows_tutorial_when_user_has_no_food_cards(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert len(sent) == 2
        assert "你还没设置过口味偏好呢" in sent[0]
        assert "/food help" in sent[0]
        assert sent[1] == "热汤面"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_handle_eat_skips_tutorial_when_served_history_exists(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="event",
            scope="user",
            scope_id="123",
            content="推荐了热汤面（05-04 22:27）",
            source="food_plugin",
        ))

        plugin = _food_plugin(store, _FakeLLM("鸡蛋羹"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["鸡蛋羹"]
    finally:
        await store.close()


@pytest.mark.parametrize("history_status", ["expired", "superseded"])
@pytest.mark.asyncio
async def test_food_handle_eat_skips_tutorial_when_only_inactive_food_history_exists(
    tmp_path,
    history_status: str,
) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        card_id = await store.add_card(NewCard(
            category="event",
            scope="user",
            scope_id="123",
            content="推荐了热汤面（05-04 22:27）",
            source="food_plugin",
        ))
        await store.update_card(card_id, status=history_status)

        plugin = _food_plugin(store, _FakeLLM("鸡蛋羹"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["鸡蛋羹"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_handle_eat_skips_tutorial_when_only_food_series_exists(tmp_path) -> None:
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

        plugin = _food_plugin(store, _FakeLLM("鸡蛋羹"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["鸡蛋羹"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_handle_eat_skips_tutorial_when_preference_cards_exist(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="preference",
            scope="user",
            scope_id="123",
            content="喜欢吃辣的",
            source="user_config",
        ))

        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["热汤面"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_handle_eat_skips_tutorial_when_location_fact_exists(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await store.add_card(NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="位于北京",
            source="user_config",
        ))

        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["热汤面"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_handle_eat_tutorial_is_shown_once_per_process(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]
        plugin._record_served_safe = _async_noop  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))
        await plugin._handle_eat(_cmd_ctx("123"))

        tutorial_msgs = [msg for msg in sent if "你还没设置过口味偏好呢" in msg]
        assert len(tutorial_msgs) == 1
        assert sent.count("热汤面") == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_tutorial_persists_even_when_recommendation_fails(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]
        plugin._do_recommend = _async_none  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert len(sent) == 2
        assert "你还没设置过口味偏好呢" in sent[0]
        assert sent[1] == "脑袋空空了…等会儿再问我吧"

        restarted = _food_plugin(store, _FakeLLM("鸡蛋羹"))
        restarted_sent: list[str] = []
        restarted._send_reply = _capture_reply(restarted_sent)  # type: ignore[method-assign]

        await restarted._handle_eat(_cmd_ctx("123"))

        assert restarted_sent == ["鸡蛋羹"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_handle_eat_records_served_series_on_success(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(store, _FakeLLM("热汤面"))
        plugin._tutorial_shown.add("123")
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
        plugin._tutorial_shown.add("123")
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


def _food_plugin(store: CardStore, llm_client: Any) -> FoodPlugin:
    plugin = FoodPlugin()
    plugin._ctx = SimpleNamespace(
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


async def _async_noop(*args, **kwargs) -> None:
    del args, kwargs


async def _async_none(*args, **kwargs) -> None:
    del args, kwargs
    return None


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def _call(self, *args, **kwargs) -> dict[str, str]:
        del args, kwargs
        return {"text": self._text}


class _FailingLLM:
    async def _call(self, *args, **kwargs) -> dict[str, str]:
        del args, kwargs
        raise RuntimeError("service busy")
