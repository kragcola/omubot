"""StylePlugin: inject approved expression habits into prompts."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any, cast

from loguru import logger
from pydantic import BaseModel, Field

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext, PromptContext, ReplyContext
from services.learning_extract_coordinator import (
    ExtractRunParams,
    run_coordinated_extract,
)
from services.style import StyleStore, run_style_manual_extract

_L = logger.bind(channel="system")


class StyleConfig(BaseModel):
    enabled: bool = True
    max_items: int = Field(default=3, ge=1, le=5)
    max_chars: int = Field(default=800, ge=160, le=2000)
    min_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    global_enabled_group_ids: list[str] = Field(default_factory=list)
    profile_enabled: bool = True
    profile_max_chars: int = Field(default=900, ge=160, le=2400)
    collect_bot_replies: bool = True


class StylePlugin(AmadeusPlugin):
    name = "style"
    description = "表达学习：注入已审核的表达习惯参考"
    version = "1.0.0"
    priority = 43

    def __init__(self, config: StyleConfig | None = None) -> None:
        super().__init__()
        self._config_override = config
        self._enabled = False
        self._max_items = 3
        self._max_chars = 800
        self._min_confidence = 0.45
        self._profile_enabled = True
        self._profile_max_chars = 900
        self._collect_bot_replies = True
        self._global_enabled_groups: set[str] = set()
        self._store: StyleStore | None = None
        self._owns_store = False
        self._provider_superseded: bool = False
        self._last_extract_monotonic: float = 0.0
        self._tick_task: asyncio.Task[None] | None = None

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = self._config_override or load_plugin_config("plugins/style/config.default.json", StyleConfig)
        self._enabled = cfg.enabled
        self._max_items = cfg.max_items
        self._max_chars = cfg.max_chars
        self._min_confidence = cfg.min_confidence
        self._profile_enabled = cfg.profile_enabled
        self._profile_max_chars = cfg.profile_max_chars
        self._collect_bot_replies = cfg.collect_bot_replies
        self._global_enabled_groups = {
            str(group_id).strip()
            for group_id in cfg.global_enabled_group_ids
            if str(group_id).strip()
        }
        if not self._enabled:
            _L.info("style plugin disabled")
            return

        ctx_store = getattr(ctx, "style_store", None)
        if ctx_store is not None:
            store = cast(StyleStore, ctx_store)
            self._store = store
            if not getattr(store, "initialized", False):
                await store.init()
        else:
            db_path = Path(getattr(ctx, "storage_dir", Path("storage"))) / "style.db"
            self._store = StyleStore(db_path)
            await self._store.init()
            cast(Any, ctx).style_store = self._store
            self._owns_store = True
        _L.info(
            "style plugin enabled | max_items={} max_chars={} global_groups={}",
            self._max_items,
            self._max_chars,
            len(self._global_enabled_groups),
        )
        provider_bus = getattr(ctx, "provider_bus", None)
        if provider_bus is not None and provider_bus.has_provider("style"):
            self._provider_superseded = True
            _L.info("style prompt injection delegated to provider bus")

        # Phase E.2 graph edge double-write — mirror approve/mute/reject
        # status flips to knowledge_graph as `style_applies_to_situation`
        # edges. Best-effort: a graph write failure must never block
        # `update_expression` (audit § E.2).
        try:
            from services.knowledge_graph.graph_writer import GraphWriter
            from services.style.graph_bridge import StyleGraphBridge

            kg_service = getattr(ctx, "knowledge_graph", None)
            kg_store = getattr(kg_service, "_store", None) if kg_service else None
            if (
                kg_store is not None
                and getattr(kg_store, "_db", None) is not None
                and self._store is not None
            ):
                ctx.style_graph_bridge = StyleGraphBridge(GraphWriter(kg_store))
                ctx.style_graph_bridge.attach(self._store)
        except Exception as exc:
            _L.warning("style graph bridge attach failed | err={}", exc)

        # Phase E.3 graph edge double-write — mirror negative feedback to
        # `user_corrected_bot_about` edges. Same best-effort discipline.
        try:
            from services.knowledge_graph.graph_writer import GraphWriter
            from services.style.feedback_graph_bridge import (
                StyleFeedbackGraphBridge,
            )

            kg_service = getattr(ctx, "knowledge_graph", None)
            kg_store = getattr(kg_service, "_store", None) if kg_service else None
            if (
                kg_store is not None
                and getattr(kg_store, "_db", None) is not None
                and self._store is not None
            ):
                ctx.style_feedback_graph_bridge = StyleFeedbackGraphBridge(
                    GraphWriter(kg_store),
                )
                ctx.style_feedback_graph_bridge.attach(self._store)
        except Exception as exc:
            _L.warning("style feedback graph bridge attach failed | err={}", exc)

    async def on_shutdown(self, ctx: PluginContext) -> None:
        del ctx
        tick_task = self._tick_task
        self._tick_task = None
        if tick_task is not None:
            if not tick_task.done():
                tick_task.cancel()
            await asyncio.gather(tick_task, return_exceptions=True)
        if self._owns_store and self._store is not None:
            await self._store.close()
        self._store = None
        self._owns_store = False

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if self._provider_superseded:
            return
        if not self._enabled or self._store is None or not ctx.group_id:
            return
        include_global = str(ctx.group_id) in self._global_enabled_groups
        if self._profile_enabled:
            profile_block = await self._store.build_profile_prompt_block(
                group_id=str(ctx.group_id),
                include_global=include_global,
                max_chars=self._profile_max_chars,
            )
            if profile_block:
                ctx.add_block(text=profile_block, label="动态风格档案", position="dynamic", priority=42, source="style")
        block = await self._store.build_prompt_block(
            group_id=str(ctx.group_id),
            conversation_text=ctx.conversation_text,
            include_global=include_global,
            max_items=self._max_items,
            max_chars=self._max_chars,
            min_confidence=self._min_confidence,
        )
        if block:
            ctx.add_block(text=block, label="表达习惯参考", position="dynamic", priority=45, source="style")

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        if not self._enabled or not self._collect_bot_replies or self._store is None or not ctx.group_id:
            return
        if not ctx.reply_content.strip():
            return
        await self._store.record_feedback(
            target_type="reply",
            target_id="",
            group_id=str(ctx.group_id),
            rating="neutral",
            source="weak_signal",
            actor="style_plugin",
            raw_text=ctx.reply_content,
            context=ctx.user_msg,
            meta={
                "elapsed_ms": ctx.elapsed_ms,
                "thinker_action": ctx.thinker_action,
                "tool_call_count": len(ctx.tool_calls),
            },
        )

    async def on_tick(self, ctx: PluginContext) -> None:
        if not self._enabled or self._store is None:
            return
        if self._tick_task is not None and not self._tick_task.done():
            _L.debug("style tick skipped | background job still running")
            return
        self._tick_task = asyncio.create_task(
            self._run_tick_job(ctx),
            name="style:periodic-extract",
        )
        self._tick_task.add_done_callback(self._on_tick_job_done)

    async def _run_tick_job(self, ctx: PluginContext) -> None:
        style_store = self._store
        if not self._enabled or style_store is None:
            return
        from services import learning_settings

        settings = learning_settings.load(getattr(ctx, "storage_dir", "storage"))
        style_cfg = settings.get("style", {})
        if not style_cfg.get("extract_enabled", True):
            return
        interval_s = int(style_cfg.get("extract_interval_minutes", 120)) * 60
        now = time.monotonic()
        if now - self._last_extract_monotonic < interval_s:
            return
        message_log = getattr(ctx, "msg_log", None)
        llm_client = getattr(ctx, "llm_client", None)
        if message_log is None or llm_client is None:
            return

        async def extract() -> dict[str, Any]:
            return await run_style_manual_extract(
                style_store=style_store,
                message_log=message_log,
                llm_client=llm_client,
                slang_store=getattr(ctx, "slang_store", None),
                auto_approve=False,
                limit=40,
                max_batches=1,
            )

        try:
            result = await run_coordinated_extract(
                ctx,
                noun="style",
                params=ExtractRunParams(
                    limit=40,
                    max_batches=1,
                    timeout_seconds=120.0,
                ),
                runner=extract,
            )
            if result.get("ok") is False:
                _L.debug(
                    "style periodic extract skipped | error={}",
                    result.get("error"),
                )
                return
            self._last_extract_monotonic = now
            _L.info("style periodic extract completed")
        except Exception as exc:
            _L.warning("style periodic extract failed | err={}", exc)

    def _on_tick_job_done(self, task: asyncio.Task[None]) -> None:
        if self._tick_task is task:
            self._tick_task = None
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            task.result()


def config_schema() -> dict[str, Any]:
    return StyleConfig.model_json_schema()
