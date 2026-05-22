from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from kernel.config import BotConfig
from kernel.types import Identity, PluginContext, PromptContext
from plugins.slang.plugin import SlangPlugin
from services.slang.types import DEFAULT_DAILY_AI_REVIEW_TIMES, SlangSettings
from services.tools.context import ToolContext


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
            },
            {
                "role": "user",
                "speaker": "Bob(10002)",
                "content_text": "对，这个猫饼太典了",
                "message_id": 11,
                "created_at": 2.0,
            },
        ]


class _DummyLLM:
    async def _call(self, request):
        del request
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"群里说离谱但可爱的操作",'
                '"aliases":["猫猫饼"],"evidence":"猫饼就是群里说离谱但可爱的操作",'
                '"confidence":0.7,"reason":"有群内解释","repeat_policy":"understand_only"}]}'
            )
        }


class _LowSignalLLM:
    async def _call(self, request):
        del request
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"猫饼","aliases":[],"evidence":"猫饼",'
                '"confidence":0.66,"reason":"只是重复原词","repeat_policy":"understand_only"}]}'
            )
        }


class _GenericMeaningLLM:
    async def _call(self, request):
        del request
        return {
            "text": (
                '{"terms":[{"term":"猫饼","meaning":"一个梗","aliases":["猫饼","哈哈"],'
                '"evidence":"猫饼就是群里说离谱但可爱的操作",'
                '"confidence":0.66,"reason":"解释过于泛化","repeat_policy":"understand_only"}]}'
            )
        }


class _SingleMessageLog:
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
            },
        ]


