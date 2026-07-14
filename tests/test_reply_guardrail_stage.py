"""Contract test for the isolated visible-reply guardrail stage."""

from __future__ import annotations

import importlib
from typing import Any

import pytest


def _load_stage_contract() -> tuple[type[Any], type[Any], type[Any]]:
    try:
        module = importlib.import_module("services.llm.reply_guardrail_stage")
    except ImportError:
        module = None

    if module is None:
        pytest.fail(
            "services.llm.reply_guardrail_stage does not exist yet",
            pytrace=False,
        )

    required_names = (
        "VisibleReplyGuardrailInput",
        "VisibleReplyGuardrailOutput",
        "VisibleReplyGuardrailStage",
    )
    missing = [name for name in required_names if not hasattr(module, name)]
    if missing:
        pytest.fail(
            "services.llm.reply_guardrail_stage must define " + ", ".join(missing),
            pytrace=False,
        )

    return tuple(getattr(module, name) for name in required_names)  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_visible_reply_guardrail_stage_delegates_and_preserves_result_identity() -> None:
    input_type, output_type, stage_type = _load_stage_contract()
    calls: list[dict[str, object]] = []
    first_hit = object()
    second_hit = object()
    ordered_hits = (second_hit, first_hit)
    metadata = {"sentinel_strip_hits": 1, "rewrite_applied": True}

    async def guardrail(
        reply: str,
        *,
        enabled: bool,
        thinker_thought: str,
        last_assistant_text: str,
        user_message: str,
        session_count: int,
        bot_name: str,
    ) -> tuple[str, tuple[object, ...], dict[str, object], bool]:
        calls.append(
            {
                "reply": reply,
                "enabled": enabled,
                "thinker_thought": thinker_thought,
                "last_assistant_text": last_assistant_text,
                "user_message": user_message,
                "session_count": session_count,
                "bot_name": bot_name,
            }
        )
        return "clean visible reply", ordered_hits, metadata, True

    stage_input = input_type(
        reply="raw visible reply",
        enabled=True,
        thinker_thought="private reasoning",
        last_assistant_text="previous assistant reply",
        user_message="current user message",
        session_count=7,
        bot_name="小沐",
    )
    stage = stage_type(guardrail)

    output = await stage.run(stage_input)

    assert isinstance(output, output_type)
    assert calls == [
        {
            "reply": "raw visible reply",
            "enabled": True,
            "thinker_thought": "private reasoning",
            "last_assistant_text": "previous assistant reply",
            "user_message": "current user message",
            "session_count": 7,
            "bot_name": "小沐",
        }
    ]
    assert output.reply == "clean visible reply"
    assert output.hits is ordered_hits
    assert output.hits == (second_hit, first_hit)
    assert output.metadata is metadata
    assert output.blocked is True
