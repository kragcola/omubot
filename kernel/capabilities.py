"""Typed capability views over the legacy PluginContext service locator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LegacyPluginContext(Protocol):
    msg_log: Any
    timeline: Any
    short_term: Any
    scheduler: Any
    research_event_capture: Any
    config: Any
    tool_registry: Any
    runtime_state: Any
    block_trace_store: Any
    outbound_group_access_guard: Any
    persona_runtime: Any
    identity: Any
    affection_engine: Any
    mood_engine: Any


@dataclass(frozen=True)
class ConversationCapabilities:
    message_log: Any = None
    timeline: Any = None
    short_term: Any = None
    scheduler: Any = None
    research_event_capture: Any = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    config: Any = None
    tool_registry: Any = None
    runtime_state: Any = None
    block_trace_store: Any = None
    outbound_group_access_guard: Any = None


@dataclass(frozen=True)
class PersonaCapabilities:
    persona_runtime: Any = None
    identity: Any = None
    affection_engine: Any = None
    mood_engine: Any = None


@dataclass(frozen=True)
class PluginServiceCapabilities:
    conversation: ConversationCapabilities
    runtime: RuntimeCapabilities
    persona: PersonaCapabilities

    @classmethod
    def from_legacy(cls, ctx: LegacyPluginContext) -> PluginServiceCapabilities:
        return cls(
            conversation=ConversationCapabilities(
                message_log=ctx.msg_log,
                timeline=ctx.timeline,
                short_term=ctx.short_term,
                scheduler=ctx.scheduler,
                research_event_capture=ctx.research_event_capture,
            ),
            runtime=RuntimeCapabilities(
                config=ctx.config,
                tool_registry=ctx.tool_registry,
                runtime_state=ctx.runtime_state,
                block_trace_store=ctx.block_trace_store,
                outbound_group_access_guard=ctx.outbound_group_access_guard,
            ),
            persona=PersonaCapabilities(
                persona_runtime=ctx.persona_runtime,
                identity=ctx.identity,
                affection_engine=ctx.affection_engine,
                mood_engine=ctx.mood_engine,
            ),
        )
