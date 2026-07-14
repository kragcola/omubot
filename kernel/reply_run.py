"""Metadata-only lifecycle contract for one scheduler reply attempt."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReplyOrigin(StrEnum):
    TRIGGERED = "triggered"
    PROACTIVE = "proactive"


class ReplyStage(StrEnum):
    INPUT = "input"
    DECISION = "decision"
    GENERATION_STARTED = "generation_started"
    POSTPROCESSED = "postprocessed"
    DELIVERED = "delivered"
    GENERATION_FINISHED = "generation_finished"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReplyOutcome(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ReplyStageRecord:
    stage: ReplyStage
    elapsed_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplyRun:
    run_id: str
    session_id: str
    group_id: str
    user_id: str
    origin: ReplyOrigin
    trigger_mode: str
    started_at: float = field(default_factory=time.monotonic)
    records: list[ReplyStageRecord] = field(default_factory=list)
    delivered_segments: int = 0
    outcome: ReplyOutcome | None = None

    @classmethod
    def start(
        cls,
        *,
        session_id: str,
        group_id: str,
        user_id: str,
        origin: ReplyOrigin,
        trigger_mode: str = "",
    ) -> ReplyRun:
        run = cls(
            run_id="rr_" + secrets.token_hex(6),
            session_id=session_id,
            group_id=group_id,
            user_id=user_id,
            origin=origin,
            trigger_mode=trigger_mode,
        )
        run.record(ReplyStage.INPUT)
        return run

    def record(self, stage: ReplyStage, **metadata: Any) -> None:
        self.records.append(
            ReplyStageRecord(
                stage=stage,
                elapsed_ms=round((time.monotonic() - self.started_at) * 1000, 3),
                metadata=dict(metadata),
            )
        )
        if stage is ReplyStage.DELIVERED:
            self.delivered_segments += 1

    def finish(self, outcome: ReplyOutcome) -> None:
        if self.outcome is not None:
            return
        self.outcome = outcome
        self.record(ReplyStage(outcome.value))

    def to_metric_metadata(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "origin": self.origin.value,
            "trigger_mode": self.trigger_mode,
            "outcome": self.outcome.value if self.outcome is not None else "",
            "delivered_segments": self.delivered_segments,
            "stages": [record.stage.value for record in self.records],
            "elapsed_ms": round((time.monotonic() - self.started_at) * 1000, 3),
        }
