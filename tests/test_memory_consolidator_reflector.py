"""ReflectionGenerator (D.3) tests.

Covers:

- happy path: a negative signal becomes one episode-domain candidate row
  + one reflection_log row, all in one transaction
- dedup: re-feeding the same (source_table, source_id) is a noop —
  signals_skipped_dedup increments, no extra LLM call, no extra rows
- LLM unparseable JSON: failures+1, run continues to next signal
- LLM missing situation/reflection: same — caught by validator
- D2 cancel-path: asyncio.wait_for(timeout=0.0) on run_once leaves run
  row marked ``failed`` and no orphaned candidate / log rows
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from services.memory_consolidator import (
    ConsolidatorCandidatesStore,
    ReflectionGenerator,
)
from services.memory_consolidator.feedback_sources import NegativeSignal


@pytest.fixture
async def store(tmp_path):
    s = ConsolidatorCandidatesStore(str(tmp_path / "consolidator.db"))
    await s.init()
    yield s
    await s.close()


class _StubLLM:
    """Minimal LLM client that returns canned responses one-by-one.

    Each ``responses`` entry is dispatched to the next ``_call``
    invocation; if exhausted, returns ``{"text": ""}`` so reflector's
    parse-failure branch is hit.
    """

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[Any] = []

    async def _call(self, request: Any) -> dict[str, Any]:
        self.calls.append(request)
        if not self._responses:
            return {"text": ""}
        next_resp = self._responses.pop(0)
        if isinstance(next_resp, Exception):
            raise next_resp
        return {"text": next_resp}


class _SlowStubLLM:
    """LLM stub that hangs forever — used to exercise cancel-path."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def _call(self, request: Any) -> dict[str, Any]:
        self.calls.append(request)
        await asyncio.sleep(3600)
        return {"text": ""}


def _signal(
    source_table: str = "style_feedback",
    source_id: str = "fb1",
    group_id: str = "g1",
) -> NegativeSignal:
    return NegativeSignal(
        source_table=source_table,
        source_id=source_id,
        group_id=group_id,
        summary="test summary",
        detail="test detail",
        occurred_at=0.0,
        meta={"actor": "alice"},
    )


def _good_payload_json() -> str:
    return json.dumps(
        {
            "situation": "用户问技术细节",
            "observed_context": "早晨、用户语气急",
            "action_taken": "直接给结论没解释",
            "outcome_signal": "用户给了 negative",
            "reflection": "下次先简短确认再展开",
            "confidence": 0.45,
        }
    )


@pytest.mark.asyncio
async def test_run_once_creates_candidate_and_log(store, monkeypatch):
    llm = _StubLLM(responses=[_good_payload_json()])
    gen = ReflectionGenerator(store=store, llm_client=llm)

    async def fake_collect(**_kwargs: Any) -> list[NegativeSignal]:
        return [_signal()]

    monkeypatch.setattr(
        "services.memory_consolidator.reflector.collect_negative_signals",
        fake_collect,
    )

    report = await gen.run_once(group_id="g1", triggered_by="test")

    assert report.candidates == 1
    assert report.failures == 0
    assert report.signals_skipped_dedup == 0
    assert report.signals_total == 1
    assert report.status == "done"

    cands = await store.list_candidates(run_id=report.run_id)
    assert len(cands) == 1
    assert cands[0].domain == "episode"
    assert cands[0].state == "dry_run"
    assert cands[0].payload["situation"] == "用户问技术细节"
    assert cands[0].payload["reflection"] == "下次先简短确认再展开"

    log = await store.get_reflection_log(
        source_table="style_feedback", source_id="fb1",
    )
    assert log is not None
    assert log["candidate_id"] == cands[0].candidate_id


@pytest.mark.asyncio
async def test_run_once_dedups_existing_signal(store, monkeypatch):
    llm = _StubLLM(responses=[_good_payload_json(), _good_payload_json()])
    gen = ReflectionGenerator(store=store, llm_client=llm)

    async def fake_collect(**_kwargs: Any) -> list[NegativeSignal]:
        return [_signal()]

    monkeypatch.setattr(
        "services.memory_consolidator.reflector.collect_negative_signals",
        fake_collect,
    )

    first = await gen.run_once(group_id="g1", triggered_by="test")
    assert first.candidates == 1

    second = await gen.run_once(group_id="g1", triggered_by="test")
    assert second.candidates == 0
    assert second.signals_skipped_dedup == 1
    # second run must not have called the LLM again
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_run_once_unparseable_json_counts_failure(store, monkeypatch):
    llm = _StubLLM(responses=["this is not json", _good_payload_json()])
    gen = ReflectionGenerator(store=store, llm_client=llm)

    async def fake_collect(**_kwargs: Any) -> list[NegativeSignal]:
        return [_signal(source_id="fb_bad"), _signal(source_id="fb_good")]

    monkeypatch.setattr(
        "services.memory_consolidator.reflector.collect_negative_signals",
        fake_collect,
    )

    report = await gen.run_once(group_id="g1", triggered_by="test")

    # bad signal counts as failure but pipeline continues
    assert report.candidates == 1
    assert report.failures == 1
    # only the good one made it to the candidate table
    cands = await store.list_candidates(run_id=report.run_id)
    assert len(cands) == 1


