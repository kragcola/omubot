from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from kernel.config import BotConfig
from kernel.types import MessageContext, PluginContext
from plugins.chat.plugin import ChatPlugin
from services.humanization import REGISTER_LABEL_SLOT, RegisterClassifier, create_humanization_state_bus
from services.system_module import Scope


class _FakeLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[Any] = []

    async def _call(self, request: Any) -> dict[str, str]:
        self.requests.append(request)
        return {"text": json.dumps(self.payload, ensure_ascii=False)}


class _SlowLLM:
    async def _call(self, request: Any) -> dict[str, str]:
        del request
        await asyncio.sleep(60)
        return {"text": '{"label":"playful","confidence":1.0}'}


class _Timeline:
    def get_turns(self, group_id: str) -> list[dict[str, Any]]:
        assert group_id == "100"
        return [
            {"role": "user", "content": "alice: 先认真讲一下"},
            {"role": "assistant", "content": "可以，我先给结论。"},
        ]


def _message(text: str = "这事认真点说", *, group_id: str = "100") -> MessageContext:
    return MessageContext(
        session_id=f"group_{group_id}",
        group_id=group_id,
        user_id="u1",
        content=text,
        raw_message={},
        nickname="alice",
    )


def _plugin_ctx(
    *,
    register_classifier: bool,
    classifier: object | None,
    runtime_state: object | None,
    runtime_groups: list[str] | None = None,
) -> PluginContext:
    return PluginContext(
        config=BotConfig.model_validate({
            "humanization": {
                "register_classifier": register_classifier,
                "runtime_groups": runtime_groups or [],
            }
        }),
        runtime_state=runtime_state,
        timeline=_Timeline(),
        humanization_register_classifier=classifier,
    )


def test_chat_plugin_wires_register_classifier_when_flag_enabled() -> None:
    plugin = ChatPlugin()
    ctx = PluginContext()
    llm = object()

    plugin._wire_humanization_runtime(
        ctx,
        BotConfig.model_validate({"humanization": {"register_classifier": True}}),
        llm,
    )

    assert isinstance(ctx.humanization_register_classifier, RegisterClassifier)


def test_chat_plugin_leaves_register_classifier_unwired_by_default() -> None:
    plugin = ChatPlugin()
    ctx = PluginContext(humanization_register_classifier=object())

    plugin._wire_humanization_runtime(ctx, BotConfig(), object())

    assert ctx.humanization_register_classifier is None


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_default_off_does_not_consume_or_write() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    classifier = RegisterClassifier(_FakeLLM({"label": "serious", "confidence": 0.9}))
    plugin._ctx = _plugin_ctx(register_classifier=False, classifier=classifier, runtime_state=bus)

    consumed = await plugin.on_message(_message())

    assert consumed is False
    assert bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1")) is None


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_skips_non_gray_group() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    llm = _FakeLLM({"label": "serious", "confidence": 0.9})
    plugin._ctx = _plugin_ctx(
        register_classifier=True,
        classifier=RegisterClassifier(llm),
        runtime_state=bus,
        runtime_groups=["200"],
    )

    consumed = await plugin.on_message(_message(group_id="100"))

    assert consumed is False
    assert llm.requests == []
    assert bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1")) is None


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_writes_state_and_does_not_consume() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    llm = _FakeLLM({"label": "serious", "confidence": 0.9, "reason": "在处理问题"})
    plugin._ctx = _plugin_ctx(
        register_classifier=True,
        classifier=RegisterClassifier(llm),
        runtime_state=bus,
    )

    consumed = await plugin.on_message(_message())

    snapshot = bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1"))
    assert consumed is False
    assert snapshot is not None
    assert snapshot.value["label"] == "serious"
    assert snapshot.value["window_size"] == 3
    assert len(llm.requests) == 1


@pytest.mark.asyncio
async def test_chat_plugin_register_classifier_cancel_path_does_not_dirty_write() -> None:
    plugin = ChatPlugin()
    bus = create_humanization_state_bus()
    plugin._ctx = _plugin_ctx(
        register_classifier=True,
        classifier=RegisterClassifier(_SlowLLM()),
        runtime_state=bus,
    )
    task = asyncio.create_task(plugin.on_message(_message("接一下这个梗")))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bus.get(REGISTER_LABEL_SLOT, scope=Scope(session_id="group_100", group_id="100", user_id="u1")) is None
