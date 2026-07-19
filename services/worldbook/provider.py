"""PromptProviderBus adapter for worldbook chat projection."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any, cast

from services.block_trace.providers import QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.worldbook.config import WorldbookConfig
from services.worldbook.projection import ProjectionResult


class WorldbookPromptProvider:
    """ContextProvider that projects worldbook blocks into the chat prompt.

    Registered only when worldbook.enabled and chat_projection_enabled.
    """

    name = "worldbook"

    def __init__(
        self,
        runtime: Any,
        config: WorldbookConfig | None = None,
    ) -> None:
        self._runtime = runtime
        self._config = config or getattr(runtime, "config", WorldbookConfig())

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        cfg = self._config
        if not cfg.enabled or not cfg.chat_projection_enabled:
            return []
        project = getattr(self._runtime, "project_chat", None)
        if not callable(project):
            return []
        project_chat = cast(Callable[..., Awaitable[ProjectionResult]], project)
        result = await project_chat(
            conversation_text=ctx.conversation_text,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
        )
        return candidates_from_projection(result, group_id=ctx.group_id or "")


def candidates_from_projection(
    result: ProjectionResult,
    *,
    group_id: str = "",
) -> list[PromptBlockCandidate]:
    candidates: list[PromptBlockCandidate] = []
    for block in result.blocks:
        if not block.text.strip():
            continue
        candidates.append(
            PromptBlockCandidate(
                candidate_id="pbc_" + secrets.token_hex(6),
                source="worldbook",
                provider="worldbook",
                layer="dynamic",
                label=block.label,
                text=block.text,
                priority=int(block.meta.priority),
                position="dynamic",
                scope=block.meta.scope,
                group_id=group_id,
                hit_reason=block.meta.hit_reason or block.meta.source,
                char_count=block.char_count,
                evidence_refs=block.meta.evidence_refs,
                metadata={
                    "block_id": block.block_id,
                    "source": block.meta.source,
                    "budget_decision": block.meta.budget_decision,
                    "privacy": block.meta.privacy,
                    "confidence": block.meta.confidence,
                    "traces": [
                        t.to_dict()
                        for t in result.traces
                        if t.label == block.label
                        or (
                            isinstance(t.metadata, dict)
                            and t.metadata.get("block_id") == block.block_id
                        )
                    ],
                },
            )
        )
    return candidates
