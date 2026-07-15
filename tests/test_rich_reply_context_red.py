from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from kernel.router import _render_message


def _text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


class _ReplyChainBot:
    def __init__(self, messages: dict[int, dict[str, Any]]) -> None:
        self._messages = messages
        self.get_msg_ids: list[int] = []
        self.call_api_calls: list[tuple[str, dict[str, Any]]] = []

    async def get_msg(self, message_id: int) -> dict[str, Any]:
        self.get_msg_ids.append(message_id)
        return self._messages[message_id]

    async def call_api(self, api: str, **data: Any) -> Any:
        self.call_api_calls.append((api, data))
        raise AssertionError(f"reply ancestry must use get_msg, not call_api: {api}")


@pytest.mark.asyncio
@pytest.mark.parametrize("in_group", [False, True], ids=["private", "group-active"])
async def test_render_message_completes_reply_ancestry_from_embedded_reply_id(
    in_group: bool,
) -> None:
    """The adapter-provided parent is authoritative; only its reply segment is fetched."""
    parent = SimpleNamespace(
        message_id=202,
        sender=SimpleNamespace(user_id="22", nickname="Parent"),
        message=Message(
            [
                MessageSegment("reply", {"id": "101"}),
                MessageSegment.text("parent body"),
            ]
        ),
    )
    bot = _ReplyChainBot(
        {
            101: {
                "message_id": 101,
                "sender": {"user_id": 11, "nickname": "Grand"},
                "message": [
                    {"type": "text", "data": {"text": "grand body"}},
                ],
            }
        }
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current body")]),
        reply=parent,
        self_id="42",
        bot=bot,  # type: ignore[arg-type]
        in_group=in_group,
        vision_enabled=False,
    )
    text = _text_content(rendered)

    assert "Grand" in text and "grand body" in text
    assert "Parent" in text and "parent body" in text
    assert "current body" in text
    assert text.index("grand body") < text.index("parent body") < text.index("current body")
    assert bot.get_msg_ids == [101]
    assert bot.call_api_calls == []
