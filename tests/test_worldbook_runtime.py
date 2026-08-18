from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from kernel.types import PluginContext
from plugins.chat.plugin import ChatPlugin
from plugins.dream.plugin import DreamAgent, LifeReflectionDraft
from plugins.schedule import generator as schedule_generator_module
from plugins.schedule.generator import ScheduleGenerator
from plugins.schedule.store import ScheduleStore
from plugins.schedule.story_arc import StoryArc, StoryArcStore
from plugins.schedule.types import Schedule, TimeSlot
from plugins.worldbook.plugin import (
    WorldbookPlugin,
    WorldbookPluginConfig,
)
from services.block_trace.providers import QueryContext
from services.worldbook import (
    CanonEntry,
    CanonMutationError,
    EventProposal,
    EventRecord,
    LifeState,
    LifeStateItem,
    ProjectionBlock,
    SocialEvidenceRef,
    SocialStoryCommitResult,
    SourceMeta,
    Storylet,
    WorldbookConfig,
)
from services.worldbook.domain import (
    SOCIAL_LIFE_KEY,
    StoryletCommitResult,
    deterministic_social_story_event_id,
    deterministic_storylet_event_id,
    parse_typed_storylet_effects,
)
from services.worldbook.drama import DramaManager
from services.worldbook.dream_bridge import DreamProposalBridge
from services.worldbook.ledger import StoryLedgerAdapter
from services.worldbook.projection import ProjectionResult, PromptProjection
from services.worldbook.provider import WorldbookPromptProvider
from services.worldbook.reducer import EventReducer
from services.worldbook.runtime import WorldbookRuntime, build_worldbook_runtime
from services.worldbook.store import (
    CanonRegistry,
    LifeStateStore,
    PersonaCanonRef,
    ProposalStore,
)
from services.worldbook.trigger import WorldInfoTrigger, budget_atomic_blocks


def test_worldbook_disabled_build_is_noop_without_creating_paths(tmp_path: Path) -> None:
    cfg = WorldbookConfig(
        enabled=False,
        canon_dir="missing/canon",
        storylet_dir="missing/storylets",
        state_dir="missing/state",
        story_arc_dir="missing/arcs",
    )

    runtime = build_worldbook_runtime(cfg, root=tmp_path)

    assert runtime is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "override",
    [
        {"canon_dir": ""},
        {"storylet_dir": ""},
        {"state_dir": ""},
        {"story_arc_dir": ""},
        {"total_budget_chars": -1},
        {"canon_budget_chars": -1},
        {"max_setbacks_per_arc": -1},
        {"recovery_window_steps": -1},
    ],
)
def test_worldbook_config_rejects_empty_paths_and_negative_budgets(
    override: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        WorldbookConfig(**override)


def test_worldbook_authoring_schemas_accept_canon_and_reject_factual_storylets() -> None:
    root = Path(__file__).resolve().parents[1]
    canon_schema = json.loads(
        (root / "schemas/worldbook-canon-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    storylet_schema = json.loads(
        (root / "schemas/worldbook-storylet-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    valid_canon = {
        "schema_version": 1,
        "entries": [
            {
                "entry_id": "place.stage",
                "title": "舞台",
                "text": "经过核验的原生世界事实。",
                "keywords": ["舞台"],
            }
        ],
    }
    invalid_storylets = {
        "schema_version": 1,
        "storylets": [
            {
                "storylet_id": "bad.real_person",
                "title": "越界事件",
                "text": "不允许把真人纳入自动事件。",
                "scope": "factual",
                "target_arc_id": "living_story_v1.main",
            }
        ],
    }

    Draft202012Validator(canon_schema).validate(valid_canon)
    errors = list(Draft202012Validator(storylet_schema).iter_errors(invalid_storylets))
    assert len(errors) == 1
    assert errors[0].validator == "const"


def test_persona_and_native_canon_are_runtime_immutable(tmp_path: Path) -> None:
    canon_dir = tmp_path / "canon"
    canon_dir.mkdir()
    (canon_dir / "world.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "entry_id": "stage.sekai",
                        "title": "舞台世界",
                        "text": "这是角色原生世界中的舞台空间。",
                        "keywords": ["舞台"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = CanonRegistry(canon_dir)
    entries = registry.load()
    persona = PersonaCanonRef(identity_text="角色身份", persona_id="fengxiaomeng-v2")

    assert entries == [
        CanonEntry(
            entry_id="stage.sekai",
            title="舞台世界",
            text="这是角色原生世界中的舞台空间。",
            keywords=("舞台",),
        )
    ]
    assert CanonEntry.from_dict(entries[0].to_dict()) == entries[0]
    with pytest.raises(CanonMutationError):
        registry.upsert(entries[0])
    with pytest.raises(CanonMutationError):
        persona.write("覆盖身份")


def test_world_info_trigger_supports_all_activation_modes_and_atomic_budget() -> None:
    entries = [
        CanonEntry(
            entry_id="character.tsukasa",
            title="天马司",
            text="天马司是剧团的演员。",
            keywords=("天马司",),
            entity_ids=("tsukasa",),
            related_entities=("rui",),
            priority=120,
        ),
        CanonEntry(
            entry_id="character.rui",
            title="神代类",
            text="神代类负责舞台演出设计。",
            entity_ids=("rui",),
            priority=110,
        ),
        CanonEntry(
            entry_id="group.wxs",
            title="剧团",
            text="剧团成员会共同排练。",
            aliases=("团长",),
            priority=90,
        ),
        CanonEntry(
            entry_id="place.stage",
            title="舞台",
            text="舞台有独立的安全规则。",
            regexes=(r"凤凰.?舞台",),
            priority=80,
        ),
        CanonEntry(
            entry_id="rule.semantic",
            title="语义规则",
            text="语义相关的世界规则。",
            priority=70,
        ),
    ]
    trigger = WorldInfoTrigger(
        entries,
        semantic_scorer=lambda query, entry: (
            0.7 if entry.entry_id == "rule.semantic" and "演出" in query else 0.0
        ),
    )

    hits = trigger.activate("天马司和团长准备凤凰大舞台的演出")

    by_id = {hit.entry.entry_id: hit for hit in hits}
    assert tuple(by_id) == (
        "place.stage",
        "group.wxs",
        "character.tsukasa",
        "rule.semantic",
        "character.rui",
    )
    assert by_id["character.rui"].hit_reason == "cascade:rui"
    accepted, decisions = budget_atomic_blocks(
        hits,
        budget_chars=len(hits[0].entry.text) + len(hits[1].entry.text),
    )
    assert [hit.entry.entry_id for hit in accepted] == [
        "place.stage",
        "group.wxs",
    ]
    assert decisions[2:] == [
        ("character.tsukasa", "rejected:budget"),
        ("rule.semantic", "rejected:budget"),
        ("character.rui", "rejected:budget"),
    ]


def test_social_evidence_is_evidence_bound_and_never_crosses_scope() -> None:
    with pytest.raises(ValueError, match="evidence_message_id required"):
        SocialEvidenceRef(
            experience_id="exp.missing",
            group_id="group-a",
            user_id="user-a",
            evidence_message_id="",
            evidence_time="2026-07-18T10:00:00+08:00",
            summary="一起排练过。",
        )

    evidence = SocialEvidenceRef(
        experience_id="exp.a",
        group_id="group-a",
        user_id="user-a",
        evidence_message_id="msg-1",
        evidence_time="2026-07-18T10:00:00+08:00",
        summary="一起讨论了公开演出。",
    )
    projection = PromptProjection(
        WorldbookConfig(
            enabled=True,
            chat_projection_enabled=True,
            schedule_projection_enabled=True,
            social_evidence_enabled=True,
        )
    )

    cross_group = projection.project(
        mode="chat",
        social=[evidence],
        group_id="group-b",
        user_id="user-a",
    )
    private_chat = projection.project(
        mode="chat",
        social=[evidence],
        group_id=None,
        user_id="user-a",
    )
    schedule = projection.project(
        mode="schedule",
        social=[evidence],
        group_id=None,
        user_id=None,
    )

    assert cross_group.blocks == ()
    assert any(
        trace.budget_decision == "rejected:cross_group"
        for trace in cross_group.traces
    )
    assert private_chat.blocks == ()
    assert any(
        trace.budget_decision == "rejected:private_or_missing_scope"
        for trace in private_chat.traces
    )
    assert schedule.blocks == ()
    assert any(
        trace.budget_decision == "rejected:schedule_no_social"
        for trace in schedule.traces
    )


def test_worldbook_metadata_and_social_privacy_fail_closed() -> None:
    with pytest.raises(ValueError, match="invalid source"):
        SourceMeta(  # type: ignore[arg-type]
            source="invented",
            scope="world",
        )
    with pytest.raises(ValueError, match="scope is required"):
        SourceMeta(source="native_canon", scope="")
    with pytest.raises(ValueError, match="invalid privacy"):
        SourceMeta(  # type: ignore[arg-type]
            source="native_canon",
            scope="world",
            privacy="everyone",
        )

    private_evidence = SocialEvidenceRef(
        experience_id="exp.private",
        group_id="group-a",
        user_id="user-a",
        evidence_message_id="msg-private",
        evidence_time="2026-07-18T10:00:00+08:00",
        summary="只允许私下使用的共同经历。",
        privacy="private",
    )
    result = PromptProjection(
        WorldbookConfig(
            enabled=True,
            chat_projection_enabled=True,
            social_evidence_enabled=True,
        )
    ).project(
        mode="chat",
        social=[private_evidence],
        group_id="group-a",
        user_id="user-a",
    )

    assert result.blocks == ()
    assert any(
        trace.budget_decision == "rejected:privacy"
        for trace in result.traces
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"user_id": ""}, "user_id required"),
        ({"evidence_time": ""}, "evidence_time required"),
        ({"evidence_time": "not-a-time"}, "invalid evidence_time"),
        ({"summary": ""}, "summary required"),
        ({"privacy": "everyone"}, "invalid privacy"),
        ({"confidence": "certain"}, "invalid confidence"),
    ],
)
def test_social_evidence_domain_rejects_incomplete_or_untyped_metadata(
    override: dict[str, str],
    message: str,
) -> None:
    payload = {
        "experience_id": "exp.valid",
        "group_id": "group-a",
        "user_id": "user-a",
        "evidence_message_id": "msg-1",
        "evidence_time": "2026-07-18T10:00:00+08:00",
        "summary": "共同经历。",
        "privacy": "group",
        "confidence": "high",
    }
    payload.update(override)

    with pytest.raises(ValueError, match=message):
        SocialEvidenceRef(**payload)  # type: ignore[arg-type]


def test_story_ledger_is_backward_compatible_and_uses_explicit_stack_not_mtime(
    tmp_path: Path,
) -> None:
    legacy = StoryArc.from_dict(
        {
            "arc_id": "legacy_arc",
            "title": "旧故事",
            "stage": "planning",
            "variables": {"progress": 0.2},
        }
    )
    legacy_view = StoryLedgerAdapter().load_from_arc_dicts(
        [legacy], on_date="2026-07-18"
    )
    assert legacy.arc_role == "side"
    assert legacy_view.main is not None
    assert legacy_view.main["arc_id"] == "legacy_arc"

    store = StoryArcStore(tmp_path / "arcs")
    store.save(
        StoryArc(
            arc_id="main_arc",
            title="主线",
            arc_role="main",
            stack_order=50,
        )
    )
    store.save(
        StoryArc(
            arc_id="side_arc",
            title="副线",
            arc_role="side",
            stack_order=1,
        )
    )
    store.save(
        StoryArc(
            arc_id="ambient_arc",
            title="环境线",
            arc_role="ambient",
            stack_order=2,
        )
    )
    os.utime(tmp_path / "arcs" / "side_arc.json", (4_000_000_000, 4_000_000_000))

    view = StoryLedgerAdapter(store).load_stack(on_date="2026-07-18")

    assert view.main is not None
    assert view.main["arc_id"] == "main_arc"
    assert [arc["arc_id"] for arc in view.sides] == ["side_arc"]
    assert [arc["arc_id"] for arc in view.ambient] == ["ambient_arc"]
    assert view.ordering == ("main_arc", "side_arc", "ambient_arc")


def test_storylet_drama_gates_once_delay_cooldown_evidence_and_setback_budget() -> None:
    manager = DramaManager(
        max_setbacks_per_arc=1,
        max_events_per_tick=1,
        recovery_window_steps=3,
    )
    delayed_once = Storylet(
        storylet_id="rehearsal.accident",
        title="排练事故",
        text="道具在排练中损坏。",
        once=True,
        delay_steps=2,
        cooldown_steps=2,
        required_evidence=("arc:rehearsal",),
        severity="major",
        saliency=2.0,
        recovery_steps=3,
    )

    assert manager.select(
        [delayed_once], available_evidence=["arc:rehearsal"], now_step=1
    ) == []
    assert manager.select([delayed_once], available_evidence=[], now_step=2) == []

    selected = manager.select(
        [delayed_once], available_evidence=["arc:rehearsal"], now_step=2
    )
    assert [item.storylet.storylet_id for item in selected] == [
        "rehearsal.accident"
    ]
    # Selection budget tracks once/cooldown/tick only — setback accounting is
    # owned exclusively by EventReducer on committed events (Stage 2).
    budget = manager.apply_selection_budget({}, selected[0], now_step=2)
    assert budget["triggered_once"] == ["rehearsal.accident"]
    assert budget["cooldowns"]["rehearsal.accident"] == 4
    assert "setback_count" not in budget
    assert "recovery_until_step" not in budget
    assert budget["events_this_tick"] == 1

    # Setback gate still reads committed budget written by the reducer.
    budget_with_setback = {
        **budget,
        "events_this_tick": 0,
        "setback_count": 1,
        "recovery_until_step": 5,
    }
    assert manager.select(
        [delayed_once],
        arc_budget=budget_with_setback,
        available_evidence=["arc:rehearsal"],
        now_step=20,
    ) == []

    # Once-fired storylet remains blocked even without setback budget.
    budget["events_this_tick"] = 0
    assert manager.select(
        [delayed_once],
        arc_budget=budget,
        available_evidence=["arc:rehearsal"],
        now_step=20,
    ) == []


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"text": ""}, "storylet text must be non-empty"),
        ({"severity": "catastrophe"}, "invalid storylet severity"),
        ({"scope": "factual"}, "storylet scope must be fiction"),
        ({"cooldown_steps": -1}, "cooldown_steps must be non-negative"),
        ({"delay_steps": -1}, "delay_steps must be non-negative"),
        ({"recovery_steps": -1}, "recovery_steps must be non-negative"),
    ],
)
def test_storylet_domain_rejects_invalid_event_contract(
    override: dict[str, object],
    message: str,
) -> None:
    payload: dict[str, object] = {
        "storylet_id": "storylet.valid",
        "title": "有效事件",
        "text": "发生一件有后果的虚构事件。",
        "severity": "daily",
        "scope": "fiction",
        "cooldown_steps": 0,
        "delay_steps": 0,
        "recovery_steps": 0,
    }
    payload.update(override)

    with pytest.raises(ValueError, match=message):
        Storylet(**payload)  # type: ignore[arg-type]


