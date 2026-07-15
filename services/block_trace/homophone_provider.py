"""PromptProviderBus adapter for conservative homophone interpretation."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from services.block_trace.providers import ContextProvider, QueryContext
from services.block_trace.types import PromptBlockCandidate
from services.homophone import (
    filter_approved_slang_conflicts,
    format_homophone_hint,
    interpret_homophones,
)


class HomophoneProvider:
    name = "homophone"

    def __init__(self, slang_store_getter: Callable[[], Any] | None = None) -> None:
        self._slang_store_getter = slang_store_getter

    async def provide(self, ctx: QueryContext) -> list[PromptBlockCandidate]:
        interpretation = interpret_homophones(ctx.conversation_text)
        if not interpretation.changed:
            return []
        interpretation = await filter_approved_slang_conflicts(
            interpretation,
            group_id=ctx.group_id,
            store_getter=self._slang_store_getter,
        )
        if not interpretation.changed:
            return []
        hint = format_homophone_hint(interpretation)
        if not hint:
            return []
        rule_ids = tuple(dict.fromkeys(item.rule_id for item in interpretation.evidence))
        return [PromptBlockCandidate(
            candidate_id="pbc_" + secrets.token_hex(6),
            source="homophone",
            provider="homophone_provider",
            layer="dynamic",
            label="输入理解辅助",
            text=hint,
            priority=45,
            position="dynamic",
            scope="group" if ctx.group_id else "session",
            group_id=ctx.group_id or "",
            hit_reason="high_confidence_homophone",
            char_count=len(hint),
            evidence_refs=rule_ids,
            metadata={
                "confidence": interpretation.confidence,
                "rule_ids": list(rule_ids),
                "source_spans": [
                    [item.start, item.end]
                    for item in interpretation.evidence
                ],
                "atomic": True,
            },
        )]


assert isinstance(HomophoneProvider(), ContextProvider)
