from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.schedule.generator import ScheduleGenerator
from plugins.schedule.store import ScheduleStore
from plugins.schedule.story_arc import StoryArc, StoryArcStore
from plugins.worldbook.plugin import WorldbookPlugin, WorldbookPluginConfig
from services.worldbook.ledger import StoryLedgerAdapter


class _ScheduleGeneratorCapture:
    def __init__(self) -> None:
        self.runtime = None
        self.story_arc_store = None

    def set_worldbook_runtime(self, runtime: object | None) -> None:
        self.runtime = runtime

    def set_worldbook_story_arc_store(self, store: object | None) -> None:
        self.story_arc_store = store


@pytest.mark.asyncio
async def test_schedule_projection_provisions_story_arc_store_when_legacy_gate_is_off(
    tmp_path,
) -> None:
    canon_dir = tmp_path / "canon"
    storylet_dir = tmp_path / "storylets"
    canon_dir.mkdir()
    storylet_dir.mkdir()
    schedule_gen = _ScheduleGeneratorCapture()
    ctx = SimpleNamespace(
        persona_runtime=None,
        story_arc_store=None,
        social_narrative_store=None,
        schedule_gen=schedule_gen,
        provider_bus=None,
        worldbook_runtime=None,
        worldbook_dream_bridge=None,
    )
    plugin = WorldbookPlugin(
        WorldbookPluginConfig(
            enabled=True,
            schedule_projection_enabled=True,
            canon_dir=str(canon_dir),
            storylet_dir=str(storylet_dir),
            state_dir=str(tmp_path / "state"),
            story_arc_dir=str(tmp_path / "arcs"),
        )
    )

    await plugin.on_startup(ctx)

    assert ctx.story_arc_store is not None
    assert schedule_gen.story_arc_store is ctx.story_arc_store
    assert schedule_gen.runtime is ctx.worldbook_runtime


@pytest.mark.asyncio
async def test_worldbook_schedule_refuses_mtime_fallback_without_explicit_main(
    tmp_path,
) -> None:
    story_store = StoryArcStore(storage_dir=str(tmp_path / "arcs"))
    await story_store.startup()
    story_store.save(StoryArc(arc_id="legacy-b", arc_role="side", stack_order=2))
    story_store.save(StoryArc(arc_id="legacy-a", arc_role="side", stack_order=1))
    runtime = SimpleNamespace(
        config=SimpleNamespace(enabled=True, schedule_projection_enabled=True),
        ledger=StoryLedgerAdapter(story_store),
    )
    schedule_store = ScheduleStore(storage_dir=str(tmp_path / "schedule"))
    generator = ScheduleGenerator(
        store=schedule_store,
        story_arc_enabled=True,
        story_arc_store=story_store,
        worldbook_runtime=runtime,
    )

    selected = generator._load_active_story_arc("2026-07-18")

    assert selected is None
