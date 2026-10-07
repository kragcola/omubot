"""CPU-only fixed-snapshot topic research, without platform or model ports.

The CPU L0/L1/L2 and ngram rules follow the July 15 Phase2 source contract.
The file CLI accepts synthetic fixtures only. ResearchEvents supplies the
separate, currently authorized anonymous snapshot for real captured events.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import portalocker
from pydantic import BaseModel, ConfigDict, Field, model_validator

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "omubot-new:offline-topic:v1")


class ReplayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(min_length=1, max_length=128)
    bot_id: str = Field(min_length=1, max_length=128)
    group_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    event_time: float = Field(ge=0, allow_inf_nan=False)
    direction: Literal["inbound", "outbound"] = "inbound"
    actor_type: Literal["human", "ai"] = "human"
    message_id: str = ""
    reply_to: str = ""
    mention_targets: tuple[str, ...] = ()
    text: str = Field(default="", max_length=8192)

    @property
    def scope(self) -> tuple[str, str]:
        return self.bot_id, self.group_id


class ReplaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    source_kind: Literal["synthetic"]
    # This expiry constrains use of even a synthetic fixture; it is not a grant.
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    events: tuple[ReplayEvent, ...] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_events(self) -> ReplaySnapshot:
        keys = [(e.scope, e.event_id) for e in self.events]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate scoped event identity")
        return self


class AuthorizedReplaySnapshot(BaseModel):
    """Internal ResearchEvents projection; never accepted by the fixture CLI."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    source_kind: Literal["live", "history", "offline", "mixed", "empty"]
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    events: tuple[ReplayEvent, ...] = Field(max_length=2048)
    # Keyed hashes supplied by the research owner, rather than public hashes of text.
    source_hashes: tuple[str, ...]

    @model_validator(mode="after")
    def exact_sources(self) -> AuthorizedReplaySnapshot:
        keys = [(e.scope, e.event_id) for e in self.events]
        if len(keys) != len(set(keys)) or len(self.source_hashes) != len(self.events):
            raise ValueError("invalid authorized source identities")
        return self


class ReplayParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recent_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    max_blocks: int = Field(default=6, ge=1, le=64)
    decay_a: float = Field(default=0.998, ge=0.9, le=1, allow_inf_nan=False)
    decay_lambda: float = Field(default=1, ge=0.1, allow_inf_nan=False)
    activity_floor: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    reservoir_max: int = Field(default=12, ge=1, le=128)
    utterance_gap_seconds: float = Field(default=5, ge=0, allow_inf_nan=False)


_DEFAULT_PARAMETERS = ReplayParameters()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _identity(kind: str, *parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, _canonical([kind, *parts])))


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", "", text)
    text = re.sub(r"^[#>\-\s*`_~]+", "", text)
    return re.sub(r"""[\s`*_~#>\[\](){}《》<>:：,，。.!！?？;；"'“”‘’|/\\]+""", "", text)


def _similarity(left: str, right: str) -> float:
    a, b = _normalized(left), _normalized(right)
    if not a or not b:
        return 0
    if a == b:
        return 1
    if a in b or b in a:
        return 0.82
    size = 2 if len(a) > 2 and len(b) > 2 else 1
    aa = {a[i : i + size] for i in range(max(1, len(a) - size + 1))}
    bb = {b[i : i + size] for i in range(max(1, len(b) - size + 1))}
    return len(aa & bb) / len(aa | bb)


@dataclass(slots=True)
class _Block:
    block_id: str
    seed_id: str
    speakers: set[str] = field(default_factory=lambda: set[str]())
    text: str = ""
    last_active: float = 0
    last_access: float = 0
    activity: float = 0
    bot_involved: bool = False


