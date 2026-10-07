"""Advance reviewed Storylets from the committed daily Schedule clock.

This runner owns no timer, model, or sender. StoryArcStore remains the only
writer of Storylet decisions, story events, and projection receipts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .schedule_life import ScheduleDayRecord, ScheduleLife
from .story import StoryArcStore, StoryCommit
from .types import OperationError, Scope
from .worldbook import StoryletEngine


@dataclass(frozen=True, slots=True)
class StoryletRunFailure:
    scope: Scope
    code: str


@dataclass(frozen=True, slots=True)
class StoryletRunReport:
    local_day: str | None
    committed: tuple[StoryCommit, ...] = ()
    failed: tuple[StoryletRunFailure, ...] = ()


class StoryletDailyRunner:
    """Run at most one approved event per Arc and committed Schedule step."""

    def __init__(
        self,
        schedule: ScheduleLife,
        story: StoryArcStore,
        engine: StoryletEngine,
        *,
        bot_id: str,
    ) -> None:
        if schedule.store is not story.store:
            raise OperationError("storylet_runtime_store_mismatch")
        if not schedule.enabled or not story.enabled or not story.assembled:
            raise OperationError("storylet_runtime_unavailable")
        if type(engine) is not StoryletEngine:
            raise OperationError("invalid_storylet_engine")
        if not set(engine.allowed_groups) <= set(schedule.allowed_groups):
            raise OperationError("storylet_runtime_scope_mismatch")
        try:
            Scope(bot_id=bot_id, group_id="runtime-check")
        except (TypeError, ValueError) as exc:
            raise OperationError("invalid_storylet_bot") from exc
        self.schedule = schedule
        self.story = story
        self.engine = engine
        self.bot_id = bot_id

    async def run_due(self, at: datetime) -> StoryletRunReport:
        if not self.engine.enabled or not self.engine.allowed_groups:
            return StoryletRunReport(local_day=None)
        if type(at) is not datetime or at.tzinfo is None or at.utcoffset() is None:
            raise OperationError("invalid_storylet_clock")
        local_day = at.astimezone(ZoneInfo(self.schedule.default_timezone)).date().isoformat()
        committed: list[StoryCommit] = []
        failed: list[StoryletRunFailure] = []
        target_ids = {item.target_arc_id for item in self.engine.registry.storylets if item.target_arc_id}
        use_main = any(not item.target_arc_id for item in self.engine.registry.storylets)

        for group_id in sorted(self.engine.allowed_groups):
            scope = Scope(bot_id=self.bot_id, group_id=group_id)
            try:
                batch = await self.schedule.read_storylet_batch(
                    scope,
                    through_day=local_day,
                    updated_at=at.astimezone(UTC).timestamp(),
                )
                cursor = batch.last_step
                for item in batch.steps:
                    day = item.day
                    if day.storylet_fingerprint == self.engine.registry.fingerprint:
                        arcs_by_id: dict[str, tuple[str, int, str]] = {}
                        if target_ids or use_main:
                            arcs_by_id = await self._checked_schedule_arcs(scope, day)
                            await self.story.visible_stack(scope)
                            snapshot_main = any(
                                role == "main" for _, role, _ in day.arc_revisions
                            )
                            if use_main and not snapshot_main and day.local_day == local_day:
                                # Keep today's explicit missing/ambiguous-main gate.  A
                                # main registered after an older day was committed is
                                # not a retroactive target for that Schedule step.
                                await self.story.visible_stack(scope)
                        selected = tuple(
                            arc_id
                            for arc_id, (role, _, status) in arcs_by_id.items()
                            if status == "active"
                            and (
                                arc_id in target_ids
                                or (use_main and role == "main")
                            )
                        )
                        for arc_id in selected:
                            try:
                                result = await self.story.commit_storylet(
                                    scope,
                                    self.engine,
                                    arc_id=arc_id,
                                    step=item.step,
                                    expected_arc_revision=arcs_by_id[arc_id][1],
                                    now=day.created_at,
                                )
                            except OperationError as exc:
                                if exc.code != "storylet_no_candidate":
                                    raise
                            else:
                                committed.append(result)

                    # StoryArc owns the event and its typed projections.  Only
                    # acknowledge this Schedule step after all pending effects
                    # have been applied, including after a crash/retry.
                    await self.story.catch_up(scope, now=day.created_at)
                    if await self.story.pending_projections(scope):
                        raise OperationError("storylet_projection_pending")
                    await self.schedule.advance_storylet_cursor(
                        scope,
                        expected_step=cursor,
                        step=item.step,
                        updated_at=at.astimezone(UTC).timestamp(),
                    )
                    cursor = item.step
            except asyncio.CancelledError:
                raise
            except OperationError as exc:
                failed.append(StoryletRunFailure(scope, exc.code))
            except Exception:
                failed.append(StoryletRunFailure(scope, "storylet_runtime_failed"))

        return StoryletRunReport(local_day, tuple(committed), tuple(failed))

    async def _checked_schedule_arcs(
        self, scope: Scope, day: ScheduleDayRecord
    ) -> dict[str, tuple[str, int, str]]:
        if type(day) is not ScheduleDayRecord:
            raise OperationError("invalid_schedule_storylet_snapshot")
        result: dict[str, tuple[str, int, str]] = {}
        for arc_id, role, expected_revision in day.arc_revisions:
            arc = await self.story.read_arc(scope, arc_id)
            if arc is None or arc.role != role or arc.revision < expected_revision:
                raise OperationError("storylet_arc_history_conflict")
            if arc.revision > expected_revision:
                rows = await self.story.store.transaction(
                    lambda db, selected_arc=arc_id, start=expected_revision: db.execute(
                        "SELECT group_id,origin_kind,from_revision,to_revision FROM story_events "
                        "WHERE bot_id=? AND arc_id=? AND to_revision>? "
                        "ORDER BY from_revision,to_revision",
                        (scope.bot_id, selected_arc, start),
                    ).fetchall()
                )
                current_revision = expected_revision
                saw_storylet_event = False
                for row in rows:
                    if (
                        row["group_id"] != scope.group_id
                        or row["origin_kind"] != "storylet"
                        or int(row["from_revision"]) != current_revision
                        or int(row["to_revision"]) > arc.revision
                    ):
                        raise OperationError("storylet_arc_history_conflict")
                    saw_storylet_event = True
                    current_revision = int(row["to_revision"])
                if current_revision != arc.revision:
                    raise OperationError("storylet_arc_history_conflict")
                if saw_storylet_event:
                    state_fingerprint = await self.story.store.transaction(
                        lambda db, selected_arc=arc_id: db.execute(
                            "SELECT registry_fingerprint FROM storylet_states "
                            "WHERE bot_id=? AND group_id=? AND arc_id=?",
                            (scope.bot_id, scope.group_id, selected_arc),
                        ).fetchone()
                    )
                    if (
                        state_fingerprint is None
                        or state_fingerprint[0] != day.storylet_fingerprint
                    ):
                        raise OperationError("storylet_arc_history_conflict")
            result[arc_id] = (arc.role, arc.revision, arc.status)
        return result
