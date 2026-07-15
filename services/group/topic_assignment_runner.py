"""Offline Phase 2 projector for versioned topic assignments."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from services.group.topic_assignment_store import (
    AssignmentRun,
    TopicAssignment,
    TopicAssignmentStore,
    TopicBlockIdentity,
    UtteranceMembership,
)
from services.group.topic_block import TopicBlockTracker, topic_block_algorithm_contract
from services.similarity import create_similarity_provider

_BLOCK_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "omubot:research-topic-block:v1")
_UTTERANCE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "omubot:research-utterance:v1")
_RUN_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "omubot:research-assignment-run:v1")


@dataclass(frozen=True, slots=True)
class TopicAssignmentParameters:
    attrib_recent_seconds: float = 120.0
    max_blocks: int = 6
    decay_a: float = 0.998
    decay_lambda: float = 1.0
    reservoir_max: int = 12
    activity_floor: float = 0.5
    similarity_backend: str = "ngram"
    utterance_gap_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class TopicAssignmentRunResult:
    status: str
    run_uuid: str
    algorithm_version: str
    input_digest: str
    input_cutoff: str
    event_count: int
    inserted_assignments: int
    inserted_memberships: int


@dataclass(frozen=True, slots=True)
class _RawEvent:
    event_uid: str
    run_id: str
    event_time: datetime
    ingested_at: datetime
    direction: str
    actor_type: str
    actor_id: str
    source: str
    group_id: str
    message_id: int | None
    reply_to_message_id: int | None
    at_user_ids: tuple[str, ...]
    text: str | None
    content_type: str


@dataclass(slots=True)
class _UtteranceState:
    actor_id: str
    direction: str
    last_event_time: float
    utterance_uuid: str
    next_ordinal: int


def _paths_refer_to_same_file(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.samefile(right)
    except OSError:
        return False


class TopicAssignmentRunner:
    """Project one deterministic raw snapshot into the independent derived DB."""

    def __init__(
        self,
        *,
        raw_db_path: str | Path,
        derived_db_path: str | Path,
        utterance_gap_seconds: float = 5.0,
        parameters: TopicAssignmentParameters | None = None,
        input_cutoff: str | None = None,
    ) -> None:
        self._raw_db_path = Path(raw_db_path).expanduser().resolve()
        self._derived_db_path = Path(derived_db_path).expanduser().resolve()
        if _paths_refer_to_same_file(self._raw_db_path, self._derived_db_path):
            raise ValueError("raw and derived databases must be different SQLite files")
        self._parameters = parameters or TopicAssignmentParameters(
            utterance_gap_seconds=max(0.0, float(utterance_gap_seconds))
        )
        cutoff = str(input_cutoff or "").strip()
        self._input_cutoff = _normalize_timestamp(cutoff, field="input cutoff") if cutoff else ""

    async def run(self) -> TopicAssignmentRunResult:
        started_at = datetime.now(UTC).isoformat()
        events = await _read_raw_snapshot(
            self._raw_db_path,
            input_cutoff=self._input_cutoff,
        )
        input_digest = _input_digest(events)
        input_cutoff = self._input_cutoff or (
            max(event.ingested_at for event in events).astimezone(UTC).isoformat()
            if events
            else ""
        )
        config_hash, algorithm_version = _algorithm_identity(self._parameters)
        run_uuid = str(uuid.uuid5(_RUN_NAMESPACE, f"{algorithm_version}\x1f{input_digest}"))
        assigned_at = datetime.now(UTC).isoformat()
        blocks, assignments, memberships = _project_events(
            events,
            parameters=self._parameters,
            algorithm_version=algorithm_version,
            run_uuid=run_uuid,
            assigned_at=assigned_at,
        )
        completed_at = datetime.now(UTC).isoformat()
        run = AssignmentRun(
            run_uuid=run_uuid,
            algorithm_version=algorithm_version,
            algorithm_config_hash=config_hash,
            input_digest=input_digest,
            input_cutoff=input_cutoff,
            started_at=started_at,
            completed_at=completed_at,
            status="completed",
            event_count=len(events),
            assignment_count=len(assignments),
        )
        store = TopicAssignmentStore(self._derived_db_path)
        await store.init()
        try:
            commit = await store.commit_projection(
                run=run,
                blocks=blocks,
                assignments=assignments,
                memberships=memberships,
            )
        finally:
            await store.close()
        return TopicAssignmentRunResult(
            status=commit.status,
            run_uuid=run_uuid,
            algorithm_version=algorithm_version,
            input_digest=input_digest,
            input_cutoff=input_cutoff,
            event_count=len(events),
            inserted_assignments=commit.inserted_assignments,
            inserted_memberships=commit.inserted_memberships,
        )


async def _read_raw_snapshot(
    raw_db_path: Path,
    *,
    input_cutoff: str,
) -> tuple[_RawEvent, ...]:
    uri = f"{raw_db_path.resolve().as_uri()}?mode=ro"
    db = await aiosqlite.connect(uri, uri=True)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("BEGIN")
        version_cursor = await db.execute("PRAGMA user_version")
        try:
            version_row = await version_cursor.fetchone()
        finally:
            await version_cursor.close()
        version = int(version_row[0]) if version_row is not None else 0
        if version != 1:
            raise RuntimeError(f"raw research database must be schema v1, got {version}")
        cursor = await db.execute(
            """
            SELECT * FROM research_message_event
            """
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        await db.commit()
    finally:
        await db.close()
    events = tuple(_raw_event_from_row(row) for row in rows)
    if input_cutoff:
        cutoff = datetime.fromisoformat(input_cutoff)
        events = tuple(event for event in events if event.ingested_at <= cutoff)
    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.group_id,
                event.event_time,
                event.ingested_at,
                event.event_uid,
            ),
        )
    )


def _normalize_timestamp(value: str, *, field: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return timestamp.astimezone(UTC).isoformat()


def _raw_event_from_row(row: aiosqlite.Row) -> _RawEvent:
    at_user_ids = json.loads(str(row["at_user_ids"]))
    return _RawEvent(
        event_uid=str(row["event_uid"]),
        run_id=str(row["run_id"]),
        event_time=datetime.fromisoformat(
            _normalize_timestamp(str(row["event_time"]), field="event_time")
        ),
        ingested_at=datetime.fromisoformat(
            _normalize_timestamp(str(row["ingested_at"]), field="ingested_at")
        ),
        direction=str(row["direction"]),
        actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]),
        source=str(row["source"]),
        group_id=str(row["group_id"]),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        reply_to_message_id=(
            int(row["reply_to_message_id"])
            if row["reply_to_message_id"] is not None
            else None
        ),
        at_user_ids=tuple(str(item) for item in at_user_ids),
        text=str(row["text"]) if row["text"] is not None else None,
        content_type=str(row["content_type"]),
    )


def _algorithm_identity(parameters: TopicAssignmentParameters) -> tuple[str, str]:
    payload = {
        "tracker": topic_block_algorithm_contract(),
        "parameters": {
            "attrib_recent_seconds": parameters.attrib_recent_seconds,
            "max_blocks": parameters.max_blocks,
            "decay_a": parameters.decay_a,
            "decay_lambda": parameters.decay_lambda,
            "reservoir_max": parameters.reservoir_max,
            "activity_floor": parameters.activity_floor,
            "similarity_backend": parameters.similarity_backend,
            "utterance_gap_seconds": parameters.utterance_gap_seconds,
        },
        "evidence_schema": 1,
        "projector_schema": "causal-message-id-index-v2",
        "utterance_schema": "contiguous-same-actor-gap-v1",
    }
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return digest, f"topic-block-l0l3-v1-{digest[:12]}"


def _input_digest(events: tuple[_RawEvent, ...]) -> str:
    digest = hashlib.sha256()
    for event in events:
        payload = {
            "event_uid": event.event_uid,
            "run_id": event.run_id,
            "event_time": event.event_time.isoformat(),
            "ingested_at": event.ingested_at.isoformat(),
            "direction": event.direction,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "source": event.source,
            "group_id": event.group_id,
            "message_id": event.message_id,
            "reply_to_message_id": event.reply_to_message_id,
            "at_user_ids": event.at_user_ids,
            "text": event.text,
            "content_type": event.content_type,
        }
        digest.update(_canonical_json(payload).encode())
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _project_events(
    events: tuple[_RawEvent, ...],
    *,
    parameters: TopicAssignmentParameters,
    algorithm_version: str,
    run_uuid: str,
    assigned_at: str,
) -> tuple[
    tuple[TopicBlockIdentity, ...],
    tuple[TopicAssignment, ...],
    tuple[UtteranceMembership, ...],
]:
    similarity = create_similarity_provider(parameters.similarity_backend)  # type: ignore[arg-type]
    tracker = TopicBlockTracker(similarity=similarity)
    tracker.configure(
        attrib_recent_seconds=parameters.attrib_recent_seconds,
        max_blocks=parameters.max_blocks,
        decay_a=parameters.decay_a,
        decay_lambda=parameters.decay_lambda,
        reservoir_max=parameters.reservoir_max,
        activity_floor=parameters.activity_floor,
    )
    message_events: dict[tuple[str, int], _RawEvent] = {}
    ai_actors: dict[str, set[str]] = {}
    for event in events:
        if event.actor_type == "ai":
            ai_actors.setdefault(event.group_id, set()).add(event.actor_id)

    block_by_local_id: dict[tuple[str, str], TopicBlockIdentity] = {}
    assignments: list[TopicAssignment] = []
    memberships: list[UtteranceMembership] = []
    utterance_state: dict[str, _UtteranceState] = {}

    for event in events:
        predecessor = (
            message_events.get((event.group_id, event.reply_to_message_id))
            if event.reply_to_message_id is not None
            else None
        )
        decision = tracker.observe_with_evidence(
            event.group_id,
            message_id=event.message_id,
            speaker=event.actor_id,
            text=event.text or "",
            reply_to_sender_id=predecessor.actor_id if predecessor is not None else "",
            reply_to_message_id=event.reply_to_message_id,
            reply_to_self=predecessor is not None and predecessor.actor_type == "ai",
            at_targets=event.at_user_ids,
            at_self=any(
                target in ai_actors.get(event.group_id, set())
                for target in event.at_user_ids
            ),
            now=event.event_time.timestamp(),
        )
        local_key = (event.group_id, decision.block.block_id)
        block = block_by_local_id.get(local_key)
        if block is None:
            block_uuid = str(uuid.uuid5(
                _BLOCK_NAMESPACE,
                f"{algorithm_version}\x1f{event.group_id}\x1f{event.event_uid}",
            ))
            block = TopicBlockIdentity(
                block_uuid=block_uuid,
                algorithm_version=algorithm_version,
                group_id=event.group_id,
                seed_event_uid=event.event_uid,
                created_at=event.event_time.isoformat(),
            )
            block_by_local_id[local_key] = block
        assignments.append(
            TopicAssignment(
                event_uid=event.event_uid,
                algorithm_version=algorithm_version,
                run_uuid=run_uuid,
                block_uuid=block.block_uuid,
                assigned_at=assigned_at,
                reason=decision.reason,
                score=decision.score,
                runner_up_margin=decision.runner_up_margin,
                reply_predecessor_event_uid=(
                    predecessor.event_uid if predecessor is not None else None
                ),
                evidence={
                    "candidate_count": decision.candidate_count,
                    "reply_edge_rejected": decision.reply_edge_rejected,
                    "reply_to_message_id": event.reply_to_message_id,
                    "content_type": event.content_type,
                    "source": event.source,
                },
            )
        )
        memberships.append(
            _membership_for_event(
                event,
                state_by_group=utterance_state,
                parameters=parameters,
                algorithm_version=algorithm_version,
                run_uuid=run_uuid,
                assigned_at=assigned_at,
            )
        )
        if event.message_id is not None:
            message_events[(event.group_id, event.message_id)] = event

    return (
        tuple(block_by_local_id.values()),
        tuple(assignments),
        tuple(memberships),
    )


def _membership_for_event(
    event: _RawEvent,
    *,
    state_by_group: dict[str, _UtteranceState],
    parameters: TopicAssignmentParameters,
    algorithm_version: str,
    run_uuid: str,
    assigned_at: str,
) -> UtteranceMembership:
    event_time = event.event_time.timestamp()
    state = state_by_group.get(event.group_id)
    continues = (
        state is not None
        and state.actor_id == event.actor_id
        and state.direction == event.direction
        and 0.0 <= event_time - state.last_event_time <= parameters.utterance_gap_seconds
    )
    if continues and state is not None:
        utterance_uuid = state.utterance_uuid
        ordinal = state.next_ordinal
        reason = "same_actor_gap"
    else:
        utterance_uuid = str(uuid.uuid5(
            _UTTERANCE_NAMESPACE,
            f"{algorithm_version}\x1f{event.group_id}\x1f{event.event_uid}",
        ))
        ordinal = 0
        reason = "utterance_seed"
    state_by_group[event.group_id] = _UtteranceState(
        actor_id=event.actor_id,
        direction=event.direction,
        last_event_time=event_time,
        utterance_uuid=utterance_uuid,
        next_ordinal=ordinal + 1,
    )
    return UtteranceMembership(
        event_uid=event.event_uid,
        algorithm_version=algorithm_version,
        run_uuid=run_uuid,
        utterance_uuid=utterance_uuid,
        ordinal=ordinal,
        assigned_at=assigned_at,
        reason=reason,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
