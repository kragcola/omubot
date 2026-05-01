import pytest

from services.identity import Identity
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


def test_build_static_called_once(identity: Identity) -> None:
    pb = PromptBuilder(instruction="Test instruction.")
    pb.build_static(identity, bot_self_id="999")
    assert pb.static_block is not None
    assert "I am a bot." in pb.static_block["text"]
    assert "Test instruction." in pb.static_block["text"]
    assert "Proactive rules." in pb.static_block["text"]
    assert pb.static_block["cache_control"] == {"type": "ephemeral"}


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