class _ScopeProjector:
    def __init__(
        self, parameters: ReplayParameters, version: str, scope: tuple[str, str], ai_actors: set[str]
    ) -> None:
        self.parameters, self.version, self.scope = parameters, version, scope
        self.ai_actors = ai_actors
        self.predecessors: dict[str, ReplayEvent] = {}
        self.active: dict[str, _Block] = {}
        self.reservoir: dict[str, _Block] = {}
        self.messages: dict[str, tuple[ReplayEvent, _Block]] = {}
        self.blocks: dict[str, dict[str, Any]] = {}

    def _cold(self, block: _Block) -> None:
        self.active.pop(block.block_id)
        self.reservoir[block.block_id] = block
        if len(self.reservoir) > self.parameters.reservoir_max:
            evicted = min(self.reservoir.values(), key=lambda b: b.activity)
            del self.reservoir[evicted.block_id]
            self.messages = {mid: pair for mid, pair in self.messages.items() if pair[1] is not evicted}

    def assign(self, event: ReplayEvent) -> dict[str, Any]:
        now, params = event.event_time, self.parameters
        for block in tuple(self.active.values()):
            block.activity *= math.pow(params.decay_a, params.decay_lambda * max(0, now - block.last_access))
            block.last_access = now
            if block.activity <= params.activity_floor:
                self._cold(block)
        active = sorted(self.active.values(), key=lambda b: b.activity, reverse=True)
        candidates = [*active, *self.reservoir.values()]
        previous = self.predecessors.get(event.reply_to) if event.reply_to else None
        indexed = self.messages.get(event.reply_to) if event.reply_to else None
        target: _Block | None = None
        reason, score, margin = "new_block", None, None
        if indexed is not None:
            target = indexed[1]
            reason = "reply_message_active" if target in active else "reply_message_reservoir"
        if target is None and previous is not None and previous.actor_type == "ai":
            target = next((b for b in active if b.bot_involved), None)
            if target is not None:
                reason = "reply_to_self"
        if target is None and previous is not None:
            target = next((b for b in active if previous.actor_id in b.speakers), None)
            if target is not None:
                reason = "reply_speaker"
        if target is None and event.mention_targets:
            target = next(
                (
                    b
                    for b in active
                    if now - b.last_active <= params.recent_seconds
                    and b.speakers.intersection(event.mention_targets)
                ),
                None,
            )
            if target is not None:
                reason = "at_target"
        if target is None:
            scored = [
                (
                    b,
                    0.25 * (event.actor_id in b.speakers)
                    + 0.25 * max(0, 1 - max(0, now - b.last_active) / params.recent_seconds)
                    + 0.5 * _similarity(event.text, b.text),
                )
                for b in candidates
            ]
            best = 0.3
            for block, value in scored:
                if value >= best:
                    target, best = block, value
            if target is not None:
                reason, score = "linear_score", best
                margin = best - max((v for b, v in scored if b is not target), default=0.3)
            elif scored:
                values = sorted((v for _, v in scored), reverse=True)
                score = values[0]
                margin = values[0] - values[1] if len(values) > 1 else None
        if target is None:
            bid = _identity("block", self.version, *self.scope, event.event_id)
            target = _Block(bid, event.event_id)
            self.blocks[bid] = {
                "block_id": bid,
                "seed_event_id": event.event_id,
                "bot_id": event.bot_id,
                "group_id": event.group_id,
            }
        self.reservoir.pop(target.block_id, None)
        self.active[target.block_id] = target
        target.speakers.add(event.actor_id)
        if event.text:
            target.text = event.text
        target.last_active = now
        target.activity += 1
        target.last_access = max(target.last_access, now)
        if (previous is not None and previous.actor_type == "ai") or self.ai_actors.intersection(
            event.mention_targets
        ):
            target.bot_involved = True
        if event.message_id:
            self.messages[event.message_id] = (event, target)
            self.predecessors[event.message_id] = event
        while len(self.active) > params.max_blocks:
            self._cold(min(self.active.values(), key=lambda b: b.activity))
        return {
            "event_id": event.event_id,
            "bot_id": event.bot_id,
            "group_id": event.group_id,
            "event_hash": _hash(event.model_dump(mode="json")),
            "block_id": target.block_id,
            "reason": reason,
            "score": score,
            "runner_up_margin": margin,
            "reply_predecessor_event_id": previous.event_id if previous else None,
            "candidate_count": len(candidates),
        }


