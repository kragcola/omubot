from __future__ import annotations

import json

import pytest

from services.slang.semantic_reviewer import SlangSemanticReviewer
from services.slang.types import SlangPendingCandidate


class _SemanticLLM:
    def __init__(
        self,
        *,
        context_response: object,
        literal_response: object,
        compare_response: object,
        delay_s: float = 0.0,
    ) -> None:
        self.context_response = context_response
        self.literal_response = literal_response
        self.compare_response = compare_response
        self.delay_s = delay_s
        self.calls: list[dict[str, object]] = []

    async def _call(self, request):
        system_blocks = request.system_blocks()
        messages = request.user_messages
        system_text = str(system_blocks[0].get("text", "")) if system_blocks else ""
        payload = json.loads(str(messages[0].get("content", "{}")))
        stage = "compare"
        if "上下文推断" in system_text:
            stage = "context"
        elif "裸推断" in system_text:
            stage = "literal"
        self.calls.append({"stage": stage, "payload": payload})
        if self.delay_s:
            import asyncio

            await asyncio.sleep(self.delay_s)
        response = {
            "context": self.context_response,
            "literal": self.literal_response,
            "compare": self.compare_response,
        }[stage]
        if isinstance(response, str):
            return {"text": response}
        return {"text": json.dumps(response, ensure_ascii=False)}


def _make_pending(
    *,
    count: int,
    meaning: str = "旧释义",
    meta: dict[str, object] | None = None,
) -> SlangPendingCandidate:
    return SlangPendingCandidate(
        pending_id="pending_1",
        term="猫饼",
        meaning=meaning,
        aliases=["猫猫饼"],
        group_id="100",
        confidence=0.4,
        count=count,
        unique_users=["u1"],
        evidence="猫饼就是群里说离谱但可爱的操作",
        reason="test",
        repeat_policy="understand_only",
        first_seen_at="2026-05-10T00:00:00+08:00",
        last_seen_at="2026-05-10T00:00:00+08:00",
        meta=meta or {},
    )


@pytest.mark.asyncio
async def test_slang_semantic_reviewer_includes_old_meaning_only_at_high_thresholds():
    llm = _SemanticLLM(
        context_response={"meaning": "群内黑话", "no_info": False, "confidence": 0.92, "reason": "有上下文"},
        literal_response={"meaning": "常见字面义", "confidence": 0.93, "reason": "词面可知"},
        compare_response={"is_similar": False, "confidence": 0.94, "reason": "上下文与字面不同"},
    )
    reviewer = SlangSemanticReviewer(llm)

    low_pending = _make_pending(count=12)
    await reviewer.review_pending(low_pending, group_id="100", user_rows=[{"content_text": "今天猫饼了"}])
    low_payload = next(call["payload"] for call in llm.calls if call["stage"] == "context")
    assert "existing_meaning_reference" not in low_payload

    llm.calls.clear()
    high_pending = _make_pending(count=24)
    await reviewer.review_pending(high_pending, group_id="100", user_rows=[{"content_text": "今天猫饼了"}])
    high_payload = next(call["payload"] for call in llm.calls if call["stage"] == "context")
    assert high_payload["existing_meaning_reference"] == "旧释义"


@pytest.mark.asyncio
async def test_slang_semantic_reviewer_no_info_keeps_pending_closed():
    llm = _SemanticLLM(
        context_response={"meaning": "", "no_info": True, "confidence": 0.12, "reason": "上下文不足"},
        literal_response={"meaning": "常见字面义", "confidence": 0.9, "reason": "词面可知"},
        compare_response={"is_similar": False, "confidence": 0.9, "reason": "不应走到这里"},
    )
    reviewer = SlangSemanticReviewer(llm)

    assessment = await reviewer.review_pending(_make_pending(count=2), group_id="100", user_rows=[])
    assert assessment.reviewed is True
    assert assessment.no_info is True
    assert assessment.complete is False
    assert assessment.error == ""
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_slang_semantic_reviewer_force_review_bypasses_threshold_gate():
    llm = _SemanticLLM(
        context_response={"meaning": "", "no_info": True, "confidence": 0.12, "reason": "上下文不足"},
        literal_response={"meaning": "常见字面义", "confidence": 0.9, "reason": "词面可知"},
        compare_response={"is_similar": False, "confidence": 0.9, "reason": "不应走到这里"},
    )
    reviewer = SlangSemanticReviewer(llm)

    assessment = await reviewer.review_pending(_make_pending(count=1), group_id="100", user_rows=[], force=True)
    assert assessment.reviewed is True
    assert assessment.forced is True
    assert assessment.threshold == 1
    assert assessment.no_info is True
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_slang_semantic_reviewer_parse_failure_fails_closed():
    llm = _SemanticLLM(
        context_response="not json",
        literal_response={"meaning": "常见字面义", "confidence": 0.9, "reason": "词面可知"},
        compare_response={"is_similar": False, "confidence": 0.9, "reason": "不应走到这里"},
    )
    reviewer = SlangSemanticReviewer(llm)

    assessment = await reviewer.review_pending(_make_pending(count=2), group_id="100", user_rows=[])
    assert assessment.reviewed is True
    assert assessment.error == "context_parse_failed"
    assert assessment.no_info is False
    assert assessment.complete is False
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_slang_semantic_reviewer_timeout_fails_closed():
    llm = _SemanticLLM(
        context_response={"meaning": "群内黑话", "no_info": False, "confidence": 0.92, "reason": "有上下文"},
        literal_response={"meaning": "常见字面义", "confidence": 0.93, "reason": "词面可知"},
        compare_response={"is_similar": False, "confidence": 0.94, "reason": "上下文与字面不同"},
        delay_s=0.2,
    )
    reviewer = SlangSemanticReviewer(llm, stage_timeout_s=0.1)

    assessment = await reviewer.review_pending(_make_pending(count=2), group_id="100", user_rows=[])
    assert assessment.reviewed is True
    assert assessment.error == "semantic_review_timeout"
    assert assessment.complete is False
    assert len(llm.calls) == 1
