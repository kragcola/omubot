"""Guard test: every slang_* LLM entry must prepend the shared prefix.

Per docs/migrations/spine-2026-05-18.md "P2 — 短任务前缀增强", DeepSeek's
common-prefix cache only activates when slang_* tasks share a sufficiently
long static prefix. We inject ``get_shared_slang_prefix()`` at the head
of ``static_blocks`` for all four slang_* call sites
(extractor / review_utils.assess_with_llm / drift_reviewer / semantic_reviewer).

Removing or reordering that block silently regresses cache hit rate from
~26% back to 0%. This test fails closed if any of the four sites stops
putting ``get_shared_slang_prefix()`` first in ``static_blocks``.
"""

from __future__ import annotations

import pytest

from services.slang.drift_reviewer import SlangDriftReviewer
from services.slang.extractor import SlangExtractor
from services.slang.review_utils import assess_with_llm
from services.slang.semantic_reviewer import SlangSemanticReviewer
from services.slang.shared_prefix import get_shared_slang_prefix
from services.slang.types import (
    SlangExtraction,
    SlangPendingCandidate,
    SlangTerm,
)


class _CapturingClient:
    """Captures the LLMRequest passed to ``_call`` and returns scripted text."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[object] = []

    async def _call(self, request):
        self.requests.append(request)
        if self._responses:
            return {"text": self._responses.pop(0)}
        return {"text": '{"terms": []}'}


def _assert_shared_prefix_first(request) -> None:
    static_blocks = list(request.static_blocks)
    assert static_blocks, f"task={request.task!r} has empty static_blocks"
    head = static_blocks[0]
    head_text = head if isinstance(head, str) else str(head.get("text", ""))
    expected = get_shared_slang_prefix()
    assert head_text == expected, (
        f"task={request.task!r} static_blocks[0] is not the shared slang prefix; "
        f"len(head)={len(head_text)} expected_len={len(expected)}"
    )


@pytest.mark.asyncio
async def test_slang_extractor_prepends_shared_prefix() -> None:
    client = _CapturingClient(['{"terms": []}'])
    extractor = SlangExtractor(client)
    await extractor.extract([
        {"speaker": "u1", "content_text": "今天群里聊了一个新词"},
        {"speaker": "u2", "content_text": "什么词"},
    ])
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.task == "slang"
    _assert_shared_prefix_first(request)


@pytest.mark.asyncio
async def test_slang_review_utils_prepends_shared_prefix() -> None:
    client = _CapturingClient(['{"approved": false}'])
    item = SlangExtraction(
        term="猫饼",
        meaning="离谱但可爱的操作",
        aliases=["猫猫饼"],
        evidence="发了个猫饼",
        confidence=0.5,
        reason="test",
    )
    await assess_with_llm(
        client,
        item,
        group_id="100",
        context="recent context",
        search_result="",
    )
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.task == "slang_review"
    _assert_shared_prefix_first(request)


@pytest.mark.asyncio
async def test_slang_drift_reviewer_prepends_shared_prefix() -> None:
    client = _CapturingClient([
        '{"verdict":"same_meaning","confidence":0.9,"reason":"ok"}',
    ])
    reviewer = SlangDriftReviewer(client)
    term = SlangTerm(
        term_id="slang_1",
        term="没米",
        meaning="没钱",
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
    await reviewer.review_drift(
        existing=term,
        new_meaning="没钱或没资源",
        aliases=[],
        evidence="没米",
        confidence=0.9,
        reason="test",
    )
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.task == "slang_drift"
    _assert_shared_prefix_first(request)


@pytest.mark.asyncio
async def test_slang_semantic_reviewer_prepends_shared_prefix_on_every_stage() -> None:
    """All three semantic review stages (context/literal/compare) must share the prefix."""
    client = _CapturingClient([
        '{"meaning":"群内黑话","no_info":false,"confidence":0.92,"reason":"a"}',
        '{"meaning":"字面义","confidence":0.93,"reason":"b"}',
        '{"is_similar":false,"confidence":0.94,"reason":"c"}',
    ])
    reviewer = SlangSemanticReviewer(client)
    pending = SlangPendingCandidate(
        pending_id="pending_1",
        term="猫饼",
        meaning="旧释义",
        aliases=["猫猫饼"],
        group_id="100",
        confidence=0.4,
        count=4,
        unique_users=["u1"],
        evidence="猫饼就是群里说离谱但可爱的操作",
        reason="test",
        repeat_policy="understand_only",
        first_seen_at="2026-05-10T00:00:00+08:00",
        last_seen_at="2026-05-10T00:00:00+08:00",
        meta={},
    )
    assessment = await reviewer.review_pending(
        pending,
        group_id="100",
        user_rows=[{"speaker": "u1", "content_text": "猫饼了"}],
    )
    assert assessment.reviewed is True
    assert len(client.requests) == 3, "semantic reviewer must invoke 3 stages"
    for request in client.requests:
        assert request.task == "slang_semantic"
        _assert_shared_prefix_first(request)
