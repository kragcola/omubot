"""RED contracts for FoodPlugin feedback-window lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from kernel.types import MessageContext
from plugins.food import plugin as food_plugin_module
from plugins.food.plugin import FoodPlugin


@pytest.mark.asyncio
async def test_expired_feedback_windows_are_reclaimed_globally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000.0
    monkeypatch.setattr(food_plugin_module.time, "time", lambda: now)
    plugin = _food_plugin()
    sent: list[tuple[str, str, str]] = []
    plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

    for index in range(64):
        await plugin._handle_eat(
            _cmd_ctx(user_id=f"user-{index}", group_id=f"group-{index}"),
        )

    assert len(sent) == 64
    assert len(plugin._pending_feedback) == 64

    now += plugin._feedback_window + 1
    consumed = await plugin.on_message(
        _message_ctx(user_id="observer", group_id="observer-group", text="普通消息"),
    )

    assert consumed is False
    assert len(plugin._pending_feedback) == 0


@pytest.mark.asyncio
async def test_same_user_feedback_in_two_groups_starts_two_background_tasks() -> None:
    plugin = _food_plugin()
    sent: list[tuple[str, str, str]] = []
    plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]
    await plugin._handle_eat(_cmd_ctx(user_id="user-1", group_id="group-1"))
    await plugin._handle_eat(_cmd_ctx(user_id="user-1", group_id="group-2"))

    first_started = asyncio.Event()
    first_release = asyncio.Event()
    first_completed = asyncio.Event()
    second_started = asyncio.Event()
    second_completed = asyncio.Event()

    async def controlled_feedback(
        bot: Any,
        group_id: str,
        user_id: str,
        user_feedback: str,
        *,
        message_id: int | None = None,
    ) -> None:
        del bot, user_id, user_feedback, message_id
        if group_id == "group-1":
            first_started.set()
            await first_release.wait()
            first_completed.set()
            return
        second_started.set()
        second_completed.set()

    plugin._feedback_recommend = controlled_feedback  # type: ignore[method-assign]

    try:
        first_consumed = await plugin.on_message(
            _message_ctx(user_id="user-1", group_id="group-1", text="换一个"),
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)

        second_consumed = await plugin.on_message(
            _message_ctx(user_id="user-1", group_id="group-2", text="换一个"),
        )
        try:
            await asyncio.wait_for(second_started.wait(), timeout=0.2)
            second_did_start = True
        except TimeoutError:
            second_did_start = False
    finally:
        first_release.set()
        await asyncio.wait_for(first_completed.wait(), timeout=1)
        await asyncio.sleep(0)

    assert first_consumed is True
    assert second_consumed is True
    assert second_did_start, "feedback in the second group was consumed but never started"
    assert second_completed.is_set()
    assert len(plugin._pending_feedback) == 0
    assert len(plugin._feedback_tasks) == 0


def _food_plugin() -> FoodPlugin:
    plugin = FoodPlugin()
    cast(Any, plugin)._ctx = SimpleNamespace(
        llm_client=_FakeLLM("热汤面"),
        tool_registry=None,
        scheduler=None,
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


def _cmd_ctx(*, user_id: str, group_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        args="",
        is_private=False,
        group_id=group_id,
        event=SimpleNamespace(message_id=1),
        bot=SimpleNamespace(),
    )


def _message_ctx(*, user_id: str, group_id: str, text: str) -> MessageContext:
    return MessageContext(
        session_id=f"group_{group_id}",
        group_id=group_id,
        user_id=user_id,
        content=text,
        raw_message={"plain_text": text},
        message_id=2,
        bot=SimpleNamespace(),
    )


def _capture_reply(sent: list[tuple[str, str, str]]):
    async def _send_reply(cmd_ctx: Any, text: str) -> None:
        sent.append((str(cmd_ctx.user_id), str(cmd_ctx.group_id), text))

    return _send_reply


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def _call(self, *args, **kwargs) -> dict[str, str]:
        del args, kwargs
        return {"text": self._text}
