"""Chat-domain runtime assembly boundary."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol, cast

import nonebot
from loguru import logger

from kernel.bot_pair_guard import BotPairLoopGuard
from kernel.config import BotConfig, GroupMemoryConfig, load_plugin_config
from kernel.types import PluginContext
from services.admin_access import effective_admin_ids
from services.coalesce import MessageCoalescer
from services.name_registry import NameVariationRegistry

Finalizer = Callable[[], Awaitable[None] | None]
Builder = Callable[["ChatRuntimeAssembly"], Awaitable[None] | None]
_MISSING = object()
_L = logger.bind(channel="system")


def resolve_effective_plugin_enabled(
    ctx: Any,
    name: str,
    *,
    config_enabled: bool,
) -> bool:
    """Resolve plugin state from the runtime owner before config fallback."""
    bus = getattr(ctx, "bus", None)
    get_plugin = getattr(bus, "get_plugin", None)
    if callable(get_plugin):
        plugin = get_plugin(name)
        if plugin is not None:
            return bool(getattr(plugin, "enabled", False))
    return bool(config_enabled)


def load_initial_persona_or_raise(persona_runtime: Any, persona_id: str) -> None:
    """Load the startup persona and abort assembly when no usable bundle exists."""
    normalized_id = str(persona_id or "default")
    try:
        loaded = bool(persona_runtime.load(normalized_id))
    except Exception as exc:
        raise RuntimeError(
            f"initial persona load failed: persona_id={normalized_id}"
        ) from exc
    if loaded:
        return
    reason = str(getattr(persona_runtime, "last_error", "") or "load_failed")
    raise RuntimeError(
        f"initial persona load failed: persona_id={normalized_id} reason={reason}"
    )


class DialogueClimateSensorHubBridge:
    """Keep M3 shadow collection separate from M4 behavior ownership."""

    def __init__(self, hub: Any, *, owns_behavior: bool) -> None:
        self._hub = hub
        self._owns_behavior = bool(owns_behavior)
        self._engine = getattr(hub, "_engine", None)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._hub, "enabled", False))

    def collect(self, data: Any, *, now_ts: float | None = None) -> int:
        if now_ts is None:
            registered = int(self._hub.collect(data) or 0)
        else:
            registered = int(self._hub.collect(data, now_ts=now_ts) or 0)
        return registered if self._owns_behavior else 0


def load_and_wire_group_memory_config(
    ctx: Any,
    *,
    config_path: str = "config/group-memory.json",
    loader: Callable[[str], Any] = GroupMemoryConfig.load,
) -> Any:
    """Load group-memory policy after AffectionEngine exists and wire it once."""
    config = loader(config_path)
    ctx.group_memory_config = config
    affection_engine = getattr(ctx, "affection_engine", None)
    if affection_engine is not None:
        affection_engine.set_group_memory_config(config)
        affection_engine.set_runtime_state_bus(ctx.runtime_state)
    return config


class HumanizationResolver(Protocol):
    def __call__(
        self,
        config: BotConfig,
        group_id: str | int | None,
        *,
        performance_degraded: bool | None = None,
    ) -> Any: ...


class HumanizationRuntimeWire(Protocol):
    def __call__(self, ctx: PluginContext, config: BotConfig, llm: Any) -> None: ...


class ArbiterBuilder(Protocol):
    def __call__(
        self,
        config: BotConfig,
        llm: Any,
        usage_tracker: Any,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ChatRuntimeCallbacks:
    """Plugin-owned behavior callbacks required by the chat composition root."""

    build_schedule_persona_brief: Callable[[Any, Any], Any]
    build_fiction_partner_profiles: Callable[[Any], tuple[Any, ...]]
    humanization_runtime_groups: Callable[[BotConfig], frozenset[str]]
    humanization_resolver: HumanizationResolver
    register_humanization_interaction_tools: Callable[[BotConfig, Any], None]
    wrap_scoped_humanization_provider: Callable[[Any, frozenset[str]], Any]
    wire_humanization_runtime: HumanizationRuntimeWire
    build_arbiter_client: ArbiterBuilder


class ChatRuntimeAssembly:
    """Own chat resources and transactional context publication."""

    def __init__(self, ctx: Any, builder: Builder) -> None:
        self._ctx = ctx
        self._builder = builder
        self._finalizers: list[tuple[str, Finalizer]] = []
        self._rollback_actions: list[tuple[str, Finalizer]] = []
        self._commit_actions: list[tuple[str, Finalizer]] = []
        self._previous_fields: dict[str, Any] = {}
        self._context_snapshot: dict[str, Any] | None = None
        self._started = False
        self._closed = False
        self._close_task: asyncio.Task[list[BaseException]] | None = None
        self._close_result_consumed = False
        self._explicitly_owned_resource_ids: set[int] = set()
        self._committed = False

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("chat runtime assembly cannot restart after close")
        try:
            self._context_snapshot = dict(vars(self._ctx))
        except TypeError:
            self._context_snapshot = None
        try:
            result = self._builder(self)
            if inspect.isawaitable(result):
                await result
        except BaseException:
            await self._run_finalizers(suppress_errors=True)
            await self._run_rollback_actions()
            self._rollback_published_fields()
            self._closed = True
            raise
        self._started = True
        self._context_snapshot = None
        self._previous_fields.clear()

    def publish(self, field: str, value: Any) -> None:
        if field not in self._previous_fields:
            self._previous_fields[field] = getattr(self._ctx, field, _MISSING)
        setattr(self._ctx, field, value)

    def own(self, name: str, finalizer: Finalizer) -> None:
        self._finalizers.append((name, finalizer))
        owner = getattr(finalizer, "__self__", None)
        if owner is not None:
            self._explicitly_owned_resource_ids.add(id(owner))

    def rollback(self, name: str, action: Finalizer) -> None:
        """Register a failure-only compensating action for global side effects."""
        self._rollback_actions.append((name, action))

    def commit_action(self, name: str, action: Finalizer) -> None:
        """Register an action deferred until the whole application commits."""
        self._commit_actions.append((name, action))

    async def commit(self) -> None:
        if self._committed:
            return
        while self._commit_actions:
            _name, action = self._commit_actions[0]
            result = action()
            if inspect.isawaitable(result):
                await result
            self._commit_actions.pop(0)
        self._rollback_actions.clear()
        self._committed = True

    async def close(self) -> None:
        if self._close_task is None and self._closed:
            return
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned_resources())
        errors = await asyncio.shield(self._close_task)
        if self._close_result_consumed:
            return
        self._close_result_consumed = True
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("chat runtime shutdown failed", errors)

    def explicitly_owns(self, resource: Any) -> bool:
        return id(resource) in self._explicitly_owned_resource_ids

    async def _close_owned_resources(self) -> list[BaseException]:
        errors = await self._run_finalizers(suppress_errors=False)
        if not self._committed:
            await self._run_rollback_actions()
        return errors

    async def _run_finalizers(self, *, suppress_errors: bool) -> list[BaseException]:
        errors: list[BaseException] = []
        while self._finalizers:
            _name, finalizer = self._finalizers.pop()
            try:
                result = finalizer()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:
                errors.append(exc)
        return [] if suppress_errors else errors

    async def _run_rollback_actions(self) -> None:
        while self._rollback_actions:
            _name, action = self._rollback_actions.pop()
            try:
                result = action()
                if inspect.isawaitable(result):
                    await result
            except BaseException:
                pass

    def _rollback_published_fields(self) -> None:
        if self._context_snapshot is not None:
            try:
                current = vars(self._ctx)
                for field in tuple(current):
                    if field not in self._context_snapshot:
                        del current[field]
                current.update(self._context_snapshot)
                self._context_snapshot = None
                self._previous_fields.clear()
                return
            except (AttributeError, TypeError):
                pass
        for field, previous in reversed(tuple(self._previous_fields.items())):
            try:
                if previous is _MISSING:
                    delattr(self._ctx, field)
                else:
                    setattr(self._ctx, field, previous)
            except BaseException:
                pass
        self._previous_fields.clear()


def create_chat_runtime_assembly(ctx: Any, builder: Builder) -> ChatRuntimeAssembly:
    """Create the production chat assembly with dependency-safe finalizers."""
    resource_fields = (
        "message_coalescer",
        "scheduler",
        "scheduler_hawkes_refresher",
        "humanization_health_guard",
        "research_event_capture",
        "llm_client",
        "climate_baseline_store",
        "knowledge_graph",
        "msg_log",
        "usage_tracker",
        "block_trace_store",
        "social_narrative_store",
        "visual_identity_store",
        "card_store",
        "entity_alias_store",
        "memory_consolidator_store",
        "memory_consolidator_normalizer",
        "catchphrase_normalizer",
        "episode_store",
        "recognition_cache",
        "character_registry_db",
        "sticker_store",
    )
    original_resources = {field: getattr(ctx, field, _MISSING) for field in resource_fields}
    original_nested = {
        ("climate_engine", "_recorder"): getattr(
            getattr(ctx, "climate_engine", None), "_recorder", _MISSING
        ),
    }
    owned_resources: dict[str, Any] = {}
    owned_nested: dict[tuple[str, str], Any] = {}
    build_completed = False

    async def build_and_capture(assembly: ChatRuntimeAssembly) -> None:
        nonlocal build_completed
        result = builder(assembly)
        if inspect.isawaitable(result):
            await result
        for field in resource_fields:
            resource = getattr(ctx, field, _MISSING)
            if resource is not original_resources[field]:
                owned_resources[field] = resource
        for key in original_nested:
            resource = getattr(getattr(ctx, key[0], None), key[1], _MISSING)
            if resource is not original_nested[key]:
                owned_nested[key] = resource
        build_completed = True

    assembly = ChatRuntimeAssembly(ctx, build_and_capture)

    async def close_attr(field: str, method: str = "close") -> None:
        if build_completed:
            resource = owned_resources.get(field, _MISSING)
        else:
            resource = getattr(ctx, field, _MISSING)
            if resource is original_resources[field]:
                return
        if resource is _MISSING or assembly.explicitly_owns(resource):
            return
        callback = getattr(resource, method, None)
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            await result

    async def close_nested(field: str, nested: str) -> None:
        key = (field, nested)
        if build_completed:
            resource = owned_nested.get(key, _MISSING)
        else:
            owner = getattr(ctx, field, None)
            resource = getattr(owner, nested, _MISSING)
            if resource is original_nested[key]:
                return
        if resource is _MISSING or assembly.explicitly_owns(resource):
            return
        callback = getattr(resource, "close", None)
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            await result

    async def close_research_capture() -> None:
        if build_completed:
            capture = owned_resources.get("research_event_capture", _MISSING)
        else:
            capture = getattr(ctx, "research_event_capture", _MISSING)
            if capture is original_resources["research_event_capture"]:
                return
        if capture is _MISSING or assembly.explicitly_owns(capture):
            return
        capture = cast(Any, capture)
        await capture.close()
        metrics = capture.metrics_snapshot()
        _L.info(
            "research event capture closed | received={} enqueued={} persisted={} "
            "duplicate={} dropped={} write_error={} pending={}",
            metrics.received,
            metrics.enqueued,
            metrics.persisted,
            metrics.duplicate,
            metrics.dropped_queue_full,
            metrics.write_error,
            metrics.pending,
        )

    # Register in reverse because ChatRuntimeAssembly owns a LIFO finalizer stack.
    # The resulting close order keeps producers alive only while their consumers
    # are available: coalescer -> scheduler -> auxiliary producers -> LLM -> stores.
    finalizers: tuple[tuple[str, Finalizer], ...] = (
        ("message_coalescer", lambda: close_attr("message_coalescer")),
        ("scheduler", lambda: close_attr("scheduler")),
        ("scheduler_hawkes_refresher", lambda: close_attr("scheduler_hawkes_refresher", "stop")),
        ("humanization_health_guard", lambda: close_attr("humanization_health_guard", "stop")),
        ("research_event_capture", close_research_capture),
        ("llm_client", lambda: close_attr("llm_client")),
        ("dialogue_climate_m2_recorder", lambda: close_nested("climate_engine", "_recorder")),
        ("dialogue_climate_baseline_store", lambda: close_attr("climate_baseline_store")),
        ("knowledge_graph", lambda: close_attr("knowledge_graph")),
        ("message_log", lambda: close_attr("msg_log")),
        ("usage_tracker", lambda: close_attr("usage_tracker")),
        ("block_trace_store", lambda: close_attr("block_trace_store")),
        ("social_narrative_store", lambda: close_attr("social_narrative_store")),
        ("visual_identity_store", lambda: close_attr("visual_identity_store")),
        ("card_store", lambda: close_attr("card_store")),
        ("entity_alias_store", lambda: close_attr("entity_alias_store")),
        ("memory_consolidator_store", lambda: close_attr("memory_consolidator_store")),
        ("memory_consolidator_normalizer", lambda: close_attr("memory_consolidator_normalizer")),
        ("catchphrase_normalizer", lambda: close_attr("catchphrase_normalizer")),
        ("episode_store", lambda: close_attr("episode_store")),
        ("recognition_cache", lambda: close_attr("recognition_cache")),
        ("character_registry_db", lambda: close_attr("character_registry_db")),
        ("sticker_store", lambda: close_attr("sticker_store")),
    )
    for name, finalizer in reversed(finalizers):
        assembly.own(name, finalizer)
    return assembly


async def build_chat_runtime(
    ctx: PluginContext,
    assembly: ChatRuntimeAssembly,
    callbacks: ChatRuntimeCallbacks,
) -> None:
    """Build and publish all cross-domain chat runtime services."""
    config: BotConfig = ctx.config

    # ---- config-derived globals ----
    ctx.bot_start_time = time.time()

    # Prefer NoneBot's nickname config (NICKNAME env var), fall back to BOT_NICKNAMES.
    nb_nicknames = nonebot.get_driver().config.nickname
    if nb_nicknames:
        ctx.bot_nicknames = list(nb_nicknames)
    else:
        import json as _json
        raw = os.environ.get("BOT_NICKNAMES", "[]")
        try:
            ctx.bot_nicknames = _json.loads(raw)
        except _json.JSONDecodeError:
            ctx.bot_nicknames = []

    ctx.allowed_groups = set(config.group.allowed_groups)
    ctx.allowed_private_users = set(config.allowed_private_users)
    ctx.admins = dict(config.admins)

    # ---- opt-in raw research event capture ----
    ctx.research_event_capture = None
    research_policy = config.research_event_capture
    research_secret = os.environ.get(
        research_policy.pseudonymization_salt_env, ""
    ).strip()
    if research_policy.enabled and research_policy.group_allowlist and research_secret:
        from services.group.research_event_store import (
            ResearchEventCapture,
            ResearchEventRecorder,
            ResearchEventStore,
        )

        research_store = None
        research_recorder = None
        research_cleanup_done = False

        async def close_unpublished_research_resources() -> None:
            nonlocal research_cleanup_done
            if research_cleanup_done or ctx.research_event_capture is not None:
                return
            if research_recorder is not None:
                try:
                    await research_recorder.close()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _L.warning("research recorder cleanup failed | err={}", exc)
            if research_store is not None:
                try:
                    await research_store.close()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _L.warning("research store cleanup failed | err={}", exc)
            research_cleanup_done = True

        assembly.rollback(
            "unpublished_research_resources",
            close_unpublished_research_resources,
        )
        try:
            research_store = ResearchEventStore(research_policy.db_path)
            await research_store.init()
            research_recorder = ResearchEventRecorder(
                research_store,
                max_queue_size=research_policy.max_queue_size,
                batch_size=research_policy.batch_size,
                flush_interval_seconds=research_policy.flush_interval_seconds,
            )
            await research_recorder.start()
            ctx.research_event_capture = ResearchEventCapture(
                research_recorder,
                store=research_store,
                pseudonymization_secret=research_secret,
                group_allowlist=research_policy.group_allowlist,
            )
            _L.info(
                "research event capture armed | db={} groups={} queue={} batch={}",
                research_policy.db_path,
                len(research_policy.group_allowlist),
                research_policy.max_queue_size,
                research_policy.batch_size,
            )
        except Exception as exc:
            await close_unpublished_research_resources()
            _L.warning("research event capture disabled | init failed: {}", exc)
    elif research_policy.enabled:
        reason = (
            "empty group allowlist"
            if not research_policy.group_allowlist
            else f"missing secret env {research_policy.pseudonymization_salt_env}"
        )
        _L.warning("research event capture disabled | {}", reason)

    # ---- anti-detect / humanizer ----
    from services.humanizer import Humanizer, get_humanizer, set_humanizer

    humanizer = Humanizer(
        enabled=config.anti_detect.enabled,
        min_delay=config.anti_detect.min_delay,
        max_delay=config.anti_detect.max_delay,
        char_delay=config.anti_detect.char_delay,
    )
    previous_humanizer = get_humanizer()
    assembly.rollback(
        "humanizer",
        lambda: cast(Any, set_humanizer)(previous_humanizer),
    )
    set_humanizer(humanizer)
    ctx.humanizer = humanizer

    # ---- vision / image cache ----
    from services.media.image_cache import ImageCache

    image_cache = ImageCache(
        cache_dir=config.vision.cache_dir,
        max_dimension=config.vision.max_dimension,
    )
    await image_cache.cleanup(max_age=timedelta(hours=config.vision.cache_max_age_hours))
    ctx.image_cache = image_cache
    ctx.vision_enabled = config.vision.enabled
    ctx.max_images_per_message = config.vision.max_images_per_message

    # ---- sticker store ----
    from plugins.sticker import StickerConfig

    sticker_cfg = load_plugin_config("plugins/sticker/config.default.json", StickerConfig)
    if sticker_cfg.enabled:
        from services.media.sticker_store import StickerStore
        ctx.sticker_store = StickerStore(
            storage_dir=sticker_cfg.storage_dir,
            max_count=sticker_cfg.max_count,
            prompt_view_max=sticker_cfg.prompt_view_max,
            prompt_view_recent_slice=sticker_cfg.prompt_view_recent_slice,
        )
    else:
        ctx.sticker_store = None

    ctx.character_recognizer = None
    ctx.character_registry_db = None
    ctx.recognition_cache = None
    if config.vision.character_recognition.enabled:
        from services.media.character_pack_migrator import auto_merge_series_packs
        from services.media.character_recognizer import CharacterRecognizer
        from services.media.character_registry_db import CharacterRegistryDB
        from services.media.recognition_cache import RecognitionCache

        cr_cfg = config.vision.character_recognition
        if cr_cfg.auto_merge_series_packs:
            merge_stats = auto_merge_series_packs(cr_cfg.packs_dir)
            if merge_stats.get("merged") or merge_stats.get("archived") or merge_stats.get("skipped"):
                _L.info("character pack series auto-merge | {}", merge_stats)
        registry_db = CharacterRegistryDB(db_path="storage/character_recognition.db")
        ctx.character_registry_db = registry_db
        await registry_db.init()
        await registry_db.scan_and_sync(cr_cfg.packs_dir)
        recognition_cache = RecognitionCache(db_path="storage/character_recognition.db")
        ctx.recognition_cache = recognition_cache
        await recognition_cache.init()
        animetrace_client = None
        if cr_cfg.animetrace_enabled:
            from services.media.animetrace_client import AnimeTraceClient
            animetrace_client = AnimeTraceClient(
                model=cr_cfg.animetrace_model,
                timeout_seconds=cr_cfg.animetrace_timeout_seconds,
            )
        ctx.character_recognizer = CharacterRecognizer(
            base_url=cr_cfg.sidecar_url,
            packs_dir=cr_cfg.packs_dir,
            timeout_seconds=cr_cfg.timeout_seconds,
            registry_db=registry_db,
            recognition_cache=recognition_cache,
            animetrace_client=animetrace_client,
            multi_char_enabled=cr_cfg.multi_char_enabled,
        )

    # ---- card store ----
    from plugins.memo import MemoConfig
    from services.memory.card_store import CardStore
    from services.memory.entity_alias_store import EntityAliasStore
    from services.memory.visual_identity import VisualIdentityStore
    from services.social_narrative import SocialNarrativeStore

    memo_cfg = load_plugin_config("plugins/memo/config.default.json", MemoConfig)
    card_store = CardStore(db_path="storage/memory_cards.db")
    ctx.card_store = card_store
    await card_store.init(migrate_from_md=memo_cfg.dir)
    visual_identity_store = VisualIdentityStore(db_path="storage/memory_cards.db")
    ctx.visual_identity_store = visual_identity_store
    await visual_identity_store.init()
    social_narrative_store = SocialNarrativeStore(db_path="storage/memory_cards.db")
    ctx.social_narrative_store = social_narrative_store
    await social_narrative_store.init()

    # ---- entity alias store (nickname / speaker surface → entity_key) ----
    entity_alias_store = EntityAliasStore("storage/entity_aliases.db")
    ctx.entity_alias_store = entity_alias_store
    await entity_alias_store.init()

    # ---- short term memory ----
    from services.memory.short_term import ShortTermMemory

    ctx.short_term = ShortTermMemory()

    # ---- persona v2 runtime singleton (single source of identity + prompt) ----
    from services.persona import PersonaRuntime

    persona_v2_cfg = getattr(config, "persona_v2", None)
    persona_runtime = PersonaRuntime(
        group_config_resolver=(
            config.group.resolve if hasattr(config, "group") else None
        ),
    )
    if persona_v2_cfg is not None:
        persona_id = getattr(persona_v2_cfg, "persona_id", "default")
        load_initial_persona_or_raise(persona_runtime, persona_id)
    ctx.persona_runtime = persona_runtime
    ctx.identity = persona_runtime.identity_snapshot()

    # ---- schedule system ----
    from plugins.schedule.plugin import ScheduleConfig

    schedule_cfg = load_plugin_config("plugins/schedule/config.default.json", ScheduleConfig)
    schedule_enabled = resolve_effective_plugin_enabled(
        ctx,
        "schedule",
        config_enabled=schedule_cfg.enabled,
    )
    ctx.schedule_event_replan_enabled = bool(
        schedule_enabled and schedule_cfg.event_replan_enabled
    )
    if schedule_enabled:
        from plugins.schedule import (
            FictionPartnerStateStore,
            MoodEngine,
            ScheduleGenerator,
            ScheduleStore,
            create_default_story_arc_store,
        )

        schedule_persona_brief = callbacks.build_schedule_persona_brief(persona_runtime, ctx.identity)
        fiction_partner_profiles = callbacks.build_fiction_partner_profiles(persona_runtime)
        ctx.schedule_store = ScheduleStore(storage_dir=schedule_cfg.storage_dir)
        await ctx.schedule_store.startup()
        story_arc_store = None
        partner_state_store = None
        if schedule_cfg.story_arc_enabled:
            story_arc_store = create_default_story_arc_store()
            await story_arc_store.startup()
            partner_state_store = FictionPartnerStateStore()
            await partner_state_store.startup()
        ctx.story_arc_store = story_arc_store
        ctx.partner_state_store = partner_state_store
        ctx.mood_engine = MoodEngine(
            anomaly_chance=schedule_cfg.mood_anomaly_chance,
            refresh_minutes=schedule_cfg.mood_refresh_minutes,
            calendar_service=getattr(ctx, "calendar_service", None),
        )
        # Dialogue Climate M2/M3: full-dimension ClimateEngine + Sensor hub.
        # Dormant unless m2_enabled; sensors fed only when m3_sensors_enabled.
        if schedule_cfg.dialogue_climate.m2_enabled:
            try:
                from services.dialogue_climate import ClimateBaselineStore, ClimateEngine
                from services.dialogue_climate.sensors import SensorHub

                ctx.climate_engine = ClimateEngine(m2_enabled=True)
                ctx.climate_baseline_store = ClimateBaselineStore()
                await ctx.climate_baseline_store.start()
                ctx.climate_engine.set_baseline_store(ctx.climate_baseline_store)
                sensor_hub = SensorHub(
                    ctx.climate_engine,
                    m3_sensors_enabled=schedule_cfg.dialogue_climate.m3_sensors_enabled,
                )
                ctx.dialogue_climate_m4_enabled = bool(
                    schedule_cfg.dialogue_climate.m4_policy_enabled
                )
                ctx.climate_sensor_hub = DialogueClimateSensorHubBridge(
                    sensor_hub,
                    owns_behavior=ctx.dialogue_climate_m4_enabled,
                )
                if schedule_cfg.dialogue_climate.m3_sensors_enabled:
                    from services.dialogue_climate import ClimateMetricsRecorder

                    ctx.climate_engine.set_recorder(ClimateMetricsRecorder())
            except Exception as exc:
                _L.warning("climate engine wiring failed | err={}", exc)
        ctx.schedule_gen = ScheduleGenerator(
            store=ctx.schedule_store,
            generate_at_hour=schedule_cfg.generate_at_hour,
            identity_name=ctx.identity.name,
            persona_driven_enabled=schedule_cfg.persona_driven_enabled,
            persona_brief=schedule_persona_brief,
            memory_card_store=ctx.card_store,
            story_arc_enabled=schedule_cfg.story_arc_enabled,
            story_arc_store=story_arc_store,
            partner_state_store=partner_state_store,
            fiction_partner_profiles=fiction_partner_profiles,
            event_replan_enabled=schedule_cfg.event_replan_enabled,
            local_billing_fallback_enabled=schedule_cfg.local_billing_fallback_enabled,
            task_supervisor=getattr(ctx, "background_task_supervisor", None),
            calendar_service=getattr(ctx, "calendar_service", None),
        )
        calendar_service = getattr(ctx, "calendar_service", None)
        set_self_names = getattr(calendar_service, "set_self_names", None)
        if callable(set_self_names):
            set_self_names(ctx.identity.name)
        _L.info("schedule system initialized | dir={}", schedule_cfg.storage_dir)
    else:
        ctx.schedule_store = None
        ctx.mood_engine = None
        ctx.schedule_gen = None
        ctx.story_arc_store = None
        ctx.partner_state_store = None
        ctx.schedule_event_replan_enabled = False
        ctx.climate_baseline_store = None
    ctx.schedule_enabled = schedule_enabled

    # ---- affection system ----
    from plugins.affection.plugin import AffectionConfig

    affection_cfg = load_plugin_config("plugins/affection/config.default.json", AffectionConfig)
    affection_enabled = resolve_effective_plugin_enabled(
        ctx,
        "affection",
        config_enabled=affection_cfg.enabled,
    )
    if affection_enabled:
        from plugins.affection import AffectionEngine, AffectionStore

        ctx.affection_store = AffectionStore(storage_dir=affection_cfg.storage_dir)
        await ctx.affection_store.startup()
        ctx.affection_engine = AffectionEngine(
            store=ctx.affection_store,
            score_increment=affection_cfg.score_increment,
            daily_cap=affection_cfg.daily_cap,
        )
        _L.info("affection system initialized | dir={}", affection_cfg.storage_dir)
    else:
        ctx.affection_store = None
        ctx.affection_engine = None
    ctx.affection_enabled = affection_enabled

    # ---- message log (ConversationArchive owns storage/messages.db) ----
    # MessageLog remains a compatibility client module; composition root uses
    # ConversationArchive so scanners/consolidator get cursor-backed reads.
    # Downstream consumers are annotated with MessageLogPort (structural);
    # no cast(Any) needed at the construction site.
    from services.conversation_archive import ConversationArchive

    message_log = ConversationArchive(db_path="storage/messages.db")
    ctx.msg_log = message_log
    await message_log.init()

    # ---- timeline ----
    from services.memory.timeline import GroupTimeline

    ctx.timeline = GroupTimeline(message_log=message_log)

    # ---- state board ----
    from services.memory.state_board import GroupStateBoard

    ctx.state_board = GroupStateBoard(message_log=message_log, bot_self_id="")

    # ---- group memory config ----
    group_memory_config = load_and_wire_group_memory_config(ctx)
    _L.info("group memory config loaded | mode={}", group_memory_config.memory.mode)

    # ---- retrieval gate ----
    from services.memory.retrieval import RetrievalGate

    ctx.retrieval = RetrievalGate(
        card_store=card_store,
        refresh_interval=10,
        group_memory_config=group_memory_config,
        semantic_enabled=config.memory.semantic.enabled,
        semantic_backend=config.memory.semantic.backend,
    )

    # Unified context search for Admin debugging and ContextPlugin prompt takeover.
    from services.context import ContextService

    ctx.context_service = ContextService.from_runtime(ctx, bus=ctx.bus)

    # ---- derived knowledge graph (safe, rebuildable fact layer) ----
    from services.knowledge_graph import KnowledgeGraphService

    kg_cfg = getattr(config, "knowledge_graph", None)
    kg_gate = bool(getattr(kg_cfg, "provenance_gate_enabled", True))
    kg_obs = bool(getattr(kg_cfg, "observability_enabled", True))
    ctx.knowledge_graph = KnowledgeGraphService(
        "storage/knowledge_graph.db",
        provenance_gate_enabled=kg_gate,
        observability_enabled=kg_obs,
    )
    await ctx.knowledge_graph.init()

    # Phase E.4 graph edge double-write — mirror doc-backed facts to
    # `doc_supports_fact` edges. Best-effort: a graph write failure
    # must never block the fact governance path (audit § E.4).
    try:
        from services.knowledge_graph.fact_graph_bridge import FactGraphBridge
        from services.knowledge_graph.graph_writer import GraphWriter

        kg_store = getattr(ctx.knowledge_graph, "_store", None)
        if kg_store is not None and getattr(kg_store, "_db", None) is not None:
            ctx.fact_graph_bridge = FactGraphBridge(GraphWriter(kg_store))
            ctx.fact_graph_bridge.attach(ctx.knowledge_graph)
    except Exception as exc:
        logger.warning("fact graph bridge attach failed | err={}", exc)

    # ---- memory consolidator (Phase C dry-run; lazy LLM/normalizer wiring) ----
    from services.episodic.store import EpisodeStore
    from services.learning_normalizer.store import LearningNormalizerStore
    from services.memory_consolidator import (
        ConsolidatorCandidatesStore,
        EpisodePromoter,
        MemoryConsolidator,
        ReflectionGenerator,
    )

    ctx.memory_consolidator_store = ConsolidatorCandidatesStore(
        "storage/consolidator_candidates.db"
    )
    await ctx.memory_consolidator_store.init()
    ctx.memory_consolidator_normalizer = LearningNormalizerStore(
        "storage/consolidator_normalizer.db"
    )
    await ctx.memory_consolidator_normalizer.init()
    ctx.catchphrase_normalizer = None
    if config.humanization.context_providers:
        ctx.catchphrase_normalizer = LearningNormalizerStore("storage/learning_normalizer.db")
        await ctx.catchphrase_normalizer.init()

    # Phase D singleton — episode store + promote bridge.
    ctx.episode_store = EpisodeStore("storage/episodic.db")
    await ctx.episode_store.init()
    ctx.episode_promoter = EpisodePromoter(
        candidates_store=ctx.memory_consolidator_store,
        episode_store=ctx.episode_store,
        message_archive=ctx.msg_log,
        card_store=ctx.card_store,
        knowledge_graph=ctx.knowledge_graph,
        entity_alias_store=ctx.entity_alias_store,
    )
    # D.5 graph edge double-write: approved/disabled episodes mirror
    # into knowledge_graph.db as episode_supports_profile edges.
    # Best-effort — graph mirroring failure must never block the
    # episode state machine (audit § D.5).
    try:
        from services.episodic import EpisodeGraphBridge
        from services.knowledge_graph.graph_writer import GraphWriter

        kg_store = getattr(ctx.knowledge_graph, "_store", None)
        if kg_store is not None and getattr(kg_store, "_db", None) is not None:
            ctx.episode_graph_bridge = EpisodeGraphBridge(GraphWriter(kg_store))
            ctx.episode_graph_bridge.attach(ctx.episode_store)
    except Exception as exc:
        logger.warning("episode graph bridge attach failed | err={}", exc)
    ctx.memory_consolidator = None  # set after llm_client is built below

    # ---- prompt builder (v2 — owns persona via PersonaRuntime) ----
    from services.llm.prompt_builder import PromptBuilder

    initial_humanization = callbacks.humanization_resolver(config, None)
    prompt_builder = PromptBuilder(
        persona_runtime=persona_runtime,
        state_board=ctx.state_board,
        retrieval_gate=ctx.retrieval,
        state_board_layout=initial_humanization.state_board_layout,
        state_board_granularity=initial_humanization.state_board_granularity,
    )
    ctx.prompt_builder = prompt_builder

    # ---- dream agent (created by DreamPlugin) ----
    from plugins.dream import DreamConfig

    dream_cfg = load_plugin_config("plugins/dream/config.default.json", DreamConfig)
    ctx.dream = None
    ctx.dream_enabled = resolve_effective_plugin_enabled(
        ctx,
        "dream",
        config_enabled=dream_cfg.enabled,
    )

    # ---- usage tracker ----
    from services.llm.usage import UsageTracker

    usage_tracker = UsageTracker(db_path="storage/usage.db")
    ctx.usage_tracker = usage_tracker
    if config.llm.usage.enabled:
        await usage_tracker.init()

    from services.humanization.health_guard import HumanizationHealthGuard

    ctx.humanization_health_guard = HumanizationHealthGuard(
        db_path="storage/usage.db",
        task_supervisor=getattr(ctx, "background_task_supervisor", None),
    )
    ctx.humanization_health_guard.start()
    ctx.name_registry = NameVariationRegistry()

    # ---- tool registry ----
    from services.tools.registry import ToolRegistry

    tools = ToolRegistry()
    callbacks.register_humanization_interaction_tools(config, tools)
    # Tools are registered by individual plugins via bus.collect_tools()
    ctx.tool_registry = tools

    # ---- block trace + budget manager ----
    from services.block_trace.budget_manager import PromptBudgetManager
    from services.block_trace.store import BlockTraceStore

    bt_cfg = getattr(config, "block_trace", None)
    joint_jdt = bool(
        getattr(bt_cfg, "joint_dual_path_telemetry_enabled", True)
    )
    trace_store = BlockTraceStore(
        db_path="storage/block_trace.db",
        joint_dual_path_telemetry_enabled=joint_jdt,
    )
    ctx.block_trace_store = trace_store
    await trace_store.init()
    ctx.bot_pair_guard = BotPairLoopGuard(
        self_id="",
        known_other_bots=config.bot_pair_guard.known_other_bots,
        max_per_minute=config.bot_pair_guard.max_per_minute,
        cooldown_seconds=config.bot_pair_guard.cooldown_seconds,
        loop_alt_threshold=config.bot_pair_guard.loop_alt_threshold,
        known_peer_alt_threshold=config.bot_pair_guard.known_peer_alt_threshold,
    )
    ctx.message_coalescer = MessageCoalescer(
        idle_window_seconds=config.coalesce.idle_window_seconds,
        max_window_seconds=config.coalesce.max_window_seconds,
    )
    budget_mgr = PromptBudgetManager(
        trace_store,
        slang_store_getter=lambda: getattr(ctx, "slang_store", None),
        style_store_getter=lambda: getattr(ctx, "style_store", None),
        episode_store_getter=lambda: getattr(ctx, "episode_store", None),
    )

    # ---- LLM client ----
    from services.llm.client import LLMClient
    from services.llm.llm_request import all_llm_tasks

    llm_tasks = all_llm_tasks()
    task_profiles = {
        task: config.llm.resolve_task_profile(task)
        for task in llm_tasks
    }
    task_profile_names = {
        task: config.llm.profile_name_for_task(task)
        for task in llm_tasks
    }
    main_profile = task_profiles["main"]

    def runtime_mood_getter(*, group_id: str | int | None = None, session_id: str = "") -> Any:
        if not resolve_effective_plugin_enabled(
            ctx,
            "schedule",
            config_enabled=bool(ctx.schedule_enabled),
        ) or ctx.mood_engine is None:
            return None
        schedule = ctx.schedule_store.current if ctx.schedule_store else None
        recent_count = 0
        if group_id is not None and ctx.timeline is not None:
            recent_count = ctx.timeline.recent_interaction_count(str(group_id), window_s=60.0)
        return ctx.mood_engine.evaluate(
            schedule,
            recent_interaction_count=recent_count,
            group_id=group_id,
            session_id=session_id,
        )

    def runtime_clock_getter(*, group_id: str | int | None = None, session_id: str = "") -> dict[str, Any]:
        from services.runtime_clock import now_cst, slot_features

        now = now_cst()
        schedule = ctx.schedule_store.current if ctx.schedule_store else None
        calendar_service = getattr(ctx, "calendar_service", None)
        get_day_context = getattr(calendar_service, "get_day_context", None)
        day_context = get_day_context(now) if callable(get_day_context) else None
        return slot_features(now=now, schedule=schedule, day_context=day_context)

    def runtime_climate_context_getter(
        *,
        session_id: str,
        group_id: str | int | None,
        user_id: str | int | None,
        privacy_mask: bool,
    ) -> Any:
        engine = getattr(ctx, "climate_engine", None)
        if (
            not bool(getattr(ctx, "dialogue_climate_m4_enabled", False))
            or engine is None
            or not bool(getattr(engine, "enabled", False))
        ):
            return None
        with contextlib.suppress(Exception):
            engine.clear_stale()
        hub = getattr(ctx, "climate_sensor_hub", None)
        if hub is not None and bool(getattr(hub, "enabled", False)):
            try:
                from datetime import datetime

                from services.dialogue_climate.sensors import SensorInput

                profile = runtime_mood_getter(group_id=group_id, session_id=session_id)
                familiarity = None
                affection_engine = getattr(ctx, "affection_engine", None)
                if affection_engine is not None and getattr(ctx, "affection_enabled", False):
                    familiarity_getter = getattr(affection_engine, "familiarity_score", None)
                    if callable(familiarity_getter):
                        familiarity = float(cast(Any, familiarity_getter)(str(user_id or "")))
                now = datetime.now()
                calendar_service = getattr(ctx, "calendar_service", None)
                day_getter = getattr(calendar_service, "get_day_context", None)
                day_context = day_getter(now) if callable(day_getter) else None
                hub.collect(SensorInput(
                    group_id=str(group_id or ""),
                    user_id=str(user_id or ""),
                    mood_energy=getattr(profile, "energy", None) if profile else None,
                    mood_valence=getattr(profile, "valence", None) if profile else None,
                    mood_openness=getattr(profile, "openness", None) if profile else None,
                    mood_tension=getattr(profile, "tension", None) if profile else None,
                    hour=now.hour,
                    familiarity=familiarity,
                    is_holiday=bool(getattr(day_context, "is_holiday", False)),
                    has_self_birthday=bool(getattr(day_context, "is_self_birthday", False)),
                ))
            except Exception as exc:
                _L.debug("climate runtime sensor feed failed | err={}", exc)
        relationship_text = ""
        affection_engine = getattr(ctx, "affection_engine", None)
        if affection_engine is not None and getattr(ctx, "affection_enabled", False):
            try:
                pool_ids = None
                if group_id is not None and getattr(ctx, "group_memory_config", None) is not None:
                    pool_ids = ctx.group_memory_config.resolve_group_pools(str(group_id))
                relationship_text = affection_engine.build_affection_block(
                    str(user_id or ""),
                    in_group=bool(group_id is not None and privacy_mask),
                    pool_ids=pool_ids,
                )
            except Exception as exc:
                _L.debug("climate relationship context failed | user={} err={}", user_id, exc)
        from services.block_trace.climate_provider import build_climate_turn_snapshot

        return build_climate_turn_snapshot(
            state=engine.resolve(group_id=group_id, user_id=user_id),
            group_id=group_id,
            user_id=user_id,
            relationship_text=relationship_text,
        )

    # Issue 15 — instruction authority gate (additive, default-off).
    instruction_gate = None
    authority_store = None
    if getattr(config, "instruction_gate", None) and config.instruction_gate.enabled:
        from services.llm.instruction_gate import AuthorityStore, InstructionAuthorityGate

        instruction_gate = InstructionAuthorityGate(config.instruction_gate)
        authority_store = AuthorityStore(
            storage_dir="storage",
            seed=dict(getattr(config.instruction_gate, "authority_overrides", {}) or {}),
        )

    llm = LLMClient(
        base_url=main_profile.base_url,
        api_key=main_profile.api_key,
        model=main_profile.model,
        prompt_builder=prompt_builder,
        short_term=ctx.short_term,
        tools=tools,
        api_format=main_profile.api_format or config.llm.api_format,
        max_context_tokens=config.llm.context.max_context_tokens,
        compact_ratio=config.compact.ratio,
        compress_ratio=config.compact.compress_ratio,
        max_compact_failures=config.compact.max_failures,
        group_timeline=ctx.timeline,
        card_store=card_store,
        on_compact=None,
        image_cache=image_cache if ctx.vision_enabled else None,
        message_log=message_log,
        affection_engine=ctx.affection_engine,
        thinker_enabled=config.thinker.enabled,
        thinker_max_tokens=config.thinker.max_tokens,
        thinker_force_reply_enabled=config.thinker.force_reply_enabled,
        thinker_necessity_gate_enabled=config.thinker.necessity_gate_enabled,
        thinker_necessity_gate_addressed_exempt=config.thinker.necessity_gate_addressed_exempt,
        mood_getter=runtime_mood_getter if ctx.mood_engine else None,
        bus=ctx.bus,
        runtime_state=ctx.runtime_state,
        clock_context_getter=runtime_clock_getter,
        task_profiles=task_profiles,
        group_config=config.group,
        reply_segmentation_config=config.reply_segmentation,
        slang_store_getter=lambda: getattr(ctx, "slang_store", None),
        budget_manager=budget_mgr,
        thinker_provider_enabled=(
            config.humanization.context_providers
            and config.humanization.thinker_provider
        ),
        humanization_rewrite_threshold=config.humanization.rewrite_threshold,
        humanization_kaomoji_enforce_strict=config.humanization.kaomoji_enforce_strict,
        humanization_runtime_groups=config.humanization.runtime_groups,
        humanization_resolver=lambda group_id, *, performance_degraded=None: callbacks.humanization_resolver(
            config,
            group_id,
            performance_degraded=performance_degraded,
        ),
        pass_turn_confidence_gate=config.humanization.pass_turn_confidence_gate,
        pass_turn_confidence_threshold=config.humanization.pass_turn_confidence_threshold,
        sentinel_guardrail_config=config.sentinel_guardrail,
        schedule_overshare_config=config.schedule_overshare,
        persona_drift_config=config.persona_drift,
        anchor_reinjection_config=config.anchor_reinjection,
        addressee_hint_config=config.addressee_hint,
        mention_post_processor_config=config.mention_post_processor,
        slang_lookup_config=config.slang_lookup,
        sticker_placement_config=config.sticker_placement,
        text_preflight_config=config.text_preflight,
        name_registry=getattr(ctx, "name_registry", None),
        instruction_gate=instruction_gate,
        authority_store=authority_store,
        admins=dict.fromkeys(effective_admin_ids(ctx), "effective admin"),
        known_other_bots=dict(getattr(config.bot_pair_guard, "known_other_bots", {}) or {}),
    )
    assembly.publish("llm_client", llm)
    llm.set_task_profile_names(task_profile_names)
    llm.set_climate_context_getter(
        runtime_climate_context_getter
        if bool(getattr(ctx, "dialogue_climate_m4_enabled", False))
        else None
    )

    # ---- prompt provider bus (active mode — providers are sole injection path) ----
    from plugins.style.plugin import StyleConfig as _StyleConfig
    from services.block_trace.catchphrase_provider import CatchphraseProvider
    from services.block_trace.climate_provider import ClimateProvider
    from services.block_trace.episode_provider import EpisodeProvider
    from services.block_trace.homophone_provider import HomophoneProvider
    from services.block_trace.provider_bus import PromptProviderBus
    from services.block_trace.register_provider import RegisterProvider
    from services.block_trace.slang_provider import SlangProvider
    from services.block_trace.sticker_register_provider import StickerRegisterProvider
    from services.block_trace.style_provider import StyleProvider
    from services.block_trace.thinker_provider import ThinkerProvider

    style_cfg = load_plugin_config("plugins/style/config.default.json", _StyleConfig)
    style_global_groups = {
        str(gid).strip()
        for gid in style_cfg.global_enabled_group_ids
        if str(gid).strip()
    }

    provider_bus = PromptProviderBus(trace_store)
    provider_bus.mode = "active"
    humanization_groups = callbacks.humanization_runtime_groups(config)
    if bool(getattr(ctx, "dialogue_climate_m4_enabled", False)):
        provider_bus.register(ClimateProvider())
    if config.humanization.context_providers:
        provider_bus.register(callbacks.wrap_scoped_humanization_provider(
            RegisterProvider(),
            humanization_groups,
        ))
        provider_bus.register(callbacks.wrap_scoped_humanization_provider(
            CatchphraseProvider(
                store_getter=lambda: getattr(ctx, "catchphrase_normalizer", None),
            ),
            humanization_groups,
        ))
        if config.humanization.sticker_register_provider:
            provider_bus.register(callbacks.wrap_scoped_humanization_provider(
                StickerRegisterProvider(
                    store_getter=lambda: getattr(ctx, "sticker_store", None),
                ),
                humanization_groups,
            ))
        if config.humanization.thinker_provider:
            provider_bus.register(callbacks.wrap_scoped_humanization_provider(
                ThinkerProvider(),
                humanization_groups,
            ))
    provider_bus.register(SlangProvider(
        store_getter=lambda: getattr(ctx, "slang_store", None),
        group_config=config.group,
    ))
    provider_bus.register(HomophoneProvider(
        slang_store_getter=lambda: getattr(ctx, "slang_store", None),
    ))
    provider_bus.register(StyleProvider(
        store_getter=lambda: getattr(ctx, "style_store", None),
        enabled=style_cfg.enabled,
        profile_enabled=style_cfg.profile_enabled,
        profile_max_chars=style_cfg.profile_max_chars,
        max_items=style_cfg.max_items,
        max_chars=style_cfg.max_chars,
        min_confidence=style_cfg.min_confidence,
        global_enabled_groups=style_global_groups,
    ))
    # D.4 episode recall — only ``enabled_for_prompt`` reflections
    # surface, ranked below slang/style so the budget manager trims
    # them first under pressure.
    provider_bus.register(EpisodeProvider(
        store_getter=lambda: getattr(ctx, "episode_store", None),
        top_k=3,
        enabled=True,
    ))
    llm.set_provider_bus(provider_bus)
    ctx.provider_bus = provider_bus

    # ---- memo extractor ----
    from plugins.memo import MemoExtractor
    memo_extractor = MemoExtractor(
        card_store=card_store,
        api_call=llm._call,
        config=memo_cfg,
    )
    ctx.memo_extractor = memo_extractor
    if config.llm.usage.enabled:
        llm._usage_tracker = usage_tracker

    callbacks.wire_humanization_runtime(ctx, config, llm)

    # PR2 (2026-05-21): wire LLMClient into KnowledgeGraphService for the
    # LLM-driven graph extractor. Prior to this, the regex MVP extractor
    # leaked Chinese conjunctions/adverbs into the candidate queue. The
    # service was constructed earlier (before LLMClient existed); we
    # late-bind here so on_pre_prompt extraction routes through LLM.
    if getattr(ctx, "knowledge_graph", None) is not None:
        ctx.knowledge_graph.attach_llm_client(llm)

    # Now that llm_client + msg_log are wired, attach MemoryConsolidator.
    ctx.memory_consolidator = MemoryConsolidator(
        store=ctx.memory_consolidator_store,
        archive=ctx.msg_log,
        normalizer=ctx.memory_consolidator_normalizer,
        llm_client=llm,
    )

    # D.3 reflection generator — style/slang stores wire late
    # (StylePlugin priority=43, SlangPlugin priority=42), so we hand
    # the generator getter callables that defer ctx attribute lookup
    # until run_once() actually fires.
    ctx.reflection_generator = ReflectionGenerator(
        store=ctx.memory_consolidator_store,
        llm_client=llm,
        style_store_getter=lambda: getattr(ctx, "style_store", None),
        slang_store_getter=lambda: getattr(ctx, "slang_store", None),
    )

    # ---- desc cache (for vision) ----
    ctx.desc_cache = {}
    ctx.memory_relation_signals = {}

    # ---- scheduler Hawkes cache refresher ----
    hawkes_cache = None
    ctx.scheduler_hawkes_refresher = None
    if config.humanization.rws_hawkes:
        from services.scheduler_hawkes import HawkesCache, HawkesOfflineRefresher

        hawkes_cache = HawkesCache()
        ctx.scheduler_hawkes_refresher = HawkesOfflineRefresher(
            message_log=message_log,
            cache=hawkes_cache,
            task_supervisor=getattr(ctx, "background_task_supervisor", None),
        )
        ctx.scheduler_hawkes_refresher.start()

    # ---- scheduler ----
    from services.scheduler import GroupChatScheduler
    from services.talk_schedule import TalkSchedule

    talk_schedule = TalkSchedule("config/talk_schedule.json")
    assembly.publish("talk_schedule", talk_schedule)

    ctx.scheduler = GroupChatScheduler(
        llm=llm,
        timeline=ctx.timeline,
        persona_runtime=persona_runtime,
        group_config=config.group,
        arbiter_config=config.arbiter,
        humanizer=humanizer,
        talk_schedule=talk_schedule,
        mood_getter=runtime_mood_getter if ctx.mood_engine else None,
        runtime_state=ctx.runtime_state,
        humanization_config=config.humanization,
        memory_signal_getter=lambda group_id, user_id: (
            getattr(ctx, "memory_relation_signals", {}) or {}
        ).get((str(group_id), str(user_id))),
        hawkes_cache=hawkes_cache,
        bot_pair_guard=ctx.bot_pair_guard if config.bot_pair_guard.enabled else None,
        block_trace_store=trace_store,
        self_mute_config=config.self_mute,
        group_inventory_getter=lambda: getattr(ctx, "group_inventory", None),
        topic_block_config=config.topic_block,
        thinker_config=config.thinker,
        research_event_capture=ctx.research_event_capture,
    )
    ctx.scheduler.set_arbiter(callbacks.build_arbiter_client(config, llm, usage_tracker))

    # ---- usage API routes (whole-application commit, exactly once) ----
    if config.llm.usage.enabled:
        from services.llm.usage_routes import create_usage_router

        def install_usage_router() -> None:
            app = nonebot.get_app()
            state = app.state
            if not getattr(state, "omubot_usage_router_installed", False):
                app.include_router(create_usage_router(usage_tracker))
                state.omubot_usage_router_installed = True

        assembly.commit_action("usage_router", install_usage_router)

    _L.info("ChatPlugin startup complete")



def create_production_chat_runtime_assembly(
    ctx: PluginContext,
    callbacks: ChatRuntimeCallbacks,
) -> ChatRuntimeAssembly:
    """Create the production assembly without importing the ChatPlugin module."""
    return create_chat_runtime_assembly(
        ctx,
        lambda assembly: build_chat_runtime(ctx, assembly, callbacks),
    )