def project_snapshot(
    snapshot: ReplaySnapshot | AuthorizedReplaySnapshot,
    *,
    version: str,
    parameters: ReplayParameters = _DEFAULT_PARAMETERS,
    now: float | None = None,
) -> dict[str, Any]:
    """Derive metadata from a fixed fixture or a purpose-authorized snapshot."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", version):
        raise ValueError("invalid algorithm version")
    if snapshot.expires_at <= (time.time() if now is None else now):
        raise ValueError("snapshot expired")
    config = {
        "parameters": parameters.model_dump(),
        "schema": "causal-id-ngram-l0l2-v1",
        "weights": [0.25, 0.25, 0.5],
        "floor": 0.3,
        "tie": "last-ge",
        "utterance": "contiguous-same-actor-direction-gap-v1",
    }
    config_hash = _hash(config)
    algorithm = version + "-" + config_hash[:16]
    ordered = sorted(snapshot.events, key=lambda e: (*e.scope, e.event_time, e.event_id))
    source_hashes = (
        {(event.scope, event.event_id): source_hash for event, source_hash in
         zip(snapshot.events, snapshot.source_hashes, strict=True)}
        if isinstance(snapshot, AuthorizedReplaySnapshot) else None
    )
    digest = _hash(
        [(event.scope, event.event_id, source_hashes[event.scope, event.event_id]) for event in ordered]
        if source_hashes is not None else [e.model_dump(mode="json") for e in ordered]
    )
    ai_actors: dict[tuple[str, str], set[str]] = {}
    for event in ordered:
        if event.actor_type == "ai":
            ai_actors.setdefault(event.scope, set()).add(event.actor_id)
    scopes: dict[tuple[str, str], _ScopeProjector] = {}
    assignments: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []
    utterances: dict[tuple[str, str], tuple[ReplayEvent, str, int]] = {}
    for event in ordered:
        if event.scope not in scopes:
            scopes[event.scope] = _ScopeProjector(
                parameters, algorithm, event.scope, ai_actors.get(event.scope, set())
            )
        projector = scopes[event.scope]
        assignment = projector.assign(event)
        if source_hashes is not None:
            assignment["event_hash"] = source_hashes[event.scope, event.event_id]
        assignments.append(assignment)
        last = utterances.get(event.scope)
        continues = (
            last is not None
            and last[0].actor_id == event.actor_id
            and (
                last[0].direction == event.direction
                and 0 <= event.event_time - last[0].event_time <= parameters.utterance_gap_seconds
            )
        )
        uid = (
            last[1] if continues and last else _identity("utterance", algorithm, *event.scope, event.event_id)
        )
        ordinal = last[2] + 1 if continues and last else 0
        memberships.append(
            {
                "event_id": event.event_id,
                "bot_id": event.bot_id,
                "group_id": event.group_id,
                "utterance_id": uid,
                "ordinal": ordinal,
            }
        )
        utterances[event.scope] = event, uid, ordinal
    result = {
        "algorithm_version": algorithm,
        "config_hash": config_hash,
        "input_digest": digest,
        "run_id": _identity("run", algorithm, digest),
        "event_count": len(ordered),
        "assignment_count": len(assignments),
        "membership_count": len(memberships),
        "blocks": [b for p in scopes.values() for b in p.blocks.values()],
        "assignments": assignments,
        "memberships": memberships,
    }
    if isinstance(snapshot, AuthorizedReplaySnapshot):
        result["source_kind"] = snapshot.source_kind
    return result


def derive_file(
    input_path: Path,
    output_path: Path,
    *,
    version: str,
    parameters: ReplayParameters = _DEFAULT_PARAMETERS,
) -> Literal["committed", "duplicate"]:
    """Read one explicit JSON fixture and atomically append a complete derived run.

    portalocker gives concurrent CLI writers one owner; atomic replacement means
    cancellation/errors before the replace leave the previous report intact.
    """
    if input_path.resolve() == output_path.resolve() or (
        output_path.exists() and input_path.samefile(output_path)
    ):
        raise ValueError("input and report must be different files")
    snapshot = ReplaySnapshot.model_validate_json(input_path.read_bytes())
    projection = project_snapshot(snapshot, version=version, parameters=parameters)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_path.with_name(output_path.name + ".lock")
    with portalocker.Lock(str(lock_path), timeout=5):
        os.chmod(lock_path, 0o600)
        report: dict[str, Any] = {"schema_version": 1, "runs": []}
        if output_path.exists():
            report = json.loads(output_path.read_text())
            if report.get("schema_version") != 1 or not isinstance(report.get("runs"), list):
                raise ValueError("unsupported derived report")
        runs: list[dict[str, Any]] = report["runs"]
        for run in runs:
            if run["run_id"] == projection["run_id"]:
                if run != projection:
                    raise ValueError("conflicting completed run")
                return "duplicate"
            if run["algorithm_version"] != projection["algorithm_version"]:
                continue
            for field_name in ("assignments", "memberships"):
                previous = {(r["bot_id"], r["group_id"], r["event_id"]): r for r in run[field_name]}
                for row in projection[field_name]:
                    key = row["bot_id"], row["group_id"], row["event_id"]
                    if key in previous and previous[key] != row:
                        raise ValueError("conflicting versioned " + field_name)
        # Recheck expiry after the bounded writer lock, immediately before commit.
        if snapshot.expires_at <= time.time():
            raise ValueError("snapshot expired")
        runs.append(projection)
        fd, temporary = tempfile.mkstemp(prefix="." + output_path.name + ".", dir=output_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(_canonical(report) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if snapshot.expires_at <= time.time():
                raise ValueError("snapshot expired")
            os.replace(temporary, output_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return "committed"