@pytest.mark.asyncio
async def test_run_once_rejects_missing_required_fields(store, monkeypatch):
    bad = json.dumps(
        {
            "situation": "",  # empty — must reject
            "reflection": "ok",
            "confidence": 0.5,
        }
    )
    llm = _StubLLM(responses=[bad])
    gen = ReflectionGenerator(store=store, llm_client=llm)

    async def fake_collect(**_kwargs: Any) -> list[NegativeSignal]:
        return [_signal()]

    monkeypatch.setattr(
        "services.memory_consolidator.reflector.collect_negative_signals",
        fake_collect,
    )

    report = await gen.run_once(group_id="g1", triggered_by="test")
    assert report.candidates == 0
    assert report.failures == 1


@pytest.mark.asyncio
async def test_run_once_llm_exception_is_logged_not_raised(
    store, monkeypatch,
):
    llm = _StubLLM(responses=[RuntimeError("provider down"), _good_payload_json()])
    gen = ReflectionGenerator(store=store, llm_client=llm)

    async def fake_collect(**_kwargs: Any) -> list[NegativeSignal]:
        return [_signal(source_id="fb_err"), _signal(source_id="fb_ok")]

    monkeypatch.setattr(
        "services.memory_consolidator.reflector.collect_negative_signals",
        fake_collect,
    )

    report = await gen.run_once(group_id="g1", triggered_by="test")
    assert report.candidates == 1
    assert report.failures == 1


@pytest.mark.asyncio
async def test_run_once_cancel_marks_run_failed(store, monkeypatch):
    """D2 cancel-path: timed-out run_once must leave clean state.

    Reflection writes one candidate + one log row in one transaction;
    cancellation must not leave a half-applied row, and the run row must
    be marked ``failed`` (not stuck on ``running``).
    """
    llm = _SlowStubLLM()
    gen = ReflectionGenerator(store=store, llm_client=llm)

    async def fake_collect(**_kwargs: Any) -> list[NegativeSignal]:
        return [_signal()]

    monkeypatch.setattr(
        "services.memory_consolidator.reflector.collect_negative_signals",
        fake_collect,
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            gen.run_once(group_id="g1", triggered_by="test"),
            timeout=0.05,
        )

    runs = await store.list_runs(limit=5)
    assert runs, "run row must exist after cancel"
    latest = runs[0]
    assert latest.status == "failed"
    assert latest.candidates_count == 0

    # candidate + reflection_log tables must be empty
    orphans = await store.list_candidates(run_id=latest.run_id)
    assert orphans == []
    log = await store.get_reflection_log(
        source_table="style_feedback", source_id="fb1",
    )
    assert log is None


@pytest.mark.asyncio
async def test_run_once_invalid_scope_raises_value_error(store):
    gen = ReflectionGenerator(store=store, llm_client=_StubLLM(responses=[]))
    with pytest.raises(ValueError):
        await gen.run_once(scope="not_a_scope")


@pytest.mark.asyncio
async def test_run_once_uses_store_getters_lazily(store, monkeypatch):
    """style_store_getter / slang_store_getter resolve at run time.

    Plugins inject their stores after ChatPlugin.on_startup, so the
    reflector must not snapshot None at construction.
    """
    holder: dict[str, Any] = {"style": None, "slang": None}

    captured: dict[str, Any] = {}

    async def fake_collect(*, style_store, slang_store, **_kwargs):
        captured["style"] = style_store
        captured["slang"] = slang_store
        return []

    monkeypatch.setattr(
        "services.memory_consolidator.reflector.collect_negative_signals",
        fake_collect,
    )

    gen = ReflectionGenerator(
        store=store,
        llm_client=_StubLLM(responses=[]),
        style_store_getter=lambda: holder["style"],
        slang_store_getter=lambda: holder["slang"],
    )

    holder["style"] = "later-style"
    holder["slang"] = "later-slang"

    await gen.run_once(group_id="g1", triggered_by="test")

    assert captured["style"] == "later-style"
    assert captured["slang"] == "later-slang"
