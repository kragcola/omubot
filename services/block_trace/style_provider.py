"""StyleProvider — wraps StyleStore for the ContextProvider interface."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from loguru import logger

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate

_L = logger.bind(channel="provider")


class StyleProvider:
    """Produces PromptBlockCandidates from StyleStore.

    Replicates the exact gating + text generation of StylePlugin.on_pre_prompt
    (two blocks: profile + expressions) for shadow/active comparison.
    """

    name = "style"

    def __init__(
        self,
        store_getter: Callable[[], Any],
        *,
        enabled: bool = True,
        profile_enabled: bool = True,
        profile_max_chars: int = 900,
        max_items: int = 3,
        max_chars: int = 800,
        min_confidence: float = 0.0,
        global_enabled_groups: set[str] | None = None,
    ) -> None:
        self._get_store = store_getter
        self._enabled = enabled
        self._profile_enabled = profile_enabled
        self._profile_max_chars = profile_max_chars
        self._max_items = max_items
        self._max_chars = max_chars
        self._min_confidence = min_confidence
        self._global_enabled_groups: set[str] = global_enabled_groups or set()

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        if not self._enabled or not ctx.group_id:
            return []
        store = self._get_store()
        if store is None:
            return []

        include_global = str(ctx.group_id) in self._global_enabled_groups
        candidates: list[PromptBlockCandidate] = []

        if self._profile_enabled:
            if hasattr(store, "build_profile_prompt_block_with_refs"):
                profile_block, profile_refs = await store.build_profile_prompt_block_with_refs(
                    group_id=str(ctx.group_id),
                    include_global=include_global,
                    max_chars=self._profile_max_chars,
                )
            else:
                profile_block = await store.build_profile_prompt_block(
                    group_id=str(ctx.group_id),
                    include_global=include_global,
                    max_chars=self._profile_max_chars,
                )
                profile_refs = ()
            if profile_block:
                candidates.append(PromptBlockCandidate(
                    candidate_id="pbc_" + secrets.token_hex(6),
                    source="style",
                    provider="style_provider",
                    layer="dynamic",
                    label="动态风格档案",
                    text=profile_block,
                    priority=42,
                    position="dynamic",
                    scope="group",
                    group_id=ctx.group_id,
                    hit_reason="style_profile_injection",
                    char_count=len(profile_block),
                    evidence_refs=tuple(profile_refs),
                ))

        if hasattr(store, "build_prompt_block_with_refs"):
            block, expression_refs = await store.build_prompt_block_with_refs(
                group_id=str(ctx.group_id),
                conversation_text=ctx.conversation_text,
                include_global=include_global,
                max_items=self._max_items,
                max_chars=self._max_chars,
                min_confidence=self._min_confidence,
            )
        else:
            block = await store.build_prompt_block(
                group_id=str(ctx.group_id),
                conversation_text=ctx.conversation_text,
                include_global=include_global,
                max_items=self._max_items,
                max_chars=self._max_chars,
                min_confidence=self._min_confidence,
            )
            expression_refs = ()
        if block:
            candidates.append(PromptBlockCandidate(
                candidate_id="pbc_" + secrets.token_hex(6),
                source="style",
                provider="style_provider",
                layer="dynamic",
                label="表达习惯参考",
                text=block,
                priority=45,
                position="dynamic",
                scope="group",
                group_id=ctx.group_id,
                hit_reason="style_expression_injection",
                char_count=len(block),
                evidence_refs=tuple(expression_refs),
            ))

        return candidates


assert isinstance(StyleProvider(store_getter=lambda: None), ContextProvider)
