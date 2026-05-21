from __future__ import annotations

import asyncio

import pytest

from kernel.config import BotConfig
from kernel.types import Identity, PluginContext, PromptContext
from plugins.slang.plugin import SlangPlugin
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
