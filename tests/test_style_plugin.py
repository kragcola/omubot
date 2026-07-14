import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from kernel.bus import PluginBus
from kernel.types import Identity, PluginContext, PromptContext, ReplyContext
from plugins.style.plugin import StyleConfig, StylePlugin
from services.learning_extract_coordinator import LearningExtractCoordinator
from services.style import NewStyleExpression, StyleStore


def test_style_manifest_declares_reply_permission() -> None:
    manifest_path = Path(__file__).resolve().parent.parent / "plugins" / "style" / "plugin.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "reply" in data["permissions"]


@pytest.fixture
async def style_store(tmp_path) -> AsyncIterator[StyleStore]:
    store = StyleStore(tmp_path / "style.db")
    await store.init()
    yield store
    await store.close()


def _prompt_ctx(group_id: str, text: str) -> PromptContext:
    return PromptContext(
        session_id=f"group_{group_id}",
        group_id=group_id,
        user_id="10001",
        identity=Identity(name="凤笑梦"),
        conversation_text=text,
    )


def _plugin_ctx(store: StyleStore) -> PluginContext:
    ctx = PluginContext()
    cast(Any, ctx).style_store = store
    return ctx


@pytest.mark.asyncio
async def test_style_plugin_injects_approved_group_expression(style_store: StyleStore) -> None:
    expression = await style_store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            group_id="100",
            confidence=0.8,
        )
    )
    await style_store.set_status(expression.expression_id, "approved")
    plugin = StylePlugin(StyleConfig())
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    prompt_ctx = _prompt_ctx("100", "大家在轻松吐槽这件事")
    await plugin.on_pre_prompt(prompt_ctx)
    await plugin.on_shutdown(plugin_ctx)

    assert [block.label for block in prompt_ctx.blocks] == ["表达习惯参考"]
    assert "不要照抄" in prompt_ctx.blocks[0].text
    assert "大家在轻松吐槽" in prompt_ctx.blocks[0].text


@pytest.mark.asyncio
async def test_style_plugin_global_pool_only_for_enabled_groups(style_store: StyleStore) -> None:
    expression = await style_store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            scope="global",
            group_id="100",
            confidence=0.8,
        )
    )
    await style_store.set_status(expression.expression_id, "approved")
    plugin = StylePlugin(StyleConfig(global_enabled_group_ids=["200"]))
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    closed_ctx = _prompt_ctx("100", "大家在轻松吐槽")
    opened_ctx = _prompt_ctx("200", "大家在轻松吐槽")
    await plugin.on_pre_prompt(closed_ctx)
    await plugin.on_pre_prompt(opened_ctx)
    await plugin.on_shutdown(plugin_ctx)

    assert closed_ctx.blocks == []
    assert [block.label for block in opened_ctx.blocks] == ["表达习惯参考"]


@pytest.mark.asyncio
async def test_style_plugin_injects_enabled_profile_before_expression_block(style_store: StyleStore) -> None:
    expression = await style_store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            group_id="100",
            confidence=0.8,
        )
    )
    await style_store.set_status(expression.expression_id, "approved")
    await style_store.generate_profile(group_id="100", actor="tester", enable=True)
    plugin = StylePlugin(StyleConfig())
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    prompt_ctx = _prompt_ctx("100", "大家在轻松吐槽")
    await plugin.on_pre_prompt(prompt_ctx)
    await plugin.on_shutdown(plugin_ctx)

    assert [block.label for block in prompt_ctx.blocks] == ["动态风格档案", "表达习惯参考"]
    assert "不得改变核心人设" in prompt_ctx.blocks[0].text


@pytest.mark.asyncio
async def test_style_plugin_records_bot_reply_feedback(style_store: StyleStore) -> None:
    plugin = StylePlugin(StyleConfig())
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    await plugin.on_post_reply(
        ReplyContext(
            session_id="group_100",
            group_id="100",
            user_id="10001",
            reply_content="哇，听起来很有意思！",
            user_msg="我刚刚打了新成绩",
            elapsed_ms=123.0,
            thinker_action="reply",
        )
    )
    await plugin.on_shutdown(plugin_ctx)

    feedback, total = await style_store.list_feedback(target_type="reply", group_id="100")
    assert total == 1
    assert feedback[0].rating == "neutral"
    assert feedback[0].source == "weak_signal"
    assert feedback[0].raw_text == "哇，听起来很有意思！"


@pytest.mark.asyncio
async def test_style_plugin_disabled_does_not_create_block(style_store: StyleStore) -> None:
    plugin = StylePlugin(StyleConfig(enabled=False))
    await plugin.on_startup(_plugin_ctx(style_store))

    prompt_ctx = _prompt_ctx("100", "大家在轻松吐槽")
    await plugin.on_pre_prompt(prompt_ctx)

    assert prompt_ctx.blocks == []


