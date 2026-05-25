from __future__ import annotations

from services.identity import Identity
from services.llm.prompt_builder import PromptBuilder


def _builder() -> PromptBuilder:
    builder = PromptBuilder(instruction="")
    builder.build_static(Identity(id="t", name="Bot", personality="persona"), bot_self_id="999")
    return builder


async def test_build_blocks_inserts_read_mark_as_second_group_block() -> None:
    builder = _builder()

    blocks = await builder.build_blocks(
        group_id="200",
        read_mark=True,
        include_state_board=False,
        plugin_static=[{"type": "text", "text": "static1"}],
    )

    assert blocks[1]["text"] == "--- 以上消息是你已经看过，请关注以下未读的新消息 ---"
    assert blocks[2]["text"] == "static1"


async def test_build_blocks_skips_read_mark_without_group_or_pending() -> None:
    builder = _builder()

    private_blocks = await builder.build_blocks(group_id=None, read_mark=True, include_state_board=False)
    group_blocks = await builder.build_blocks(group_id="200", read_mark=False, include_state_board=False)

    assert len(private_blocks) == 1
    assert len(group_blocks) == 1
