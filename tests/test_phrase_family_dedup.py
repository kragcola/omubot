"""Bounded multi-turn phrase-family repetition; quoted user text excluded."""

from __future__ import annotations

from types import SimpleNamespace

from services.humanization.scorer import StylometricScorer
from services.llm.client import LLMClient
from services.llm.dedup_gate import (
    PhraseFamilyHistory,
    dedup_rule,
    is_phrase_family_repeat,
)
from services.llm.reply_guardrail_stage import VisibleReplyGuardrailInput, VisibleReplyGuardrailStage
from services.llm.sentinel_registry import GuardrailContext, apply_guardrails


def test_caught_out_family_detected_across_variants() -> None:
    history = [
        "被你看穿了，下次藏好点。",
        "哎呀被发现了嘛。",
        "被你抓到啦，别说出去。",
    ]
    current = "被你发现啦，我认栽。"
    assert is_phrase_family_repeat(current, history, family="caught_out") is True


def test_quoted_user_text_is_not_bot_self_repetition() -> None:
    history = PhraseFamilyHistory.from_turns(
        [
            {"role": "user", "text": "被你发现了吧"},
            {"role": "assistant", "text": "什么呀我在听你说话"},
        ]
    )
    current = "你说「被你发现了吧」？我没那么容易被抓到。"
    assert is_phrase_family_repeat(current, history.assistant_texts, family="caught_out") is False


def test_dedup_rule_flags_phrase_family_with_history() -> None:
    cfg = SimpleNamespace(sentinel_guardrail=SimpleNamespace(enabled=True, dedup_action="rewrite"))
    ctx = GuardrailContext(
        last_assistant_text="完全无关的上一句",
        user_message="",
        config=cfg,
        assistant_history=(
            "被你看穿了呢",
            "被发现了嘛哈哈",
            "被你抓到啦",
        ),
    )
    result = dedup_rule(
        "被你发现啦，下次注意",
        ctx,
    )
    assert result.passed is False or any(h.name == "phrase_family_repeat" for h in result.hits)


def test_apply_guardrails_uses_assistant_history_across_non_adjacent_turns() -> None:
    """Production apply_guardrails path with assistant_history (not only last turn)."""
    cfg = SimpleNamespace(
        sentinel_guardrail=SimpleNamespace(
            enabled=True,
            dedup_action="rewrite",
            dedup_ngram=5,
            dedup_threshold=0.9,
        )
    )
    # last_assistant_text is unrelated; multi-turn history carries the family.
    result = apply_guardrails(
        "被你发现啦，我认栽。",
        last_assistant_text="今天天气不错",
        user_message="哈哈",
        config=cfg,
        assistant_history=(
            "被你看穿了，下次藏好点。",
            "嗯嗯",
            "哎呀被发现了嘛。",
            "好的",
            "被你抓到啦，别说出去。",
        ),
    )
    assert result.passed is False or any(
        getattr(h, "name", "") == "phrase_family_repeat" for h in result.hits
    )


def test_client_latest_assistant_history_skips_user_turns() -> None:
    client = object.__new__(LLMClient)

    class _TL:
        def get_turns(self, group_id: str):
            del group_id
            return [
                {"role": "user", "content": "被你发现了吧"},
                {"role": "assistant", "content": "被你看穿了呢"},
                {"role": "user", "content": "哈哈哈"},
                {"role": "assistant", "content": "被你抓到啦"},
                {"role": "user", "content": "引用：被你发现了"},
            ]

    client._timeline = _TL()  # type: ignore[attr-defined]
    client._short_term = None  # type: ignore[attr-defined]
    hist = LLMClient._latest_assistant_history(
        client, session_id="g:1", group_id="1", is_group=True, limit=12
    )
    assert hist == ("被你看穿了呢", "被你抓到啦")
    assert all("引用" not in t for t in hist)


def test_visible_reply_stage_forwards_assistant_history() -> None:
    seen: dict[str, object] = {}

    def _guard(reply: str, **kwargs):
        seen.update(kwargs)
        return reply, (), {}, False

    stage = VisibleReplyGuardrailStage(_guard)
    import asyncio

    async def _run():
        return await stage.run(
            VisibleReplyGuardrailInput(
                reply="hi",
                enabled=True,
                thinker_thought="",
                last_assistant_text="x",
                user_message="y",
                session_count=1,
                bot_name="bot",
                assistant_history=("a", "b"),
            )
        )

    out = asyncio.run(_run())
    assert out.reply == "hi"
    assert seen.get("assistant_history") == ("a", "b")


def test_scorer_marks_caught_out_template_phrase() -> None:
    score = StylometricScorer().score("被你发现啦")
    assert "surface.template_phrase" in score.issues or "surface.phrase_family" in score.issues
