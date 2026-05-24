"""JSON API layer — aggregates all /api/admin/* routers.

Each sub-module exports a create_*_router() factory that accepts the
specific service dependencies it needs, keeping the API layer decoupled
from the global PluginContext.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter


def create_api_router(
    *,
    ctx: Any = None,
    usage_tracker: Any = None,
    message_log: Any = None,
    config: Any = None,
    group_config: Any = None,
    bot_start_time: float = 0.0,
    soul_dir: str = "config/soul",
    log_dir: str = "storage/logs",
    config_path: str = "config/config.json",
    card_store: Any = None,
    group_memory_config: Any = None,
    retrieval_gate: Any = None,
    state_board: Any = None,
    tool_registry: Any = None,
    sticker_store: Any = None,
    mood_engine: Any = None,
    schedule_store: Any = None,
    affection_store: Any = None,
    affection_engine: Any = None,
    scheduler: Any = None,
    identity_mgr: Any = None,
    bus: Any = None,
    plugin_state_store: Any = None,
    plugin_config_store: Any = None,
    dream_agent: Any = None,
    knowledge_base: Any = None,
    memo_store: Any = None,
    short_term_memory: Any = None,
    humanizer: Any = None,
    talk_schedule: Any = None,
    llm_client: Any = None,
    bot: Any = None,
) -> APIRouter:
    """Create the aggregated /api/admin router."""
    router = APIRouter(prefix="/api/admin")

    from admin.routes.api.affection import create_affection_router
    from admin.routes.api.auth import create_auth_router
    from admin.routes.api.backup import create_backup_router
    from admin.routes.api.block_trace import create_block_trace_router
    from admin.routes.api.config import create_config_router
    from admin.routes.api.context import create_context_router
    from admin.routes.api.cross_group import create_cross_group_router
    from admin.routes.api.dashboard import create_dashboard_router
    from admin.routes.api.dream import create_dream_router
    from admin.routes.api.episodes import create_episodes_router
    from admin.routes.api.events import create_events_router
    from admin.routes.api.groups import create_groups_router
    from admin.routes.api.knowledge import create_knowledge_router
    from admin.routes.api.learning import create_learning_router
    from admin.routes.api.learning_normalizer import create_learning_normalizer_router
    from admin.routes.api.learning_pipeline import create_learning_pipeline_router
    from admin.routes.api.logs import create_logs_router
    from admin.routes.api.memory import create_memory_router
    from admin.routes.api.memory_consolidator import (
        create_memory_consolidator_router,
    )
    from admin.routes.api.memos import create_memos_router
    from admin.routes.api.persona_importer import create_persona_importer_router
    from admin.routes.api.plugins import create_plugins_router
    from admin.routes.api.protocol import create_protocol_router
    from admin.routes.api.providers import create_providers_router
    from admin.routes.api.sandbox import create_sandbox_router
    from admin.routes.api.schedule import create_schedule_router
    from admin.routes.api.scheduler import create_scheduler_router
    from admin.routes.api.slang import create_slang_router
    from admin.routes.api.soul import create_soul_router
    from admin.routes.api.stickers import create_stickers_router
    from admin.routes.api.style import create_style_router
    from admin.routes.api.system import create_system_router
    from admin.routes.api.usage import create_usage_router

    router.include_router(create_auth_router())
    router.include_router(create_dashboard_router(
        usage_tracker=usage_tracker, bot_start_time=bot_start_time,
        mood_engine=mood_engine, schedule_store=schedule_store, ctx=ctx,
    ))
    router.include_router(create_usage_router(usage_tracker=usage_tracker))
    router.include_router(create_groups_router(
        config=config,
        group_config=group_config,
        message_log=message_log,
        state_board=state_board,
        scheduler=scheduler,
        tool_registry=tool_registry,
        bus=bus,
        bot=bot,
        ctx=ctx,
        config_path=config_path,
    ))
    router.include_router(create_config_router(config_path=config_path))
    router.include_router(create_context_router(ctx=ctx, bus=bus))
    router.include_router(create_soul_router(soul_dir=soul_dir, identity_mgr=identity_mgr))
    router.include_router(create_logs_router(log_dir=log_dir))
    router.include_router(create_memory_router(
        card_store=card_store, group_memory_config=group_memory_config,
        retrieval_gate=retrieval_gate, ctx=ctx,
    ))
    router.include_router(create_affection_router(
        affection_store=affection_store, affection_engine=affection_engine,
        ctx=ctx,
    ))
    router.include_router(create_stickers_router(sticker_store=sticker_store, ctx=ctx))
    router.include_router(create_schedule_router(
        mood_engine=mood_engine, schedule_store=schedule_store,
        talk_schedule=talk_schedule, dream_agent=dream_agent,
        ctx=ctx,
    ))
    router.include_router(create_plugins_router(
        bus=bus,
        tool_registry=tool_registry,
        plugin_state_store=plugin_state_store,
        plugin_config_store=plugin_config_store,
    ))
    router.include_router(create_providers_router(
        config=config,
        config_path=config_path,
        llm_client=llm_client,
    ))
    router.include_router(create_protocol_router(config=config, ctx=ctx, bot=bot))
    router.include_router(create_scheduler_router(scheduler=scheduler, ctx=ctx))
    router.include_router(create_system_router(
        config=config,
        short_term_memory=short_term_memory, humanizer=humanizer,
        ctx=ctx,
        bot=bot,
    ))
    router.include_router(create_knowledge_router(
        knowledge_base=knowledge_base,
        ctx=ctx,
        bus=bus,
    ))
    router.include_router(create_memos_router(card_store=card_store, ctx=ctx))
    router.include_router(create_persona_importer_router(
        ctx=ctx,
        soul_dir=soul_dir,
        identity_mgr=identity_mgr,
        config=config,
        bot=bot,
    ))
    router.include_router(create_dream_router(dream_agent=dream_agent))
    router.include_router(create_sandbox_router(llm_client=llm_client, identity_mgr=identity_mgr, ctx=ctx))
    router.include_router(create_slang_router(ctx=ctx, bus=bus, message_log=message_log, llm_client=llm_client))
    router.include_router(create_style_router(ctx=ctx, message_log=message_log, llm_client=llm_client))
    router.include_router(create_learning_normalizer_router(ctx=ctx))
    router.include_router(create_learning_router(ctx=ctx))
    router.include_router(create_learning_pipeline_router(ctx=ctx))
    router.include_router(create_cross_group_router(ctx=ctx, bus=bus))
    router.include_router(create_episodes_router(ctx=ctx, bus=bus))
    router.include_router(create_memory_consolidator_router(ctx=ctx))
    router.include_router(create_events_router(
        scheduler=scheduler,
        message_log=message_log,
        ctx=ctx,
        usage_tracker=usage_tracker,
    ))
    router.include_router(create_block_trace_router(ctx=ctx, bus=bus))
    router.include_router(create_backup_router(
        backup_scheduler=getattr(ctx, "backup_scheduler", None),
        config_path=config_path,
    ))

    return router
