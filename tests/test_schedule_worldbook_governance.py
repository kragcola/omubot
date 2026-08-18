"""Contract tests for the governed Worldbook schedule cutover."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from plugins.schedule import generator as schedule_generator_module
from plugins.schedule.generator import ScheduleGenerator
from plugins.schedule.store import ScheduleStore
from plugins.schedule.story_arc import StoryArc, StoryArcStore
from plugins.schedule.types import Schedule, TimeSlot
from plugins.schedule.worldbook_governance import ScheduleWorldbookGovernanceBridge
from plugins.worldbook.plugin import WorldbookPluginConfig
from services.worldbook.governance_contracts import WorldbookOperatorDecisionV1
from services.worldbook.governance_store import WorldbookGovernanceStore
from services.worldbook.runtime import build_worldbook_runtime


def _schedule() -> Schedule:
    return Schedule(
        date="2026-08-18",
        theme="设备检查日",
        day_narrative="排练前先解决设备隐患。",
        generated_at="2026-08-18T02:00:00+08:00",
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


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        return cls(2026, 8, 18, 9, 30, tzinfo=tz)


async def _prepared_approved_bridge(tmp_path):
    schedule_store = ScheduleStore(storage_dir=str(tmp_path / "schedule"))
    await schedule_store.startup()
    story_store = StoryArcStore(tmp_path / "arcs")
    await story_store.startup()
    story_store.save(StoryArc(arc_id="arc.main", arc_role="main"))
    runtime = build_worldbook_runtime(
        WorldbookPluginConfig(enabled=True, schedule_projection_enabled=True),
        root=tmp_path,
        story_arc_store=story_store,
    )
    assert runtime is not None
    governance = WorldbookGovernanceStore(tmp_path / "worldbook-governance.db")
    await governance.init()
    bridge = ScheduleWorldbookGovernanceBridge(
        schedule_store=schedule_store,
        worldbook_runtime=runtime,
        governance_store=governance,
        now=lambda: datetime(2026, 8, 17, 18, 0, tzinfo=UTC),
    )
    prepared = await bridge.prepare_fresh(_schedule())
    await governance.append_operator_decision(
        WorldbookOperatorDecisionV1.create(
            proposal_id=prepared.proposal_id,
            decision="approve",
            reason_code="schedule_reviewed",
            operator_ref="operator:fixture",
            decided_at=datetime(2026, 8, 17, 18, 1, tzinfo=UTC),
        )
    )
    return schedule_store, story_store, runtime, governance, bridge, prepared


def test_governed_schedule_snapshot_preserves_atomic_intent_marker(tmp_path) -> None:
    schedule_dir = tmp_path / "schedule"
    schedule_dir.mkdir()
    store = ScheduleStore(storage_dir=str(schedule_dir))
    intent = {
        "contract_version": "schedule.worldbook_governance_intent.v1",
        "world_id": "omubot.default",
        "arc_id": "arc.main",
        "proposal_id": "wprop_schedule_canary",
        "proposal_sha256": "a" * 64,
        "proposed_at": "2026-08-17T18:00:00Z",
    }

    store.save(_schedule(), governance_intent=intent)
    snapshot = store.load_governance_snapshot("2026-08-18")

    assert snapshot is not None
    assert snapshot.schedule.date == "2026-08-18"
    assert snapshot.governance_intent == intent
    assert len(snapshot.source_sha256) == 64


def test_governed_schedule_snapshot_rejects_filename_date_mismatch(tmp_path) -> None:
    schedule_dir = tmp_path / "schedule"
    schedule_dir.mkdir()
    store = ScheduleStore(storage_dir=str(schedule_dir))
    payload = ScheduleStore._source_payload(_schedule())
    payload["date"] = "2026-08-19"
    (schedule_dir / "2026-08-18.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    assert store.load_governance_snapshot("2026-08-18") is None


def test_load_preserves_invalid_marker_source_for_governed_recovery(tmp_path) -> None:
    schedule_dir = tmp_path / "schedule"
    schedule_dir.mkdir()
    store = ScheduleStore(storage_dir=str(schedule_dir))
    payload = ScheduleStore._source_payload(_schedule())
    payload["slots"][0]["activity"] = "invalid-governed-activity"
    payload["worldbook_governance_intent"] = {
        "contract_version": "schedule.worldbook_governance_intent.v1",
        "world_id": "omubot.default",
        "arc_id": "arc.main",
        "schedule_date": "2026-08-18",
        "summary_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "proposed_at": "2026-08-17T18:00:00Z",
        "proposal_id": "wprop_schedule_canary",
        "proposal_sha256": "c" * 64,
    }
    path = schedule_dir / "2026-08-18.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    assert store.load("2026-08-18") is None
    assert path.read_bytes() == before


def test_save_refuses_to_overwrite_unreadable_governed_source(tmp_path) -> None:
    schedule_dir = tmp_path / "schedule"
    schedule_dir.mkdir()
    store = ScheduleStore(storage_dir=str(schedule_dir))
    path = schedule_dir / "2026-08-18.json"
    original = '{"worldbook_governance_intent":{"contract_version":"v1"'
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="governed schedule source is unreadable"):
        store.save(_schedule())

    assert path.read_text(encoding="utf-8") == original


def test_save_refuses_to_overwrite_null_governance_marker(tmp_path) -> None:
    schedule_dir = tmp_path / "schedule"
    schedule_dir.mkdir()
    store = ScheduleStore(storage_dir=str(schedule_dir))
    payload = ScheduleStore._source_payload(_schedule())
    payload["worldbook_governance_intent"] = None
    path = schedule_dir / "2026-08-18.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="governed schedule source is immutable"):
        store.save(_schedule())


def test_governed_schedule_source_cannot_be_overwritten_without_its_marker(tmp_path) -> None:
    schedule_dir = tmp_path / "schedule"
    schedule_dir.mkdir()
    store = ScheduleStore(storage_dir=str(schedule_dir))
    intent = {
        "contract_version": "schedule.worldbook_governance_intent.v1",
        "world_id": "omubot.default",
        "arc_id": "arc.main",
        "proposal_id": "wprop_schedule_canary",
        "proposal_sha256": "a" * 64,
        "proposed_at": "2026-08-17T18:00:00Z",
    }
    original = _schedule()
    store.save(original, governance_intent=intent)

    with pytest.raises(ValueError, match="immutable"):
        store.save(replace(original, theme="被重规划的设备检查日"))

    snapshot = store.load_governance_snapshot(original.date)
    assert snapshot is not None
    assert snapshot.schedule == original
    assert snapshot.governance_intent == intent


@pytest.mark.asyncio
async def test_fresh_schedule_requires_proposal_then_named_decision_then_receipt(tmp_path) -> None:
    schedule_store = ScheduleStore(storage_dir=str(tmp_path / "schedule"))
    await schedule_store.startup()
    story_store = StoryArcStore(tmp_path / "arcs")
    await story_store.startup()
    story_store.save(StoryArc(arc_id="arc.main", arc_role="main"))
    runtime = build_worldbook_runtime(
        WorldbookPluginConfig(enabled=True, schedule_projection_enabled=True),
        root=tmp_path,
        story_arc_store=story_store,
    )
    assert runtime is not None
    governance = WorldbookGovernanceStore(tmp_path / "worldbook-governance.db")
    await governance.init()
    proposed_at = datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
    bridge = ScheduleWorldbookGovernanceBridge(
        schedule_store=schedule_store,
        worldbook_runtime=runtime,
        governance_store=governance,
        now=lambda: proposed_at,
    )
    try:
        prepared = await bridge.prepare_fresh(_schedule())
        proposal = await governance.get_proposal(prepared.proposal_id)

        assert prepared.status == "proposed"
        assert proposal is not None
        assert (story_store.load("arc.main") or StoryArc("missing")).event_history == []

        await governance.append_operator_decision(
            WorldbookOperatorDecisionV1.create(
                proposal_id=proposal.proposal_id,
                decision="approve",
                reason_code="schedule_reviewed",
                operator_ref="operator:fixture",
                decided_at=proposed_at + timedelta(minutes=1),
            )
        )
        committed = await bridge.commit_approved(proposal.proposal_id)
        receipt = await governance.get_commit_receipt(proposal.proposal_id)
        arc = story_store.load("arc.main")

        assert committed.status == "committed"
        assert receipt is not None
        assert arc is not None
        assert [event["event_id"] for event in arc.event_history] == [proposal.event.event_id]
        assert arc.event_budget["worldbook_schedule_governance_v1"]["2026-08-18"][
            "proposal_id"
        ] == proposal.proposal_id
    finally:
        await bridge.close()
        await governance.close()


@pytest.mark.asyncio
async def test_bridge_never_leaves_an_arc_marker_when_reducer_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    schedule_store = ScheduleStore(storage_dir=str(tmp_path / "schedule"))
    await schedule_store.startup()
    story_store = StoryArcStore(tmp_path / "arcs")
    await story_store.startup()
    story_store.save(StoryArc(arc_id="arc.main", arc_role="main"))
    runtime = build_worldbook_runtime(
        WorldbookPluginConfig(enabled=True, schedule_projection_enabled=True),
        root=tmp_path,
        story_arc_store=story_store,
    )
    assert runtime is not None
    governance = WorldbookGovernanceStore(tmp_path / "worldbook-governance.db")
    await governance.init()
    bridge = ScheduleWorldbookGovernanceBridge(
        schedule_store=schedule_store,
        worldbook_runtime=runtime,
        governance_store=governance,
        now=lambda: datetime(2026, 8, 17, 18, 0, tzinfo=UTC),
    )
    try:
        prepared = await bridge.prepare_fresh(_schedule())
        await governance.append_operator_decision(
            WorldbookOperatorDecisionV1.create(
                proposal_id=prepared.proposal_id,
                decision="approve",
                reason_code="schedule_reviewed",
                operator_ref="operator:fixture",
                decided_at=datetime(2026, 8, 17, 18, 1, tzinfo=UTC),
            )
        )
        before = story_store.load("arc.main")
        assert before is not None
        monkeypatch.setattr(runtime, "_reducer", None)

        outcome = await bridge.commit_approved(prepared.proposal_id)

        after = story_store.load("arc.main")
        assert outcome.status == "blocked"
        assert outcome.reason == "worldbook_reducer_unavailable"
        assert after is not None and after.to_dict() == before.to_dict()
        assert await governance.get_commit_receipt(prepared.proposal_id) is None
    finally:
        await bridge.close()
        await governance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    (
        ("intent", "invalid_intent"),
        ("source", "invalid_intent"),
        ("main", "main_arc_unavailable"),
    ),
)
async def test_bridge_never_mutates_an_arc_after_a_bound_input_changes(
    tmp_path,
    mutation: str,
    expected_reason: str,
) -> None:
    _schedule_store, story_store, _runtime, governance, bridge, prepared = (
        await _prepared_approved_bridge(tmp_path)
    )
    try:
        if mutation in {"intent", "source"}:
            source_path = tmp_path / "schedule" / "2026-08-18.json"
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            if mutation == "intent":
                payload["worldbook_governance_intent"]["summary_sha256"] = "0" * 64
            else:
                payload["theme"] = "被篡改的设备检查日"
            source_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        else:
            story_store.update("arc.main", lambda arc: setattr(arc, "arc_role", "side"))

        before = story_store.load("arc.main")
        assert before is not None
        outcome = await bridge.commit_approved(prepared.proposal_id)
        after = story_store.load("arc.main")

        assert outcome.status == "blocked"
        assert outcome.reason == expected_reason
        assert after is not None and after.to_dict() == before.to_dict()
        assert await governance.get_commit_receipt(prepared.proposal_id) is None
    finally:
        await bridge.close()
        await governance.close()


@pytest.mark.asyncio
async def test_bridge_rejects_legacy_same_day_fact_without_mutating_the_arc(tmp_path) -> None:
    _schedule_store, story_store, _runtime, governance, bridge, prepared = (
        await _prepared_approved_bridge(tmp_path)
    )
    try:
        story_store.update(
            "arc.main",
            lambda arc: arc.event_history.append(
                {"event_id": "schedule.2026-08-18", "source": "schedule_generator"}
            ),
        )
        before = story_store.load("arc.main")
        assert before is not None

        outcome = await bridge.commit_approved(prepared.proposal_id)

        after = story_store.load("arc.main")
        assert outcome.status == "blocked"
        assert outcome.reason == "legacy_schedule_fact_exists"
        assert after is not None and after.to_dict() == before.to_dict()
        assert await governance.get_commit_receipt(prepared.proposal_id) is None
    finally:
        await bridge.close()
        await governance.close()


@pytest.mark.asyncio
async def test_bridge_retries_exactly_after_cancellation_before_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    _schedule_store, story_store, _runtime, governance, bridge, prepared = (
        await _prepared_approved_bridge(tmp_path)
    )
    entered_receipt_write = asyncio.Event()
    original_append = governance.append_commit_receipt

    async def block_receipt_write(receipt):
        entered_receipt_write.set()
        await asyncio.Event().wait()
        return await original_append(receipt)

    monkeypatch.setattr(governance, "append_commit_receipt", block_receipt_write)
    try:
        commit = asyncio.create_task(bridge.commit_approved(prepared.proposal_id))
        await entered_receipt_write.wait()
        commit.cancel()
        with pytest.raises(asyncio.CancelledError):
            await commit
        monkeypatch.setattr(governance, "append_commit_receipt", original_append)

        after_cancel = story_store.load("arc.main")
        proposal = await governance.get_proposal(prepared.proposal_id)
        assert proposal is not None
        assert after_cancel is not None
        assert [event["event_id"] for event in after_cancel.event_history] == [
            proposal.event.event_id
        ]
        assert await governance.get_commit_receipt(prepared.proposal_id) is None

        recovered = await bridge.commit_approved(prepared.proposal_id)
        recovered_arc = story_store.load("arc.main")
        assert recovered.status == "committed"
        assert recovered.receipt_present is True
        assert recovered_arc is not None
        assert len(recovered_arc.event_history) == 1
        assert await governance.get_commit_receipt(prepared.proposal_id) is not None
    finally:
        await bridge.close()
        await governance.close()


@pytest.mark.asyncio
async def test_bridge_serializes_concurrent_approved_retries_to_one_receipt(tmp_path) -> None:
    _schedule_store, story_store, _runtime, governance, bridge, prepared = (
        await _prepared_approved_bridge(tmp_path)
    )
    try:
        first, second = await asyncio.gather(
            bridge.commit_approved(prepared.proposal_id),
            bridge.commit_approved(prepared.proposal_id),
        )
        arc = story_store.load("arc.main")

        assert [first.status, second.status] == ["committed", "committed"]
        assert arc is not None and len(arc.event_history) == 1
        assert await governance.get_commit_receipt(prepared.proposal_id) is not None
    finally:
        await bridge.close()
        await governance.close()


@pytest.mark.asyncio
async def test_generator_uses_bound_bridge_instead_of_direct_worldbook_commit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(schedule_generator_module, "datetime", _FixedDateTime)
    schedule_store = ScheduleStore(storage_dir=str(tmp_path / "schedule"))
    await schedule_store.startup()
    story_store = StoryArcStore(tmp_path / "arcs")
    await story_store.startup()
    story_store.save(StoryArc(arc_id="arc.main", arc_role="main"))
    runtime = build_worldbook_runtime(
        WorldbookPluginConfig(enabled=True, schedule_projection_enabled=True),
        root=tmp_path,
        story_arc_store=story_store,
    )
    assert runtime is not None
    governance = WorldbookGovernanceStore(tmp_path / "worldbook-governance.db")
    await governance.init()
    bridge = ScheduleWorldbookGovernanceBridge(
        schedule_store=schedule_store,
        worldbook_runtime=runtime,
        governance_store=governance,
        now=lambda: datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
    )
    generator = ScheduleGenerator(
        store=schedule_store,
        story_arc_enabled=True,
        story_arc_store=story_store,
        worldbook_runtime=runtime,
    )
    generator.set_worldbook_governance_bridge(bridge)

    async def api_call(*_args, **_kwargs):
        return {
            "text": json.dumps(
                {
                    "date": "2026-08-18",
                    "theme": "设备检查日",
                    "day_narrative": "排练前先解决设备隐患。",
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

    try:
        assert await generator._generate(api_call) is True
        snapshot = schedule_store.load_governance_snapshot("2026-08-18")
        assert snapshot is not None and snapshot.governance_intent is not None
        proposal = await governance.get_proposal(snapshot.governance_intent["proposal_id"])
        assert proposal is not None
        arc = story_store.load("arc.main")
        assert arc is not None and arc.event_history == []
    finally:
        await bridge.close()
        await governance.close()


@pytest.mark.asyncio
async def test_generator_refuses_to_create_an_unmarked_source_without_a_bridge(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(schedule_generator_module, "datetime", _FixedDateTime)
    schedule_store = ScheduleStore(storage_dir=str(tmp_path / "schedule"))
    await schedule_store.startup()
    story_store = StoryArcStore(tmp_path / "arcs")
    await story_store.startup()
    story_store.save(StoryArc(arc_id="arc.main", arc_role="main"))
    runtime = build_worldbook_runtime(
        WorldbookPluginConfig(enabled=True, schedule_projection_enabled=True),
        root=tmp_path,
        story_arc_store=story_store,
    )
    assert runtime is not None
    generator = ScheduleGenerator(
        store=schedule_store,
        story_arc_enabled=True,
        story_arc_store=story_store,
        worldbook_runtime=runtime,
    )

    async def api_call(*_args, **_kwargs):
        return {
            "text": json.dumps(
                {
                    "date": "2026-08-18",
                    "theme": "设备检查日",
                    "day_narrative": "排练前先解决设备隐患。",
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

    assert await generator._generate(api_call) is False
    assert schedule_store.source_exists("2026-08-18") is False
    arc = story_store.load("arc.main")
    assert arc is not None and arc.event_history == []
