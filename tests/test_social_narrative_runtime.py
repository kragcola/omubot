"""RED contracts for Social Narrative runtime and scheduler wiring."""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import fields
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bootstrap.chat_runtime import build_chat_runtime, create_chat_runtime_assembly
from kernel.config import GroupConfig
from kernel.types import PluginContext, TriggerContext
from services.memory.timeline import GroupTimeline
from services.persona import IdentitySnapshot
from services.scheduler import GroupChatScheduler


class _Runtime:
    def identity_snapshot(self) -> IdentitySnapshot:
        return IdentitySnapshot(
            id="bot",
            name="bot",
            personality="test",
            proactive="on",
        )


class _CaptureLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _CloseProbe:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _builder_tree() -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(build_chat_runtime)))


def _constructor_calls(tree: ast.AST, constructor: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == constructor
    ]


def _db_path_expression(call: ast.Call) -> str:
    for keyword in call.keywords:
        if keyword.arg == "db_path":
            return ast.dump(keyword.value, include_attributes=False)
    assert call.args, "runtime store constructors must receive an explicit database path"
    return ast.dump(call.args[0], include_attributes=False)


def _publishes_social_narrative_store(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "ctx"
                and target.attr == "social_narrative_store"
                for target in targets
            ):
                return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "assembly"
            and node.func.attr == "publish"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "social_narrative_store"
        ):
            return True
    return False


@pytest.mark.asyncio
async def test_scheduler_passes_anchor_user_text_and_message_id_to_llm() -> None:
    timeline = GroupTimeline()
    timeline.add(
        "200",
        role="user",
        speaker="旧成员(99)",
        content="这是更早的待处理消息",
        message_id=6999,
    )
    anchor_text = "我们刚才一起把排练节奏理顺了"
    timeline.add(
        "200",
        role="user",
        speaker="当前成员(target)",
        content=anchor_text,
        message_id=7001,
    )
    llm = _CaptureLLM()
    scheduler = GroupChatScheduler(
        llm=cast(Any, llm),
        timeline=timeline,
        persona_runtime=cast(Any, _Runtime()),
        group_config=GroupConfig(talk_value=1.0, planner_smooth=0.0),
    )
    trigger = TriggerContext(
        reason="有人@了你",
        mode="at_mention",
        target_message_id=7001,
        target_user_id="target",
    )

    try:
        scheduler.notify(
            "200",
            trigger=trigger,
            user_id="target",
            message_text=anchor_text,
            message_id=7001,
        )
        slot = scheduler.get_slot("200")
        assert slot is not None
        task = slot.running_task
        assert task is not None, "an addressed message must schedule one LLM call"
        slot.last_user_id = "later"
        timeline.add(
            "200",
            role="user",
            speaker="后续成员(later)",
            content="这是排队后到达的另一条消息",
            message_id=7002,
        )
        await task

        assert len(llm.calls) == 1
        call = llm.calls[0]
        assert call["trigger"].target_message_id == 7001
        assert call["user_id"] == "target", (
            "group chat must keep the trigger's target user as the LLM owner; "
            "a later speaker must not overwrite the anchored user"
        )
        assert call["user_content"] == anchor_text, (
            "group chat must pass the current anchor message's real user text; "
            "a later pending message must not replace factual evidence for post-reply adapters"
        )
    finally:
        await scheduler.close()


def test_plugin_context_exposes_social_narrative_store() -> None:
    field_names = {field.name for field in fields(PluginContext)}
    assert "social_narrative_store" in field_names, (
        "PluginContext must expose the bootstrap-owned SocialNarrativeStore"
    )


def test_production_builder_uses_card_store_database_for_social_narrative() -> None:
    tree = _builder_tree()
    card_calls = _constructor_calls(tree, "CardStore")
    narrative_calls = _constructor_calls(tree, "SocialNarrativeStore")

    assert len(card_calls) == 1
    assert len(narrative_calls) == 1, (
        "build_chat_runtime must create exactly one SocialNarrativeStore"
    )
    assert _db_path_expression(narrative_calls[0]) == _db_path_expression(card_calls[0]), (
        "SocialNarrativeStore and CardStore must open the same memory_cards.db"
    )
    assert _publishes_social_narrative_store(tree), (
        "build_chat_runtime must publish SocialNarrativeStore to PluginContext"
    )


@pytest.mark.asyncio
async def test_production_assembly_closes_owned_social_narrative_store_once() -> None:
    store = _CloseProbe()
    ctx = SimpleNamespace(social_narrative_store=None)

    async def builder(_assembly: Any) -> None:
        ctx.social_narrative_store = store

    assembly = create_chat_runtime_assembly(ctx, builder)
    await assembly.start()
    await assembly.close()
    await assembly.close()

    assert store.close_calls == 1


@pytest.mark.asyncio
async def test_social_narrative_start_failure_closes_store_and_rolls_back_context() -> None:
    store = _CloseProbe()
    ctx = SimpleNamespace()
    startup_error = RuntimeError("failure after social narrative init")

    async def builder(_assembly: Any) -> None:
        ctx.social_narrative_store = store
        raise startup_error

    assembly = create_chat_runtime_assembly(ctx, builder)

    with pytest.raises(RuntimeError) as raised:
        await assembly.start()

    assert raised.value is startup_error
    assert store.close_calls == 1
    assert not hasattr(ctx, "social_narrative_store")
