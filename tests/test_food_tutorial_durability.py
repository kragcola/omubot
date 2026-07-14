"""Durability contracts for the one-time FoodPlugin tutorial."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from plugins.food.plugin import FoodPlugin
from services.memory.card_store import CardStore

TUTORIAL_TEXT = (
    "你还没设置过口味偏好呢~\n"
    "发送 /food help 查看如何设置，之后推荐会更准哦\n"
    "（本消息只显示一次）"
)
_NO_CARD_STORE = object()


@pytest.mark.asyncio
async def test_first_eat_without_card_store_skips_tutorial_but_recommends() -> None:
    plugin = _food_plugin(_FakeLLM("热汤面"))
    sent: list[str] = []
    plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

    await plugin._handle_eat(_cmd_ctx("123"))

    assert sent == ["热汤面"]


@pytest.mark.asyncio
async def test_tutorial_lookup_failure_skips_tutorial_but_recommends(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        failing_store = _TutorialLookupFailingStore(store, "food_tutorial:123")
        plugin = _food_plugin(_FakeLLM("鸡蛋羹"), card_store=failing_store)
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["鸡蛋羹"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_first_eat_sends_one_tutorial_and_two_recommendations(
    tmp_path,
) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        delayed_store = _DelayedTutorialLookupStore(store)
        plugin = _food_plugin(_FakeLLM("热汤面"), card_store=delayed_store)
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await asyncio.gather(
            plugin._handle_eat(_cmd_ctx("123", message_id=1)),
            plugin._handle_eat(_cmd_ctx("123", message_id=2)),
        )

        recommendations = [message for message in sent if message != TUTORIAL_TEXT]

        assert sent.count(TUTORIAL_TEXT) == 1
        assert len(recommendations) == 2
        assert set(recommendations) <= {"热汤面", "鸡蛋羹"}
        assert len(sent) == 3
        assert await store.get_series_by_key("food_tutorial:123") is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_existing_tutorial_marker_skips_tutorial(tmp_path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        await _mark_tutorial_shown(store, "123")
        plugin = _food_plugin(_FakeLLM("鸡蛋羹"), card_store=store)
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_cmd_ctx("123"))

        assert sent == ["鸡蛋羹"]
        assert await store.get_series_by_key("food_tutorial:123") is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_tutorial_marker_survives_reply_error_and_prevents_retry(
    tmp_path,
) -> None:
    db_path = tmp_path / "memory.db"
    first_store = CardStore(str(db_path))
    await first_store.init()
    try:
        first = _food_plugin(_FakeLLM("热汤面"), card_store=first_store)
        attempted: list[str] = []
        first._send_reply = _failing_tutorial_reply(  # type: ignore[method-assign]
            attempted,
            RuntimeError("send failed"),
        )

        with pytest.raises(RuntimeError, match="send failed"):
            await first._handle_eat(_cmd_ctx("123"))

        assert attempted == [TUTORIAL_TEXT]
        assert await first_store.get_series_by_key("food_tutorial:123") is not None
    finally:
        await first_store.close()

    restarted_store = CardStore(str(db_path))
    await restarted_store.init()
    try:
        restarted = _food_plugin(
            _FakeLLM("鸡蛋羹"),
            card_store=restarted_store,
        )
        restarted_sent: list[str] = []
        restarted._send_reply = _capture_reply(  # type: ignore[method-assign]
            restarted_sent,
        )

        await restarted._handle_eat(_cmd_ctx("123"))

        assert restarted_sent == ["鸡蛋羹"]
    finally:
        await restarted_store.close()


@pytest.mark.asyncio
async def test_tutorial_marker_survives_reply_cancellation_and_prevents_retry(
    tmp_path,
) -> None:
    db_path = tmp_path / "memory.db"
    first_store = CardStore(str(db_path))
    await first_store.init()
    try:
        first = _food_plugin(_FakeLLM("热汤面"), card_store=first_store)
        attempted: list[str] = []
        first._send_reply = _failing_tutorial_reply(  # type: ignore[method-assign]
            attempted,
            asyncio.CancelledError(),
        )

        with pytest.raises(asyncio.CancelledError):
            await first._handle_eat(_cmd_ctx("123"))

        assert attempted == [TUTORIAL_TEXT]
        assert await first_store.get_series_by_key("food_tutorial:123") is not None
    finally:
        await first_store.close()

    restarted_store = CardStore(str(db_path))
    await restarted_store.init()
    try:
        restarted = _food_plugin(
            _FakeLLM("鸡蛋羹"),
            card_store=restarted_store,
        )
        restarted_sent: list[str] = []
        restarted._send_reply = _capture_reply(  # type: ignore[method-assign]
            restarted_sent,
        )

        await restarted._handle_eat(_cmd_ctx("123"))

        assert restarted_sent == ["鸡蛋羹"]
    finally:
        await restarted_store.close()


def _food_plugin(
    llm_client: Any,
    *,
    card_store: Any = _NO_CARD_STORE,
) -> FoodPlugin:
    plugin = FoodPlugin()
    ctx = {
        "llm_client": llm_client,
        "tool_registry": None,
    }
    if card_store is not _NO_CARD_STORE:
        ctx["card_store"] = card_store
    cast(Any, plugin)._ctx = SimpleNamespace(**ctx)
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


def _cmd_ctx(user_id: str, *, message_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        args="",
        is_private=False,
        group_id="456",
        event=SimpleNamespace(message_id=message_id),
        bot=SimpleNamespace(),
    )


def _capture_reply(sent: list[str]):
    async def _send_reply(cmd_ctx: Any, text: str) -> None:
        del cmd_ctx
        sent.append(text)

    return _send_reply


def _failing_tutorial_reply(attempted: list[str], error: BaseException):
    async def _send_reply(cmd_ctx: Any, text: str) -> None:
        del cmd_ctx
        attempted.append(text)
        if text == TUTORIAL_TEXT:
            raise error

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


class _TutorialLookupFailingStore:
    def __init__(self, store: CardStore, failing_key: str) -> None:
        self._store = store
        self._failing_key = failing_key

    async def get_series_by_key(self, key: str):
        if key == self._failing_key:
            raise RuntimeError("tutorial lookup unavailable")
        return await self._store.get_series_by_key(key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class _DelayedTutorialLookupStore:
    def __init__(self, store: CardStore) -> None:
        self._store = store

    async def get_series_by_key(self, key: str):
        if key.startswith("food_tutorial:"):
            await asyncio.sleep(0.02)
        return await self._store.get_series_by_key(key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)
