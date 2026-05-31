from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from services.llm.anchor_reinjection import AnchorConfig, AnchorReinjector
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.registry import ToolRegistry


def _messages(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    return list(rows)


def _reinjector(*, enabled: bool = True, **overrides: Any) -> AnchorReinjector:
    config = AnchorConfig(
        enabled=enabled,
        min_turns_between_anchors=int(overrides.pop("min_turns_between_anchors", 1)),
        max_turns_without_anchor=int(overrides.pop("max_turns_without_anchor", 3)),
        anchor_token_budget=int(overrides.pop("anchor_token_budget", 80)),
    )
    return AnchorReinjector(
        bot_name="凤笑梦",
        personality="元气、反应快、有一点调皮。",
        proactive="有人叫名字就接一句。",
        voice_text="短句优先\n别太正式",
        examples_text=(
            "正例：用户：你是谁？ / 回复：我是凤笑梦呀，在群里陪你们聊天的。\n"
            "反例：作为凤笑梦，根据我的设定…… -> 别这么正式啦，我直接说就好。"
        ),
        config=config,
        **overrides,
    )


def _result(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "tool_uses": [],
        "input_tokens": 64,
        "output_tokens": 16,
        "cache_read": 0,
        "cache_create": 0,
    }


def _normalize_reply(text: str | None) -> str:
    return str(text or "").replace("\n", "").replace(" ", "").rstrip("。")


async def _client(
    persona_runtime: PersonaRuntime,
    *,
    short_term: ShortTermMemory | None = None,
    timeline: GroupTimeline | None = None,
    anchor_enabled: bool = True,
    max_turns_without_anchor: int = 1,
    min_turns_between_anchors: int = 1,
) -> LLMClient:
    return LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=short_term or ShortTermMemory(),
        tools=ToolRegistry(),
        group_timeline=timeline,
        thinker_enabled=False,
        runtime_state=SimpleNamespace(),
        anchor_reinjection_config=SimpleNamespace(
            enabled=anchor_enabled,
            min_turns_between_anchors=min_turns_between_anchors,
            max_turns_without_anchor=max_turns_without_anchor,
            anchor_token_budget=80,
        ),
    )


def test_should_inject_when_turn_threshold_is_reached() -> None:
    reinjector = _reinjector(max_turns_without_anchor=3)

    messages = _messages(
        {"role": "user", "content": "第一轮聊吃的"},
        {"role": "assistant", "content": "嗯嗯"},
        {"role": "user", "content": "第二轮还在聊吃的"},
        {"role": "assistant", "content": "好呀"},
        {"role": "user", "content": "第三轮继续"},
    )

    assert reinjector.should_inject(messages, last_anchor_turn=0) is True


def test_should_inject_on_tool_result_boundary() -> None:
    reinjector = _reinjector(min_turns_between_anchors=1, max_turns_without_anchor=99)
    messages = _messages(
        {"role": "user", "content": "帮我查一下天气"},
        {"role": "assistant", "content": "我去看一下"},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "明天 26 度"},
                {"type": "text", "text": "继续聊"},
            ],
        },
    )

    assert reinjector.should_inject(messages, last_anchor_turn=1) is True


def test_should_inject_on_new_mention_boundary() -> None:
    reinjector = _reinjector(min_turns_between_anchors=1, max_turns_without_anchor=99)
    messages = _messages(
        {"role": "user", "content": "先聊电影"},
        {"role": "assistant", "content": "好呀"},
        {"role": "user", "content": "再聊作业"},
        {"role": "assistant", "content": "嗯"},
        {"role": "user", "content": "@凤笑梦 你看这个"},
    )

    assert reinjector.should_inject(messages, last_anchor_turn=1) is True


def test_should_inject_on_topic_shift_boundary() -> None:
    reinjector = _reinjector(min_turns_between_anchors=1, max_turns_without_anchor=99)
    messages = _messages(
        {"role": "user", "content": "午饭 吃 面 吗"},
        {"role": "assistant", "content": "行"},
        {"role": "user", "content": "面条 汤底 辣 不辣"},
        {"role": "assistant", "content": "微辣"},
        {"role": "user", "content": "相机 镜头 光圈 怎么选"},
        {"role": "assistant", "content": "看需求"},
        {"role": "user", "content": "夜景 拍摄 快门 多少"},
    )

    assert reinjector.should_inject(messages, last_anchor_turn=1) is True


def test_build_anchor_message_contains_runtime_identity_and_voice() -> None:
    reinjector = _reinjector()

    message = reinjector.build_anchor_message()

    assert message["role"] == "user"
    assert message["content"].startswith("[ANCHOR]")
    assert "凤笑梦" in message["content"]
    assert "元气" in message["content"]
    assert "示例语气" in message["content"]


@pytest.mark.asyncio
async def test_chat_injects_anchor_only_into_request_messages_and_not_short_term(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    short_term = ShortTermMemory()
    client = await _client(persona_runtime, short_term=short_term, max_turns_without_anchor=1)
    captured: dict[str, Any] = {}

    async def _fake_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["messages"] = args[5]
        return _result("我先接一下。")

    try:
        with patch("services.llm.client.call_api", new=_fake_call_api):
            reply = await client.chat(
                session_id="private_100",
                user_id="100",
                user_content="hello",
                identity=identity_snapshot,
            )
    finally:
        await client.close()

    assert _normalize_reply(reply) == "我先接一下"
    request_messages = captured["messages"]
    assert request_messages[-1]["role"] == "user"
    assert str(request_messages[-1]["content"]).startswith("[ANCHOR]")
    stored_messages = short_term.get("private_100")
    assert [message["role"] for message in stored_messages] == ["user", "assistant"]
    assert all("[ANCHOR]" not in str(message["content"]) for message in stored_messages)
    assert client._anchor_last_turns["session:private_100"] == 1


@pytest.mark.asyncio
async def test_chat_cancelled_before_call_returns_does_not_commit_anchor_turn(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    short_term = ShortTermMemory()
    client = await _client(persona_runtime, short_term=short_term, max_turns_without_anchor=1)

    async def _slow_call_api(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(60)
        return _result("我先接一下。")

    try:
        with patch("services.llm.client.call_api", new=_slow_call_api):
            task = asyncio.create_task(
                client.chat(
                    session_id="private_cancel",
                    user_id="100",
                    user_content="hello",
                    identity=identity_snapshot,
                )
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await client.close()

    assert client._anchor_last_turns == {}
    assert [message["role"] for message in short_term.get("private_cancel")] == ["user"]
