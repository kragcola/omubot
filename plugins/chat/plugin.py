"""ChatPlugin: locked adapter for chat-runtime assembly and chat hooks.

ApplicationRuntime owns process/component ordering. ChatRuntimeAssembly owns only
the chat-scoped resources it builds; domain owners retain their own lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from pathlib import Path
from typing import Any, cast

from loguru import logger

from bootstrap.chat_runtime import (
    ChatRuntimeAssembly,
    ChatRuntimeCallbacks,
    create_production_chat_runtime_assembly,
)
from kernel.config import BotConfig, ResolvedHumanization
from kernel.types import AmadeusPlugin, MessageContext, PluginContext
from services.llm.arbiter import ArbiterClient

_L = logger.bind(channel="system")

def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return str(content or "").strip()


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_short_text_tuple(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return tuple(items)


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _build_schedule_persona_brief(persona_runtime: Any, identity: Any) -> Any:
    from plugins.schedule import PersonaScheduleBrief

    role = str(getattr(identity, "description", "") or "").strip()
    traits: tuple[str, ...] = ()
    known_facts: tuple[str, ...] = ()
    bundle = getattr(persona_runtime, "bundle", None)
    pending_dir = getattr(bundle, "pending_freeze_dir", None)
    if isinstance(pending_dir, Path):
        persona_yaml = _read_yaml_mapping(pending_dir / "persona.yaml")
        raw_identity_data = persona_yaml.get("identity")
        identity_data = raw_identity_data if isinstance(raw_identity_data, dict) else {}
        if not role:
            role = str(identity_data.get("role") or "").strip()
        traits = _as_short_text_tuple(identity_data.get("essence"), limit=5)

        knowledge_yaml = _read_yaml_mapping(pending_dir / "knowledge.yaml")
        known_facts = _as_short_text_tuple(knowledge_yaml.get("known_facts"), limit=3)

    if not role:
        role = _first_nonempty_line(str(getattr(identity, "personality", "") or ""))

    return PersonaScheduleBrief(
        identity=role,
        traits=traits,
        known_facts=known_facts,
        partner_context="天马司、草薙宁宁、神代类、朝比奈真冬是可用于日常安排的虚构伙伴关系；无伙伴状态卡前按稳定的同伴/排练关系处理。",
    )


def _build_fiction_partner_profiles(persona_runtime: Any) -> tuple[Any, ...]:
    from plugins.schedule import FictionPartnerProfile

    partner_names = ("天马司", "草薙宁宁", "神代类", "朝比奈真冬")
    known_facts: list[str] = []
    bundle = getattr(persona_runtime, "bundle", None)
    pending_dir = getattr(bundle, "pending_freeze_dir", None)
    if isinstance(pending_dir, Path):
        knowledge_yaml = _read_yaml_mapping(pending_dir / "knowledge.yaml")
        raw_facts = knowledge_yaml.get("known_facts")
        if isinstance(raw_facts, list):
            known_facts = [str(item or "") for item in raw_facts]
    joined_facts = "；".join(known_facts)
    if joined_facts:
        detected = tuple(name for name in partner_names if name in joined_facts)
        if detected:
            partner_names = detected
    constraints = (
        "kind=fiction，仅作为虚构伙伴状态演绎",
        "不得写入真人 factual 或群友线下行为",
        "私聊内容不得进入群叙事",
    )
    profile_by_name = {
        "天马司": "W×S 成员，外向、自信、舞台中心感强。",
        "草薙宁宁": "W×S 成员，冷静细致，重视表演完成度。",
        "神代类": "W×S 成员，擅长舞台机关与演出设计，点子很多。",
        "朝比奈真冬": "宫益坂相关的虚构伙伴关系，情绪表达更克制，需要尊重边界。",
    }
    return tuple(
        FictionPartnerProfile(
            entity_id=_fiction_partner_entity_id(name),
            display_name=name,
            pinned_profile=profile_by_name.get(name, "凤笑梦的虚构伙伴关系。"),
            constraints=constraints,
        )
        for name in partner_names
    )


def _fiction_partner_entity_id(name: str) -> str:
    mapping = {
        "天马司": "tenma_tsukasa",
        "草薙宁宁": "kusanagi_nene",
        "神代类": "kamishiro_rui",
        "朝比奈真冬": "asahina_mafuyu",
    }
    return mapping.get(name, re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "fiction_partner")


def _register_classifier_window(ctx: PluginContext, msg_ctx: MessageContext, current_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    timeline = getattr(ctx, "timeline", None)
    if timeline is not None and msg_ctx.group_id is not None:
        try:
            turns = list(timeline.get_turns(str(msg_ctx.group_id)))[-4:]
        except Exception as exc:
            _L.debug("register classifier timeline read failed | group={} err={}", msg_ctx.group_id, exc)
            turns = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            text = _message_content_text(turn.get("content"))
            if not text:
                continue
            rows.append({
                "speaker": str(turn.get("speaker") or turn.get("role") or "history"),
                "content_text": text,
            })
    rows.append({
        "speaker": msg_ctx.nickname or msg_ctx.user_id,
        "content_text": current_text,
    })
    return rows[-5:]


def _mood_classifier_window(
    ctx: PluginContext,
    msg_ctx: MessageContext,
    current_text: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    timeline = getattr(ctx, "timeline", None)
    if timeline is not None and msg_ctx.group_id is not None:
        try:
            all_turns = list(timeline.get_turns(str(msg_ctx.group_id)))
            turns = all_turns[-11:]
        except Exception as exc:
            _L.debug("mood classifier timeline read failed | group={} err={}", msg_ctx.group_id, exc)
            all_turns = []
            turns = []
        start = max(0, len(all_turns) - len(turns))
        for offset, turn in enumerate(turns):
            if not isinstance(turn, dict):
                continue
            text = _message_content_text(turn.get("content"))
            if not text:
                continue
            row: dict[str, Any] = {
                "role": str(turn.get("role") or "user"),
                "content_text": text,
            }
            get_turn_time = getattr(timeline, "get_turn_time", None)
            if callable(get_turn_time):
                with contextlib.suppress(Exception):
                    row["created_at"] = float(
                        cast(Any, get_turn_time)(str(msg_ctx.group_id), start + offset)
                    )
            rows.append(row)
    rows.append({
        "role": "user",
        "content_text": current_text,
        "created_at": time.time(),
    })
    return rows[-12:]


def _timeline_reply_delay_s(ctx: PluginContext, group_id: str) -> float:
    timeline = getattr(ctx, "timeline", None)
    if timeline is None:
        return 0.0
    try:
        turns = list(timeline.get_turns(group_id))
    except Exception:
        return 0.0
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if str(turn.get("role") or "").strip().lower() != "assistant":
            continue
        try:
            ts = float(timeline.get_turn_time(group_id, index))
        except Exception:
            return 0.0
        return max(0.0, time.time() - ts) if ts > 0 else 0.0
    return 0.0


def _timeline_consecutive_no_reply(ctx: PluginContext, group_id: str) -> int:
    timeline = getattr(ctx, "timeline", None)
    if timeline is None:
        return 1
    try:
        turns = list(timeline.get_turns(group_id))
    except Exception:
        return 1
    streak = 1  # current inbound message has not been replied to yet
    for turn in reversed(turns):
        role = str(turn.get("role") or "").strip().lower()
        if role == "assistant":
            break
        if role == "user":
            streak += 1
    return max(0, streak)


def _runtime_register_confidence(ctx: PluginContext, *, scope: Any) -> float:
    from services.humanization import REGISTER_LABEL_SLOT

    runtime_state = getattr(ctx, "runtime_state", None)
    if runtime_state is None:
        return 0.0
    try:
        snapshot = runtime_state.get(REGISTER_LABEL_SLOT, scope=scope)
    except Exception:
        return 0.0
    value = getattr(snapshot, "value", None)
    if not isinstance(value, dict):
        return 0.0
    try:
        return float(value.get("confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _humanization_runtime_groups(config: BotConfig) -> frozenset[str]:
    return frozenset(str(group_id).strip() for group_id in config.humanization.runtime_groups if str(group_id).strip())


def _humanization_group_allowed(config: BotConfig, group_id: str | None) -> bool:
    groups = _humanization_runtime_groups(config)
    if not groups:
        return True
    return str(group_id or "").strip() in groups


def _humanization_resolve(
    config: BotConfig,
    group_id: str | int | None,
    *,
    performance_degraded: bool | None = None,
) -> ResolvedHumanization:
    profile_override = None
    if group_id is not None:
        try:
            group_profile = config.group.resolve(int(group_id))
            profile_override = group_profile.humanization_profile
        except Exception:
            profile_override = None
    return config.humanization.resolve_profile(
        profile_override,
        group_id,
        performance_degraded=performance_degraded,
    )


def _register_humanization_interaction_tools(config: BotConfig, tools: Any) -> None:
    if not hasattr(tools, "register_interaction_tools"):
        return
    tools.register_interaction_tools(
        resolved_humanization=config.humanization.resolve_profile(config.humanization.profile),
        profile=config.humanization.profile,
    )


class _ScopedHumanizationProvider:
    def __init__(self, provider: Any, *, allowed_groups: frozenset[str]) -> None:
        self._provider = provider
        self._allowed_groups = allowed_groups
        self.name = str(getattr(provider, "name", "humanization"))

    async def provide(self, ctx: Any) -> list[Any]:
        if self._allowed_groups and str(getattr(ctx, "group_id", "") or "") not in self._allowed_groups:
            return []
        return await self._provider.provide(ctx)


class ChatPlugin(AmadeusPlugin):
    name = "chat"
    version = "1.1.25"
    description = "核心聊天：消息路由、LLM 调用、工具循环与上下文调度"
    priority = 0

    def __init__(self) -> None:
        super().__init__()
        self._ctx: PluginContext | None = None
        self._runtime_assembly: ChatRuntimeAssembly | None = None

    def _wire_humanization_runtime(self, ctx: PluginContext, config: BotConfig, llm: Any) -> None:
        if config.humanization.register_classifier:
            from services.humanization import RegisterClassifier

            ctx.humanization_register_classifier = RegisterClassifier(llm)
            _L.info("humanization register classifier enabled")
        else:
            ctx.humanization_register_classifier = None
        climate_hub = getattr(ctx, "climate_sensor_hub", None)
        if climate_hub is not None and bool(getattr(climate_hub, "enabled", False)):
            from services.humanization import MoodClassifier

            ctx.dialogue_climate_mood_classifier = MoodClassifier()
            _L.info("dialogue climate mood classifier enabled")
        else:
            ctx.dialogue_climate_mood_classifier = None

    def _build_arbiter_client(self, config: BotConfig, llm: Any, usage_tracker: Any) -> ArbiterClient | None:
        arbiter_config = getattr(config, "arbiter", None)
        session = getattr(llm, "_session", None)
        if arbiter_config is None or session is None:
            return None
        arbiter_config.resolved_api_base = str(getattr(arbiter_config, "api_base", "") or config.llm.base_url)
        arbiter_config.resolved_api_key = str(getattr(arbiter_config, "api_key", "") or config.llm.api_key)
        arbiter_config.resolved_model = str(getattr(arbiter_config, "model", "") or config.llm.model)
        return ArbiterClient(
            arbiter_config,
            session,
            usage_tracker=usage_tracker,
        )

    async def on_message(self, ctx: MessageContext) -> bool:
        plugin_ctx = self._ctx
        if plugin_ctx is None or ctx.group_id is None or ctx.is_private:
            return False
        if not ctx.allow_speaking:
            return False
        config = getattr(plugin_ctx, "config", None)
        if not isinstance(config, BotConfig) or not _humanization_group_allowed(config, ctx.group_id):
            return False
        runtime_state = getattr(plugin_ctx, "runtime_state", None)
        current_text = _message_content_text(ctx.content)
        if not current_text:
            return False

        from services.humanization import WILLINGNESS_STAGE_SLOT, humanization_source
        from services.persona.willingness import episodic_situation_lookup, willingness_stage
        from services.scheduler_rws.memory_signals import (
            familiarity_score,
            mood_trend,
            recent_outcome_ratio,
            willingness_phase_score,
        )
        from services.system_module import Scope

        scope = Scope(
            session_id=ctx.session_id,
            group_id=ctx.group_id,
            user_id=ctx.user_id,
        )
        climate_classifier = getattr(plugin_ctx, "dialogue_climate_mood_classifier", None)
        climate_hub = getattr(plugin_ctx, "climate_sensor_hub", None)
        if (
            climate_classifier is not None
            and climate_hub is not None
            and bool(getattr(climate_hub, "enabled", False))
        ):
            try:
                from services.dialogue_climate.sensors import SensorInput

                mood_decision = await climate_classifier.classify(
                    _mood_classifier_window(plugin_ctx, ctx, current_text),
                )
                climate_hub.collect(SensorInput(
                    group_id=str(ctx.group_id),
                    user_id=str(ctx.user_id),
                    event_id=(
                        f"message:{ctx.group_id}:{ctx.message_id}"
                        if ctx.message_id is not None
                        else ""
                    ),
                    message_label=str(getattr(mood_decision, "label", "") or ""),
                    message_confidence=float(getattr(mood_decision, "confidence", 0.0) or 0.0),
                ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _L.debug(
                    "dialogue climate mood classifier failed | session={} user={} err={}",
                    ctx.session_id,
                    ctx.user_id,
                    exc,
                )
        register_decision = None
        classifier = getattr(plugin_ctx, "humanization_register_classifier", None)
        if (
            bool(getattr(getattr(config, "humanization", None), "register_classifier", False))
            and classifier is not None
            and runtime_state is not None
        ):
            try:
                register_decision = await classifier.classify_and_write(
                    _register_classifier_window(plugin_ctx, ctx, current_text),
                    bus=runtime_state,
                    scope=scope,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _L.debug(
                    "humanization register classifier failed | session={} user={} err={}",
                    ctx.session_id,
                    ctx.user_id,
                    exc,
                )

        if runtime_state is not None:
            try:
                register_consistency = (
                    float(getattr(register_decision, "confidence", 0.0))
                    if register_decision is not None
                    else _runtime_register_confidence(plugin_ctx, scope=scope)
                )
                interaction_count = 0
                timeline = getattr(plugin_ctx, "timeline", None)
                if timeline is not None:
                    try:
                        interaction_count = len(list(timeline.get_turns(str(ctx.group_id)))) + 1
                    except Exception:
                        interaction_count = 1
                recent_episodes = await episodic_situation_lookup(
                    getattr(plugin_ctx, "episode_store", None),
                    str(ctx.group_id),
                    current_text,
                )
                willingness = willingness_stage(
                    recent_reply_delay_s=_timeline_reply_delay_s(plugin_ctx, str(ctx.group_id)),
                    register_consistency=register_consistency,
                    interaction_count=interaction_count,
                    consecutive_no_reply=_timeline_consecutive_no_reply(plugin_ctx, str(ctx.group_id)),
                    recent_outcomes=[
                        str(getattr(episode, "outcome_signal", "") or "")
                        for episode in recent_episodes
                    ],
                )
                runtime_state.set(
                    WILLINGNESS_STAGE_SLOT,
                    willingness.to_state_value(),
                    scope=scope,
                    source=humanization_source("willingness:classify"),
                    confidence=willingness.confidence,
                )
                signal_cache = getattr(plugin_ctx, "memory_relation_signals", None)
                if not isinstance(signal_cache, dict):
                    signal_cache = {}
                    plugin_ctx.memory_relation_signals = signal_cache
                signal_cache[(str(ctx.group_id), str(ctx.user_id))] = {
                    "outcome_ratio": await recent_outcome_ratio(
                        getattr(plugin_ctx, "episode_store", None),
                        str(ctx.group_id),
                    ),
                    "familiarity": await familiarity_score(
                        getattr(plugin_ctx, "card_store", None),
                        str(ctx.user_id),
                    ),
                    "willingness_stage": willingness.stage,
                    "willingness_phase": await willingness_phase_score(willingness.stage),
                    "mood_trend": await mood_trend(
                        getattr(plugin_ctx, "mood_engine", None),
                        str(ctx.group_id),
                    ),
                    "recent_outcomes": [
                        str(getattr(episode, "outcome_signal", "") or "")
                        for episode in recent_episodes
                    ][:3],
                    "updated_at": time.time(),
                }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _L.debug(
                    "willingness memory signal update failed | session={} user={} err={}",
                    ctx.session_id,
                    ctx.user_id,
                    exc,
                )
        return False

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        callbacks = ChatRuntimeCallbacks(
            build_schedule_persona_brief=_build_schedule_persona_brief,
            build_fiction_partner_profiles=_build_fiction_partner_profiles,
            humanization_runtime_groups=_humanization_runtime_groups,
            humanization_resolver=_humanization_resolve,
            register_humanization_interaction_tools=(
                _register_humanization_interaction_tools
            ),
            wrap_scoped_humanization_provider=lambda provider, groups: (
                _ScopedHumanizationProvider(provider, allowed_groups=groups)
            ),
            wire_humanization_runtime=self._wire_humanization_runtime,
            build_arbiter_client=self._build_arbiter_client,
        )
        assembly = create_production_chat_runtime_assembly(ctx, callbacks)
        self._runtime_assembly = assembly
        await assembly.start()
        ctx.chat_runtime_commit = assembly.commit

    async def on_shutdown(self, ctx: PluginContext) -> None:
        del ctx
        assembly = self._runtime_assembly
        if assembly is not None:
            await assembly.close()
        _L.info("ChatPlugin shutdown complete")
