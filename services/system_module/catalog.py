"""Canonical SystemModule catalog for Persona v2 Part B S1'."""

from __future__ import annotations

from typing import Any

from .models import ModuleContract, StateSlotDefinition, SwitchSurface

REQUIRED_MODULE_IDS = frozenset({
    "core.identity",
    "core.guard",
    "runtime.adapter",
    "runtime.scheduler",
    "memory.short_term",
    "output.send",
})

RESERVED_MODULE_IDS = frozenset({
    "state.world",
    "state.desire",
    "state.values_drift",
    "runtime.planner",
    "state.intimacy",
    "observer.feedback",
    "self.reflection",
})


def _slot(slot_id: str, schema: str, *, ttl: str = "per_turn", privacy: str = "public") -> StateSlotDefinition:
    return StateSlotDefinition.from_dict({
        "id": slot_id,
        "schema": schema,
        "ttl": ttl,
        "privacy": privacy,
    })


def _module(
    module_id: str,
    *,
    persona_reads: tuple[str, ...] = (),
    persona_writes: tuple[str, ...] = (),
    state_owns: tuple[StateSlotDefinition, ...] = (),
    state_consumes: tuple[str, ...] = (),
    depends_on: tuple[str, ...] = (),
    enabled: bool = True,
    reserved: bool = False,
    required: bool | None = None,
) -> ModuleContract:
    return ModuleContract(
        id=module_id,
        group=module_id.split(".", 1)[0],  # type: ignore[arg-type]
        enabled=enabled,
        required=module_id in REQUIRED_MODULE_IDS if required is None else required,
        reserved=reserved,
        status="schema_only" if reserved else "active",
        persona_reads=persona_reads,
        persona_writes=persona_writes,
        state_owns=state_owns,
        state_consumes=state_consumes,
        depends_on=depends_on,
        switch_surface=SwitchSurface(
            persona_level=True,
            group_level=True,
            turn_level=False,
        ),
    )


