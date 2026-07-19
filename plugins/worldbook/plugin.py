"""Worldbook plugin — gated Living Story Runtime adapter (Stage-0, default off)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext
from services.worldbook.config import WorldbookConfig
from services.worldbook.provider import WorldbookPromptProvider
from services.worldbook.runtime import WorldbookRuntime, build_worldbook_runtime

_L = logger.bind(channel="worldbook")


class WorldbookPluginConfig(WorldbookConfig):
    """Plugin-facing config (same gates as service WorldbookConfig)."""

    # Keep pydantic model identity for load_plugin_config.
    schema_version: int = 1


class WorldbookPlugin(AmadeusPlugin):
    """Lifecycle wrapper: mounts runtime/provider only when enabled."""

    name = "worldbook"
    description = "Living Story Runtime：Canon / Life / Storylet / Drama / Projection（默认关闭）"
    version = "0.1.0"
    priority = 45
    silent_safe = True

    def __init__(self, config: WorldbookPluginConfig | None = None) -> None:
        super().__init__()
        self._config_override = config
        self._runtime: WorldbookRuntime | None = None
        self._owned_story_arc_store: Any | None = None

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = self._config_override or load_plugin_config(
            "plugins/worldbook/config.default.json",
            WorldbookPluginConfig,
        )
        # Strip schema_version for service config
        service_cfg = WorldbookConfig.model_validate(
            cfg.model_dump(exclude={"schema_version"})
        )
        ctx.worldbook_config = service_cfg
        if not service_cfg.enabled:
            ctx.worldbook_runtime = None
            _L.info("worldbook disabled; no I/O, no provider, no state mutation")
            return

        story_arc_store = getattr(ctx, "story_arc_store", None)
        needs_store = service_cfg.needs_story_arc_store()
        if needs_store and story_arc_store is None:
            from plugins.schedule import create_default_story_arc_store

            story_arc_store = create_default_story_arc_store(
                service_cfg.story_arc_dir
            )
            await story_arc_store.startup()
            ctx.story_arc_store = story_arc_store
            self._owned_story_arc_store = story_arc_store

        # Production seed pack: only when storylet gate is on. Social/input
        # paths never seed. Validate full pack then seed missing IDs only.
        if service_cfg.storylet_enabled and story_arc_store is not None:
            from plugins.worldbook.arc_seed import seed_missing_arcs

            result = seed_missing_arcs(story_arc_store, service_cfg.arc_seed_dir)
            _L.info(
                "worldbook arc seed | seeded={} existing={} seeded_ids={} existing_ids={}",
                len(result.seeded_ids),
                len(result.existing_ids),
                result.seeded_ids,
                result.existing_ids,
            )

        persona = getattr(ctx, "persona_runtime", None)
        identity = ""
        persona_id = ""
        if persona is not None:
            identity = str(getattr(persona, "static_text", "") or "")
            persona_id = str(getattr(persona, "persona_id", "") or "")
            snap = getattr(persona, "identity_snapshot", None)
            if callable(snap):
                try:
                    data = snap()
                    if isinstance(data, dict):
                        persona_id = str(data.get("persona_id") or persona_id)
                except Exception:
                    pass

        runtime = build_worldbook_runtime(
            service_cfg,
            story_arc_store=story_arc_store,
            social_narrative_store=getattr(ctx, "social_narrative_store", None),
            persona_identity_text=identity,
            persona_id=persona_id,
        )
        self._runtime = runtime
        ctx.worldbook_runtime = runtime
        ctx.worldbook_dream_bridge = (
            runtime.dream_bridge
            if runtime is not None and service_cfg.dream_proposal_enabled
            else None
        )

        schedule_gen = getattr(ctx, "schedule_gen", None)
        set_schedule_runtime = getattr(schedule_gen, "set_worldbook_runtime", None)
        set_schedule_store = getattr(
            schedule_gen,
            "set_worldbook_story_arc_store",
            None,
        )
        if (
            runtime is not None
            and service_cfg.schedule_projection_enabled
        ):
            if callable(set_schedule_store):
                set_schedule_store(story_arc_store)
            if callable(set_schedule_runtime):
                set_schedule_runtime(runtime)

        bus = getattr(ctx, "provider_bus", None)
        if (
            runtime is not None
            and service_cfg.chat_projection_enabled
            and bus is not None
            and hasattr(bus, "register")
            and not getattr(bus, "has_provider", lambda _n: False)("worldbook")
        ):
            bus.register(WorldbookPromptProvider(runtime, service_cfg))
            _L.info("worldbook prompt provider registered")

        _L.info(
            "worldbook enabled | chat={} schedule={} storylet={} dream={} social={}",
            service_cfg.chat_projection_enabled,
            service_cfg.schedule_projection_enabled,
            service_cfg.storylet_enabled,
            service_cfg.dream_proposal_enabled,
            service_cfg.social_evidence_enabled,
        )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        schedule_gen = getattr(ctx, "schedule_gen", None)
        set_schedule_runtime = getattr(schedule_gen, "set_worldbook_runtime", None)
        if callable(set_schedule_runtime):
            set_schedule_runtime(None)
        if self._owned_story_arc_store is not None:
            set_schedule_store = getattr(
                schedule_gen,
                "set_worldbook_story_arc_store",
                None,
            )
            if callable(set_schedule_store):
                set_schedule_store(None)
            if getattr(ctx, "story_arc_store", None) is self._owned_story_arc_store:
                ctx.story_arc_store = None
        if getattr(ctx, "worldbook_runtime", None) is self._runtime:
            ctx.worldbook_runtime = None
        ctx.worldbook_dream_bridge = None
        self._runtime = None
        self._owned_story_arc_store = None


def worldbook_config_from_ctx(ctx: Any) -> WorldbookConfig:
    cfg = getattr(ctx, "worldbook_config", None)
    if isinstance(cfg, WorldbookConfig):
        return cfg
    return WorldbookConfig()
