"""PromptBuilder integration tests (v2 only — backed by PersonaRuntime)."""
from __future__ import annotations

import pytest

from services.llm.client import _append_tail_metadata
from services.llm.prompt_builder import PromptBuilder
from services.memory.card_store import CardStore, NewCard
from services.persona import PersonaRuntime


@pytest.fixture
async def store(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    await s.add_card(NewCard(category="fact", scope="user", scope_id="100", content="测试用户｜test"))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="200", content="测试群｜test"))
    return s


def test_static_block_text_populated(persona_runtime: PersonaRuntime) -> None:
    pb = PromptBuilder(persona_runtime=persona_runtime)
    assert pb.static_block is not None
    assert pb.static_block["type"] == "text"
    assert pb.static_block["text"]
    # bot_self_id was bound in the fixture.
    assert "999" in pb.static_block["text"]
    # Cache markers are stamped by LLMClient, not PromptBuilder.
    assert "cache_control" not in pb.static_block


async def test_build_blocks_base(persona_runtime: PersonaRuntime, store: CardStore) -> None:
    """Base build_blocks returns [static, state_board] without plugins."""
    pb = PromptBuilder(persona_runtime=persona_runtime)
    blocks = await pb.build_blocks(user_id="100", group_id=None, card_store=store)
    assert len(blocks) == 2  # static, state_board
    # State board is empty for private chats
    assert blocks[1]["type"] == "text"
    assert blocks[1]["text"] == ""


async def test_build_blocks_with_plugin_blocks(
    persona_runtime: PersonaRuntime, store: CardStore,
) -> None:
    """Plugin blocks are positioned correctly by type."""
    pb = PromptBuilder(persona_runtime=persona_runtime)
    blocks = await pb.build_blocks(
        user_id="100", group_id=None, card_store=store,
        plugin_static=[{"type": "text", "text": "static1", "cache_control": {"type": "ephemeral"}}],
        plugin_stable=[{"type": "text", "text": "stable1", "cache_control": {"type": "ephemeral"}}],
        plugin_dynamic=[{"type": "text", "text": "dynamic1"}],
    )
    # [static, static1, state_board, stable1, dynamic1]
    assert len(blocks) == 5
    assert blocks[1]["text"] == "static1"
    assert blocks[3]["text"] == "stable1"
    assert blocks[4]["text"] == "dynamic1"


async def test_build_blocks_tail_state_board_layout(
    persona_runtime: PersonaRuntime, store: CardStore,
) -> None:
    """Part 6 tail layout keeps volatile state_board after plugin blocks."""
    pb = PromptBuilder(persona_runtime=persona_runtime, state_board_layout="tail")
    blocks = await pb.build_blocks(
        user_id="100", group_id=None, card_store=store,
        plugin_static=[{"type": "text", "text": "static1"}],
        plugin_stable=[{"type": "text", "text": "stable1"}],
        plugin_dynamic=[{"type": "text", "text": "dynamic1"}],
    )

    assert [block["text"] for block in blocks[1:]] == [
        "static1",
        "stable1",
        "dynamic1",
        "",
    ]


async def test_build_blocks_accepts_per_turn_state_board_layout(
    persona_runtime: PersonaRuntime,
    store: CardStore,
) -> None:
    pb = PromptBuilder(persona_runtime=persona_runtime)

    blocks = await pb.build_blocks(
        user_id="100",
        group_id=None,
        card_store=store,
        plugin_static=[{"type": "text", "text": "static1"}],
        plugin_stable=[{"type": "text", "text": "stable1"}],
        state_board_layout="tail",
    )

    assert [block["text"] for block in blocks[1:]] == ["static1", "stable1", ""]


async def test_build_state_board_uses_configured_granularity(
    persona_runtime: PersonaRuntime,
) -> None:
    class _Snapshot:
        def __init__(self, text: str) -> None:
            self._text = text

        def to_prompt_text(self) -> str:
            return self._text

    class _StateBoard:
        async def query_state(self, group_id: str, *, granularity: str = "fine") -> _Snapshot:
            return _Snapshot(f"{group_id}:{granularity}")

    pb = PromptBuilder(
        persona_runtime=persona_runtime,
        state_board=_StateBoard(),  # type: ignore[arg-type]
        state_board_granularity="coarse",
    )

    block = await pb.build_state_board_block("200")

    assert block["text"] == "200:coarse"


async def test_build_state_board_accepts_per_turn_granularity(
    persona_runtime: PersonaRuntime,
) -> None:
    class _Snapshot:
        def __init__(self, text: str) -> None:
            self._text = text

        def to_prompt_text(self) -> str:
            return self._text

    class _StateBoard:
        async def query_state(self, group_id: str, *, granularity: str = "fine") -> _Snapshot:
            return _Snapshot(f"{group_id}:{granularity}")

    pb = PromptBuilder(persona_runtime=persona_runtime, state_board=_StateBoard())  # type: ignore[arg-type]

    block = await pb.build_state_board_block(
        "200",
        state_board_granularity="coarse",
    )

    assert block["text"] == "200:coarse"


async def test_deepseek_native_dynamic_context_stays_out_of_stable_system_prefix(
    persona_runtime: PersonaRuntime,
    store: CardStore,
) -> None:
    """DeepSeek native keeps dynamic context in tail metadata, not system prefix."""
    pb = PromptBuilder(persona_runtime=persona_runtime)
    plugin_static = [{"type": "text", "text": "static plugin", "cache_control": {"type": "ephemeral"}}]
    plugin_stable = [{"type": "text", "text": "stable plugin", "cache_control": {"type": "ephemeral"}}]

    system_a = await pb.build_blocks(
        user_id="100",
        group_id="200",
        card_store=store,
        plugin_static=plugin_static,
        plugin_stable=plugin_stable,
        plugin_dynamic=None,
        include_state_board=False,
    )
    system_b = await pb.build_blocks(
        user_id="100",
        group_id="200",
        card_store=store,
        plugin_static=plugin_static,
        plugin_stable=plugin_stable,
        plugin_dynamic=None,
        include_state_board=False,
    )
    messages_a = [{"role": "user", "content": "这一轮问题"}]
    messages_b = [{"role": "user", "content": "这一轮问题"}]
    _append_tail_metadata(messages_a, [{"type": "text", "text": "【上下文资料】\n动态资料 A"}])
    _append_tail_metadata(messages_b, [{"type": "text", "text": "【上下文资料】\n动态资料 B"}])

    assert system_a == system_b
    assert "动态资料 A" not in "\n".join(str(block.get("text", "")) for block in system_a)
    assert "动态资料 B" not in "\n".join(str(block.get("text", "")) for block in system_b)
    assert messages_a != messages_b


async def test_invalidate_noop_without_retrieval_gate(persona_runtime: PersonaRuntime) -> None:
    """invalidate() without retrieval_gate is a no-op (should not crash)."""
    pb = PromptBuilder(persona_runtime=persona_runtime)
    pb.invalidate(group_id="123")
    pb.invalidate(user_id="456")
    pb.invalidate()
