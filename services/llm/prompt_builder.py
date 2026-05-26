"""Soul layer: build system prompt blocks with cache-aware layout.

Cache layout:
  ① tools[-1]                          — global shared
  ② system block 1: personality+instr  — global shared, built once at startup
  ③ plugin_static blocks               — plugin-contributed, rarely changed
  ④ state_board                        — always fresh, legacy head layout
  ⑤ plugin_stable blocks               — plugin-contributed, occasionally changed
  ⑥ plugin_dynamic blocks              — plugin-contributed, every-turn content
  ⑦ state_board                        — Part 6 tail layout option
  ⑧ messages[near-end]                 — per-conversation

Mood, affection, memo, sticker blocks are now contributed by plugins via
bus.fire_on_pre_prompt() and arrive as plugin_static/stable/dynamic lists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from services.identity import Identity
from services.memory.state_board import GroupStateBoard
from services.persona.runtime_selector import PersonaRuntimeSelector
from services.url_meta import build_url_title_context

_L = logger.bind(channel="system")
_READ_MARK_TEXT = "--- 以上消息是你已经看过，请关注以下未读的新消息 ---"


def load_instruction(soul_dir: str) -> str:
    """Load behavioral instructions from the soul directory.

    The runtime uses the legacy two-file soul layout:
    identity.md for persona, instruction.md for behavior rules.
    Returns empty string if instruction.md does not exist.
    """
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
        state_board_layout: str = "head",
        state_board_granularity: str = "fine",
    ) -> None:
        self._instruction = instruction
        self._admins = admins or {}
        self._state_board = state_board
        self._retrieval_gate = retrieval_gate
        self._state_board_layout = "tail" if state_board_layout == "tail" else "head"
        self._state_board_granularity = (
            "coarse" if state_board_granularity == "coarse" else "fine"
        )
        self._static_block: dict[str, Any] = {}
        self._runtime_selector: PersonaRuntimeSelector | None = None

    def _resolve_state_board_layout(self, layout: str | None = None) -> str:
        return "tail" if (layout or self._state_board_layout) == "tail" else "head"

    def _resolve_state_board_granularity(self, granularity: str | None = None) -> str:
        return "coarse" if (granularity or self._state_board_granularity) == "coarse" else "fine"

    @property
    def static_block(self) -> dict[str, Any]:
        return self._static_block

    def set_runtime_selector(
        self, selector: PersonaRuntimeSelector | None
    ) -> None:
        """Wire the v2 runtime selector. Called once on bot connect.

        ``None`` disables v2 substitution (turn returns ``_static_block``).
        """
        self._runtime_selector = selector

    def resolve_static_block(self, group_id: str | None) -> dict[str, Any]:
        """Return v1 or v2 static block based on selector decision.

        Called per turn from build_blocks() and from LLMClient's fallback
        path. Always returns a ``{"type": "text", "text": "..."}`` dict —
        falling back to ``_static_block`` whenever v2 substitution is
        unavailable (selector unset, flag off, group not listed, bundle
        missing, compile error). Pure synchronous, never raises.
        """
        if self._runtime_selector is None:
            return self._static_block
        selection = self._runtime_selector.resolve_for_group(group_id)
        if selection.use_v2 and selection.v2_static_text:
            return {"type": "text", "text": selection.v2_static_text}
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
        read_mark: bool = False,
        plugin_static: list[dict[str, Any]] | None = None,
        plugin_stable: list[dict[str, Any]] | None = None,
        plugin_dynamic: list[dict[str, Any]] | None = None,
        include_state_board: bool = True,
        state_board_layout: str | None = None,
        state_board_granularity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build system prompt blocks for a conversation turn.

        Legacy layout: [static, *plugin_static, state_board, *plugin_stable, *plugin_dynamic]
        Tail layout: [static, *plugin_static, *plugin_stable, *plugin_dynamic, state_board]

        Most blocks are now contributed by plugins via bus.fire_on_pre_prompt().
        PromptBuilder only owns the static identity block and state_board.
        """
        layout = self._resolve_state_board_layout(state_board_layout)
        state_board_block = await self.build_state_board_block(
            group_id,
            state_board_granularity=state_board_granularity,
        )
        if include_state_board and state_board_block["text"]:
            st_len = len(state_board_block["text"])
            st_preview = state_board_block["text"][:80]
            logger.info("state board | chars={} preview={!r}", st_len, st_preview)

        blocks: list[dict[str, Any]] = [self.resolve_static_block(group_id)]
        group_context_block = self._build_group_context_block(group_id, read_mark=read_mark)
        if group_context_block is not None:
            blocks.append(group_context_block)
        if group_id is not None and conversation_text:
            url_title_text = await build_url_title_context(conversation_text)
            if url_title_text:
                blocks.append({"type": "text", "text": url_title_text})
        if plugin_static:
            blocks.extend(plugin_static)
        if include_state_board and layout == "head":
            blocks.append(state_board_block)
        if plugin_stable:
            blocks.extend(plugin_stable)
        if plugin_dynamic:
            blocks.extend(plugin_dynamic)
        if include_state_board and layout == "tail":
            blocks.append(state_board_block)

        return blocks

    def _build_group_context_block(
        self,
        group_id: str | None,
        *,
        read_mark: bool = False,
    ) -> dict[str, Any] | None:
        if group_id is None or not read_mark:
            return None
        return {"type": "text", "text": _READ_MARK_TEXT}

    async def build_state_board_block(
        self,
        group_id: str | None,
        *,
        state_board_granularity: str | None = None,
    ) -> dict[str, Any]:
        """Build a fresh state_board block for group conversations.

        Returns an empty block for private chats or when state_board is not configured.
        """
        if self._state_board is None or group_id is None:
            return {"type": "text", "text": ""}
        snapshot = await self._state_board.query_state(
            group_id,
            granularity=self._resolve_state_board_granularity(state_board_granularity),
        )
        text = snapshot.to_prompt_text()
        return {"type": "text", "text": text}