@pytest.mark.asyncio
async def test_style_tick_uses_canonical_msg_log_and_calls_coordinator(
    style_store: StyleStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = StylePlugin(StyleConfig())
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=object(),
        llm_client=object(),
    )
    cast(Any, ctx).style_store = style_store
    await plugin.on_startup(ctx)
    extract_called = asyncio.Event()

    async def coordinated_extract(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        extract_called.set()
        return {"ok": True}

    coordinator = AsyncMock(side_effect=coordinated_extract)
    monkeypatch.setattr(
        "services.learning_settings.load",
        lambda _storage_dir: {
            "style": {
                "extract_enabled": True,
                "extract_interval_minutes": 0,
            }
        },
    )
    monkeypatch.setattr(
        "plugins.style.plugin.run_coordinated_extract",
        coordinator,
    )

    try:
        await plugin.on_tick(ctx)
        await asyncio.wait_for(extract_called.wait(), timeout=1.0)
    finally:
        await plugin.on_shutdown(ctx)

    coordinator.assert_awaited_once()
    await_args = coordinator.await_args
    assert await_args is not None
    assert await_args.kwargs["noun"] == "style"


@pytest.mark.asyncio
async def test_style_tick_is_reachable_through_manifest_governed_plugin_bus(
    style_store: StyleStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = StylePlugin(StyleConfig())
    bus = PluginBus()
    bus.register(plugin)
    coordinator = LearningExtractCoordinator()
    message_log = object()
    llm_client = object()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=message_log,
        llm_client=llm_client,
    )
    cast(Any, ctx).style_store = style_store
    cast(Any, ctx).learning_extract_coordinator = coordinator
    extract_called = asyncio.Event()

    async def manual_extract_runner(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        extract_called.set()
        return {"ok": True}

    manual_extract = AsyncMock(side_effect=manual_extract_runner)
    monkeypatch.setattr(
        "services.learning_settings.load",
        lambda _storage_dir: {
            "style": {
                "extract_enabled": True,
                "extract_interval_minutes": 0,
            }
        },
    )
    monkeypatch.setattr(
        "plugins.style.plugin.run_style_manual_extract",
        manual_extract,
    )

    await bus.fire_on_startup(ctx)
    try:
        await bus.fire_on_tick(ctx)
        await asyncio.wait_for(extract_called.wait(), timeout=1.0)
    finally:
        await bus.fire_on_shutdown(ctx)
        await coordinator.stop()

    manual_extract.assert_awaited_once_with(
        style_store=style_store,
        message_log=message_log,
        llm_client=llm_client,
        slang_store=None,
        auto_approve=False,
        limit=40,
        max_batches=1,
    )
    assert len(coordinator.runs) == 1


@pytest.mark.asyncio
async def test_style_tick_does_not_block_bus_or_start_duplicate_extract(
    style_store: StyleStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = StylePlugin(StyleConfig())
    bus = PluginBus()
    bus.register(plugin)
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=object(),
        llm_client=object(),
    )
    cast(Any, ctx).style_store = style_store
    started = asyncio.Event()
    release = asyncio.Event()

    async def coordinated_extract(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(
        "services.learning_settings.load",
        lambda _storage_dir: {
            "style": {
                "extract_enabled": True,
                "extract_interval_minutes": 0,
            }
        },
    )
    coordinator = AsyncMock(side_effect=coordinated_extract)
    monkeypatch.setattr(
        "plugins.style.plugin.run_coordinated_extract",
        coordinator,
    )

    await bus.fire_on_startup(ctx)
    first_tick = asyncio.create_task(bus.fire_on_tick(ctx))
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await asyncio.sleep(0)
        assert first_tick.done(), "on_tick must return while extraction continues in the background"

        await bus.fire_on_tick(ctx)
        assert coordinator.await_count == 1
    finally:
        release.set()
        await first_tick
        await bus.fire_on_shutdown(ctx)


@pytest.mark.asyncio
async def test_style_shutdown_cancels_and_awaits_background_extract(
    style_store: StyleStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = StylePlugin(StyleConfig())
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=object(),
        llm_client=object(),
    )
    cast(Any, ctx).style_store = style_store
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def coordinated_extract(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(
        "services.learning_settings.load",
        lambda _storage_dir: {
            "style": {
                "extract_enabled": True,
                "extract_interval_minutes": 0,
            }
        },
    )
    monkeypatch.setattr(
        "plugins.style.plugin.run_coordinated_extract",
        AsyncMock(side_effect=coordinated_extract),
    )

    await plugin.on_startup(ctx)
    await plugin.on_tick(ctx)
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await plugin.on_shutdown(ctx)

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_style_shutdown_closes_real_coordinator_run(
    style_store: StyleStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = StylePlugin(StyleConfig())
    coordinator = LearningExtractCoordinator()
    ctx = PluginContext(
        storage_dir=tmp_path,
        msg_log=object(),
        llm_client=object(),
    )
    cast(Any, ctx).style_store = style_store
    cast(Any, ctx).learning_extract_coordinator = coordinator
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def manual_extract(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(
        "services.learning_settings.load",
        lambda _storage_dir: {
            "style": {
                "extract_enabled": True,
                "extract_interval_minutes": 0,
            }
        },
    )
    monkeypatch.setattr(
        "plugins.style.plugin.run_style_manual_extract",
        AsyncMock(side_effect=manual_extract),
    )

    await plugin.on_startup(ctx)
    await plugin.on_tick(ctx)
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await plugin.on_shutdown(ctx)

    assert cancelled.is_set()
    assert len(coordinator.runs) == 1
    run = next(iter(coordinator.runs.values()))
    assert run["status"] != "running"
    assert run["finished_at"]
    await coordinator.stop()