@pytest.mark.asyncio
async def test_slang_plugin_extracts_candidates_and_injects_only_approved(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 1

        assert plugin.store is not None
        terms, _total = await plugin.store.list_terms(group_id="100")
        assert len(terms) == 1
        assert terms[0].status == "candidate"

        prompt_ctx = PromptContext(
            session_id="group_100",
            group_id="100",
            user_id="u1",
            identity=Identity(name="omu"),
            conversation_text="今天猫饼了",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        assert prompt_ctx.blocks == []

        await plugin.store.set_status(terms[0].term_id, "approved")
        await plugin.on_pre_prompt(prompt_ctx)
        assert len(prompt_ctx.blocks) == 1
        assert prompt_ctx.blocks[0].label == "群内黑话"
        assert "猫饼" in prompt_ctx.blocks[0].text
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_records_run_and_buffers_below_min_count(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_SingleMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        settings = await plugin.store.load_settings()
        settings.candidate_min_count = 2
        await plugin.store.save_settings(settings)

        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["run_id"]
        assert result["candidates"] == 0

        terms, total = await plugin.store.list_terms(group_id="100")
        assert terms == []
        assert total == 0
        pending, pending_total = await plugin.store.list_pending(group_id="100")
        assert pending_total == 1
        assert pending[0].count == 1
        runs = await plugin.store.list_extraction_runs()
        assert runs[0].run_id == result["run_id"]
        assert runs[0].promoted_candidates == 0
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_lookup_tool_uses_current_group_and_global_terms(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin.on_startup(ctx)
    try:
        assert plugin.store is not None
        await plugin.store.create_term(
            term="猫饼",
            meaning="当前群的离谱可爱操作",
            scope="group",
            group_id="100",
            status="approved",
        )
        await plugin.store.create_term(
            term="全局猫饼",
            meaning="所有群都能理解的猫饼",
            scope="global",
            status="approved",
        )
        await plugin.store.create_term(
            term="隔壁猫饼",
            meaning="隔壁群专用解释",
            scope="group",
            group_id="200",
            status="approved",
        )

        tools = plugin.register_tools()
        assert len(tools) == 1
        result = await tools[0].execute(ToolContext(group_id="100", session_id="group_100"), query="猫饼")
        assert "当前群的离谱可爱操作" in result
        assert "所有群都能理解" in result
        assert "隔壁群专用解释" not in result

        settings = await plugin.store.load_settings()
        settings.lookup_tool_enabled = False
        await plugin.store.save_settings(settings)
        disabled = await tools[0].execute(ToolContext(group_id="100"), query="猫饼")
        assert "已关闭" in disabled
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_extractor_filters_low_signal_meaning(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_LowSignalLLM())
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 0
        assert plugin.store is not None
        terms, total = await plugin.store.list_terms(group_id="100")
        assert total == 0
        assert terms == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_extractor_filters_generic_placeholder_meaning(tmp_path):
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_GenericMeaningLLM())
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 0
        assert plugin.store is not None
        terms, total = await plugin.store.list_terms(group_id="100")
        assert total == 0
        assert terms == []
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_slang_plugin_respects_group_profile_disable(tmp_path):
    plugin = SlangPlugin()
    config = BotConfig.model_validate({
        "group": {
            "allowed_groups": [100],
            "overrides": {
                "100": {
                    "slang_enabled": False,
                },
            },
        },
    })
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=_DummyMessageLog(),
        llm_client=_DummyLLM(),
        config=config,
    )
    await plugin.on_startup(ctx)
    try:
        result = await plugin.run_manual_extract(group_id="100", limit=20)
        assert result["ok"] is True
        assert result["candidates"] == 0

        prompt_ctx = PromptContext(
            session_id="group_100",
            group_id="100",
            user_id="u1",
            identity=Identity(name="omu"),
            conversation_text="今天猫饼了",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        assert prompt_ctx.blocks == []
    finally:
        await plugin.on_shutdown(ctx)


class _SlowLLM:
    """LLM stub that hangs forever — simulates a slow API call so wait_for fires."""

    async def _call(self, request):
        del request
        await asyncio.sleep(60)  # never returns within the test budget
        raise AssertionError("should never reach")


@pytest.mark.asyncio
async def test_run_manual_extract_finishes_run_when_cancelled(tmp_path):
    """When the surrounding tick wraps run_manual_extract in wait_for and times
    out, the extraction run row must NOT stay on 'running' — that was the
    original bug where the dashboard showed 0 / 0 / 0 forever."""
    plugin = SlangPlugin()
    ctx = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_SlowLLM())
    await plugin.on_startup(ctx)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                plugin.run_manual_extract(group_id="100", limit=20),
                timeout=0.05,
            )
        # Give the shielded finish task a tick to land
        runs: list = []
        for _ in range(20):
            assert plugin.store is not None
            runs = await plugin.store.list_extraction_runs(limit=5)
            if runs and runs[0].status != "running":
                break
            await asyncio.sleep(0.05)
        assert runs, "expected at least one extraction run row"
        latest = runs[0]
        assert latest.status == "cancelled"
        assert latest.finished_at  # populated, not NULL
        assert "cancel" in latest.error.lower()
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_on_startup_marks_orphan_running_runs_abandoned(tmp_path):
    """Crashes before this fix left runs stuck on 'running'. on_startup must
    sweep them so the admin dashboard reflects truth after restart."""
    # First boot: leak an orphan run on disk
    plugin_a = SlangPlugin()
    ctx_a = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin_a.on_startup(ctx_a)
    try:
        assert plugin_a.store is not None
        await plugin_a.store.start_extraction_run(group_count=3, meta={"leak": True})
    finally:
        # Skip on_shutdown to mirror a hard crash — the run row stays 'running'
        if plugin_a.store is not None:
            await plugin_a.store.close()
            plugin_a.store = None

    # Second boot: on_startup should reap the orphan
    plugin_b = SlangPlugin()
    ctx_b = PluginContext(storage_dir=tmp_path, msg_log=_DummyMessageLog(), llm_client=_DummyLLM())
    await plugin_b.on_startup(ctx_b)
    try:
        assert plugin_b.store is not None
        runs = await plugin_b.store.list_extraction_runs(limit=5)
        assert runs
        assert all(run.status != "running" for run in runs)
        assert any(run.status == "abandoned" for run in runs)
    finally:
        await plugin_b.on_shutdown(ctx_b)


# ---------- U-5: daily_ai_review_times slot semantics ----------


def test_settings_validator_default_when_empty_or_invalid():
    assert SlangSettings(daily_ai_review_times=[]).daily_ai_review_times == list(
        DEFAULT_DAILY_AI_REVIEW_TIMES
    )
    assert SlangSettings(daily_ai_review_times=None).daily_ai_review_times == list(
        DEFAULT_DAILY_AI_REVIEW_TIMES
    )
    assert SlangSettings(daily_ai_review_times="").daily_ai_review_times == list(
        DEFAULT_DAILY_AI_REVIEW_TIMES
    )


def test_settings_validator_dedup_and_sort():
    s = SlangSettings(daily_ai_review_times=["16:00", "04:00", "16:00", "9:30"])
    assert s.daily_ai_review_times == ["04:00", "09:30", "16:00"]


def test_settings_validator_rejects_bad_format():
    for bad in ("25:00", "12:60", "abc", "12-30"):
        with pytest.raises(ValueError):
            SlangSettings(daily_ai_review_times=[bad])


class _SlotMemStore:
    """Minimal store stub for slot meta tests; only get_meta/set_meta used."""

    def __init__(self) -> None:
        self.meta: dict[str, str] = {}

    async def get_meta(self, key: str, default: str = "") -> str:
        return self.meta.get(key, default)

    async def set_meta(self, key: str, value: str) -> None:
        self.meta[key] = value


def _patch_clock(monkeypatch: pytest.MonkeyPatch, fixed: datetime) -> None:
    """Freeze plugins.slang.plugin.datetime.now() to a known instant."""

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed if tz is None else fixed.astimezone(tz)

    import plugins.slang.plugin as slang_plugin_mod

    monkeypatch.setattr(slang_plugin_mod, "datetime", _FrozenDateTime)


@pytest.mark.asyncio
async def test_run_backlog_review_skipped_when_no_slots(monkeypatch):
    plugin = SlangPlugin()
    plugin.store = _SlotMemStore()  # type: ignore[assignment]
    settings = SlangSettings(
        backlog_review_enabled=True,
        daily_ai_review_times=[],
    )
    settings.daily_ai_review_times = []  # bypass validator default for this case
    _patch_clock(monkeypatch, datetime(2026, 5, 23, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = await plugin.run_backlog_review_one_batch_if_due(settings=settings)
    assert result == {"ok": True, "skipped": "no_slots"}


@pytest.mark.asyncio
async def test_run_backlog_review_skipped_when_not_due(monkeypatch):
    plugin = SlangPlugin()
    plugin.store = _SlotMemStore()  # type: ignore[assignment]
    settings = SlangSettings(
        backlog_review_enabled=True,
        daily_ai_review_times=["04:00", "16:00"],
    )
    # 03:00 — earliest slot 04:00 hasn't fired yet today
    _patch_clock(monkeypatch, datetime(2026, 5, 23, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = await plugin.run_backlog_review_one_batch_if_due(settings=settings)
    assert result == {"ok": True, "skipped": "not_due"}


@pytest.mark.asyncio
async def test_run_backlog_review_locks_slot_after_full_drain(monkeypatch):
    """Two slots in a day; running at the first slot must lock it; same-slot rerun must short-circuit."""
    plugin = SlangPlugin()
    store = _SlotMemStore()
    plugin.store = store  # type: ignore[assignment]
    settings = SlangSettings(
        backlog_review_enabled=True,
        daily_ai_review_times=["04:00", "16:00"],
    )
    call_count = {"n": 0}

    async def _fake_run_now(*args, **kwargs):
        call_count["n"] += 1
        # First batch processes a couple of items; subsequent calls hit "no eligible".
        if call_count["n"] == 1:
            return {
                "ok": True,
                "approved_in_batch": 2,
                "muted_in_batch": 0,
                "kept_in_batch": 1,
                "batch_size": 3,
                "completed": True,
                "remaining": 0,
            }
        return {"ok": True, "skipped": "no_eligible"}

    monkeypatch.setattr(plugin, "run_backlog_review_now", _fake_run_now)
    # 04:30 — slot 04:00 is due
    _patch_clock(monkeypatch, datetime(2026, 5, 23, 4, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    result = await plugin.run_backlog_review_one_batch_if_due(settings=settings)
    assert result["ok"] is True
    assert result["slot"] == "2026-05-23:04:00"
    assert result["completed"] is True
    assert store.meta["last_backlog_review_slot"] == "2026-05-23:04:00"

    # Same slot rerun: must short-circuit on already_ran without invoking run_now
    call_count["n"] = 0
    result2 = await plugin.run_backlog_review_one_batch_if_due(settings=settings)
    assert result2 == {"ok": True, "skipped": "already_ran"}
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_run_backlog_review_next_slot_resets(monkeypatch):
    """After the 04:00 slot is locked, advancing past 16:00 must allow a fresh run for the new slot."""
    plugin = SlangPlugin()
    store = _SlotMemStore()
    store.meta["last_backlog_review_slot"] = "2026-05-23:04:00"
    plugin.store = store  # type: ignore[assignment]
    settings = SlangSettings(
        backlog_review_enabled=True,
        daily_ai_review_times=["04:00", "16:00"],
    )

    async def _fake_run_now(*args, **kwargs):
        return {
            "ok": True,
            "approved_in_batch": 0,
            "muted_in_batch": 0,
            "kept_in_batch": 0,
            "batch_size": 0,
            "completed": True,
            "remaining": 0,
        }

    monkeypatch.setattr(plugin, "run_backlog_review_now", _fake_run_now)
    # 16:30 — slot 16:00 is now the latest due slot
    _patch_clock(monkeypatch, datetime(2026, 5, 23, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    result = await plugin.run_backlog_review_one_batch_if_due(settings=settings)
    assert result["slot"] == "2026-05-23:16:00"
    assert store.meta["last_backlog_review_slot"] == "2026-05-23:16:00"


@pytest.mark.asyncio
async def test_run_backlog_review_skipped_when_disabled(monkeypatch):
    plugin = SlangPlugin()
    plugin.store = _SlotMemStore()  # type: ignore[assignment]
    settings = SlangSettings(
        backlog_review_enabled=False,
        daily_ai_review_times=["04:00"],
    )
    _patch_clock(monkeypatch, datetime(2026, 5, 23, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    result = await plugin.run_backlog_review_one_batch_if_due(settings=settings)
    assert result == {"ok": True, "skipped": "disabled"}