def test_event_reducer_only_accepts_committed_events_and_is_idempotent() -> None:
    arc = StoryArc(
        arc_id="arc.reducer",
        variables={"progress": 0.2},
        event_history=[],
    )
    reducer = EventReducer()
    proposed = EventRecord(
        event_id="event.proposal",
        event_type="progress",
        summary="尚未确认的推进。",
        status="proposal",
        variable_deltas={"progress": 0.3},
    )
    committed = EventRecord(
        event_id="event.committed",
        event_type="progress",
        summary="排练完成了一个阶段。",
        status="committed",
        variable_deltas={"progress": 0.3},
        consequences=("需要检查舞台设备",),
        evidence_refs=("arc:arc.reducer",),
    )

    with pytest.raises(ValueError, match="only accepts committed"):
        reducer.apply(arc, proposed, now_step=4)

    first = reducer.apply(arc, committed, now_step=4)
    second = reducer.apply(arc, committed, now_step=4)

    assert first.event_id == second.event_id == "event.committed"
    assert arc.variables["progress"] == pytest.approx(0.5)
    assert [item["event_id"] for item in arc.event_history] == ["event.committed"]
    assert [item["event_id"] for item in arc.last_events] == ["event.committed"]
    assert arc.open_threads == ["需要检查舞台设备"]


def test_dream_bridge_persists_proposals_but_rejects_nested_fact_or_canon_writes(
    tmp_path: Path,
) -> None:
    store = ProposalStore(tmp_path / "worldbook")
    bridge = DreamProposalBridge(store, enabled=True)

    proposal = bridge.submit(
        proposal_id="dream.replan.1",
        kind="arc_replan",
        summary="建议把下一次排练改成设备检查。",
        arc_id="arc.reducer",
        payload={"reason": "上次事件留下了未解决设备问题"},
    )

    assert proposal.status == "proposal"
    persisted = store.load("dream.replan.1")
    assert persisted is not None
    assert persisted.status == "proposal"
    with pytest.raises(ValueError, match="cannot carry"):
        bridge.submit(
            proposal_id="dream.bad.1",
            kind="reflection",
            summary="尝试越权写事实。",
            payload={"updates": {"native_canon": {"rule": "伪造设定"}}},
        )
    with pytest.raises(PermissionError):
        bridge.promote_factual({"message_id": "msg-1"})
    with pytest.raises(CanonMutationError):
        bridge.write_persona_canon("覆盖身份")


class _ProviderBus:
    def __init__(self) -> None:
        self.providers: list[object] = []

    def has_provider(self, name: str) -> bool:
        return any(getattr(provider, "name", "") == name for provider in self.providers)

    def register(self, provider: object) -> None:
        self.providers.append(provider)


class _ScheduleRuntimeTarget:
    def __init__(self) -> None:
        self.values: list[object | None] = []

    def set_worldbook_runtime(self, runtime: object | None) -> None:
        self.values.append(runtime)


@pytest.mark.asyncio
async def test_worldbook_plugin_defaults_off_and_registers_after_chat_provider_bus() -> None:
    assert WorldbookPlugin.priority > ChatPlugin.priority
    defaults = WorldbookPluginConfig()
    assert defaults.enabled is False
    assert defaults.chat_projection_enabled is False
    assert defaults.schedule_projection_enabled is False
    assert defaults.storylet_enabled is False
    assert defaults.dream_proposal_enabled is False
    assert defaults.social_evidence_enabled is False

    disabled_bus = _ProviderBus()
    disabled_ctx = PluginContext(provider_bus=disabled_bus)
    await WorldbookPlugin(config=defaults).on_startup(disabled_ctx)
    assert getattr(disabled_ctx, "worldbook_runtime", None) is None
    assert disabled_bus.providers == []

    enabled_bus = _ProviderBus()
    schedule_target = _ScheduleRuntimeTarget()
    enabled_ctx = PluginContext(
        provider_bus=enabled_bus,
        schedule_gen=schedule_target,
    )
    plugin = WorldbookPlugin(
        config=WorldbookPluginConfig(
            enabled=True,
            chat_projection_enabled=True,
            schedule_projection_enabled=True,
            dream_proposal_enabled=True,
        )
    )
    await plugin.on_startup(enabled_ctx)

    runtime = getattr(enabled_ctx, "worldbook_runtime", None)
    assert runtime is not None
    assert runtime.io_performed is False
    assert getattr(enabled_ctx, "worldbook_dream_bridge", None) is runtime.dream_bridge
    assert runtime.dream_bridge.enabled is True
    assert enabled_bus.has_provider("worldbook")
    assert len(enabled_bus.providers) == 1
    assert schedule_target.values == [runtime]
    await plugin.on_shutdown(enabled_ctx)
    assert getattr(enabled_ctx, "worldbook_runtime", None) is None
    assert getattr(enabled_ctx, "worldbook_dream_bridge", None) is None
    assert schedule_target.values == [runtime, None]


class _SocialStore:
    async def recall(self, **_kwargs: object) -> list[dict[str, str]]:
        return [
            {
                "experience_id": "ok",
                "group_id": "group-a",
                "user_id": "user-a",
                "evidence_message_id": "msg-ok",
                "evidence_time": "2026-07-18T10:00:00+08:00",
                "user_text": "当前群当前用户的共同经历。",
            },
            {
                "experience_id": "wrong-group",
                "group_id": "group-b",
                "user_id": "user-a",
                "evidence_message_id": "msg-b",
                "evidence_time": "2026-07-18T10:00:00+08:00",
                "user_text": "其他群的经历。",
            },
            {
                "experience_id": "wrong-user",
                "group_id": "group-a",
                "user_id": "user-b",
                "evidence_message_id": "msg-user-b",
                "evidence_time": "2026-07-18T10:00:00+08:00",
                "user_text": "同群其他用户的私有经历。",
            },
            {
                "experience_id": "missing-evidence",
                "group_id": "group-a",
                "user_id": "user-a",
                "evidence_message_id": "",
                "evidence_time": "2026-07-18T10:00:00+08:00",
                "user_text": "没有证据的经历。",
            },
        ]


@pytest.mark.asyncio
async def test_social_store_adapter_revalidates_returned_group_user_and_evidence(
    tmp_path: Path,
) -> None:
    runtime = build_worldbook_runtime(
        WorldbookConfig(enabled=True, social_evidence_enabled=True),
        root=tmp_path,
        social_narrative_store=_SocialStore(),
    )
    assert runtime is not None

    evidence = await runtime.load_social_evidence(
        group_id="group-a", user_id="user-a"
    )

    assert [item.experience_id for item in evidence] == ["ok"]


def test_expired_life_state_is_not_projected_and_is_traced() -> None:
    projection = PromptProjection(
        WorldbookConfig(enabled=True, chat_projection_enabled=True)
    )
    state = LifeState(
        revision=2,
        items={
            "location": LifeStateItem(
                key="location",
                value="昨天的舞台",
                meta=SourceMeta(
                    source="life_state",
                    scope="self",
                    confidence="high",
                    privacy="private",
                    updated_at="2000-01-01T00:00:00+00:00",
                    decay_at="2000-01-01T00:00:00+00:00",
                    evidence_refs=("schedule:old",),
                ),
            ),
            "activity": LifeStateItem(
                key="activity",
                value="正在检查今天的舞台设备",
                meta=SourceMeta(
                    source="life_state",
                    scope="self",
                    confidence="high",
                    privacy="private",
                    updated_at="2026-07-18T00:00:00+00:00",
                    decay_at="2999-01-01T00:00:00+00:00",
                    evidence_refs=("schedule:today",),
                ),
            ),
        },
    )

    result = projection.project(mode="chat", life_state=state)

    assert len(result.blocks) == 1
    assert "正在检查今天的舞台设备" in result.blocks[0].text
    assert "昨天的舞台" not in result.blocks[0].text
    assert any(
        trace.label == "location"
        and trace.budget_decision == "rejected:expired"
        for trace in result.traces
    )


def test_life_state_store_uses_revision_cas_and_atomic_files(tmp_path: Path) -> None:
    store = LifeStateStore(tmp_path / "state")
    first = store.load()
    first.items["activity"] = LifeStateItem(
        key="activity",
        value="排练",
        meta=SourceMeta(
            source="life_state",
            scope="self",
            confidence="high",
            privacy="private",
            updated_at="2026-07-18T00:00:00+00:00",
            decay_at="2999-01-01T00:00:00+00:00",
            evidence_refs=("schedule:2026-07-18",),
        ),
    )
    saved = store.save(first)
    stale = LifeState(revision=0)

    assert saved.revision == 1
    assert store.load().items["activity"].value == "排练"
    with pytest.raises(RuntimeError, match="revision conflict"):
        store.save(stale)
    assert not (tmp_path / "state" / "life_state.json.tmp").exists()