MODULE_CATALOG: tuple[ModuleContract, ...] = (
    _module(
        "core.identity",
        persona_reads=("persona.yaml",),
        state_owns=(_slot("core.identity.snapshot", "omubot.state.identity_snapshot.v2", ttl="per_session"),),
    ),
    _module(
        "core.voice",
        persona_reads=("voice.yaml",),
        persona_writes=("voice.yaml.expression_library",),
        state_owns=(_slot("core.voice.profile", "omubot.state.voice_profile.v2", ttl="per_session"),),
        state_consumes=("learning.style.deltas",),
        depends_on=("learning.style",),
    ),
    _module(
        "core.knowledge",
        persona_reads=("knowledge.yaml",),
        state_owns=(_slot("core.knowledge.facts", "omubot.state.knowledge_facts.v2", ttl="per_session"),),
    ),
    _module(
        "core.relationships",
        persona_reads=("relationships.yaml",),
        persona_writes=("relationships.yaml.profiles",),
        state_owns=(_slot("core.relationships.profile", "omubot.state.relationship_profile.v2", ttl="per_user"),),
        state_consumes=("state.affection.<user_id>",),
        depends_on=("state.affection",),
    ),
    _module(
        "core.examples",
        persona_reads=("examples.yaml",),
        state_owns=(_slot("core.examples.fewshot", "omubot.state.examples_fewshot.v2", ttl="per_turn"),),
        state_consumes=("runtime.thinker.decision",),
        depends_on=("runtime.thinker",),
    ),
    _module(
        "core.guard",
        persona_reads=("guard.yaml", "persona.yaml#constitution.hard_rules"),
        state_owns=(_slot("core.guard.verdict", "omubot.state.guard_verdict.v2", ttl="per_turn"),),
    ),
    _module(
        "runtime.adapter",
        persona_reads=("adapter.yaml",),
        state_owns=(_slot("runtime.adapter.last_event", "omubot.state.adapter_event.v2", ttl="per_turn"),),
    ),
    _module(
        "memory.short_term",
        persona_reads=("memory.yaml.short_term",),
        state_owns=(
            _slot("memory.short_term.timeline", "omubot.state.short_term_timeline.v2", ttl="per_session"),
            _slot("memory.short_term.pending", "omubot.state.short_term_pending.v2", ttl="per_turn"),
        ),
        state_consumes=("runtime.adapter.last_event",),
        depends_on=("runtime.adapter",),
    ),
    _module(
        "runtime.calendar",
        persona_reads=("runtime.yaml.calendar",),
        state_owns=(_slot("runtime.calendar.time_mult", "omubot.state.time_multiplier.v2", ttl="per_turn"),),
    ),
    _module(
        "state.calendar",
        persona_reads=("state.yaml.calendar",),
        state_owns=(_slot("state.calendar.today", "omubot.state.calendar_today.v2", ttl="per_session"),),
    ),
    _module(
        "state.schedule",
        persona_reads=("state.yaml.schedule",),
        persona_writes=("state.yaml.schedule.today",),
        state_owns=(
            _slot("state.schedule.slot", "omubot.state.schedule_slot.v2", ttl="per_session"),
            _slot("state.schedule.today", "omubot.state.schedule_today.v2", ttl="per_session"),
        ),
        state_consumes=("state.calendar.today",),
        depends_on=("state.calendar",),
    ),
    _module(
        "state.mood",
        persona_reads=("state.yaml.mood",),
        state_owns=(
            _slot("state.mood.current", "omubot.state.mood.v2", ttl="per_session"),
            _slot("state.mood.history", "omubot.state.mood_history.v2", ttl="persistent"),
        ),
        state_consumes=("state.schedule.slot", "state.calendar.today"),
        depends_on=("state.schedule", "state.calendar"),
    ),
    _module(
        "state.affection",
        persona_reads=("relationships.yaml.affection",),
        persona_writes=("relationships.yaml.affection.<uid>",),
        state_owns=(
            _slot("state.affection.<user_id>", "omubot.state.affection.v2", ttl="per_user", privacy="user_only"),
            _slot("state.affection.recent_changes", "omubot.state.affection_changes.v2", ttl="per_session"),
        ),
        state_consumes=("runtime.adapter.last_event",),
        depends_on=("runtime.adapter",),
    ),
    _module(
        "state.board",
        persona_reads=("runtime.yaml.state_board",),
        state_owns=(_slot("state.board.snapshot", "omubot.state.board_snapshot.v2", ttl="per_session"),),
    ),
    _module(
        "runtime.scheduler",
        persona_reads=("runtime.yaml.scheduler",),
        state_owns=(_slot("runtime.scheduler.fire_decision", "omubot.state.scheduler_decision.v2", ttl="per_turn"),),
        state_consumes=("state.mood.current", "runtime.calendar.time_mult", "runtime.adapter.last_event"),
        depends_on=("state.mood", "runtime.calendar", "runtime.adapter"),
    ),
    _module(
        "runtime.thinker",
        persona_reads=("thinker.yaml", "persona.yaml#identity.essence", "persona.yaml#constitution.hard_rules"),
        state_owns=(_slot("runtime.thinker.decision", "omubot.state.thinker_decision.v2", ttl="per_turn"),),
        state_consumes=(
            "state.mood.current",
            "state.affection.<user_id>",
            "state.schedule.slot",
            "state.calendar.today",
            "learning.slang.hits",
            "runtime.scheduler.fire_decision",
        ),
        depends_on=(
            "state.mood",
            "state.affection",
            "state.schedule",
            "state.calendar",
            "learning.slang",
            "runtime.scheduler",
        ),
    ),
    _module(
        "memory.long_term",
        persona_reads=("memory.yaml.long_term",),
        state_owns=(
            _slot("memory.long_term.cards", "omubot.state.long_term_cards.v2", ttl="persistent"),
            _slot("memory.long_term.index", "omubot.state.long_term_index.v2", ttl="persistent"),
        ),
        state_consumes=("memory.short_term.compaction_summary",),
        depends_on=("memory.compactor",),
    ),
    _module(
        "memory.compactor",
        persona_reads=("memory.yaml.compaction",),
        persona_writes=("memory.yaml.long_term.cards",),
        state_owns=(
            _slot("memory.short_term.compaction_summary", "omubot.state.compaction_summary.v2", ttl="per_session"),
        ),
        state_consumes=("memory.short_term.timeline",),
        depends_on=("memory.short_term",),
    ),
    _module(
        "memory.consolidator",
        persona_reads=("memory.yaml.consolidator",),
        state_owns=(
            _slot("learning.episode.candidates", "omubot.state.episode_candidates.v2", ttl="persistent"),
            _slot("learning.style.candidates", "omubot.state.style_candidates.v2", ttl="persistent"),
            _slot("learning.slang.candidates", "omubot.state.slang_candidates.v2", ttl="persistent"),
        ),
        state_consumes=("memory.long_term.cards",),
        depends_on=("memory.long_term",),
    ),
    _module(
        "learning.slang",
        persona_reads=("voice.yaml.slang",),
        persona_writes=("voice.yaml.slang",),
        state_owns=(_slot("learning.slang.hits", "omubot.state.slang_hits.v2", ttl="per_turn"),),
        state_consumes=("memory.short_term.timeline",),
        depends_on=("memory.short_term",),
    ),
    _module(
        "learning.style",
        persona_reads=("voice.yaml.expression_library",),
        persona_writes=("voice.yaml.expression_library",),
        state_owns=(
            _slot("learning.style.hits", "omubot.state.style_hits.v2", ttl="per_turn"),
            _slot("learning.style.deltas", "omubot.state.style_deltas.v2", ttl="per_session"),
        ),
        state_consumes=("memory.short_term.timeline",),
        depends_on=("memory.short_term",),
    ),
    _module(
        "learning.episode",
        persona_reads=("memory.yaml.episodes",),
        persona_writes=("memory.yaml.episodes",),
        state_owns=(_slot("learning.episode.hits", "omubot.state.episode_hits.v2", ttl="per_turn"),),
        state_consumes=("memory.short_term.timeline",),
        depends_on=("memory.short_term",),
    ),
    _module(
        "context.retrieval",
        persona_reads=("knowledge.yaml.retrieval",),
        state_owns=(_slot("context.retrieval.pack", "omubot.state.retrieval_pack.v2", ttl="per_turn"),),
        state_consumes=("runtime.thinker.decision",),
        depends_on=("runtime.thinker",),
    ),
    _module(
        "context.graph",
        persona_reads=("knowledge.yaml.graph",),
        state_owns=(_slot("context.graph.triples", "omubot.state.graph_triples.v2", ttl="per_turn"),),
        state_consumes=("runtime.thinker.decision",),
        depends_on=("runtime.thinker",),
    ),
    _module(
        "output.sticker",
        persona_reads=("capabilities.yaml.sticker",),
        state_owns=(_slot("output.sticker.library_view", "omubot.state.sticker_library.v2", ttl="per_session"),),
    ),
    _module(
        "output.send",
        persona_reads=("adapter.yaml.send",),
        state_owns=(_slot("runtime.adapter.send_receipt", "omubot.state.send_receipt.v2", ttl="per_turn"),),
        state_consumes=("core.guard.verdict",),
        depends_on=("core.guard",),
    ),
    _module(
        "eval.online",
        persona_reads=("eval.yaml.online",),
        state_owns=(_slot("eval.online.verdicts", "omubot.state.online_eval.v2", ttl="per_session"),),
        enabled=False,
    ),
    *(
        _module(module_id, enabled=False, reserved=True, required=False)
        for module_id in sorted(RESERVED_MODULE_IDS)
    ),
)


def catalog_contracts() -> tuple[ModuleContract, ...]:
    return MODULE_CATALOG


def catalog_system_modules() -> dict[str, dict[str, Any]]:
    return {
        module.id: {
            "enabled": module.enabled,
            "required": module.required,
            **({"reserved": True} if module.reserved else {}),
        }
        for module in MODULE_CATALOG
    }
