from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.chat.plugin import ChatPlugin, _sanitize_debug_reply


def test_sanitize_debug_reply_removes_internal_workflow_terms() -> None:
    text = '我的工具列表中没有音乐播放相关的功能，无法执行"开启音乐播放"这一指令。结束本轮。'

    cleaned = _sanitize_debug_reply(text)

    assert "结束本轮" not in cleaned
    assert "pass_turn" not in cleaned.lower()
    assert "内部原因" not in cleaned
    assert "音乐播放相关的功能" in cleaned


def test_sanitize_debug_reply_returns_fallback_when_only_internal_tokens_remain() -> None:
    cleaned = _sanitize_debug_reply("[pass_turn] 本轮未执行工具", fallback="这次没有执行到可用工具。")

    assert cleaned == "这次没有执行到可用工具。"


@pytest.mark.asyncio
async def test_debug_pass_turn_reply_is_user_facing() -> None:
    plugin = ChatPlugin()
    sent: list[str] = []
    bot = SimpleNamespace()

    async def _send(event, message) -> None:
        del event
        sent.append(str(message))

    bot.send = _send
    plugin._ctx = SimpleNamespace(
        sticker_store=None,
        tool_registry=SimpleNamespace(empty=True, to_openai_tools=lambda: []),
        llm_client=SimpleNamespace(
            _call=_fake_debug_call(
                {
                    "text": "",
                    "tool_uses": [SimpleNamespace(name="pass_turn", input={"reason": "结束本轮。内部原因：无法匹配任何工具"}, id="tu_1")],
                    "thinking_blocks": [],
                }
            )
        ),
        humanizer=SimpleNamespace(delay=_noop_delay),
        mood_engine=None,
        affection_engine=None,
        schedule_store=None,
        card_store=None,
        short_term=None,
        msg_log=None,
    )

    cmd_ctx = SimpleNamespace(
        bot=bot,
        event=SimpleNamespace(),
        user_id="1",
        group_id="123",
        is_private=False,
        args="开启音乐播放",
    )

    await plugin._handle_debug(cmd_ctx)

    assert sent
    reply = sent[-1]
    assert "调试：" in reply
    assert "pass_turn" not in reply.lower()
    assert "结束本轮" not in reply
    assert "内部原因" not in reply
    assert "现在没有合适的工具可用" in reply


async def _noop_delay(_text: str) -> None:
    return None


def _fake_debug_call(result: dict[str, object]):
    async def _call(*args, **kwargs):
        del args, kwargs
        return result

    return _call
