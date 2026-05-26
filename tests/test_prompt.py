import pytest

from services.identity import Identity
from services.llm.client import _append_tail_metadata
from services.llm.prompt_builder import PromptBuilder, load_instruction
from services.memory.card_store import CardStore, NewCard

# Minimal JPEG bytes (magic header + padding)
_JPEG_1PX = b"\xff\xd8\xff\xe0" + b"\x00" * 20


@pytest.fixture
async def store(tmp_path) -> CardStore:
    db_path = str(tmp_path / "test_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    await s.add_card(NewCard(category="fact", scope="user", scope_id="100", content="测试用户｜test"))
    await s.add_card(NewCard(category="fact", scope="group", scope_id="200", content="测试群｜test"))
    return s


@pytest.fixture
def identity() -> Identity:
    return Identity(id="test", name="Bot", personality="I am a bot.", proactive="Proactive rules.")


def test_load_instruction_missing(tmp_path) -> None:
    assert load_instruction(str(tmp_path)) == ""


def test_load_instruction_exists(tmp_path) -> None:
    (tmp_path / "instruction.md").write_text("Do things.")
    assert load_instruction(str(tmp_path)) == "Do things."


def test_load_instruction_prefers_legacy_instruction_over_skill_md(tmp_path) -> None:
    """Runtime instructions come only from instruction.md."""
    (tmp_path / "SKILL.md").write_text("""\
---
name: Test
description: desc
---

# 行为指令

这是行为指令正文。
""")
    (tmp_path / "instruction.md").write_text("Use legacy instruction.")
    assert load_instruction(str(tmp_path)) == "Use legacy instruction."


def test_load_instruction_ignores_skill_md_without_instruction(tmp_path) -> None:
    """SKILL.md alone is not a runtime instruction source."""
    (tmp_path / "SKILL.md").write_text("# Plain markdown\n\nJust body text.")
    assert load_instruction(str(tmp_path)) == ""


def test_build_static_called_once(identity: Identity) -> None:
    pb = PromptBuilder(instruction="Test instruction.")
    pb.build_static(identity, bot_self_id="999")
    assert pb.static_block is not None
    assert "I am a bot." in pb.static_block["text"]
    assert "Test instruction." in pb.static_block["text"]
    assert "Proactive rules." in pb.static_block["text"]
    # cache_control is no longer stamped here — spine
    # (apply_cache_breakpoints in LLMClient._dispatch_call) is the
    # single source of truth, capped at Anthropic's ≤4-marker limit.
    assert "cache_control" not in pb.static_block


async def test_build_blocks_base(identity: Identity, store: CardStore) -> None:
    """Base build_blocks returns [static, state_board] without plugins."""
    pb = PromptBuilder(instruction="")
    pb.build_static(identity, bot_self_id="999")
    blocks = await pb.build_blocks(user_id="100", group_id=None, card_store=store)
    assert len(blocks) == 2  # static, state_board
    assert blocks[0] is pb.static_block
    # State board is empty for private chats
    assert blocks[1]["type"] == "text"
    assert blocks[1]["text"] == ""


async def test_build_blocks_with_plugin_blocks(identity: Identity, store: CardStore) -> None:
    """Plugin blocks are positioned correctly by type."""
    pb = PromptBuilder(instruction="")
    pb.build_static(identity, bot_self_id="999")
    blocks = await pb.build_blocks(
        user_id="100", group_id=None, card_store=store,
        plugin_static=[{"type": "text", "text": "static1", "cache_control": {"type": "ephemeral"}}],
        plugin_stable=[{"type": "text", "text": "stable1", "cache_control": {"type": "ephemeral"}}],
        plugin_dynamic=[{"type": "text", "text": "dynamic1"}],
    )
    # [static, static1, state_board, stable1, dynamic1]
    assert len(blocks) == 5
    assert blocks[0] is pb.static_block
    assert blocks[1]["text"] == "static1"
    assert blocks[3]["text"] == "stable1"
    assert blocks[4]["text"] == "dynamic1"


async def test_build_blocks_tail_state_board_layout(identity: Identity, store: CardStore) -> None:
    """Part 6 tail layout keeps volatile state_board after plugin blocks."""
    pb = PromptBuilder(instruction="", state_board_layout="tail")
    pb.build_static(identity, bot_self_id="999")
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
    identity: Identity,
    store: CardStore,
) -> None:
    pb = PromptBuilder(instruction="")
    pb.build_static(identity, bot_self_id="999")

    blocks = await pb.build_blocks(
        user_id="100",
        group_id=None,
        card_store=store,
        plugin_static=[{"type": "text", "text": "static1"}],
        plugin_stable=[{"type": "text", "text": "stable1"}],
        state_board_layout="tail",
    )

    assert [block["text"] for block in blocks[1:]] == ["static1", "stable1", ""]


