from __future__ import annotations

import asyncio

import pytest

from services.slang.drift_reviewer import SlangDriftReviewer
from services.slang.types import SlangTerm


def _term() -> SlangTerm:
    return SlangTerm(
        term_id="slang_1",
        term="没米",
        meaning="没有钱或没有资源",
        aliases=[],
        scope="group",
        group_id="100",
        confidence=0.9,
        status="approved",
        usage_count=10,
        unique_users=[],
        first_seen_at="2026-05-11T00:00:00",
        last_seen_at="2026-05-11T00:00:00",
        last_inferred_at="2026-05-11T00:00:00",
        source="manual",
        repeat_policy="understand_only",
        notes="",
        meta={},
        created_at="2026-05-11T00:00:00",
        updated_at="2026-05-11T00:00:00",
    )


class _FakeClient:
    def __init__(self, text: str = "", *, delay_s: float = 0.0) -> None:
        self.text = text
        self.delay_s = delay_s
        self.calls: list[dict[str, object]] = []

    async def _call(self, request):
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        self.calls.append({
            "system_blocks": request.system_blocks(),
            "messages": request.user_messages,
            "kwargs": {"max_tokens": request.max_tokens, "task": request.task},
        })
        return {"text": self.text}


@pytest.mark.asyncio
async def test_slang_drift_reviewer_accepts_same_meaning_json() -> None:
    client = _FakeClient('{"verdict":"same_meaning","confidence":0.91,"reason":"核心含义一致"}')
    reviewer = SlangDriftReviewer(client)

    assessment = await reviewer.review_drift(
        existing=_term(),
        new_meaning="没钱或没资源的意思",
        aliases=[],
        evidence="没米了",
        confidence=0.9,
        reason="test",
    )

    assert assessment.reviewed is True
    assert assessment.verdict == "same_meaning"
    assert assessment.confidence == 0.91
    assert assessment.reason == "核心含义一致"
    assert client.calls[0]["kwargs"]["max_tokens"] == 240


@pytest.mark.asyncio
async def test_slang_drift_reviewer_low_confidence_fails_closed() -> None:
    client = _FakeClient('{"verdict":"real_drift","confidence":0.5,"reason":"不确定"}')
    reviewer = SlangDriftReviewer(client)

    assessment = await reviewer.review_drift(
        existing=_term(),
        new_meaning="新意思",
        aliases=[],
        evidence="没米了",
        confidence=0.9,
        reason="test",
    )

    assert assessment.reviewed is True
    assert assessment.verdict == "unclear"
    assert assessment.confidence == 0.5
    assert assessment.reason == "不确定"


@pytest.mark.asyncio
async def test_slang_drift_reviewer_parse_failure_fails_closed() -> None:
    reviewer = SlangDriftReviewer(_FakeClient("不是 JSON"))

    assessment = await reviewer.review_drift(
        existing=_term(),
        new_meaning="新意思",
        aliases=[],
        evidence="没米了",
        confidence=0.9,
        reason="test",
    )

    assert assessment.reviewed is True
    assert assessment.verdict == "unclear"
    assert assessment.error == "drift_semantic_parse_failed"


@pytest.mark.asyncio
async def test_slang_drift_reviewer_timeout_fails_closed() -> None:
    reviewer = SlangDriftReviewer(_FakeClient(delay_s=0.2), timeout_s=0.01)

    assessment = await reviewer.review_drift(
        existing=_term(),
        new_meaning="新意思",
        aliases=[],
        evidence="没米了",
        confidence=0.9,
        reason="test",
    )

    assert assessment.reviewed is True
    assert assessment.verdict == "unclear"
    assert assessment.error == "drift_semantic_timeout"


@pytest.mark.asyncio
async def test_slang_drift_reviewer_missing_llm_fails_closed() -> None:
    reviewer = SlangDriftReviewer(None)

    assessment = await reviewer.review_drift(
        existing=_term(),
        new_meaning="新意思",
        aliases=[],
        evidence="没米了",
        confidence=0.9,
        reason="test",
    )

    assert assessment.reviewed is False
    assert assessment.verdict == "unclear"
    assert assessment.error == "llm_unavailable"
