"""Content-free evidence of actual prompt candidates and budget decisions."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field

from .types import Message, StrictModel


class PromptBlockDecision(StrictModel):
    candidate_id: str
    source: str
    role: Literal["user", "assistant"]
    character_count: int = Field(ge=0)
    evidence_ids: list[str] = Field(max_length=8)
    decision: Literal["accepted", "trimmed", "rejected"]
    reason: Literal["within_budget", "history_budget", "protected_input_budget"]


class PromptBlockTrace(StrictModel):
    request_id: str
    action_key: str
    phase: Literal["reply_budget", "model_port"]
    candidate_count: int = Field(ge=0)
    recorded_count: int = Field(ge=0, le=64)
    truncated: bool
    character_limit: int | None = Field(default=None, ge=0)
    built_characters: int = Field(ge=0)
    final_characters: int = Field(ge=0)
    system_characters: int | None = Field(default=None, ge=0)
    outcome: Literal["accepted", "rejected"]
    blocks: list[PromptBlockDecision] = Field(max_length=64)
    proves_semantic_use: Literal[False] = False


def prompt_block_trace(
    *, request_id: str, action_key: str, phase: Literal["reply_budget", "model_port"],
    candidates: Sequence[Message], accepted: Sequence[Message],
    sources: Mapping[int, str], rejected: bool = False,
    character_limit: int | None = None, system_characters: int | None = None,
) -> PromptBlockTrace:
    accepted_objects = {id(message) for message in accepted}
    blocks: list[PromptBlockDecision] = []
    for ordinal, message in enumerate(candidates[:64]):
        identity = json.dumps([action_key, phase, ordinal], separators=(",", ":"))
        kept = id(message) in accepted_objects
        blocks.append(PromptBlockDecision(
            candidate_id=hashlib.sha256(identity.encode()).hexdigest(),
            source=sources.get(id(message), "context"), role=message.role,
            character_count=len(message.content),
            evidence_ids=[hashlib.sha256(source.encode()).hexdigest() for source in message.source_ids],
            decision="rejected" if rejected else "accepted" if kept else "trimmed",
            reason="protected_input_budget" if rejected else "within_budget" if kept else "history_budget",
        ))
    return PromptBlockTrace(
        request_id=request_id, action_key=action_key, phase=phase,
        candidate_count=len(candidates), recorded_count=len(blocks), truncated=len(candidates) > 64,
        character_limit=character_limit, built_characters=sum(len(message.content) for message in candidates),
        final_characters=0 if rejected else sum(len(message.content) for message in accepted),
        system_characters=system_characters, outcome="rejected" if rejected else "accepted",
        blocks=blocks,
    )
