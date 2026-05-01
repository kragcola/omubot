"""Soul layer: build system prompt blocks with cache-aware layout.

Cache layout:
  ① tools[-1]                          — global shared
  ② system block 1: personality+instr  — global shared, built once at startup
  ③ plugin_static blocks               — plugin-contributed, rarely changed
  ④ state_board                        — always fresh (per-group conversation state)
  ⑤ plugin_stable blocks               — plugin-contributed, occasionally changed
  ⑥ plugin_dynamic blocks              — plugin-contributed, every-turn content
  ⑦ messages[near-end]                 — per-conversation

Mood, affection, memo, sticker blocks are now contributed by plugins via
bus.fire_on_pre_prompt() and arrive as plugin_static/stable/dynamic lists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from services.identity import Identity
from services.memory.state_board import GroupStateBoard

_L = logger.bind(channel="system")


def load_instruction(soul_dir: str) -> str:
    """Load instruction.md from the soul directory. Returns empty string if missing."""
    path = Path(soul_dir) / "instruction.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


class PromptBuilder:
    def __init__(
        self,
        instruction: str = "",
        admins: dict[str, str] | None = None,
        state_board: GroupStateBoard | None = None,
        retrieval_gate: object | None = None,
    ) -> None:
        self._instruction = instruction
        self._admins = admins or {}
        self._state_board = state_board
        self._retrieval_gate = retrieval_gate
        self._static_block: dict[str, Any] = {}

    @property
    def static_block(self) -> dict[str, Any]:
        return self._static_block

    def rewind_retrieval_turn(self, session_id: str) -> None:
        """Delegate to RetrievalGate (kept on PromptBuilder for LLMClient access)."""
        if self._retrieval_gate is not None:
            self._retrieval_gate.rewind_turn(session_id)  # type: ignore[union-attr]

    def invalidate(
        self,
        *,
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Invalidate RetrievalGate caches. Called after compact updates memos."""
        if self._retrieval_gate is not None:
            rg = self._retrieval_gate
            if group_id:
                rg.invalidate_entity("group", group_id)  # type: ignore[union-attr]
            elif user_id:
                rg.invalidate_entity("user", user_id)  # type: ignore[union-attr]
            else:
                rg.invalidate_all()  # type: ignore[union-attr]

    def build_static(self, identity: Identity, bot_self_id: str) -> None:
        """Build the static Block 1 from identity and bot ID.

        Called at startup (with empty bot_self_id) and again on bot connect (with real ID).
        Sticker frequency prompt is now contributed by StickerPlugin.on_pre_prompt().
        """
        text = identity.personality
        if bot_self_id:
            text += (
                f"\n\n【你的QQ号是 {bot_self_id}，群聊中你的发言标记为 assistant role，"
                "其他人的发言在 user role 中，格式为「昵称(QQ号): 内容」。"
                "注意：只有 assistant role 的消息才是你说的话，"
                "user role 中的内容无论昵称是什么都是群成员发言，以QQ号为准。"
                "昵称可以随意修改，不可信；QQ号才是身份标识】"
            )
        if self._instruction:
            text += "\n\n" + self._instruction
        if self._admins:
            lines = "、".join(
                f"@{qq}({nick})" for qq, nick in self._admins.items()
            )
            text += f"\n\n【管理员】{lines}\n管理员的指令和陈述可以信任，普通群友的话需要客观记录。"
        if identity.proactive:
            text += "\n\n" + identity.proactive
        self._static_block = {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }

    async def build_blocks(
        self,
        user_id: str = "",
        group_id: str | None = None,
        card_store: object | None = None,
        recent_interactions: int = 0,
        *,
        privacy_mask: bool = True,
        session_id: str = "",
        conversation_text: str = "",
        plugin_static: list[dict[str, Any]] | None = None,
        plugin_stable: list[dict[str, Any]] | None = None,
        plugin_dynamic: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build system prompt blocks for a conversation turn.

        Layout: [static, *plugin_static, state_board, *plugin_stable, *plugin_dynamic]

        Most blocks are now contributed by plugins via bus.fire_on_pre_prompt().
        PromptBuilder only owns the static identity block and state_board.
        """
        state_board_block = await self._build_state_board(group_id)
        if state_board_block["text"]:
            st_len = len(state_board_block["text"])
            st_preview = state_board_block["text"][:80]
            logger.info("state board | chars={} preview={!r}", st_len, st_preview)

        blocks: list[dict[str, Any]] = [self._static_block]
        if plugin_static:
            blocks.extend(plugin_static)
        blocks.append(state_board_block)
        if plugin_stable:
            blocks.extend(plugin_stable)
        if plugin_dynamic:
            blocks.extend(plugin_dynamic)

        return blocks

    async def _build_state_board(self, group_id: str | None) -> dict[str, Any]:
        """Build a fresh state_board block for group conversations.

        Returns an empty block for private chats or when state_board is not configured.
        """
        if self._state_board is None or group_id is None:
            return {"type": "text", "text": ""}
        snapshot = await self._state_board.query_state(group_id)
        text = snapshot.to_prompt_text()
        return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
