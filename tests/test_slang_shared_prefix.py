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

from services.slang.drift_reviewer import _SYSTEM_PROMPT as _DRIFT_SYSTEM_PROMPT
from services.slang.drift_reviewer import SlangDriftReviewer
from services.slang.extractor import _SYSTEM_PROMPT as _EXTRACTOR_SYSTEM_PROMPT
from services.slang.extractor import SlangExtractor
from services.slang.review_utils import _REVIEW_SYSTEM_PROMPT, assess_with_llm
from services.slang.semantic_reviewer import (
    _COMPARE_SYSTEM_PROMPT,
    _CONTEXT_SYSTEM_PROMPT,
    _LITERAL_SYSTEM_PROMPT,
    SlangSemanticReviewer,
)
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


def test_slang_review_static_blocks_clear_deepseek_cache_threshold() -> None:
    """方案 D.2 — slang_review 静态系统块（shared_prefix + review prompt）
    必须 ≥ 1300 token 跨过 DeepSeek 1024 缓存门槛。

    shared_prefix.py 注释 1-15 行说明：DeepSeek 自动按词级前缀页缓存，
    最小可缓存前缀 1024 token。slang_review 之前 ~1274 token 紧贴边界，
    实测命中率 30.2%。本测试守住 1300 下界，把缓存前缀稳定推过门槛。
    下次有人删 prompt 文档时 pytest 失败兜底，避免静默回退。
    """
    shared = get_shared_slang_prefix()
    review = _REVIEW_SYSTEM_PROMPT

    def _estimate_tokens(text: str) -> int:
        cjk = sum(1 for c in text if "一" <= c <= "鿿")
        ascii_chars = len(text) - cjk
        return cjk + ascii_chars // 3

    combined = _estimate_tokens(shared) + _estimate_tokens(review)
    assert combined >= 1300, (
        f"slang_review static blocks total {combined} tokens, "
        f"below the 1300 lower bound chosen to clear DeepSeek's 1024 cache "
        f"threshold with margin. Trimming below this floor will silently "
        f"regress slang_review cache hit rate from ~60% back to ~30%."
    )


def _estimate_tokens(text: str) -> int:
    """CJK 1:1 + ASCII 1:0.3, aligned with DeepSeek word-page granularity."""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    ascii_chars = len(text) - cjk
    return cjk + ascii_chars // 3


def test_slang_extractor_static_blocks_clear_deepseek_cache_threshold() -> None:
    """方案 E.1 — slang extractor 静态系统块（shared_prefix + extractor prompt）
    必须 ≥ 1300 token 跨过 DeepSeek 1024 缓存门槛。

    实测 7 天 700 次调用 hit_pct 35.4%，shared(934)+extractor(284)=1218 token
    紧贴 1024 边界。E.1 顶部加"## 提取纪律"段把 combined 推到 ~1900 token，
    跨过 1300 留 ~600 安全余量。下次有人删 prompt 文档时 pytest 失败兜底，
    避免静默回退到 ~35% 命中率。
    """
    shared = get_shared_slang_prefix()
    extractor = _EXTRACTOR_SYSTEM_PROMPT
    combined = _estimate_tokens(shared) + _estimate_tokens(extractor)
    assert combined >= 1300, (
        f"slang extractor static blocks total {combined} tokens, below the "
        f"1300 lower bound chosen to clear DeepSeek's 1024 cache threshold "
        f"with margin. Trimming below this floor will silently regress slang "
        f"extractor cache hit rate from ~60% back to ~35%."
    )


def test_slang_drift_static_blocks_clear_deepseek_cache_threshold() -> None:
    """方案 E.2 — slang_drift 静态系统块（shared_prefix + drift prompt）
    必须 ≥ 1300 token 跨过 DeepSeek 1024 缓存门槛。

    实测 7 天 61 次调用 hit_pct 44.8%，shared(934)+drift(321)=1255 token
    紧贴 1024 边界。E.2 顶部加"## 漂移判定纪律"段把 combined 推到 ~1900,
    跨过 1300 留 ~600 安全余量。下次有人删 prompt 文档时 pytest 失败兜底。
    """
    shared = get_shared_slang_prefix()
    combined = _estimate_tokens(shared) + _estimate_tokens(_DRIFT_SYSTEM_PROMPT)
    assert combined >= 1300, (
        f"slang_drift static blocks total {combined} tokens, below the "
        f"1300 lower bound chosen to clear DeepSeek's 1024 cache threshold "
        f"with margin. Trimming below this floor will silently regress "
        f"slang_drift cache hit rate from ~60% back to ~45%."
    )


def test_slang_semantic_three_stages_clear_deepseek_cache_threshold() -> None:
    """方案 E.3 — slang_semantic 三阶段静态系统块都必须 ≥ 1300 token。

    三阶段（context / literal / compare）共享 shared_prefix(934 token)，
    但每个阶段的 _SYSTEM_PROMPT 自身只有 146-182 token，combined 1080-1116
    全部紧贴 1024 边界。E.3 顶部各加"## 阶段 N 纪律"段把每个阶段都推过
    1300，确保 DeepSeek 词级前缀缓存稳定命中。

    隔离纪律（阶段二只看词形 / 阶段三只看两段 meaning）是三阶段流水线设计
    的核心；prompt 段同时也起到了把缓存前缀拉过门槛的作用。
    """
    shared = get_shared_slang_prefix()
    shared_tok = _estimate_tokens(shared)
    for name, prompt in (
        ("context", _CONTEXT_SYSTEM_PROMPT),
        ("literal", _LITERAL_SYSTEM_PROMPT),
        ("compare", _COMPARE_SYSTEM_PROMPT),
    ):
        combined = shared_tok + _estimate_tokens(prompt)
        assert combined >= 1300, (
            f"slang_semantic stage={name!r} static blocks total {combined} "
            f"tokens, below the 1300 lower bound chosen to clear DeepSeek's "
            f"1024 cache threshold with margin. Trimming below this floor "
            f"will silently regress slang_semantic cache hit rate."
        )

