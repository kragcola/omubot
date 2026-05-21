"""D.3 negative-signal source tests.

Each source is exercised against a tiny fake store that returns a known
``(rows, total)`` tuple. The fakes deliberately cover three regressions
the source code already protects against:

- ``list_feedback`` returns mixed ``positive/negative`` rows; the source
  must filter Python-side and only emit negatives
- ``list_expressions`` is queried with ``status='rejected'``; if the
  store raises ``ValueError`` (older builds whitelisting only
  ``approved``) the fetcher must return ``[]`` instead of bubbling
- ``list_drift_reviews`` returns a ``tuple[list, int]`` (per the real
  SlangStore API); the fetcher must unpack it, not treat the tuple as
  the row list
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services.memory_consolidator.feedback_sources import (
    NegativeSignal,
    collect_negative_signals,
    fetch_slang_rejected_drifts,
    fetch_style_feedback_signals,
    fetch_style_rejected_expressions,
)


class _FakeStyleStore:
    def __init__(
        self,
        feedbacks: list[Any] | None = None,
        expressions: list[Any] | None = None,
        list_expressions_raises: type[BaseException] | None = None,
    ) -> None:
        self._feedbacks = feedbacks or []
        self._expressions = expressions or []
        self._list_expressions_raises = list_expressions_raises
        self.list_feedback_calls: list[dict[str, Any]] = []
        self.list_expressions_calls: list[dict[str, Any]] = []

    async def list_feedback(self, **kwargs: Any) -> tuple[list[Any], int]:
        self.list_feedback_calls.append(kwargs)
        return list(self._feedbacks), len(self._feedbacks)

    async def list_expressions(self, **kwargs: Any) -> tuple[list[Any], int]:
        self.list_expressions_calls.append(kwargs)
        if self._list_expressions_raises is not None:
            raise self._list_expressions_raises("status not whitelisted")
        return list(self._expressions), len(self._expressions)


class _FakeSlangStore:
    def __init__(
        self,
        drifts: list[Any] | None = None,
        return_tuple: bool = True,
    ) -> None:
        self._drifts = drifts or []
        self._return_tuple = return_tuple
        self.list_drift_reviews_calls: list[dict[str, Any]] = []

    async def list_drift_reviews(self, **kwargs: Any) -> Any:
        self.list_drift_reviews_calls.append(kwargs)
        if self._return_tuple:
            return list(self._drifts), len(self._drifts)
        return list(self._drifts)


@pytest.mark.asyncio
async def test_fetch_style_feedback_filters_negatives_only():
    feedbacks = [
        SimpleNamespace(
            feedback_id="fb1",
            rating="negative",
            raw_text="说话太冷了",
            context="ctx-1",
            target_type="message",
            target_id="m1",
            group_id="g1",
            actor="alice",
            created_at="2026-05-21T12:00:00+00:00",
        ),
        SimpleNamespace(
            feedback_id="fb2",
            rating="positive",
            raw_text="ok",
            context="",
            target_type="message",
            target_id="m2",
            group_id="g1",
            actor="bob",
            created_at="2026-05-21T12:01:00+00:00",
        ),
    ]
    store = _FakeStyleStore(feedbacks=feedbacks)

    out = await fetch_style_feedback_signals(store, group_id="g1", limit=10)

    assert len(out) == 1
    sig = out[0]
    assert isinstance(sig, NegativeSignal)
    assert sig.source_table == "style_feedback"
    assert sig.source_id == "fb1"
    assert sig.group_id == "g1"
    assert "说话太冷了" in sig.detail
    assert sig.meta["actor"] == "alice"


@pytest.mark.asyncio
async def test_fetch_style_feedback_handles_empty_store():
    out = await fetch_style_feedback_signals(None, group_id="g1", limit=10)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_style_feedback_returns_empty_when_method_missing():
    store = SimpleNamespace()  # no list_feedback method
    out = await fetch_style_feedback_signals(store, group_id="g1", limit=10)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_style_rejected_expressions_projects_correctly():
    expressions = [
        SimpleNamespace(
            expression_id="exp1",
            expression="嗯嗯然后呢",
            situation="敷衍场景",
            scope="group",
            confidence=0.42,
            group_id="g1",
            created_at="2026-05-21T10:00:00+00:00",
            updated_at="2026-05-21T11:00:00+00:00",
        ),
    ]
    store = _FakeStyleStore(expressions=expressions)

    out = await fetch_style_rejected_expressions(
        store, group_id="g1", limit=10,
    )

    assert len(out) == 1
    assert out[0].source_table == "style_expressions"
    assert out[0].source_id == "exp1"
    assert "嗯嗯然后呢" in out[0].summary
    assert "敷衍场景" in out[0].detail
    # store API was queried with status='rejected'
    assert store.list_expressions_calls[0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_fetch_style_rejected_expressions_swallows_value_error():
    store = _FakeStyleStore(list_expressions_raises=ValueError)
    out = await fetch_style_rejected_expressions(
        store, group_id="g1", limit=10,
    )
    assert out == []


@pytest.mark.asyncio
async def test_fetch_slang_rejected_drifts_unpacks_tuple_return():
    drifts = [
        SimpleNamespace(
            drift_id="d1",
            term="芜湖",
            reason="模型把它当成感叹词，但群里其实是反讽用",
            status="rejected",
            group_id="g1",
            created_at="2026-05-21T08:00:00+00:00",
            updated_at=1716285600.0,
        ),
        SimpleNamespace(
            drift_id="d2",
            term="蚌埠住了",
            reason="ok",
            status="approved",  # must be filtered out
            group_id="g1",
            created_at="2026-05-21T08:30:00+00:00",
            updated_at=1716287400.0,
        ),
    ]
    store = _FakeSlangStore(drifts=drifts, return_tuple=True)

    out = await fetch_slang_rejected_drifts(store, group_id="g1", limit=10)

    assert len(out) == 1
    assert out[0].source_table == "slang_drift_reviews"
    assert out[0].source_id == "d1"
    assert "芜湖" in out[0].summary


@pytest.mark.asyncio
async def test_fetch_slang_rejected_drifts_handles_list_return():
    drifts = [
        SimpleNamespace(
            drift_id="d1",
            term="芜湖",
            reason="r",
            status="rejected",
            group_id="g1",
            created_at="2026-05-21T08:00:00+00:00",
            updated_at=0,
        ),
    ]
    store = _FakeSlangStore(drifts=drifts, return_tuple=False)

    out = await fetch_slang_rejected_drifts(store, group_id="g1", limit=10)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_collect_negative_signals_aggregates_three_sources():
    style = _FakeStyleStore(
        feedbacks=[
            SimpleNamespace(
                feedback_id="fb1",
                rating="negative",
                raw_text="x",
                context="",
                target_type="",
                target_id="",
                group_id="g1",
                actor="",
                created_at="",
            ),
        ],
        expressions=[
            SimpleNamespace(
                expression_id="exp1",
                expression="x",
                situation="",
                scope="",
                confidence=0.0,
                group_id="g1",
                created_at="",
                updated_at="",
            ),
        ],
    )
    slang = _FakeSlangStore(
        drifts=[
            SimpleNamespace(
                drift_id="d1",
                term="x",
                reason="",
                status="rejected",
                group_id="g1",
                created_at="",
                updated_at=0,
            ),
        ],
    )

    out = await collect_negative_signals(
        style_store=style, slang_store=slang, group_id="g1",
    )

    tables = sorted(s.source_table for s in out)
    assert tables == [
        "slang_drift_reviews", "style_expressions", "style_feedback",
    ]
