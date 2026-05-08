"""Regression tests for FoodPlugin memory-card side effects."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.food.plugin import FoodPlugin
from services.memory.card_store import CardStore


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