async def test_build_state_board_uses_configured_granularity() -> None:
    class _Snapshot:
        def __init__(self, text: str) -> None:
            self._text = text

        def to_prompt_text(self) -> str:
            return self._text

    class _StateBoard:
        async def query_state(self, group_id: str, *, granularity: str = "fine") -> _Snapshot:
            return _Snapshot(f"{group_id}:{granularity}")

    pb = PromptBuilder(
        instruction="",
        state_board=_StateBoard(),  # type: ignore[arg-type]
        state_board_granularity="coarse",
    )

    block = await pb.build_state_board_block("200")

    assert block["text"] == "200:coarse"


async def test_build_state_board_accepts_per_turn_granularity() -> None:
    class _Snapshot:
        def __init__(self, text: str) -> None:
            self._text = text

        def to_prompt_text(self) -> str:
            return self._text

    class _StateBoard:
        async def query_state(self, group_id: str, *, granularity: str = "fine") -> _Snapshot:
            return _Snapshot(f"{group_id}:{granularity}")

    pb = PromptBuilder(instruction="", state_board=_StateBoard())  # type: ignore[arg-type]

    block = await pb.build_state_board_block(
        "200",
        state_board_granularity="coarse",
    )

    assert block["text"] == "200:coarse"


async def test_deepseek_native_dynamic_context_stays_out_of_stable_system_prefix(
    identity: Identity,
    store: CardStore,
) -> None:
    """DeepSeek native keeps dynamic context in tail metadata, not system prefix."""
    pb = PromptBuilder(instruction="")
    pb.build_static(identity, bot_self_id="999")
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


async def test_static_block_shared_across_calls(identity: Identity, store: CardStore) -> None:
    pb = PromptBuilder(instruction="")
    pb.build_static(identity, bot_self_id="999")
    b1 = await pb.build_blocks(user_id="100", group_id=None, card_store=store)
    b2 = await pb.build_blocks(user_id="100", group_id="200", card_store=store)
    assert b1[0] is b2[0]  # Same object reference = guaranteed cache hit


async def test_build_static_no_bot_id(identity: Identity) -> None:
    """build_static with empty bot_self_id omits the QQ number block."""
    pb = PromptBuilder(instruction="")
    pb.build_static(identity, bot_self_id="")
    assert "你的QQ号是" not in pb.static_block["text"]


async def test_build_static_with_admins(identity: Identity) -> None:
    """Admin info is included in static block."""
    pb = PromptBuilder(instruction="", admins={"123456": "管理员"})
    pb.build_static(identity, bot_self_id="999")
    assert "【管理员】" in pb.static_block["text"]
    assert "@123456(管理员)" in pb.static_block["text"]


async def test_invalidate_noop_without_retrieval_gate() -> None:
    """invalidate() without retrieval_gate is a no-op (should not crash)."""
    pb = PromptBuilder(instruction="")
    pb.invalidate(group_id="123")
    pb.invalidate(user_id="456")
    pb.invalidate()
