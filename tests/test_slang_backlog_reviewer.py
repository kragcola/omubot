from __future__ import annotations

import pytest

from kernel.types import PluginContext
from plugins.slang.plugin import SlangPlugin

# --- fakes -----------------------------------------------------------------


class _DummyMessageLog:
    async def list_group_ids(self) -> list[str]:
        return ["100"]

    async def query_recent(self, group_id: str, limit: int = 20) -> list[dict]:
        return [
            {
                "role": "user",
                "speaker": "Alice(10001)",
                "content_text": "猫饼就是群里说离谱但可爱的操作",
                "message_id": 10,
                "created_at": 1.0,
            }
        ]


class _ApprovingReviewLLM:
    """Always approve with high confidence — exercises the promote path."""

    async def _call(self, request):
        del request
        return {
            "text": (
                '{"approved":true,"term":"猫饼","meaning":"网络梗，离谱但可爱的操作",'
                '"aliases":[],"confidence":0.95,"reason":"群内证据与搜索结果一致",'
                '"repeat_policy":"understand_only","is_public_meme":true}'
            )
        }


class _RejectingReviewLLM:
    """Always reject with mid confidence — exercises the mute path."""

    async def _call(self, request):
        del request
        return {
            "text": (
                '{"approved":false,"term":"猫饼","meaning":"普通词","aliases":[],'
                '"confidence":0.7,"reason":"只是普通词不是梗",'
                '"repeat_policy":"understand_only","is_public_meme":false}'
            )
        }


class _UndecidedReviewLLM:
    """Reject but with confidence below mute threshold — exercises the keep path."""

    async def _call(self, request):
        del request
        return {
            "text": (
                '{"approved":false,"term":"猫饼","meaning":"不确定","aliases":[],'
                '"confidence":0.3,"reason":"证据不足",'
                '"repeat_policy":"understand_only","is_public_meme":false}'
            )
        }


class _RaisingReviewLLM:
    """Approve once, then raise — exercises the per-term flush behaviour.

    NOTE: assess_with_llm catches LLM exceptions and falls back to the original
    extraction values, so this class only exercises the LLM-side failure path.
    To genuinely interrupt the reviewer's loop we monkey-patch store.set_status
    in the relevant test instead.
    """

    def __init__(self, raise_after: int) -> None:
        self._calls = 0
        self._raise_after = raise_after

    async def _call(self, request):
        del request
        self._calls += 1
        if self._calls > self._raise_after:
            raise RuntimeError("simulated mid-batch failure")
        return {
            "text": (
                '{"approved":true,"term":"猫饼","meaning":"猫饼的解释","aliases":[],'
                '"confidence":0.95,"reason":"ok","repeat_policy":"understand_only",'
                '"is_public_meme":true}'
            )
        }


# --- helpers ---------------------------------------------------------------


async def _seed_candidates(plugin: SlangPlugin, count: int, *, group_id: str = "100") -> list[str]:
    assert plugin.store is not None
    settings = await plugin.store.load_settings()
    settings.candidate_min_count = 1
    settings.backlog_review_min_usage_count = 1
    settings.backlog_auto_approve_enabled = True
    await plugin.store.save_settings(settings)
    term_ids: list[str] = []
    for i in range(count):
        term_id = await plugin.store.upsert_candidate(
            term=f"猫饼{i:02d}",
            meaning=f"群里说离谱但可爱的操作 {i}",
            aliases=[f"猫猫饼{i:02d}"],
            group_id=group_id,
            user_id="10001",
            message_id=10 + i,
            raw_text=f"猫饼{i:02d}的证据 {i}",
            context="近 8 条消息",
            confidence=0.7 + (i % 3) * 0.05,
            reason="seed",
            repeat_policy="understand_only",
            source="extractor",
            meta={},
            min_count=1,
            observed_count=1,
        )
        assert term_id is not None
        term_ids.append(term_id)
    return term_ids


def _disable_search(settings) -> None:
    settings.backlog_review_search_enabled = False


