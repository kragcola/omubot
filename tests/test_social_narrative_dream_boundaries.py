"""RED boundaries between factual Social Narrative and ordinary Dream state."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from kernel.types import PluginContext
from plugins.dream import DreamAgent
from plugins.dream.plugin import LifeReflectionCardDraft, LifeReflectionDraft
from plugins.schedule.generator import ScheduleGenerator
from plugins.schedule.story_arc import StoryArc
from plugins.schedule.types import Schedule, TimeSlot
from plugins.social_narrative.plugin import SocialNarrativeConfig, SocialNarrativePlugin
from services.memory.card_store import CardStore


@pytest.fixture
async def card_store(tmp_path) -> AsyncIterator[CardStore]:
    store = CardStore(str(tmp_path / "memory_cards.db"))
    await store.init()
    try:
        yield store
    finally:
        await store.close()


def _schedule() -> Schedule:
    return Schedule(
        date="2026-07-15",
        theme="排练复盘",
        day_narrative="只提供普通日程背景。",
        slots=[TimeSlot(
            time="20:00",
            activity="practice",
            description="复盘排练",
            mood_hint="专注",
        )],
    )


def _arc() -> StoryArc:
    return StoryArc(
        arc_id="factual-boundary",
        title="虚构舞台主线",
        variables={"reflection_group_id": "200"},
        last_events=[{"date": "2026-07-14", "summary": "既有事件"}],
        open_threads=["既有线程"],
        next_day_seed="既有 seed",
    )


class _ScheduleStore:
    def __init__(self, schedule: Schedule | None = None) -> None:
        self.schedule = schedule
        self.saved: list[Schedule] = []

    def load(self, _date: str, *, update_current: bool = True) -> Schedule | None:
        del update_current
        return self.schedule

    def save(self, schedule: Schedule) -> None:
        self.saved.append(schedule)


class _StoryStore:
    def __init__(self, arc: StoryArc) -> None:
        self.arc = arc
        self.update_calls = 0
        self.save_calls = 0

    def load_active(self) -> StoryArc:
        return self.arc

    def update(self, arc_id: str, mutator: Any) -> StoryArc:
        assert arc_id == self.arc.arc_id
        self.update_calls += 1
        mutator(self.arc)
        return self.arc

    def save(self, arc: StoryArc) -> None:
        self.save_calls += 1
        self.arc = arc


class _FactualContextProvider:
    def __init__(self, *, eligible_groups: set[str] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self._eligible_groups = {
            str(group_id).strip()
            for group_id in (eligible_groups if eligible_groups is not None else {"200"})
            if str(group_id).strip()
        }

    def is_group_reflection_eligible(self, group_id: str | None) -> bool:
        normalized = str(group_id or "").strip()
        return (
            bool(normalized)
            and normalized.isdecimal()
            and int(normalized) > 0
            and normalized in self._eligible_groups
        )

    async def build_group_reflection_context(
        self,
        *,
        group_id: str,
        limit: int = 12,
    ) -> str:
        if not self.is_group_reflection_eligible(group_id):
            return ""
        self.calls.append((group_id, limit))
        return (
            "【严格群事实】group=200；entity_kind=factual；证据=7001；"
            "共同经历：真人事实不应逃逸到 Dream 卡或 StoryArc"
        )


async def test_group_factual_reflection_has_zero_card_arc_and_schedule_escape(
    card_store: CardStore,
) -> None:
    provider = _FactualContextProvider()
    story_store = _StoryStore(_arc())
    arc_before = story_store.arc.to_dict()
    schedule_store = _ScheduleStore(_schedule())
    agent = DreamAgent(
        store=card_store,
        life_reflection_enabled=True,
        schedule_store=schedule_store,
        story_arc_store=story_store,
        social_narrative_store=provider,
        reflection_allowed_group_ids={"200"},
    )
    seen_requests: list[str] = []

    async def api_call(
        _system: list[Any],
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        del tools, max_tokens
        seen_requests.append(str(messages[0]["content"]))
        return {
            "text": json.dumps({
                "cards": [
                    {
                        "scope": "global",
                        "scope_id": "global",
                        "category": "status",
                        "content": "经历洞察：真人事实不应逃逸到 global 卡。",
                    },
                    {
                        "scope": "group",
                        "scope_id": "200",
                        "category": "relationship",
                        "content": "经历洞察：真人事实不应逃逸到 group 卡。",
                    },
                ],
                "last_event_summary": "真人事实不应逃逸到 arc event",
                "open_threads": ["真人事实不应逃逸到 arc thread"],
                "next_day_seed": "真人事实不应逃逸到 arc seed",
            }, ensure_ascii=False),
            "tool_uses": [],
        }

    writes = await agent._run_life_reflection(api_call)
    leaked_cards = await card_store.search_cards("真人事实不应逃逸", limit=10)
    schedule_generator = ScheduleGenerator(
        store=schedule_store,  # type: ignore[arg-type]
        memory_card_store=card_store,
        event_replan_enabled=True,
    )
    schedule_context = await schedule_generator._build_reflection_insight_context()

    assert provider.calls == [("200", 12)]
    assert seen_requests and "【严格群事实】group=200" in seen_requests[0]
    assert writes == 0, "factual social context is read-only input to Dream"
    assert leaked_cards == []
    assert story_store.update_calls == 0
    assert story_store.save_calls == 0
    assert story_store.arc.to_dict() == arc_before
    assert schedule_context == ""


class _RawHistoricalSocialStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def build_group_reflection_context(
        self,
        *,
        group_id: str,
        limit: int = 12,
    ) -> str:
        self.calls.append((group_id, limit))
        return "RAW HISTORICAL FACTUAL DATA"


async def test_dream_direct_raw_social_store_cannot_bypass_adapter_gate(
    card_store: CardStore,
) -> None:
    raw_store = _RawHistoricalSocialStore()
    agent = DreamAgent(
        store=card_store,
        social_narrative_store=raw_store,
        reflection_allowed_group_ids={"200"},
    )

    assert await agent._load_social_reflection_context("200") == ""
    assert raw_store.calls == []


@pytest.mark.parametrize(
    ("enabled", "allowed_groups"),
    [
        (False, ["200"]),
        (True, []),
        (True, ["201"]),
    ],
    ids=["disabled", "empty-allowlist", "group-removed"],
)
async def test_dream_social_context_must_pass_adapter_owned_gate(
    card_store: CardStore,
    enabled: bool,
    allowed_groups: list[str],
) -> None:
    raw_store = _RawHistoricalSocialStore()
    adapter = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=enabled,
        allowed_group_ids=allowed_groups,
    ))
    ctx = PluginContext(social_narrative_store=raw_store)
    await adapter.on_startup(ctx)
    provider = getattr(adapter, "build_group_reflection_context", None)
    assert callable(provider), (
        "SocialNarrativePlugin must own the gated Dream reflection provider"
    )
    assert hasattr(adapter, "is_group_reflection_eligible")
    assert adapter.is_group_reflection_eligible("200") is False
    agent = DreamAgent(
        store=card_store,
        social_narrative_store=adapter,
        reflection_allowed_group_ids={"200"},
    )
    try:
        assert agent._select_reflection_group_id(_arc()) == "global"
        context = await agent._load_social_reflection_context("200")
    finally:
        await adapter.on_shutdown(ctx)

    assert context == ""
    assert raw_store.calls == [], (
        "disabled, empty, or removed-group adapters must not read historical raw facts"
    )


async def test_allowlist_skew_runtime_allows_social_denies_selects_global(
    card_store: CardStore,
) -> None:
    """Runtime allowlist has 200; Social Narrative allowlist is 201 → global."""
    raw_store = _RawHistoricalSocialStore()
    adapter = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=True,
        allowed_group_ids=["201"],
    ))
    ctx = PluginContext(social_narrative_store=raw_store)
    await adapter.on_startup(ctx)
    agent = DreamAgent(
        store=card_store,
        life_reflection_enabled=True,
        schedule_store=_ScheduleStore(_schedule()),
        story_arc_store=_StoryStore(_arc()),
        social_narrative_store=adapter,
        reflection_allowed_group_ids={"200"},
    )
    try:
        assert adapter.is_group_reflection_eligible("200") is False
        assert agent._select_reflection_group_id(_arc()) == "global"
        assert await agent._load_social_reflection_context("200") == ""
    finally:
        await adapter.on_shutdown(ctx)
    assert raw_store.calls == []


async def test_allowlist_skew_social_allows_runtime_denies_selects_global(
    card_store: CardStore,
) -> None:
    """Social Narrative allows 200; runtime allowlist empty/missing → global."""
    raw_store = _RawHistoricalSocialStore()
    adapter = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=True,
        allowed_group_ids=["200"],
    ))
    ctx = PluginContext(social_narrative_store=raw_store)
    await adapter.on_startup(ctx)
    agent = DreamAgent(
        store=card_store,
        life_reflection_enabled=True,
        schedule_store=_ScheduleStore(_schedule()),
        story_arc_store=_StoryStore(_arc()),
        social_narrative_store=adapter,
        reflection_allowed_group_ids=set(),
    )
    try:
        assert adapter.is_group_reflection_eligible("200") is True
        assert agent._select_reflection_group_id(_arc()) == "global"
        # Selection is global; load path must not be fed the skewed group either
        # when callers respect selection. Direct non-selected load still gates.
        assert await agent._load_social_reflection_context("global") == ""
    finally:
        await adapter.on_shutdown(ctx)
    assert raw_store.calls == []


async def test_missing_store_and_non_numeric_ids_are_not_eligible(
    card_store: CardStore,
) -> None:
    adapter_missing_store = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=True,
        allowed_group_ids=["200"],
    ))
    ctx_missing = PluginContext(social_narrative_store=None)
    await adapter_missing_store.on_startup(ctx_missing)

    raw_store = _RawHistoricalSocialStore()
    adapter = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=True,
        allowed_group_ids=["200"],
    ))
    ctx = PluginContext(social_narrative_store=raw_store)
    await adapter.on_startup(ctx)
    try:
        assert adapter_missing_store.is_group_reflection_eligible("200") is False
        for bad_id in (None, "", "0", "-1", "wxs", "12.5"):
            assert adapter.is_group_reflection_eligible(bad_id) is False
        agent = DreamAgent(
            store=card_store,
            social_narrative_store=adapter,
            reflection_allowed_group_ids={"200", "0", "wxs"},
        )
        for bad_value in ("0", "-1", "wxs", ""):
            arc = _arc()
            arc.variables["reflection_group_id"] = bad_value
            assert agent._select_reflection_group_id(arc) == "global"
        assert raw_store.calls == []
    finally:
        await adapter_missing_store.on_shutdown(ctx_missing)
        await adapter.on_shutdown(ctx)


async def test_adapter_shutdown_clears_eligibility_and_blocks_raw_reads(
    card_store: CardStore,
) -> None:
    raw_store = _RawHistoricalSocialStore()
    adapter = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=True,
        allowed_group_ids=["200"],
    ))
    ctx = PluginContext(social_narrative_store=raw_store)
    await adapter.on_startup(ctx)
    assert adapter.is_group_reflection_eligible("200") is True
    await adapter.on_shutdown(ctx)

    assert adapter.is_group_reflection_eligible("200") is False
    agent = DreamAgent(
        store=card_store,
        social_narrative_store=adapter,
        reflection_allowed_group_ids={"200"},
    )
    assert agent._select_reflection_group_id(_arc()) == "global"
    assert await agent._load_social_reflection_context("200") == ""
    assert raw_store.calls == []


async def test_raw_store_provider_without_eligibility_cannot_select_group(
    card_store: CardStore,
) -> None:
    """Provider mismatch: raw historical store must not authorize Arc group selection."""
    raw_store = _RawHistoricalSocialStore()
    agent = DreamAgent(
        store=card_store,
        life_reflection_enabled=True,
        schedule_store=_ScheduleStore(_schedule()),
        story_arc_store=_StoryStore(_arc()),
        social_narrative_store=raw_store,
        reflection_allowed_group_ids={"200"},
    )
    assert agent._select_reflection_group_id(_arc()) == "global"
    # Even if a caller forces the group id, selection itself must stay global.
    assert await agent._load_social_reflection_context("global") == ""
    assert raw_store.calls == []


async def test_both_allowlists_agree_selects_group_and_reads_through_adapter(
    card_store: CardStore,
) -> None:
    raw_store = _RawHistoricalSocialStore()
    adapter = SocialNarrativePlugin(SocialNarrativeConfig(
        enabled=True,
        allowed_group_ids=["200"],
    ))
    ctx = PluginContext(social_narrative_store=raw_store)
    await adapter.on_startup(ctx)
    agent = DreamAgent(
        store=card_store,
        social_narrative_store=adapter,
        reflection_allowed_group_ids={"200"},
    )
    try:
        assert adapter.is_group_reflection_eligible("200") is True
        assert agent._select_reflection_group_id(_arc()) == "200"
        context = await agent._load_social_reflection_context("200")
        assert context == "RAW HISTORICAL FACTUAL DATA"
        assert raw_store.calls == [("200", 12)]
    finally:
        await adapter.on_shutdown(ctx)


async def test_arc_update_failure_rolls_back_cards_and_retries(
    card_store: CardStore,
    tmp_path,
) -> None:
    """Direct Arc persistence failure after cards are written must roll back."""
    from plugins.schedule.story_arc import StoryArcStore

    arc_dir = tmp_path / "story_arcs"
    story_store = StoryArcStore(arc_dir)
    await story_store.startup()
    arc = _arc()
    story_store.save(arc)
    arc_before = story_store.load(arc.arc_id)
    assert arc_before is not None
    arc_before_dict = arc_before.to_dict()
    revision_before = arc_before.revision

    agent = DreamAgent(store=card_store, story_arc_store=story_store)
    draft = _ordinary_draft()
    fail_once = {"remaining": 1}
    original_update = story_store.update

    def flaky_update(arc_id: str, mutator: Any) -> Any:
        if fail_once["remaining"] > 0:
            fail_once["remaining"] -= 1
            raise RuntimeError("simulated arc update failure")
        return original_update(arc_id, mutator)

    story_store.update = flaky_update  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated arc update failure"):
        await agent._commit_life_reflection(draft, arc_before)

    partial_cards = await card_store.search_cards("原子", limit=10)
    assert partial_cards == [], "failed arc update must leave no active partial cards"
    reloaded = story_store.load(arc.arc_id)
    assert reloaded is not None
    assert reloaded.to_dict() == arc_before_dict
    assert reloaded.revision == revision_before

    story_store.update = original_update  # type: ignore[method-assign]
    assert await agent._commit_life_reflection(draft, reloaded) == 2
    completed_cards = await card_store.search_cards("原子", limit=10)
    assert sorted(card.content for card in completed_cards) == [
        "经历洞察：原子第一卡。",
        "经历洞察：原子第二卡。",
    ]
    final_arc = story_store.load(arc.arc_id)
    assert final_arc is not None
    assert final_arc.last_events[-1]["summary"] == "原子 arc event"
    assert final_arc.open_threads[-1] == "原子 arc thread"
    assert final_arc.next_day_seed == "原子 arc seed"
    # Retry is idempotent for cards (same source ids).
    assert await agent._commit_life_reflection(draft, final_arc) == 0
    assert len(await card_store.search_cards("原子", limit=10)) == 2


def _ordinary_draft() -> LifeReflectionDraft:
    return LifeReflectionDraft(
        cards=[
            LifeReflectionCardDraft(
                category="status",
                scope="global",
                scope_id="global",
                content="经历洞察：原子第一卡。",
            ),
            LifeReflectionCardDraft(
                category="event",
                scope="global",
                scope_id="global",
                content="经历洞察：原子第二卡。",
            ),
        ],
        last_event_summary="原子 arc event",
        open_threads=["原子 arc thread"],
        next_day_seed="原子 arc seed",
    )


@pytest.mark.parametrize("failure_mode", ["cancel-first", "fail-second"])
async def test_ordinary_reflection_commit_is_atomic_and_retryable(
    card_store: CardStore,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    story_store = _StoryStore(_arc())
    arc_before = story_store.arc.to_dict()
    agent = DreamAgent(store=card_store, story_arc_store=story_store)
    draft = _ordinary_draft()
    original_add = card_store.add_card
    first_add_entered = asyncio.Event()
    never_release = asyncio.Event()
    add_calls = 0

    async def interrupted_add(*args: Any, **kwargs: Any) -> str:
        nonlocal add_calls
        add_calls += 1
        if failure_mode == "cancel-first" and add_calls == 1:
            first_add_entered.set()
            await never_release.wait()
        if failure_mode == "fail-second" and add_calls == 2:
            raise RuntimeError("second card failed")
        return await original_add(*args, **kwargs)

    monkeypatch.setattr(card_store, "add_card", interrupted_add)
    if failure_mode == "cancel-first":
        task = asyncio.create_task(agent._commit_life_reflection(draft, story_store.arc))
        await asyncio.wait_for(first_add_entered.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match="second card failed"):
            await agent._commit_life_reflection(draft, story_store.arc)

    partial_cards = await card_store.search_cards("原子", limit=10)
    assert partial_cards == [], "failed reflection commit must leave no active partial card"
    assert story_store.arc.to_dict() == arc_before
    assert story_store.update_calls == 0
    assert story_store.save_calls == 0

    monkeypatch.setattr(card_store, "add_card", original_add)
    assert await agent._commit_life_reflection(draft, story_store.arc) == 2
    completed_cards = await card_store.search_cards("原子", limit=10)
    assert sorted(card.content for card in completed_cards) == [
        "经历洞察：原子第一卡。",
        "经历洞察：原子第二卡。",
    ]
    assert story_store.update_calls == 1
    assert story_store.arc.last_events[-1]["summary"] == "原子 arc event"
    assert story_store.arc.open_threads[-1] == "原子 arc thread"
    assert story_store.arc.next_day_seed == "原子 arc seed"
