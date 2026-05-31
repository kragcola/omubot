from __future__ import annotations

from kernel.types import TriggerContext
from services.llm.client import LLMClient
from services.llm.slang_lookup import SlangResult


def test_client_builds_slang_context_block() -> None:
    block = LLMClient._build_slang_context_block({
        "op": SlangResult(term="op", explanation="原作/过强", source="local_db", confidence=0.9),
    })

    assert "黑话释义" in block
    assert "op" in block


def test_client_extracts_unknown_terms() -> None:
    terms = LLMClient._extract_unknown_terms_from_text("这角色也太op了，简直awsl")
    assert "op" in terms or "awsl" in terms


def test_trigger_context_reply_sender_can_feed_preflight() -> None:
    trigger = TriggerContext(reason="reply", mode="directed_followup", extra={"reply_sender_id": "42"})
    assert trigger.extra["reply_sender_id"] == "42"