# --- tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_one_batch_initializes_state_and_processes_batch(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        await _seed_candidates(plugin, 15)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_batch_size = 10
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        before_status = await plugin.get_backlog_review_status()
        assert before_status["active"] is False
        assert before_status["remaining"] == 15

        result = await plugin.run_backlog_review_now()
        assert result["ok"] is True
        assert result["batch_size"] == 10
        assert result["processed"] == 10
        assert result["approved"] == 10
        assert result["muted"] == 0
        assert result["remaining"] == 5

        state = await plugin.store.get_meta("backlog_review_state", {})
        assert state["active"] is True
        assert state["total_at_start"] == 15
        assert state["processed"] == 10
        assert state["last_term_id"]
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_run_one_batch_resumes_from_cursor(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        await _seed_candidates(plugin, 20)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_batch_size = 10
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        first = await plugin.run_backlog_review_now()
        assert first["processed"] == 10
        assert first["approved"] == 10
        first_cursor = (await plugin.store.get_meta("backlog_review_state", {}))["last_term_id"]

        second = await plugin.run_backlog_review_now()
        assert second["processed"] == 20
        assert second["approved"] == 20
        assert second["completed"] is True
        second_cursor = (await plugin.store.get_meta("backlog_review_state", {}))["last_term_id"]
        assert first_cursor != second_cursor

        # Drained — no rows remain in candidate state.
        remaining = await plugin.store.count_backlog_candidates()
        assert remaining == 0

        # Subsequent call hits the empty-backlog skip path.
        third = await plugin.run_backlog_review_now()
        assert third["ok"] is True
        # Either skipped or completed with zero processing in this batch.
        assert third.get("skipped") == "empty_backlog" or third.get("completed") is True
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_run_one_batch_flushes_state_per_term_on_failure(tmp_path, monkeypatch):
    """Mid-batch RuntimeError must leave processed == successes_so_far and last_term_id valid."""
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        await _seed_candidates(plugin, 12)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_batch_size = 10
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        # Patch set_status so the 3rd call raises mid-batch. assess_with_llm
        # swallows LLM exceptions internally (returns a fallback) so we have to
        # raise from the store layer to genuinely interrupt the loop.
        original_set_status = plugin.store.set_status
        call_counter = {"n": 0}

        async def _flaky_set_status(term_id, status, **kwargs):  # type: ignore[no-untyped-def]
            call_counter["n"] += 1
            if call_counter["n"] == 3:
                raise RuntimeError("simulated mid-batch failure")
            return await original_set_status(term_id, status, **kwargs)

        monkeypatch.setattr(plugin.store, "set_status", _flaky_set_status)

        result = await plugin.run_backlog_review_now()
        assert result["ok"] is False
        # Two terms were approved (set_status calls 1 + 2) before the third raised.
        state = await plugin.store.get_meta("backlog_review_state", {})
        assert state["processed"] == 2
        assert state["approved"] == 2
        assert state["last_term_id"]
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_run_one_batch_marks_done_when_drained(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        # No candidates seeded.
        assert plugin.store is not None
        result = await plugin.run_backlog_review_now()
        assert result["ok"] is True
        assert result.get("skipped") == "empty_backlog"
        last_done_at = await plugin.store.get_meta("backlog_review_last_done_at", "")
        assert last_done_at
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_run_one_batch_respects_disabled_setting(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        await _seed_candidates(plugin, 3)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_enabled = False
        await plugin.store.save_settings(settings)

        result = await plugin.run_backlog_review_one_batch_if_due()
        assert result == {"ok": True, "skipped": "disabled"}
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_run_one_batch_promotes_with_audit_actor(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        term_ids = await _seed_candidates(plugin, 1)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        result = await plugin.run_backlog_review_now()
        assert result["approved"] == 1

        term = await plugin.store.get_term(term_ids[0])
        assert term is not None
        assert term.status == "approved"
        # Confidence must not have decreased.
        assert term.confidence >= 0.7
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_run_one_batch_mutes_when_rejected_with_confidence(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_RejectingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        term_ids = await _seed_candidates(plugin, 1)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        result = await plugin.run_backlog_review_now()
        assert result["muted"] == 1
        term = await plugin.store.get_term(term_ids[0])
        assert term is not None
        assert term.status == "muted"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_run_one_batch_keeps_when_low_confidence(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_UndecidedReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        term_ids = await _seed_candidates(plugin, 1)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        result = await plugin.run_backlog_review_now()
        assert result["kept"] == 1
        assert result["muted"] == 0
        assert result["approved"] == 0
        term = await plugin.store.get_term(term_ids[0])
        assert term is not None
        assert term.status == "candidate"
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_reset_backlog_review_clears_state(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        await _seed_candidates(plugin, 15)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_batch_size = 10
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        await plugin.run_backlog_review_now()
        before_state = await plugin.store.get_meta("backlog_review_state", {})
        assert before_state["active"] is True

        cleared = await plugin.reset_backlog_review()
        assert cleared["ok"] is True
        assert cleared["state"]["active"] is False
        after_state = await plugin.store.get_meta("backlog_review_state", {})
        assert after_state["active"] is False
        assert after_state["last_term_id"] == ""
    finally:
        await plugin.on_shutdown(ctx)


# --- slot-based scheduler regression tests --------------------------------
#
# The bug these guard against: tick path used to start a fresh round every
# minute the moment the previous round finished, ignoring the user's
# daily_ai_review_times schedule. Verify it now (a) drains the backlog and
# locks the slot, (b) skips re-runs in the same slot, (c) re-arms in a new slot.


@pytest.mark.asyncio
async def test_backlog_if_due_drains_pool_and_locks_slot(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        await _seed_candidates(plugin, 12)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_enabled = True
        # "00:00" so any wall-clock time is >= the slot — guaranteed due.
        settings.daily_ai_review_times = ["00:00"]
        settings.backlog_review_batch_size = 5
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        result = await plugin.run_backlog_review_one_batch_if_due(ctx, settings=settings)
        assert result["ok"] is True
        # All 12 candidates should be drained in one tick (3 batches of 5/5/2).
        assert result["completed"] is True
        assert result["batches"] >= 1
        assert result["processed"] == 12
        last_slot = await plugin.store.get_meta("last_backlog_review_slot", "")
        assert last_slot
        assert last_slot.endswith(":00:00")
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_backlog_if_due_skips_when_slot_already_ran(tmp_path):
    """Once a slot has been marked done, repeating the tick must NOT restart a
    new round even if more candidates appear before the next slot — the whole
    point is to respect the user's daily schedule."""
    plugin = SlangPlugin()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        await _seed_candidates(plugin, 8)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_enabled = True
        settings.daily_ai_review_times = ["00:00"]
        settings.backlog_review_batch_size = 100
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        first = await plugin.run_backlog_review_one_batch_if_due(ctx, settings=settings)
        assert first["completed"] is True

        # Even if a new pile of candidates appears in the same slot, no new run.
        await _seed_candidates(plugin, 5, group_id="200")
        second = await plugin.run_backlog_review_one_batch_if_due(ctx, settings=settings)
        assert second.get("skipped") == "already_ran", second
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_backlog_if_due_does_not_lock_slot_on_partial_completion(tmp_path):
    """If the tick budget runs out mid-pool (or LLM fails), the slot must NOT
    be locked — the next tick has to be able to resume the same slot rather
    than wait for tomorrow."""
    plugin = SlangPlugin()
    # Use rejecting LLM with the per-call mute path; the test only needs a
    # successful single-batch run that does not drain the pool.
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_ApprovingReviewLLM(),
    )
    await plugin.on_startup(ctx)
    try:
        # Pool larger than what a single 600s wait would finish even at 5 items;
        # we use a tiny batch_size and assume tick budget exhausts before drain.
        # Easier: monkey-patch run_backlog_review_now to return after one batch
        # without completed=True so the loop exits without setting completed.
        await _seed_candidates(plugin, 30)
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.backlog_review_enabled = True
        settings.daily_ai_review_times = ["00:00"]
        settings.backlog_review_batch_size = 5
        _disable_search(settings)
        await plugin.store.save_settings(settings)

        # Stub run_backlog_review_now to short-circuit after one batch with a
        # fake "more remaining" response that doesn't trip the deadline check
        # but signals incompleteness.
        original = plugin.run_backlog_review_now
        calls = {"n": 0}

        async def _stub(ctx=None, *, settings=None, batch_size=None, min_confidence=None, _caller_holds_lock=False):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] >= 2:
                # Force the if-due loop to stop after first iteration by
                # returning skipped on the second call. This simulates the
                # tick deadline being hit before drain.
                return {"ok": True, "skipped": "deadline"}
            return {
                "ok": True,
                "batch_size": 5,
                "approved_in_batch": 5,
                "muted_in_batch": 0,
                "kept_in_batch": 0,
                "remaining": 25,
                "completed": False,
            }

        plugin.run_backlog_review_now = _stub  # type: ignore[assignment]
        try:
            result = await plugin.run_backlog_review_one_batch_if_due(ctx, settings=settings)
        finally:
            plugin.run_backlog_review_now = original  # type: ignore[assignment]
        assert result["ok"] is True
        assert result["completed"] is False
        last_slot = await plugin.store.get_meta("last_backlog_review_slot", "")
        assert last_slot == "", (
            "incomplete tick must leave slot unlocked so the next tick resumes; "
            f"got {last_slot!r}"
        )
    finally:
        await plugin.on_shutdown(ctx)