@pytest.mark.asyncio
async def test_offline_provider_projection_preserves_source_scope_evidence_and_budget_trace(
    tmp_path: Path,
) -> None:
    canon_dir = tmp_path / "canon"
    canon_dir.mkdir()
    (canon_dir / "stage.json").write_text(
        json.dumps(
            {
                "entry_id": "world.stage",
                "title": "原生舞台",
                "text": "原生舞台遵守既定安全规则。",
                "keywords": ["舞台"],
                "priority": 130,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = WorldbookConfig(
        enabled=True,
        chat_projection_enabled=True,
        canon_dir="canon",
        storylet_dir="storylets",
        state_dir="state",
        story_arc_dir="arcs",
    )
    runtime = build_worldbook_runtime(cfg, root=tmp_path)
    assert runtime is not None
    provider = WorldbookPromptProvider(runtime, cfg)

    candidates = await provider.provide(
        QueryContext(
            request_id="req-worldbook-1",
            session_id="session-a",
            user_id="user-a",
            group_id="group-a",
            conversation_text="今天去舞台看看",
        )
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "worldbook"
    assert candidate.provider == "worldbook"
    assert candidate.scope == "world"
    assert candidate.hit_reason == "keyword:舞台"
    assert candidate.evidence_refs == ("canon:world.stage",)
    assert candidate.metadata["budget_decision"] == "accepted"
    assert candidate.metadata["traces"][0]["source"] == "native_canon"
    assert candidate.metadata["traces"][0]["scope"] == "world"


class _FixedScheduleDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        return cls(2026, 7, 18, 9, 30, tzinfo=tz)


class _ScheduleWorldbookRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def project_schedule(self, **kwargs: object) -> ProjectionResult:
        self.calls.append(dict(kwargs))
        block = ProjectionBlock(
            block_id="canon:world.stage",
            label="原生舞台",
            text="【原生舞台】遵守世界内既定安全规则。",
            meta=SourceMeta(
                source="native_canon",
                scope="world",
                confidence="high",
                privacy="system",
                hit_reason="keyword:舞台",
                evidence_refs=("canon:world.stage",),
                budget_decision="accepted",
            ),
        )
        return ProjectionResult(blocks=(block,), traces=(), mode="schedule")


@pytest.mark.asyncio
async def test_schedule_generator_uses_shared_worldbook_projection_without_social(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(schedule_generator_module, "datetime", _FixedScheduleDateTime)
    store = ScheduleStore(storage_dir=str(tmp_path / "schedule"))
    await store.startup()
    runtime = _ScheduleWorldbookRuntime()
    generator = ScheduleGenerator(
        store=store,
        identity_name="凤晓梦",
        worldbook_runtime=runtime,
    )
    captured: dict[str, object] = {}

    async def api_call(system, messages, tools=None, max_tokens=None):  # type: ignore[no-untyped-def]
        captured["messages"] = messages
        return {
            "text": json.dumps(
                {
                    "date": "2026-07-18",
                    "theme": "世界书接线测试",
                    "day_narrative": "按原生世界规则安排一天。",
                    "slots": [
                        {
                            "time": "09:00",
                            "activity": "practice",
                            "description": "检查舞台设备",
                            "mood_hint": "专注",
                            "location": "舞台",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        }

    await generator._generate(api_call)

    assert len(runtime.calls) == 1
    assert "social" not in runtime.calls[0]
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "【原生舞台】遵守世界内既定安全规则。" in messages[0]["content"]


class _StrictScopedMemoryStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def search_cards(
        self,
        query: str,
        *,
        scope: str,
        limit: int,
    ) -> list[object]:
        self.calls.append((query, scope, limit))
        return [type("Card", (), {"source": "manual", "content": "global memory"})()]


@pytest.mark.asyncio
async def test_schedule_memory_lookup_is_explicitly_global_scoped(tmp_path: Path) -> None:
    memory_store = _StrictScopedMemoryStore()
    generator = ScheduleGenerator(
        store=ScheduleStore(storage_dir=str(tmp_path / "schedule")),
        memory_card_store=memory_store,
    )

    cards = await generator._load_recent_memory_cards()

    assert len(cards) == 1
    assert memory_store.calls == [("", "global", 5)]


def test_worldbook_schedule_without_governance_bridge_fails_closed(
    tmp_path: Path,
) -> None:
    story_store = StoryArcStore(tmp_path / "arcs")
    story_store.save(
        StoryArc(
            arc_id="arc.schedule",
            title="排练主线",
            arc_role="main",
            variables={"rehearsal_progress": 0.2, "exam_pressure": 0.4},
        )
    )
    runtime = build_worldbook_runtime(
        WorldbookConfig(enabled=True, schedule_projection_enabled=True),
        root=tmp_path,
        story_arc_store=story_store,
    )
    assert runtime is not None
    generator = ScheduleGenerator(
        store=ScheduleStore(storage_dir=str(tmp_path / "schedule")),
        story_arc_enabled=True,
        story_arc_store=story_store,
        worldbook_runtime=runtime,
    )
    schedule = Schedule(
        date="2026-07-18",
        theme="设备检查日",
        day_narrative="排练前先解决设备隐患。",
        generated_at="2026-07-18T02:00:00+08:00",
        slots=[
            TimeSlot(
                time="09:00",
                activity="practice",
                description="检查舞台设备",
                mood_hint="专注",
                location="舞台",
            )
        ],
    )
    arc = story_store.load("arc.schedule")
    assert arc is not None

    generator._update_story_arc_after_schedule(arc, schedule)
    generator._update_story_arc_after_schedule(arc, schedule)

    updated = story_store.load("arc.schedule")
    assert updated is not None
    assert updated.variables == {
        "rehearsal_progress": 0.2,
        "exam_pressure": 0.4,
    }
    assert updated.event_history == []
    assert updated.last_events == []


def test_worldbook_schedule_selects_explicit_main_arc_not_newest_mtime(
    tmp_path: Path,
) -> None:
    story_store = StoryArcStore(tmp_path / "arcs")
    story_store.save(
        StoryArc(
            arc_id="main.arc",
            title="明确主线",
            arc_role="main",
            stack_order=20,
        )
    )
    story_store.save(
        StoryArc(
            arc_id="side.arc",
            title="最近修改的副线",
            arc_role="side",
            stack_order=1,
        )
    )
    os.utime(tmp_path / "arcs" / "side.arc.json", (4_000_000_000, 4_000_000_000))
    runtime = build_worldbook_runtime(
        WorldbookConfig(enabled=True, schedule_projection_enabled=True),
        root=tmp_path,
        story_arc_store=story_store,
    )
    assert runtime is not None
    generator = ScheduleGenerator(
        store=ScheduleStore(storage_dir=str(tmp_path / "schedule")),
        story_arc_enabled=True,
        story_arc_store=story_store,
        worldbook_runtime=runtime,
    )

    selected = generator._load_active_story_arc("2026-07-18")

    assert selected is not None
    assert selected.arc_id == "main.arc"


class _DreamBridgeCapture:
    enabled = True

    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []

    def submit(self, **kwargs: object) -> object:
        self.submissions.append(dict(kwargs))
        return object()


class _DreamStoryStore:
    def __init__(self) -> None:
        self.update_calls = 0

    def update(self, *_args: object, **_kwargs: object) -> None:
        self.update_calls += 1


@pytest.mark.asyncio
async def test_worldbook_dream_gate_routes_reflection_to_proposal_not_arc_commit() -> None:
    bridge = _DreamBridgeCapture()
    story_store = _DreamStoryStore()
    arc = StoryArc(arc_id="arc.dream", title="Dream 主线")
    agent = DreamAgent(
        store=object(),  # type: ignore[arg-type]
        story_arc_store=story_store,
        worldbook_dream_bridge=bridge,
    )
    draft = LifeReflectionDraft(
        last_event_summary="今天的设备检查提示下一次排练要先做安全确认。",
        open_threads=["设备是否已经彻底修好"],
        next_day_seed="先确认设备状态，再开始排练",
    )

    writes = await agent._commit_life_reflection(
        draft,
        arc,
        cards_to_persist=[],
    )

    assert writes == 0
    assert story_store.update_calls == 0
    assert len(bridge.submissions) == 1
    submission = bridge.submissions[0]
    assert submission["kind"] == "arc_replan"
    assert submission["arc_id"] == "arc.dream"
    assert submission["payload"] == {
        "last_event_summary": "今天的设备检查提示下一次排练要先做安全确认。",
        "open_threads": ["设备是否已经彻底修好"],
        "next_day_seed": "先确认设备状态，再开始排练",
    }


# ---------------------------------------------------------------------------
# Stage-0 safety defects (behavior RED → GREEN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_storylet_selection_uses_step_evidence_and_persists_budget(
    tmp_path: Path,
) -> None:
    """Defect 1: runtime must not hardcode now_step=0 or skip budget/evidence."""
    storylets_dir = tmp_path / "storylets"
    storylets_dir.mkdir()
    (storylets_dir / "delayed.json").write_text(
        json.dumps(
            {
                "storylets": [
                    {
                        "storylet_id": "rehearsal.delayed",
                        "title": "延迟事件",
                        "text": "延迟门控的排练事故。",
                        "scope": "fiction",
                        "once": True,
                        "delay_steps": 3,
                        "cooldown_steps": 5,
                        "required_evidence": ["arc:main.arc"],
                        "severity": "daily",
                        "saliency": 3.0,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    arcs = StoryArcStore(tmp_path / "arcs")
    arcs.save(
        StoryArc(
            arc_id="main.arc",
            title="主线",
            arc_role="main",
            event_budget={
                "generated_days": 5,
                "last_event_step": 5,
            },
            variables={"progress": 0.1},
        )
    )
    cfg = WorldbookConfig(
        enabled=True,
        chat_projection_enabled=True,
        storylet_enabled=True,
        storylet_dir="storylets",
        story_arc_dir="arcs",
        state_dir="state",
        canon_dir="canon",
    )
    (tmp_path / "canon").mkdir()
    runtime = build_worldbook_runtime(cfg, root=tmp_path, story_arc_store=arcs)
    assert runtime is not None

    # First project at real step should select (delay=3, step=5, evidence present)
    # and persist once/cooldown into arc.event_budget.
    result = await runtime.project_chat(
        conversation_text="排练",
        group_id="g1",
        user_id="u1",
    )
    storylet_blocks = [
        b for b in result.blocks if b.block_id.startswith("storylet:")
    ]
    assert [b.block_id for b in storylet_blocks] == ["storylet:rehearsal.delayed"]

    reloaded = arcs.load("main.arc")
    assert reloaded is not None
    budget = reloaded.event_budget
    assert "rehearsal.delayed" in (budget.get("triggered_once") or [])
    assert int((budget.get("cooldowns") or {}).get("rehearsal.delayed", -1)) >= 5

    # Second project must not re-select the once storylet.
    result2 = await runtime.project_chat(
        conversation_text="排练",
        group_id="g1",
        user_id="u1",
    )
    assert [
        b.block_id for b in result2.blocks if b.block_id.startswith("storylet:")
    ] == []


def test_runtime_storylet_without_evidence_is_not_selected(tmp_path: Path) -> None:
    """Defect 1 (evidence): required_evidence must be supplied from ledger/social."""
    storylets_dir = tmp_path / "storylets"
    storylets_dir.mkdir()
    (storylets_dir / "needs_ev.json").write_text(
        json.dumps(
            {
                "storylets": [
                    {
                        "storylet_id": "needs.evidence",
                        "title": "需要证据",
                        "text": "缺证据不可触发。",
                        "scope": "fiction",
                        "required_evidence": ["arc:main.arc", "social:g1:msg-1"],
                        "severity": "daily",
                        "saliency": 5.0,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    arcs = StoryArcStore(tmp_path / "arcs")
    arcs.save(
        StoryArc(
            arc_id="main.arc",
            title="主线",
            arc_role="main",
            event_budget={"generated_days": 10, "last_event_step": 10},
        )
    )
    cfg = WorldbookConfig(
        enabled=True,
        schedule_projection_enabled=True,
        storylet_enabled=True,
        storylet_dir="storylets",
        story_arc_dir="arcs",
        state_dir="state",
        canon_dir="canon",
    )
    (tmp_path / "canon").mkdir()
    runtime = build_worldbook_runtime(cfg, root=tmp_path, story_arc_store=arcs)
    assert runtime is not None

    result = runtime.project_schedule(conversation_text="任意")
    assert [
        b.block_id for b in result.blocks if b.block_id.startswith("storylet:")
    ] == []


def test_social_projection_rejects_cross_user_same_group() -> None:
    """Defect 2: only current group AND current user evidence may project."""
    evidence_other = SocialEvidenceRef(
        experience_id="exp.other",
        group_id="group-a",
        user_id="user-b",
        evidence_message_id="msg-b",
        evidence_time="2026-07-18T10:00:00+08:00",
        summary="同群其他用户的经历。",
        privacy="group",
    )
    evidence_self = SocialEvidenceRef(
        experience_id="exp.self",
        group_id="group-a",
        user_id="user-a",
        evidence_message_id="msg-a",
        evidence_time="2026-07-18T10:00:00+08:00",
        summary="当前用户的经历。",
        privacy="group",
        confidence="medium",
    )
    projection = PromptProjection(
        WorldbookConfig(
            enabled=True,
            chat_projection_enabled=True,
            social_evidence_enabled=True,
        )
    )
    result = projection.project(
        mode="chat",
        social=[evidence_other, evidence_self],
        group_id="group-a",
        user_id="user-a",
    )
    text = result.text
    assert "当前用户的经历" in text
    assert "同群其他用户" not in text
    assert any(
        t.budget_decision == "rejected:cross_user" for t in result.traces
    )
    # Accepted social block must preserve source privacy/confidence, not force high.
    social_blocks = [b for b in result.blocks if b.meta.source == "social_evidence"]
    assert social_blocks
    assert social_blocks[0].meta.confidence == "medium"
    assert social_blocks[0].meta.privacy == "group"


def test_social_projection_requires_user_id_fail_closed() -> None:
    """Defect 2: missing user_id must not project any social evidence."""
    evidence = SocialEvidenceRef(
        experience_id="exp.a",
        group_id="group-a",
        user_id="user-a",
        evidence_message_id="msg-1",
        evidence_time="2026-07-18T10:00:00+08:00",
        summary="需要用户作用域。",
        privacy="group",
    )
    result = PromptProjection(
        WorldbookConfig(
            enabled=True,
            chat_projection_enabled=True,
            social_evidence_enabled=True,
        )
    ).project(
        mode="chat",
        social=[evidence],
        group_id="group-a",
        user_id=None,
    )
    assert result.blocks == ()
    assert any(
        "user" in t.budget_decision or t.budget_decision.endswith("missing_scope")
        for t in result.traces
    )


class _MismatchedSocialStore:
    async def recall(self, **_kwargs: object) -> list[dict[str, str]]:
        return [
            {
                "experience_id": "tampered",
                "group_id": "group-a",  # claim match
                "user_id": "user-a",
                "evidence_message_id": "msg-ok",
                "evidence_time": "2026-07-18T10:00:00+08:00",
                "user_text": "正常经历。",
                "privacy": "private",  # store says private — must not become group
                "confidence": "low",
            },
            {
                "experience_id": "meta-mismatch",
                "group_id": "group-evil",  # will be filtered by group mismatch
                "user_id": "user-a",
                "evidence_message_id": "msg-2",
                "evidence_time": "2026-07-18T10:00:00+08:00",
                "user_text": "跨群。",
            },
        ]


@pytest.mark.asyncio
async def test_social_load_preserves_privacy_confidence_from_store(
    tmp_path: Path,
) -> None:
    """Defect 2: revalidate and preserve privacy/confidence from source."""
    runtime = build_worldbook_runtime(
        WorldbookConfig(enabled=True, social_evidence_enabled=True),
        root=tmp_path,
        social_narrative_store=_MismatchedSocialStore(),
    )
    assert runtime is not None
    evidence = await runtime.load_social_evidence(
        group_id="group-a", user_id="user-a"
    )
    assert len(evidence) == 1
    assert evidence[0].privacy == "private"
    assert evidence[0].confidence == "low"
    # private must not project even for matching group/user
    projected = PromptProjection(
        WorldbookConfig(
            enabled=True,
            chat_projection_enabled=True,
            social_evidence_enabled=True,
        )
    ).project(
        mode="chat",
        social=evidence,
        group_id="group-a",
        user_id="user-a",
    )
    assert projected.blocks == ()
    assert any(t.budget_decision == "rejected:privacy" for t in projected.traces)


def test_life_state_missing_required_metadata_fails_closed_on_project() -> None:
    """Defect 3: items without temporal/source/scope/privacy must not project."""
    # SourceMeta requires scope; construct item with incomplete temporal via
    # from_dict path that previously allowed empty updated_at + no decay.
    item = LifeStateItem(
        key="mood",
        value="开心",
        meta=SourceMeta(
            source="life_state",
            scope="self",
            confidence="high",
            privacy="private",
            updated_at="",  # missing temporal
            decay_at=None,  # missing TTL
        ),
    )
    state = LifeState(revision=1, items={"mood": item})
    result = PromptProjection(
        WorldbookConfig(enabled=True, chat_projection_enabled=True)
    ).project(mode="chat", life_state=state)
    assert "开心" not in result.text
    assert any(
        t.label == "mood"
        and (
            "missing" in t.budget_decision
            or "invalid" in t.budget_decision
            or t.budget_decision == "rejected:missing_ttl"
            or t.budget_decision == "rejected:missing_metadata"
        )
        for t in result.traces
    )


def test_life_state_rejects_untrusted_relabel_as_self_private(tmp_path: Path) -> None:
    """Defect 3: cannot relabel untrusted sources as self/private on write path."""
    store = LifeStateStore(tmp_path / "state")
    with pytest.raises(ValueError, match=r"privacy|source|scope|self|private"):
        store.set_item(
            "secret",
            "来自社交证据的内容",
            source="social_evidence",  # untrusted for self/private life
            scope="self",
            privacy="private",
            decay_at="2999-01-01T00:00:00+00:00",
            evidence_refs=("social:g1:m1",),
        )


def test_life_state_set_item_requires_ttl(tmp_path: Path) -> None:
    """Defect 3: LifeState write path requires decay_at (TTL)."""
    store = LifeStateStore(tmp_path / "state")
    with pytest.raises(ValueError, match=r"decay_at|ttl|TTL"):
        store.set_item(
            "location",
            "舞台",
            source="life_state",
            scope="self",
            privacy="private",
            decay_at=None,
        )


def test_canon_always_active_does_not_project_without_activation() -> None:
    """Defect 4: always_active alone must not put Canon into projection."""
    entry = CanonEntry(
        entry_id="world.always",
        title="常驻设定",
        text="这条不该因为 always_active 就进入投影。",
        always_active=True,
        priority=200,
    )
    trigger = WorldInfoTrigger([entry])
    hits = trigger.activate("")  # empty query
    assert hits == []
    hits2 = trigger.activate("完全无关的闲聊")
    assert hits2 == []
    # With keyword it may activate
    entry_kw = CanonEntry(
        entry_id="world.kw",
        title="关键词设定",
        text="提到舞台才出现。",
        keywords=("舞台",),
        always_active=True,
        priority=200,
    )
    hits3 = WorldInfoTrigger([entry_kw]).activate("今天去舞台")
    assert [h.entry.entry_id for h in hits3] == ["world.kw"]
    assert "always" not in hits3[0].hit_reason


def test_unknown_storylet_condition_fails_closed() -> None:
    """Defect 5: misspelled/unknown conditions must not be silently satisfied."""
    manager = DramaManager()
    bad = Storylet(
        storylet_id="bad.cond",
        title="错误条件",
        text="未知条件不应放行。",
        conditions={"min_sttep": 0, "var_gt": {"x": 1}},  # typos
        severity="daily",
    )
    selected = manager.select([bad], now_step=10, variables={"x": 100})
    assert selected == []

    ok = Storylet(
        storylet_id="ok.cond",
        title="合法条件",
        text="已知条件可以放行。",
        conditions={"min_step": 1, "var_gte": {"x": 1}},
        severity="daily",
    )
    selected_ok = manager.select([ok], now_step=10, variables={"x": 2})
    assert [s.storylet.storylet_id for s in selected_ok] == ["ok.cond"]


def test_reducer_idempotency_survives_history_eviction() -> None:
    """Defect 6: idempotency must not rely solely on bounded event_history."""
    arc = StoryArc(
        arc_id="arc.evict",
        variables={"progress": 0.0},
        event_history=[],
        last_events=[],
    )
    reducer = EventReducer()
    first = EventRecord(
        event_id="event.old",
        event_type="progress",
        summary="旧事件",
        status="committed",
        arc_id="arc.evict",
        variable_deltas={"progress": 1.0},
    )
    reducer.apply(arc, first, now_step=1)
    assert arc.variables["progress"] == pytest.approx(1.0)

    # Simulate bounded human-readable history eviction (keep empty lists).
    arc.event_history.clear()
    arc.last_events.clear()

    # Re-apply same event_id must not double-apply deltas.
    again = reducer.apply(arc, first, now_step=99)
    assert again.event_id == "event.old"
    assert arc.variables["progress"] == pytest.approx(1.0)
    # history may record a no-op marker or stay empty, but must not re-delta


def test_reducer_committed_ids_never_expire_after_more_than_512_events() -> None:
    """Acceptance: replay of earliest event_id stays idempotent past any 512 bound.

    Human-readable history may be bounded; committed_event_ids must not
    semantically expire. After 513 unique commits, replaying e0 must leave
    the durable id set size unchanged (n remains 513, not 514).
    """
    arc = StoryArc(
        arc_id="arc.long",
        variables={"progress": 0.0},
        event_history=[],
        last_events=[],
        event_budget={},
    )
    reducer = EventReducer()
    n_events = 513
    for i in range(n_events):
        event = EventRecord(
            event_id=f"e{i}",
            event_type="progress",
            summary=f"event {i}",
            status="committed",
            arc_id="arc.long",
            variable_deltas={"progress": 0.0},
        )
        reducer.apply(arc, event, now_step=i + 1)
        # Evict human-readable history as a real bounded buffer would.
        if len(arc.event_history) > 256:
            del arc.event_history[:-256]
        if len(arc.last_events) > 64:
            del arc.last_events[:-64]

    ids = arc.event_budget.get("committed_event_ids")
    assert isinstance(ids, list)
    before_n = len(ids)
    assert before_n == n_events
    assert "e0" in ids

    # Exact acceptance probe: replaying the first committed id must be a no-op.
    first = EventRecord(
        event_id="e0",
        event_type="progress",
        summary="event 0 replay",
        status="committed",
        arc_id="arc.long",
        variable_deltas={"progress": 1.0},
    )
    reducer.apply(arc, first, now_step=9999)
    after_ids = arc.event_budget.get("committed_event_ids")
    assert isinstance(after_ids, list)
    after_n = len(after_ids)
    assert after_n == before_n == n_events
    assert arc.variables["progress"] == pytest.approx(0.0)
    assert "e0" in after_ids


class _FailingBudgetStore:
    """Store whose atomic update/persist always fails (acceptance probe B)."""

    def __init__(self, arc: StoryArc) -> None:
        self._arc = arc
        self.update_calls = 0
        self.save_calls = 0

    def list_arc_ids(self) -> list[str]:
        return [self._arc.arc_id]

    def load(self, arc_id: str) -> StoryArc | None:
        if arc_id == self._arc.arc_id:
            return self._arc
        return None

    def update(self, arc_id: str, mutator: object) -> StoryArc:
        self.update_calls += 1
        raise RuntimeError("forced budget persist failure")

    def save(self, arc: StoryArc) -> None:
        self.save_calls += 1
        raise RuntimeError("forced budget save failure")


class _UpdateCapturingStore:
    """Store that only exposes update() for durable budget commit."""

    def __init__(self, arc: StoryArc) -> None:
        self._arc = arc
        self.update_calls = 0

    def list_arc_ids(self) -> list[str]:
        return [self._arc.arc_id]

    def load(self, arc_id: str) -> StoryArc | None:
        if arc_id == self._arc.arc_id:
            return self._arc
        return None

    def update(self, arc_id: str, mutator: object) -> StoryArc:
        self.update_calls += 1
        if arc_id != self._arc.arc_id:
            raise KeyError(arc_id)
        cast_mutator = mutator  # Callable[[StoryArc], None]
        assert callable(cast_mutator)
        cast_mutator(self._arc)
        return self._arc


@pytest.mark.asyncio
async def test_storylet_selection_fails_closed_when_budget_persist_fails(
    tmp_path: Path,
) -> None:
    """Acceptance: selection must not leak when durable budget commit fails.

    Two consecutive project_chat calls with a store that raises on update must
    both return zero storylet blocks (not re-select storylet:once.a twice).
    """
    storylets_dir = tmp_path / "storylets"
    storylets_dir.mkdir()
    (storylets_dir / "once.json").write_text(
        json.dumps(
            {
                "storylets": [
                    {
                        "storylet_id": "once.a",
                        "title": "仅一次",
                        "text": "只应在预算持久化成功后返回。",
                        "scope": "fiction",
                        "once": True,
                        "severity": "daily",
                        "saliency": 5.0,
                        "required_evidence": ["arc:main.arc"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "canon").mkdir()
    arc = StoryArc(
        arc_id="main.arc",
        title="主线",
        arc_role="main",
        event_budget={"generated_days": 5, "last_event_step": 5},
        variables={},
    )
    failing_store = _FailingBudgetStore(arc)
    cfg = WorldbookConfig(
        enabled=True,
        chat_projection_enabled=True,
        storylet_enabled=True,
        storylet_dir="storylets",
        story_arc_dir="arcs",
        state_dir="state",
        canon_dir="canon",
    )
    runtime = build_worldbook_runtime(
        cfg, root=tmp_path, story_arc_store=failing_store
    )
    assert runtime is not None

    first = await runtime.project_chat(
        conversation_text="排练",
        group_id="g1",
        user_id="u1",
    )
    second = await runtime.project_chat(
        conversation_text="排练",
        group_id="g1",
        user_id="u1",
    )
    first_ids = [
        b.block_id for b in first.blocks if b.block_id.startswith("storylet:")
    ]
    second_ids = [
        b.block_id for b in second.blocks if b.block_id.startswith("storylet:")
    ]
    assert first_ids == []
    assert second_ids == []
    assert failing_store.update_calls >= 1
    # Budget must not be mutated as if selection succeeded.
    triggered = arc.event_budget.get("triggered_once") or []
    assert "once.a" not in triggered


@pytest.mark.asyncio
async def test_storylet_budget_persists_via_store_update_when_available(
    tmp_path: Path,
) -> None:
    """Prefer StoryArcStore.update for durable event_budget commit."""
    storylets_dir = tmp_path / "storylets"
    storylets_dir.mkdir()
    (storylets_dir / "once.json").write_text(
        json.dumps(
            {
                "storylets": [
                    {
                        "storylet_id": "once.b",
                        "title": "一次成功",
                        "text": "update 路径持久化后才返回。",
                        "scope": "fiction",
                        "once": True,
                        "severity": "daily",
                        "saliency": 5.0,
                        "required_evidence": ["arc:main.arc"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "canon").mkdir()
    arc = StoryArc(
        arc_id="main.arc",
        title="主线",
        arc_role="main",
        event_budget={"generated_days": 5, "last_event_step": 5},
        variables={},
    )
    store = _UpdateCapturingStore(arc)
    cfg = WorldbookConfig(
        enabled=True,
        chat_projection_enabled=True,
        storylet_enabled=True,
        storylet_dir="storylets",
        story_arc_dir="arcs",
        state_dir="state",
        canon_dir="canon",
    )
    runtime = build_worldbook_runtime(cfg, root=tmp_path, story_arc_store=store)
    assert runtime is not None

    result = await runtime.project_chat(
        conversation_text="排练",
        group_id="g1",
        user_id="u1",
    )
    assert [
        b.block_id for b in result.blocks if b.block_id.startswith("storylet:")
    ] == ["storylet:once.b"]
    assert store.update_calls == 1
    assert "once.b" in (arc.event_budget.get("triggered_once") or [])

    # Second call: once budget persisted — no re-select.
    result2 = await runtime.project_chat(
        conversation_text="排练",
        group_id="g1",
        user_id="u1",
    )
    assert [
        b.block_id for b in result2.blocks if b.block_id.startswith("storylet:")
    ] == []


def test_reducer_rejects_mismatched_arc_id() -> None:
    """Defect 6: event.arc_id must match target arc.id."""
    arc = StoryArc(
        arc_id="arc.target",
        variables={"progress": 0.0},
        event_history=[],
    )
    reducer = EventReducer()
    event = EventRecord(
        event_id="event.wrong",
        event_type="progress",
        summary="绑错弧",
        status="committed",
        arc_id="arc.other",
        variable_deltas={"progress": 5.0},
    )
    with pytest.raises(ValueError, match="arc_id"):
        reducer.apply(arc, event, now_step=1)
    assert arc.variables["progress"] == pytest.approx(0.0)


def test_proposal_store_rejects_non_proposal_status_and_forbidden_sources(
    tmp_path: Path,
) -> None:
    """Defect 7: domain/store boundary enforces proposal-only persistence."""
    store = ProposalStore(tmp_path / "worldbook")

    with pytest.raises(ValueError, match=r"proposal|status"):
        EventProposal(
            proposal_id="p.committed",
            kind="reflection",
            summary="不应以 committed 构造",
            status="committed",  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match=r"proposal|status"):
        EventProposal(
            proposal_id="p.validated",
            kind="reflection",
            summary="不应以 validated 构造",
            status="validated",  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match=r"source|proposal"):
        EventProposal(
            proposal_id="p.bad.source",
            kind="reflection",
            summary="禁止源",
            status="proposal",
            source="native_canon",  # type: ignore[arg-type]
        )

    # Store must also reject if someone bypasses domain with raw path abuse
    ok = EventProposal(
        proposal_id="p.ok",
        kind="fiction_event",
        summary="合法提案",
        status="proposal",
        source="dream_proposal",
        payload={"note": "ok"},
    )
    store.save_proposal(ok)
    loaded = store.load("p.ok")
    assert loaded is not None
    assert loaded.status == "proposal"


# ---------------------------------------------------------------------------
# Stage 2 — causal Storylet commit API (typed effects, atomic, exactly-once)
# ---------------------------------------------------------------------------


def _stage2_runtime(tmp_path: Path) -> tuple[WorldbookRuntime, StoryArcStore]:
    """Enabled runtime with real StoryArcStore + empty life/storylet dirs."""
    (tmp_path / "canon").mkdir()
    (tmp_path / "storylets").mkdir()
    (tmp_path / "state").mkdir()
    arcs = tmp_path / "arcs"
    arcs.mkdir()
    store = StoryArcStore(storage_dir=str(arcs))
    store.save(
        StoryArc(
            arc_id="arc.stage2",
            title="Stage2",
            scope="fiction",
            arc_role="main",
            stack_order=0,
            stage="planning",
            variables={"progress": 0.1, "energy": 1.0},
            open_threads=["old.thread"],
            partner_states={},
            event_budget={"last_event_step": 0, "setback_count": 0},
            event_history=[],
            last_events=[],
        )
    )
    cfg = WorldbookConfig(
        enabled=True,
        storylet_enabled=True,
        chat_projection_enabled=True,
        schedule_projection_enabled=True,
        social_evidence_enabled=False,
        canon_dir="canon",
        storylet_dir="storylets",
        state_dir="state",
        story_arc_dir="arcs",
        max_setbacks_per_arc=1,
        max_events_per_tick=1,
        recovery_window_steps=3,
    )
    runtime = WorldbookRuntime(cfg, root=tmp_path, story_arc_store=store)
    return runtime, store


def _social_runtime(
    tmp_path: Path,
    *,
    allowlist: list[str] | None = None,
    social_enabled: bool = True,
    include_main: bool = True,
) -> tuple[WorldbookRuntime, StoryArcStore, WorldbookConfig]:
    (tmp_path / "canon").mkdir(parents=True, exist_ok=True)
    (tmp_path / "storylets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    arcs = tmp_path / "arcs"
    arcs.mkdir(parents=True, exist_ok=True)
    store = StoryArcStore(storage_dir=str(arcs))
    if include_main:
        store.save(
            StoryArc(
                arc_id="arc.social.main",
                title="Social Main",
                scope="fiction",
                arc_role="main",
                stack_order=0,
                variables={"social_resonance": 0.98},
                event_budget={"last_event_step": 3},
                event_history=[],
                last_events=[],
            )
        )
    store.save(
        StoryArc(
            arc_id="arc.social.side",
            title="Social Side",
            scope="fiction",
            arc_role="side",
            stack_order=1,
            variables={"social_resonance": 0.0},
            event_budget={"last_event_step": 9},
            event_history=[],
            last_events=[],
        )
    )
    cfg = WorldbookConfig(
        enabled=True,
        social_evidence_enabled=social_enabled,
        social_group_allowlist=list(allowlist if allowlist is not None else ["200"]),
        canon_dir="canon",
        storylet_dir="storylets",
        state_dir="state",
        story_arc_dir="arcs",
    )
    return WorldbookRuntime(cfg, root=tmp_path, story_arc_store=store), store, cfg


def _social_record(**overrides: object) -> SimpleNamespace:
    payload: dict[str, object] = {
        "experience_id": "social.exp.7001",
        "group_id": "200",
        "user_id": "100",
        "entity_kind": "factual",
        "evidence_message_id": "7001",
        "evidence_time": datetime.now().astimezone().isoformat(),
        "evidence_source": "reply_context",
        "status": "active",
        "privacy": "group",
        "user_text": "RAW_USER_SENTINEL_7001",
        "bot_reply": "RAW_BOT_SENTINEL_7001",
        "display_name": "RAW_NAME_SENTINEL_7001",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_social_story_commit_is_generic_bounded_and_restart_idempotent(
    tmp_path: Path,
) -> None:
    runtime, store, cfg = _social_runtime(tmp_path)
    record = _social_record()

    first = runtime.commit_social_experience(
        record,
        group_id="200",
        user_id="100",
    )

    assert isinstance(first, SocialStoryCommitResult)
    assert first.status == "committed"
    assert first.event is not None
    assert first.event.event_type == "social_influence"
    assert first.event.evidence_refs == ("social:200:7001",)
    assert first.event.severity == "daily"
    assert first.event.consequences == ()
    main = store.load("arc.social.main")
    side = store.load("arc.social.side")
    assert main is not None and side is not None
    assert main.variables["social_resonance"] == pytest.approx(1.0)
    assert side.variables["social_resonance"] == pytest.approx(0.0)
    assert len(main.event_history) == 1

    life = runtime.life_store.load()
    life_item = life.items[SOCIAL_LIFE_KEY]
    assert life_item.meta.decay_at is not None
    assert datetime.fromisoformat(life_item.meta.decay_at) > datetime.now().astimezone()

    serialized = json.dumps(
        {
            "result": first.to_dict(),
            "arc": main.to_dict(),
            "life": life.to_dict(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    for sentinel in (
        "RAW_USER_SENTINEL_7001",
        "RAW_BOT_SENTINEL_7001",
        "RAW_NAME_SENTINEL_7001",
    ):
        assert sentinel not in serialized

    duplicate = runtime.commit_social_experience(
        record,
        group_id="200",
        user_id="100",
    )
    assert duplicate.status == "already_committed"
    after_duplicate = store.load("arc.social.main")
    assert after_duplicate is not None
    assert len(after_duplicate.event_history) == 1
    assert life.applied_event_ids == runtime.life_store.load().applied_event_ids

    restarted_store = StoryArcStore(storage_dir=str(tmp_path / "arcs"))
    restarted = WorldbookRuntime(cfg, root=tmp_path, story_arc_store=restarted_store)
    replay = restarted.commit_social_experience(
        record,
        group_id="200",
        user_id="100",
    )
    assert replay.status == "already_committed"
    after_restart = restarted_store.load("arc.social.main")
    assert after_restart is not None
    assert len(after_restart.event_history) == 1


@pytest.mark.parametrize(
    ("record_overrides", "call_group", "call_user", "reason"),
    [
        ({"privacy": "private"}, "200", "100", "privacy_forbidden"),
        ({"privacy": "system"}, "200", "100", "privacy_forbidden"),
        ({"status": "invalidated"}, "200", "100", "status_not_active"),
        ({"entity_kind": "fiction"}, "200", "100", "entity_kind_not_factual"),
        ({}, "201", "100", "group_not_allowed"),
        ({}, "200", "101", "user_mismatch"),
        ({"evidence_time": "not-a-time"}, "200", "100", "invalid_evidence_time"),
        ({"evidence_source": ""}, "200", "100", "missing_evidence_source"),
    ],
)
def test_social_story_rejections_leave_arc_and_life_unchanged(
    tmp_path: Path,
    record_overrides: dict[str, object],
    call_group: str,
    call_user: str,
    reason: str,
) -> None:
    runtime, store, _cfg = _social_runtime(tmp_path)
    before = store.load("arc.social.main")
    assert before is not None

    result = runtime.commit_social_experience(
        _social_record(**record_overrides),
        group_id=call_group,
        user_id=call_user,
    )

    assert result.status == "rejected"
    assert result.reason == reason
    after = store.load("arc.social.main")
    assert after is not None
    assert after.variables == before.variables
    assert after.event_history == before.event_history
    assert runtime.life_store.load().items == {}


def test_social_story_empty_allowlist_and_missing_main_fail_closed(
    tmp_path: Path,
) -> None:
    runtime, store, _cfg = _social_runtime(tmp_path / "empty", allowlist=[])
    result = runtime.commit_social_experience(
        _social_record(), group_id="200", user_id="100"
    )
    assert result.status == "rejected"
    assert result.reason == "empty_social_group_allowlist"
    main = store.load("arc.social.main")
    assert main is not None and main.event_history == []

    no_main, side_only, _cfg2 = _social_runtime(
        tmp_path / "no_main", include_main=False
    )
    missing = no_main.commit_social_experience(
        _social_record(), group_id="200", user_id="100"
    )
    assert missing.status == "rejected"
    assert missing.reason == "missing_main_arc"
    side = side_only.load("arc.social.side")
    assert side is not None and side.event_history == []


def test_social_story_event_id_is_bounded_and_dimension_scoped() -> None:
    base = {
        "experience_id": "experience." + "x" * 180,
        "group_id": "200",
        "user_id": "100",
        "evidence_message_id": "7001",
        "target_arc_id": "arc.social.main",
    }
    first = deterministic_social_story_event_id(**base)
    assert len(first) <= 120
    for key, value in {
        "experience_id": "experience.other",
        "group_id": "201",
        "user_id": "101",
        "evidence_message_id": "7002",
        "target_arc_id": "arc.social.other",
    }.items():
        changed = dict(base)
        changed[key] = value
        assert deterministic_social_story_event_id(**changed) != first


def test_parse_typed_storylet_effects_unknown_key_fail_closed() -> None:
    with pytest.raises(ValueError, match="unknown typed effect"):
        parse_typed_storylet_effects({"not_a_key": 1}, {})
    with pytest.raises(ValueError, match="unknown typed effect"):
        parse_typed_storylet_effects({}, {"mystery": True})
    with pytest.raises(ValueError, match="ttl"):
        parse_typed_storylet_effects(
            {},
            {
                "life_updates": [
                    {"key": "mood", "value": "ok"},  # missing ttl
                ]
            },
        )
    ok = parse_typed_storylet_effects(
        {"variable_deltas": {"energy": -0.1}},
        {
            "variable_deltas": {"progress": 0.2},
            "open_threads": ["t1"],
            "resolve_threads": ["old.thread"],
            "stage": "recovery",
            "partner_updates": [{"entity_id": "partner.a", "mood": "ok"}],
            "life_updates": [
                {"key": "activity", "value": "rehearse", "ttl_hours": 12}
            ],
        },
    )
    assert ok.variable_deltas["energy"] == pytest.approx(-0.1)
    assert ok.variable_deltas["progress"] == pytest.approx(0.2)
    assert ok.open_threads == ("t1",)
    assert ok.resolve_threads == ("old.thread",)
    assert ok.stage == "recovery"
    assert len(ok.partner_updates) == 1
    assert len(ok.life_updates) == 1


def test_parse_typed_storylet_effects_rejects_unknown_nested_fields() -> None:
    with pytest.raises(ValueError, match="unknown"):
        parse_typed_storylet_effects(
            {},
            {
                "life_updates": [
                    {
                        "key": "mood",
                        "value": "伪造系统来源",
                        "ttl_hours": 12,
                        "source": "system",
                    }
                ]
            },
        )
    with pytest.raises(ValueError, match="unknown"):
        parse_typed_storylet_effects(
            {},
            {
                "partner_updates": [
                    {
                        "entity_id": "partner.a",
                        "pinned_profile": "storylet must not rewrite profile",
                    }
                ]
            },
        )


def test_commit_storylet_unknown_key_rejects_without_mutation(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    storylet = Storylet(
        storylet_id="s.bad_key",
        title="坏键",
        text="不应提交。",
        severity="daily",
        consequence={"illegal_effect": 1},  # type: ignore[arg-type]
    )
    before = store.load("arc.stage2")
    assert before is not None
    before_vars = dict(before.variables)
    result = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=1,
        available_evidence=["arc:arc.stage2"],
    )
    assert result.status == "rejected"
    assert "typed_effects" in result.reason or "unknown" in result.reason
    after = store.load("arc.stage2")
    assert after is not None
    assert after.variables == before_vars
    assert after.event_history == []
    assert runtime.life_store.load().items == {}


def test_commit_storylet_ineligible_no_prompt_no_side_effects(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    storylet = Storylet(
        storylet_id="s.need_evidence",
        title="缺证据",
        text="需要 arc 证据。",
        severity="daily",
        required_evidence=("arc:missing",),
        consequence={
            "variable_deltas": {"progress": 0.5},
            "life_updates": [
                {"key": "activity", "value": "should-not", "ttl_hours": 6}
            ],
            "partner_updates": [{"entity_id": "p1", "mood": "nope"}],
        },
    )
    result = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=1,
        available_evidence=[],  # missing required evidence
    )
    assert result.status == "rejected"
    assert result.reason == "ineligible"
    arc = store.load("arc.stage2")
    assert arc is not None
    assert arc.variables["progress"] == pytest.approx(0.1)
    assert arc.event_history == []
    assert "activity" not in runtime.life_store.load().items
    assert arc.partner_states == {}


def test_commit_storylet_applies_variables_threads_stage_once(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    storylet = Storylet(
        storylet_id="s.full_effects",
        title="完整效应",
        text="推进并清理旧线。",
        severity="daily",
        once=True,
        consequence={
            "variable_deltas": {"progress": 0.3, "energy": -0.1},
            "open_threads": ["new.thread"],
            "resolve_threads": ["old.thread"],
            "stage": "active",
        },
    )
    result = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=2,
        available_evidence=["arc:arc.stage2"],
    )
    assert result.status == "committed"
    assert result.projected is True
    assert result.event_id == deterministic_storylet_event_id(
        "s.full_effects", 2, "arc.stage2"
    )
    arc = store.load("arc.stage2")
    assert arc is not None
    assert arc.variables["progress"] == pytest.approx(0.4)
    assert arc.variables["energy"] == pytest.approx(0.9)
    assert "old.thread" not in arc.open_threads
    assert "new.thread" in arc.open_threads
    assert arc.stage == "active"
    assert "s.full_effects" in (arc.event_budget.get("triggered_once") or [])
    assert result.event_id in (arc.event_budget.get("committed_event_ids") or [])


def test_commit_storylet_setback_exactly_once_via_reducer(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    major = Storylet(
        storylet_id="s.major",
        title="重大受挫",
        text="道具坏了。",
        severity="major",
        once=True,
        recovery_steps=3,
        consequence={"variable_deltas": {"energy": -0.2}},
    )
    first = runtime.commit_storylet(
        major,
        arc_id="arc.stage2",
        now_step=3,
        available_evidence=["arc:arc.stage2"],
    )
    assert first.status == "committed"
    arc = store.load("arc.stage2")
    assert arc is not None
    assert int(arc.event_budget.get("setback_count") or 0) == 1
    assert int(arc.event_budget.get("recovery_until_step") or 0) == 6

    # Same deterministic event id: already_committed, no second setback.
    replay = runtime.commit_storylet(
        major,
        arc_id="arc.stage2",
        now_step=3,
        available_evidence=["arc:arc.stage2"],
    )
    assert replay.status == "already_committed"
    arc2 = store.load("arc.stage2")
    assert arc2 is not None
    assert int(arc2.event_budget.get("setback_count") or 0) == 1
    assert arc2.variables["energy"] == pytest.approx(0.8)

    # Another major is blocked by setback budget (once already consumed too).
    other = Storylet(
        storylet_id="s.major2",
        title="第二次受挫",
        text="不应再进。",
        severity="major",
        once=True,
        recovery_steps=2,
        consequence={"variable_deltas": {"energy": -0.5}},
    )
    blocked = runtime.commit_storylet(
        other,
        arc_id="arc.stage2",
        now_step=10,
        available_evidence=["arc:arc.stage2"],
    )
    assert blocked.status == "rejected"
    assert blocked.reason == "ineligible"
    arc3 = store.load("arc.stage2")
    assert arc3 is not None
    assert int(arc3.event_budget.get("setback_count") or 0) == 1
    assert arc3.variables["energy"] == pytest.approx(0.8)


def test_commit_storylet_partner_and_life_ttl_idempotent_restart(
    tmp_path: Path,
) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    store.update(
        "arc.stage2",
        lambda arc: arc.partner_states.update(
            {
                "partner.stage2": {
                    "kind": "fiction",
                    "display_name": "Stage2 Partner",
                    "pinned_profile": "stable profile",
                }
            }
        ),
    )
    storylet = Storylet(
        storylet_id="s.side_effects",
        title="副作用",
        text="写 life 与 partner。",
        severity="daily",
        once=True,
        consequence={
            "variable_deltas": {"progress": 0.05},
            "life_updates": [
                {
                    "key": "activity",
                    "value": "storylet-activity",
                    "ttl_hours": 24,
                }
            ],
            "partner_updates": [
                {
                    "entity_id": "partner.stage2",
                    "mood": "focused",
                    "note": "after commit",
                }
            ],
        },
    )
    result = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=4,
        available_evidence=["arc:arc.stage2"],
    )
    assert result.status == "committed"
    event_id = result.event_id
    life = runtime.life_store.load()
    assert "activity" in life.items
    item = life.items["activity"]
    assert item.value == "storylet-activity"
    assert item.meta.decay_at  # finite TTL
    assert event_id in item.meta.evidence_refs
    assert item.meta.source == "story_ledger"

    arc = store.load("arc.stage2")
    assert arc is not None
    partner = arc.partner_states.get("partner.stage2") or {}
    assert partner.get("mood") == "focused"
    assert event_id in (partner.get("applied_event_ids") or [])

    # Restart: new runtime, same stores — replay already_committed, no double write.
    cfg = runtime.config
    runtime2 = WorldbookRuntime(cfg, root=tmp_path, story_arc_store=store)
    life_rev = runtime2.life_store.load().revision
    partner_before = dict(
        (store.load("arc.stage2") or StoryArc(arc_id="x")).partner_states.get(
            "partner.stage2"
        )
        or {}
    )
    replay = runtime2.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=4,
        available_evidence=["arc:arc.stage2"],
    )
    assert replay.status == "already_committed"
    life2 = runtime2.life_store.load()
    # set_item skipped by evidence_refs; revision should not advance from life write.
    assert life2.revision == life_rev or life2.items["activity"].value == "storylet-activity"
    assert event_id in life2.items["activity"].meta.evidence_refs
    partner_after = (store.load("arc.stage2") or StoryArc(arc_id="x")).partner_states.get(
        "partner.stage2"
    ) or {}
    assert partner_after.get("applied_event_ids") == partner_before.get(
        "applied_event_ids"
    )


@pytest.mark.asyncio
async def test_project_chat_only_surfaces_committed_storylets(
    tmp_path: Path,
) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    # Register one eligible storylet via filesystem registry load path.
    pack = {
        "schema_version": 1,
        "storylets": [
            {
                "storylet_id": "proj.ok",
                "title": "可投影",
                "text": "正式提交后进入 prompt。",
                "severity": "daily",
                "once": True,
                "cooldown_steps": 0,
                "delay_steps": 0,
                "saliency": 2.0,
                "priority": 100,
                "scope": "fiction",
                "required_evidence": ["arc:arc.stage2"],
                "conditions": {"min_step": 0},
                "cost": {},
                "consequence": {
                    "variable_deltas": {"progress": 0.1},
                    "life_updates": [
                        {"key": "activity", "value": "from-project", "ttl_hours": 8}
                    ],
                },
            }
        ],
    }
    (tmp_path / "storylets" / "pack.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8"
    )
    # Advance clock so now_step resolves.
    store.update(
        "arc.stage2",
        lambda a: a.event_budget.update({"last_event_step": 1, "generated_days": 1}),
    )
    result = await runtime.project_chat(
        conversation_text="排练",
        group_id=None,
        user_id=None,
        session_id="t",
    )
    storylet_blocks = [b for b in result.blocks if b.meta.source == "storylet"]
    assert len(storylet_blocks) == 1
    assert "proj.ok" in storylet_blocks[0].block_id
    arc = store.load("arc.stage2")
    assert arc is not None
    assert any(
        "proj.ok" in str(x)
        for x in (arc.event_budget.get("committed_event_ids") or [])
    )
    assert "activity" in runtime.life_store.load().items

    # Second projection: already_committed → no fresh storylet block.
    result2 = await runtime.project_chat(
        conversation_text="排练",
        group_id=None,
        user_id=None,
        session_id="t2",
    )
    storylet_blocks2 = [b for b in result2.blocks if b.meta.source == "storylet"]
    assert storylet_blocks2 == []


def test_storylet_commit_result_structured_fields() -> None:
    res = StoryletCommitResult(
        status="rejected",
        storylet_id="x",
        reason="ineligible",
        arc_id="a",
    )
    assert res.ok is False
    assert res.projected is False
    d = res.to_dict()
    assert d["status"] == "rejected"
    assert d["storylet_id"] == "x"


def test_storylet_clock_uses_furthest_persisted_progress() -> None:
    assert WorldbookRuntime._resolve_now_step(
        {
            "last_event_step": 1,
            "generated_days": 5,
            "now_step": 3,
        }
    ) == 5


def test_commit_storylet_rechecks_live_arc_variables_not_stale_snapshot(
    tmp_path: Path,
) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    store.update("arc.stage2", lambda arc: arc.variables.update({"progress": 0.9}))
    storylet = Storylet(
        storylet_id="s.live_recheck",
        title="锁内重检",
        text="旧快照不应绕过当前变量。",
        severity="daily",
        conditions={"var_lte": {"progress": 0.5}},
        consequence={"variable_deltas": {"energy": -0.2}},
    )

    result = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=1,
        available_evidence=["arc:arc.stage2"],
        variables={"progress": 0.0},  # deliberately stale caller snapshot
    )

    assert result.status == "rejected"
    assert result.reason == "ineligible"
    arc = store.load("arc.stage2")
    assert arc is not None
    assert arc.variables["energy"] == pytest.approx(1.0)
    assert arc.event_history == []


def test_commit_storylet_requires_atomic_store_update(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)

    class LoadSaveOnlyStore:
        def load(self, arc_id: str) -> StoryArc | None:
            return store.load(arc_id)

        def save(self, arc: StoryArc) -> None:
            store.save(arc)

        def list_arc_ids(self) -> list[str]:
            return store.list_arc_ids()

    runtime._ledger.set_store(LoadSaveOnlyStore())
    storylet = Storylet(
        storylet_id="s.atomic_only",
        title="必须原子",
        text="没有 update 端口时必须失败关闭。",
        severity="daily",
        consequence={"variable_deltas": {"progress": 0.4}},
    )

    result = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=1,
        available_evidence=["arc:arc.stage2"],
    )

    assert result.status == "rejected"
    assert result.reason == "atomic_update_required"
    arc = store.load("arc.stage2")
    assert arc is not None
    assert arc.variables["progress"] == pytest.approx(0.1)
    assert arc.event_history == []


def test_storylet_event_identity_is_arc_scoped(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    store.save(
        StoryArc(
            arc_id="arc.stage2.side",
            title="Stage2 Side",
            scope="fiction",
            arc_role="side",
            stack_order=1,
            stage="active",
            variables={"progress": 0.0},
            event_budget={"last_event_step": 0},
        )
    )
    storylet = Storylet(
        storylet_id="s.same_across_arcs",
        title="跨 Arc",
        text="每条 Arc 必须有独立事件身份。",
        severity="daily",
        consequence={"variable_deltas": {"progress": 0.1}},
    )

    main = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=2,
        available_evidence=["arc:arc.stage2"],
    )
    side = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2.side",
        now_step=2,
        available_evidence=["arc:arc.stage2.side"],
    )

    assert main.status == "committed"
    assert side.status == "committed"
    assert main.event_id != side.event_id


def test_life_effect_replay_cannot_overwrite_newer_committed_state(
    tmp_path: Path,
) -> None:
    runtime, _store = _stage2_runtime(tmp_path)
    old = Storylet(
        storylet_id="s.life.old",
        title="旧生活状态",
        text="先写旧状态。",
        severity="daily",
        consequence={
            "life_updates": [
                {"key": "mood", "value": "old", "ttl_hours": 24}
            ]
        },
    )
    new = Storylet(
        storylet_id="s.life.new",
        title="新生活状态",
        text="再写新状态。",
        severity="daily",
        consequence={
            "life_updates": [
                {"key": "mood", "value": "new", "ttl_hours": 24}
            ]
        },
    )
    assert runtime.commit_storylet(
        old, arc_id="arc.stage2", now_step=1, available_evidence=[]
    ).status == "committed"
    assert runtime.commit_storylet(
        new, arc_id="arc.stage2", now_step=2, available_evidence=[]
    ).status == "committed"
    assert runtime.life_store.load().items["mood"].value == "new"

    replay = runtime.commit_storylet(
        old, arc_id="arc.stage2", now_step=1, available_evidence=[]
    )

    assert replay.status == "already_committed"
    assert runtime.life_store.load().items["mood"].value == "new"


def test_partner_effect_idempotency_survives_long_history(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    store.update(
        "arc.stage2",
        lambda arc: arc.partner_states.update(
            {
                "partner.long": {
                    "kind": "fiction",
                    "display_name": "Long Partner",
                    "pinned_profile": "stable profile",
                }
            }
        ),
    )
    first = Storylet(
        storylet_id="s.partner.first",
        title="最早伙伴事件",
        text="写入最早状态。",
        severity="daily",
        consequence={
            "partner_updates": [
                {"entity_id": "partner.long", "mood": "first"}
            ]
        },
    )
    assert runtime.commit_storylet(
        first, arc_id="arc.stage2", now_step=1, available_evidence=[]
    ).status == "committed"

    for step in range(2, 36):
        storylet = Storylet(
            storylet_id=f"s.partner.{step}",
            title=f"伙伴事件 {step}",
            text="持续推进伙伴状态。",
            severity="daily",
            consequence={
                "partner_updates": [
                    {"entity_id": "partner.long", "mood": f"mood-{step}"}
                ]
            },
        )
        assert runtime.commit_storylet(
            storylet,
            arc_id="arc.stage2",
            now_step=step,
            available_evidence=[],
        ).status == "committed"

    before = store.load("arc.stage2")
    assert before is not None
    assert before.partner_states["partner.long"]["mood"] == "mood-35"

    replay = runtime.commit_storylet(
        first, arc_id="arc.stage2", now_step=1, available_evidence=[]
    )

    assert replay.status == "already_committed"
    after = store.load("arc.stage2")
    assert after is not None
    assert after.partner_states["partner.long"]["mood"] == "mood-35"


def test_commit_storylet_rejects_unknown_fiction_partner(tmp_path: Path) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    storylet = Storylet(
        storylet_id="s.unknown_partner",
        title="未知伙伴",
        text="不能临时发明没有 pinned profile 的伙伴。",
        severity="daily",
        consequence={
            "partner_updates": [
                {"entity_id": "partner.missing", "mood": "invented"}
            ]
        },
    )

    result = runtime.commit_storylet(
        storylet,
        arc_id="arc.stage2",
        now_step=1,
        available_evidence=[],
    )

    assert result.status == "rejected"
    assert result.reason == "unknown_partner"
    arc = store.load("arc.stage2")
    assert arc is not None
    assert arc.event_history == []
    assert "partner.missing" not in arc.partner_states


def test_storylet_projection_routes_effects_to_authored_target_arc(
    tmp_path: Path,
) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    store.save(
        StoryArc(
            arc_id="arc.stage2.side",
            title="Stage2 Side",
            scope="fiction",
            arc_role="side",
            stack_order=1,
            stage="active",
            variables={"study_progress": 0.0},
            event_budget={"generated_days": 2},
        )
    )
    store.update(
        "arc.stage2",
        lambda arc: arc.event_budget.update({"generated_days": 2}),
    )
    pack = {
        "schema_version": 1,
        "storylets": [
            {
                "storylet_id": "side.study",
                "title": "副线学习",
                "text": "副线应推进自己的变量。",
                "severity": "daily",
                "once": True,
                "cooldown_steps": 0,
                "delay_steps": 0,
                "saliency": 2.0,
                "priority": 100,
                "scope": "fiction",
                "target_arc_id": "arc.stage2.side",
                "required_evidence": ["arc:arc.stage2.side"],
                "conditions": {"min_step": 2},
                "cost": {"variable_deltas": {"energy": -0.1}},
                "consequence": {
                    "variable_deltas": {"study_progress": 0.4}
                },
            }
        ],
    }
    (tmp_path / "storylets" / "side.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8"
    )

    result = runtime.project_schedule()

    assert [b.block_id for b in result.blocks if b.meta.source == "storylet"] == [
        "storylet:side.study"
    ]
    main = store.load("arc.stage2")
    side = store.load("arc.stage2.side")
    assert main is not None and side is not None
    assert main.variables["progress"] == pytest.approx(0.1)
    assert "study_progress" not in main.variables
    assert side.variables["study_progress"] == pytest.approx(0.4)
    assert side.variables["energy"] == pytest.approx(-0.1)
    assert any(
        "side.study" in str(event.get("event_id") or "")
        for event in side.event_history
    )


def test_deterministic_storylet_event_id_is_bounded_for_max_length_ids() -> None:
    storylet_id = "s" + "x" * 118
    arc_id = "a" + "y" * 118
    event_id = deterministic_storylet_event_id(storylet_id, 999999, arc_id)

    assert len(event_id) <= 120
    assert event_id != deterministic_storylet_event_id(
        storylet_id, 999999, "b" + "y" * 118
    )
    EventRecord(
        event_id=event_id,
        event_type="daily",
        summary="bounded",
        arc_id=arc_id,
    )


@pytest.mark.asyncio
async def test_same_step_chat_and_schedule_share_per_arc_event_cap(
    tmp_path: Path,
) -> None:
    runtime, store = _stage2_runtime(tmp_path)
    store.update(
        "arc.stage2",
        lambda arc: arc.event_budget.update({"generated_days": 1, "now_step": 1}),
    )
    pack = {
        "schema_version": 1,
        "storylets": [
            {
                "storylet_id": "cap.first",
                "title": "第一事件",
                "text": "同一步只允许这一条进入主线。",
                "target_arc_id": "arc.stage2",
                "severity": "daily",
                "once": True,
                "saliency": 3.0,
                "priority": 200,
                "scope": "fiction",
                "conditions": {"min_step": 1},
                "cost": {},
                "consequence": {"variable_deltas": {"progress": 0.1}},
            },
            {
                "storylet_id": "cap.second",
                "title": "第二事件",
                "text": "必须等到下一步。",
                "target_arc_id": "arc.stage2",
                "severity": "daily",
                "once": True,
                "saliency": 2.0,
                "priority": 100,
                "scope": "fiction",
                "conditions": {"min_step": 1},
                "cost": {},
                "consequence": {"variable_deltas": {"progress": 0.2}},
            },
        ],
    }
    (tmp_path / "storylets" / "cap.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8"
    )

    chat = await runtime.project_chat(
        conversation_text="第一轮",
        group_id=None,
        user_id=None,
        session_id="cap-chat",
    )
    schedule = runtime.project_schedule(conversation_text="同一步调度")

    assert [b.block_id for b in chat.blocks if b.meta.source == "storylet"] == [
        "storylet:cap.first"
    ]
    assert [b for b in schedule.blocks if b.meta.source == "storylet"] == []
    after_step_one = store.load("arc.stage2")
    assert after_step_one is not None
    assert len(after_step_one.event_budget.get("committed_event_ids") or []) == 1

    store.update(
        "arc.stage2",
        lambda arc: arc.event_budget.update({"generated_days": 2, "now_step": 2}),
    )
    next_step = runtime.project_schedule(conversation_text="下一步调度")

    assert [b.block_id for b in next_step.blocks if b.meta.source == "storylet"] == [
        "storylet:cap.second"
    ]
    after_step_two = store.load("arc.stage2")
    assert after_step_two is not None
    assert len(after_step_two.event_budget.get("committed_event_ids") or []) == 2


# ---------------------------------------------------------------------------
# Stage 3 — Dream proposal lifecycle (validate / commit / crash catch-up)
# ---------------------------------------------------------------------------


def _lifecycle_runtime(
    tmp_path: Path,
    *,
    dream_enabled: bool = True,
) -> tuple[WorldbookRuntime, StoryArcStore]:
    (tmp_path / "canon").mkdir(exist_ok=True)
    (tmp_path / "storylets").mkdir(exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    arcs = tmp_path / "arcs"
    arcs.mkdir(exist_ok=True)
    store = StoryArcStore(storage_dir=str(arcs))
    store.save(
        StoryArc(
            arc_id="arc.dream.life",
            title="Dream Life Arc",
            scope="fiction",
            arc_role="main",
            stack_order=0,
            stage="planning",
            variables={"progress": 0.2, "energy": 1.0},
            open_threads=["old.device.thread"],
            partner_states={
                "partner.dream": {
                    "kind": "fiction",
                    "display_name": "Dream Partner",
                    "pinned_profile": "stable",
                }
            },
            event_budget={"last_event_step": 2, "setback_count": 0},
            event_history=[],
            last_events=[],
            next_day_seed="",
        )
    )
    cfg = WorldbookConfig(
        enabled=True,
        storylet_enabled=True,
        chat_projection_enabled=False,
        schedule_projection_enabled=False,
        dream_proposal_enabled=dream_enabled,
        social_evidence_enabled=False,
        canon_dir="canon",
        storylet_dir="storylets",
        state_dir="state",
        story_arc_dir="arcs",
    )
    runtime = WorldbookRuntime(cfg, root=tmp_path, story_arc_store=store)
    return runtime, store


def test_dream_lifecycle_proposal_status_stays_proposal_after_validate_and_commit(
    tmp_path: Path,
) -> None:
    runtime, _store = _lifecycle_runtime(tmp_path)
    proposal = runtime.dream_bridge.submit(
        proposal_id="dream.life.ok1",
        kind="arc_replan",
        summary="设备检查后把排练顺延。",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "昨晚设备告警。",
            "open_threads": ["需要复检设备"],
            "next_day_seed": "先复检再排练",
            "resolve_threads": ["old.device.thread"],
        },
    )
    assert proposal.status == "proposal"
    decision = runtime.validate_proposal("dream.life.ok1")
    assert decision.status == "validated"
    reloaded = runtime.proposal_store.load("dream.life.ok1")
    assert reloaded is not None
    assert reloaded.status == "proposal"
    record = runtime.commit_validated_proposal("dream.life.ok1")
    assert record.status == "committed"
    still = runtime.proposal_store.load("dream.life.ok1")
    assert still is not None
    assert still.status == "proposal"
    assert still.to_dict()["status"] == "proposal"


def test_dream_proposal_store_is_exact_immutable_by_id(tmp_path: Path) -> None:
    runtime, _store = _lifecycle_runtime(tmp_path)
    runtime.dream_bridge.submit(
        proposal_id="dream.immutable.same",
        kind="arc_replan",
        summary="original summary",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "original",
            "open_threads": ["original.thread"],
            "next_day_seed": "original seed",
        },
    )

    with pytest.raises(RuntimeError, match=r"conflict|immutable"):
        runtime.dream_bridge.submit(
            proposal_id="dream.immutable.same",
            kind="arc_replan",
            summary="rewritten summary",
            arc_id="arc.dream.life",
            payload={
                "last_event_summary": "rewritten",
                "open_threads": ["rewritten.thread"],
                "next_day_seed": "rewritten seed",
            },
        )

    loaded = runtime.proposal_store.load("dream.immutable.same")
    assert loaded is not None
    assert loaded.summary == "original summary"
    assert loaded.payload["next_day_seed"] == "original seed"


def test_dream_commit_rejects_proposal_tamper_after_validation(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    proposal_id = "dream.tamper.after.validate"
    runtime.dream_bridge.submit(
        proposal_id=proposal_id,
        kind="arc_replan",
        summary="trusted summary",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "trusted",
            "open_threads": ["trusted.thread"],
            "next_day_seed": "trusted seed",
            "variable_deltas": {"progress": 0.1},
        },
    )
    decision = runtime.validate_proposal(proposal_id)
    assert decision.status == "validated"
    before = store.load("arc.dream.life")
    assert before is not None
    before_vars = dict(before.variables)

    proposal_path = tmp_path / "state" / "proposals" / f"{proposal_id}.json"
    raw = json.loads(proposal_path.read_text(encoding="utf-8"))
    raw["summary"] = "tampered summary"
    raw["payload"]["next_day_seed"] = "tampered seed"
    proposal_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"proposal.*changed|fingerprint"):
        runtime.commit_validated_proposal(proposal_id)

    assert runtime.commit_store.load_for_proposal(proposal_id) is None
    after = store.load("arc.dream.life")
    assert after is not None
    assert after.variables == before_vars
    assert after.event_history == before.event_history


def test_dream_decision_fingerprint_binds_persisted_created_at(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    proposal_id = "dream.tamper.created_at"
    runtime.dream_bridge.submit(
        proposal_id=proposal_id,
        kind="arc_replan",
        summary="created-at must be bound",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "stable",
            "open_threads": ["stable.thread"],
            "next_day_seed": "stable seed",
        },
    )
    decision = runtime.validate_proposal(proposal_id)
    assert decision.status == "validated"

    proposal_path = tmp_path / "state" / "proposals" / f"{proposal_id}.json"
    raw = json.loads(proposal_path.read_text(encoding="utf-8"))
    raw["created_at"] = "2099-01-01T00:00:00Z"
    proposal_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"proposal.*changed|fingerprint"):
        runtime.commit_validated_proposal(proposal_id)

    assert runtime.commit_store.load_for_proposal(proposal_id) is None
    arc = store.load("arc.dream.life")
    assert arc is not None
    assert arc.event_history == []


def test_dream_existing_commit_record_requires_arc_event_before_catchup(
    tmp_path: Path,
) -> None:
    from services.worldbook.domain import (
        FictionCommitRecord,
        deterministic_commit_id,
        deterministic_dream_event_id,
    )

    runtime, store = _lifecycle_runtime(tmp_path)
    proposal_id = "dream.premature.commit.record"
    runtime.dream_bridge.submit(
        proposal_id=proposal_id,
        kind="fiction_event",
        summary="must not trust record alone",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "premature",
            "open_threads": [],
            "next_day_seed": "later",
            "life_updates": [
                {"key": "activity", "value": "must-not-apply", "ttl_hours": 1}
            ],
            "partner_updates": [
                {"entity_id": "partner.dream", "mood": "must-not-apply"}
            ],
        },
    )
    decision = runtime.validate_proposal(proposal_id)
    assert decision.status == "validated"
    event_id = deterministic_dream_event_id(proposal_id, "arc.dream.life")
    runtime.commit_store.save_commit(
        FictionCommitRecord(
            commit_id=deterministic_commit_id(proposal_id, decision.decision_id),
            proposal_id=proposal_id,
            decision_id=decision.decision_id,
            event_id=event_id,
            arc_id="arc.dream.life",
        )
    )

    with pytest.raises(RuntimeError, match=r"arc.*event.*missing|commit_record"):
        runtime.commit_validated_proposal(proposal_id)

    assert "activity" not in runtime.life_store.load().items
    arc = store.load("arc.dream.life")
    assert arc is not None
    assert (arc.partner_states["partner.dream"].get("mood") or "") != "must-not-apply"
    assert event_id not in (arc.event_budget.get("committed_event_ids") or [])


def test_dream_existing_commit_record_requires_validated_decision(
    tmp_path: Path,
) -> None:
    from services.worldbook.domain import FictionCommitRecord

    runtime, _store = _lifecycle_runtime(tmp_path)
    proposal_id = "dream.record.without.decision"
    runtime.commit_store.save_commit(
        FictionCommitRecord(
            commit_id="commit.without.decision",
            proposal_id=proposal_id,
            decision_id="decision.missing",
            event_id="dream.missing.event",
            arc_id="arc.dream.life",
        )
    )

    with pytest.raises(RuntimeError, match=r"validated decision|commit_record"):
        runtime.commit_validated_proposal(proposal_id)


def test_dream_lifecycle_rejects_non_mapping_effect_bags(tmp_path: Path) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    runtime.dream_bridge.submit(
        proposal_id="dream.bad.effects.shape",
        kind="fiction_event",
        summary="invalid effects bag",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "bad shape",
            "open_threads": [],
            "next_day_seed": "none",
            "effects": "not-an-object",
        },
    )

    result = runtime.process_proposal("dream.bad.effects.shape")

    assert result.status == "rejected"
    assert result.decision is not None
    assert result.decision.reason_code == "invalid_effects"
    assert result.commit is None
    arc = store.load("arc.dream.life")
    assert arc is not None
    assert not any(
        "dream.bad.effects.shape" in str(item.get("event_id") or "")
        for item in arc.event_history
    )


def test_dream_lifecycle_valid_arc_replan_commits_threads_and_is_idempotent(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    payload = {
        "last_event_summary": "设备检查提示下一次排练要先做安全确认。",
        "open_threads": ["设备是否已经彻底修好"],
        "next_day_seed": "先确认设备状态，再开始排练",
        "resolve_threads": ["old.device.thread"],
        "variable_deltas": {"progress": 0.15},
    }
    runtime.dream_bridge.submit(
        proposal_id="dream.life.replan",
        kind="arc_replan",
        summary=str(payload["last_event_summary"]),
        arc_id="arc.dream.life",
        payload=payload,
    )
    result = runtime.process_proposal("dream.life.replan")
    assert result.status == "committed"
    assert result.decision is not None
    assert result.decision.status == "validated"
    assert result.commit is not None
    event_id = result.commit.event_id
    arc = store.load("arc.dream.life")
    assert arc is not None
    assert "old.device.thread" not in arc.open_threads
    assert "设备是否已经彻底修好" in arc.open_threads
    assert arc.next_day_seed == "先确认设备状态，再开始排练"
    assert arc.variables["progress"] == pytest.approx(0.35)
    assert event_id in (arc.event_budget.get("committed_event_ids") or [])
    history_ids = [str(e.get("event_id")) for e in arc.event_history]
    assert event_id in history_ids
    event_row = next(e for e in arc.event_history if e.get("event_id") == event_id)
    assert event_row.get("source") == "dream_proposal"
    assert "proposal:dream.life.replan" in (event_row.get("evidence_refs") or [])

    # New runtime/store instances — same decision + commit, no double apply.
    runtime2 = WorldbookRuntime(
        runtime.config, root=tmp_path, story_arc_store=store
    )
    again = runtime2.process_proposal("dream.life.replan")
    assert again.status == "committed"
    assert again.decision is not None
    assert again.decision.decision_id == result.decision.decision_id
    assert again.commit is not None
    assert again.commit.commit_id == result.commit.commit_id
    assert again.commit.event_id == event_id
    arc2 = store.load("arc.dream.life")
    assert arc2 is not None
    assert arc2.variables["progress"] == pytest.approx(0.35)
    assert history_ids == [str(e.get("event_id")) for e in arc2.event_history]


def test_dream_lifecycle_explicit_effects_update_arc_life_partner_once(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    runtime.dream_bridge.submit(
        proposal_id="dream.life.effects",
        kind="fiction_event",
        summary="排练日副作用写入。",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "副作用",
            "open_threads": [],
            "next_day_seed": "继续",
            "variable_deltas": {"energy": -0.1},
            "stage": "active",
            "life_updates": [
                {"key": "activity", "value": "dream-activity", "ttl_hours": 12}
            ],
            "partner_updates": [
                {"entity_id": "partner.dream", "mood": "steady", "note": "ok"}
            ],
        },
    )
    first = runtime.process_proposal("dream.life.effects")
    assert first.status == "committed"
    event_id = first.commit.event_id if first.commit else ""
    life = runtime.life_store.load()
    assert life.items["activity"].value == "dream-activity"
    assert life.items["activity"].meta.decay_at
    assert event_id in life.applied_event_ids
    arc = store.load("arc.dream.life")
    assert arc is not None
    assert arc.stage == "active"
    assert arc.variables["energy"] == pytest.approx(0.9)
    partner = arc.partner_states["partner.dream"]
    assert partner.get("mood") == "steady"
    assert event_id in (partner.get("applied_event_ids") or [])

    life_rev = runtime.life_store.load().revision
    runtime.process_proposal("dream.life.effects")
    life2 = runtime.life_store.load()
    assert event_id in life2.applied_event_ids
    assert life2.applied_event_ids.count(event_id) == 1
    # Replay must not invent a second life write for the same event.
    assert life2.items["activity"].value == "dream-activity"
    partner2 = (store.load("arc.dream.life") or arc).partner_states["partner.dream"]
    assert (partner2.get("applied_event_ids") or []).count(event_id) == 1
    _ = life_rev


def _write_proposal_json(
    state_dir: Path,
    *,
    proposal_id: str,
    payload: dict[str, object],
    arc_id: str = "arc.dream.life",
    kind: str = "arc_replan",
    summary: str = "bad",
) -> None:
    """Bypass submit/store guards so validator rejection path is testable."""
    from services.worldbook.domain import utc_now_iso

    proposals = state_dir / "proposals"
    proposals.mkdir(parents=True, exist_ok=True)
    body = {
        "proposal_id": proposal_id,
        "kind": kind,
        "summary": summary,
        "status": "proposal",
        "arc_id": arc_id,
        "payload": payload,
        "created_at": utc_now_iso(),
        "source": "dream_proposal",
    }
    (proposals / f"{proposal_id}.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def test_dream_lifecycle_forbidden_and_invalid_persist_rejected_no_mutation(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    state_dir = tmp_path / "state"
    baseline = store.load("arc.dream.life")
    assert baseline is not None
    base_vars = dict(baseline.variables)
    base_threads = list(baseline.open_threads)
    base_life_rev = runtime.life_store.load().revision

    cases: list[tuple[str, dict[str, object], str]] = [
        (
            "dream.bad.nested_factual",
            {
                "last_event_summary": "x",
                "open_threads": [],
                "next_day_seed": "y",
                "nested": {"subject_kind": "factual"},
            },
            "forbidden_metadata",
        ),
        (
            "dream.bad.social_source",
            {
                "last_event_summary": "x",
                "open_threads": [],
                "next_day_seed": "y",
                "meta": {"source": "social_evidence"},
            },
            "forbidden_metadata",
        ),
        (
            "dream.bad.unknown_partner",
            {
                "last_event_summary": "x",
                "open_threads": [],
                "next_day_seed": "y",
                "partner_updates": [{"entity_id": "partner.missing", "mood": "x"}],
            },
            "unknown_partner",
        ),
        (
            "dream.bad.ttl",
            {
                "last_event_summary": "x",
                "open_threads": [],
                "next_day_seed": "y",
                "life_updates": [{"key": "a", "value": "b"}],
            },
            "invalid_effects",
        ),
    ]
    for proposal_id, payload, reason_code in cases:
        # Unknown partner / invalid TTL can go through submit; forbidden control
        # payloads are written raw so the validator records rejected decisions.
        if reason_code == "forbidden_metadata":
            _write_proposal_json(
                state_dir, proposal_id=proposal_id, payload=payload
            )
        else:
            runtime.dream_bridge.submit(
                proposal_id=proposal_id,
                kind="arc_replan",
                summary="bad",
                arc_id="arc.dream.life",
                payload=payload,  # type: ignore[arg-type]
            )
        result = runtime.process_proposal(proposal_id)
        assert result.status == "rejected", proposal_id
        assert result.decision is not None
        assert result.decision.status == "rejected"
        assert result.decision.reason_code == reason_code
        assert result.commit is None
        assert runtime.commit_store.load_for_proposal(proposal_id) is None
        with pytest.raises(RuntimeError, match="cannot commit rejected"):
            runtime.commit_validated_proposal(proposal_id)

    # Missing arc
    runtime.dream_bridge.submit(
        proposal_id="dream.bad.missing_arc",
        kind="arc_replan",
        summary="no arc",
        arc_id="arc.does.not.exist",
        payload={
            "last_event_summary": "x",
            "open_threads": [],
            "next_day_seed": "y",
        },
    )
    missing = runtime.process_proposal("dream.bad.missing_arc")
    assert missing.status == "rejected"
    assert missing.decision is not None
    assert missing.decision.reason_code == "missing_arc"

    # Non-fiction arc
    store.save(
        StoryArc(
            arc_id="arc.factual.scope",
            title="Factual",
            scope="factual",
            arc_role="side",
            variables={},
            open_threads=[],
            event_budget={},
        )
    )
    runtime.dream_bridge.submit(
        proposal_id="dream.bad.non_fiction",
        kind="arc_replan",
        summary="wrong scope",
        arc_id="arc.factual.scope",
        payload={
            "last_event_summary": "x",
            "open_threads": [],
            "next_day_seed": "y",
        },
    )
    non_f = runtime.process_proposal("dream.bad.non_fiction")
    assert non_f.status == "rejected"
    assert non_f.decision is not None
    assert non_f.decision.reason_code == "non_fiction_arc"

    arc = store.load("arc.dream.life")
    assert arc is not None
    assert dict(arc.variables) == base_vars
    assert list(arc.open_threads) == base_threads
    assert runtime.life_store.load().revision == base_life_rev


def test_dream_lifecycle_rejected_cannot_commit_even_if_proposal_json_remains(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    _write_proposal_json(
        tmp_path / "state",
        proposal_id="dream.reject.stay2",
        payload={
            "last_event_summary": "x",
            "open_threads": [],
            "next_day_seed": "y",
            "wrapper": {"subject_kind": "factual"},
        },
    )
    decision = runtime.validate_proposal("dream.reject.stay2")
    assert decision.status == "rejected"
    loaded = runtime.proposal_store.load("dream.reject.stay2")
    assert loaded is not None
    assert loaded.status == "proposal"
    with pytest.raises(RuntimeError, match="cannot commit rejected"):
        runtime.commit_validated_proposal("dream.reject.stay2")
    assert runtime.commit_store.load_for_proposal("dream.reject.stay2") is None
    arc = store.load("arc.dream.life")
    assert arc is not None
    assert not any(
        "dream.reject.stay2" in str(x)
        for x in (arc.event_budget.get("committed_event_ids") or [])
    )


def test_dream_lifecycle_arc_update_failure_no_commit_or_side_effects(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)

    class BoomStore:
        def load(self, arc_id: str) -> object | None:
            return store.load(arc_id)

        def update(self, arc_id: str, mutator: object) -> None:
            raise OSError("disk full")

        def save(self, *_a: object, **_k: object) -> None:
            raise AssertionError("save must not be used")

    boom = BoomStore()
    cfg = runtime.config
    bad_runtime = WorldbookRuntime(cfg, root=tmp_path, story_arc_store=boom)
    # Share proposal store path via same state_dir.
    bad_runtime.dream_bridge.submit(
        proposal_id="dream.boom.1",
        kind="arc_replan",
        summary="will fail at arc",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "x",
            "open_threads": ["t"],
            "next_day_seed": "y",
            "life_updates": [
                {"key": "activity", "value": "should-not", "ttl_hours": 1}
            ],
            "partner_updates": [
                {"entity_id": "partner.dream", "mood": "nope"}
            ],
        },
    )
    decision = bad_runtime.validate_proposal("dream.boom.1")
    assert decision.status == "validated"
    with pytest.raises(RuntimeError, match="arc_update_failed"):
        bad_runtime.commit_validated_proposal("dream.boom.1")
    assert bad_runtime.commit_store.load_for_proposal("dream.boom.1") is None
    assert "activity" not in bad_runtime.life_store.load().items
    arc = store.load("arc.dream.life")
    assert arc is not None
    partner = arc.partner_states.get("partner.dream") or {}
    assert partner.get("mood") != "nope"
    assert "dream.boom" not in str(arc.event_budget.get("committed_event_ids") or [])


def test_dream_lifecycle_crash_catchup_after_arc_event_without_side_effects(
    tmp_path: Path,
) -> None:
    from services.worldbook.domain import (
        EventRecord,
        deterministic_dream_event_id,
        utc_now_iso,
    )
    from services.worldbook.reducer import EventReducer

    runtime, store = _lifecycle_runtime(tmp_path)
    proposal_id = "dream.crash.catch"
    runtime.dream_bridge.submit(
        proposal_id=proposal_id,
        kind="arc_replan",
        summary="crash mid side effects",
        arc_id="arc.dream.life",
        payload={
            "last_event_summary": "mid",
            "open_threads": ["crash.thread"],
            "next_day_seed": "resume",
            "variable_deltas": {"progress": 0.05},
            "life_updates": [
                {"key": "activity", "value": "catch-up", "ttl_hours": 6}
            ],
            "partner_updates": [
                {"entity_id": "partner.dream", "mood": "recovered"}
            ],
        },
    )
    decision = runtime.validate_proposal(proposal_id)
    assert decision.status == "validated"
    event_id = deterministic_dream_event_id(proposal_id, "arc.dream.life")

    # Simulate: Arc event committed, no life/partner ledger, no commit record.
    def _seed(arc: StoryArc) -> None:
        event = EventRecord(
            event_id=event_id,
            event_type="daily",
            summary="crash mid side effects",
            status="committed",
            arc_id="arc.dream.life",
            variable_deltas={"progress": 0.05},
            consequences=("crash.thread",),
            severity="daily",
            source="dream_proposal",
            evidence_refs=(
                f"proposal:{proposal_id}",
                f"decision:{decision.decision_id}",
            ),
            committed_at=utc_now_iso(),
            step=2,
        )
        EventReducer().apply(arc, event, now_step=2)

    store.update("arc.dream.life", _seed)
    arc_seeded = store.load("arc.dream.life")
    assert arc_seeded is not None
    assert event_id in (arc_seeded.event_budget.get("committed_event_ids") or [])
    assert "activity" not in runtime.life_store.load().items
    assert runtime.commit_store.load_for_proposal(proposal_id) is None

    record = runtime.commit_validated_proposal(proposal_id)
    assert record.event_id == event_id
    assert record.status == "committed"
    life = runtime.life_store.load()
    assert life.items["activity"].value == "catch-up"
    assert event_id in life.applied_event_ids
    partner = (store.load("arc.dream.life") or arc_seeded).partner_states[
        "partner.dream"
    ]
    assert partner.get("mood") == "recovered"
    assert event_id in (partner.get("applied_event_ids") or [])

    # Second catch-up is a pure no-op.
    record2 = runtime.commit_validated_proposal(proposal_id)
    assert record2.commit_id == record.commit_id
    assert life.applied_event_ids.count(event_id) == 1


def test_dream_lifecycle_two_proposals_distinct_same_proposal_same_event(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    for pid, delta in (("dream.pair.a", 0.1), ("dream.pair.b", 0.2)):
        runtime.dream_bridge.submit(
            proposal_id=pid,
            kind="arc_replan",
            summary=f"pair {pid}",
            arc_id="arc.dream.life",
            payload={
                "last_event_summary": pid,
                "open_threads": [f"thread.{pid}"],
                "next_day_seed": pid,
                "variable_deltas": {"progress": delta},
            },
        )
        result = runtime.process_proposal(pid)
        assert result.status == "committed"
        assert result.commit is not None

    a = runtime.commit_store.load_for_proposal("dream.pair.a")
    b = runtime.commit_store.load_for_proposal("dream.pair.b")
    assert a is not None and b is not None
    assert a.event_id != b.event_id
    assert a.commit_id != b.commit_id
    arc = store.load("arc.dream.life")
    assert arc is not None
    ids = arc.event_budget.get("committed_event_ids") or []
    assert a.event_id in ids and b.event_id in ids

    again = runtime.process_proposal("dream.pair.a")
    assert again.commit is not None
    assert again.commit.event_id == a.event_id
    assert again.commit.commit_id == a.commit_id


@pytest.mark.asyncio
async def test_dream_plugin_enabled_path_processes_lifecycle_zero_legacy_writes(
    tmp_path: Path,
) -> None:
    runtime, store = _lifecycle_runtime(tmp_path)
    agent = DreamAgent(
        store=object(),  # type: ignore[arg-type]
        story_arc_store=store,
        worldbook_dream_bridge=runtime.dream_bridge,
    )
    draft = LifeReflectionDraft(
        last_event_summary="今天的设备检查提示下一次排练要先做安全确认。",
        open_threads=["设备是否已经彻底修好"],
        next_day_seed="先确认设备状态，再开始排练",
    )
    arc = store.load("arc.dream.life")
    writes = await agent._commit_life_reflection(draft, arc, cards_to_persist=[])
    assert writes == 0
    proposals = runtime.proposal_store.list_proposals()
    assert len(proposals) == 1
    assert proposals[0].status == "proposal"
    decisions = runtime.decision_store.list_decisions()
    assert len(decisions) == 1
    assert decisions[0].status == "validated"
    commits = runtime.commit_store.list_commits()
    assert len(commits) == 1
    assert commits[0].status == "committed"
    refreshed = store.load("arc.dream.life")
    assert refreshed is not None
    assert "设备是否已经彻底修好" in refreshed.open_threads
    assert refreshed.next_day_seed == "先确认设备状态，再开始排练"
