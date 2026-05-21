"""SlangProvider — wraps SlangStore for the ContextProvider interface."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from loguru import logger

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate

_L = logger.bind(channel="provider")


def _is_group_slang_enabled(group_id: str, group_config: Any) -> bool:
    if group_config is None:
        return True
    try:
        resolved = group_config.resolve(int(group_id))
    except Exception:
        return True
    return bool(getattr(resolved, "slang_enabled", True))


class SlangProvider:
    """Produces a PromptBlockCandidate from SlangStore.

    Replicates the exact gating + text generation of SlangPlugin.on_pre_prompt
    so that shadow traces can be compared against plugin-injected blocks.
    """

    name = "slang"

    def __init__(
        self,
        store_getter: Callable[[], Any],
        group_config: Any = None,
    ) -> None:
        self._get_store = store_getter
        self._group_config = group_config

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        store = self._get_store()
        if store is None or not ctx.group_id:
            return []
        if not _is_group_slang_enabled(ctx.group_id, self._group_config):
            return []
        settings = await store.load_settings()
        if not settings.injection_enabled or not settings.allows_group(ctx.group_id):
            return []
        block = await store.build_prompt_block(
            group_id=ctx.group_id,
            conversation_text=ctx.conversation_text,
            max_terms=settings.max_injected_terms,
            max_chars=settings.max_prompt_chars,
        )
        if not block:
            return []
        return [PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6),
            source="slang",
            provider="slang_provider",
            layer="dynamic",
            label="群内黑话",
            text=block,
            priority=40,
            position="dynamic",
            scope="group",
            group_id=ctx.group_id,
            hit_reason="slang_injection",
            char_count=len(block),
        )]


assert isinstance(SlangProvider(store_getter=lambda: None), ContextProvider)
