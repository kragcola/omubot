"""Affection stage classifier and 24h sqlite store."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from services.humanization.contract import AFFECTION_STAGE_SLOT
from services.humanization.state import humanization_source
from services.system_module import RuntimeStateBus, Scope

AffectionStage = Literal["stranger", "acquaint", "familiar", "close", "withdraw"]
_ROLLING_WINDOW_S = 86_400
_UPSERT_SQL = (
    "INSERT INTO affection_stage (user_id, group_id, stage, confidence, reason, signals_json, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, group_id) DO UPDATE SET "
    "stage=excluded.stage, confidence=excluded.confidence, reason=excluded.reason, "
    "signals_json=excluded.signals_json, updated_at=excluded.updated_at"
)
_SELECT_SQL = (
    "SELECT stage, confidence, reason, signals_json, updated_at FROM affection_stage "
    "WHERE user_id = ? AND group_id = ?"
)
_SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS affection_stage (user_id TEXT NOT NULL, group_id TEXT NOT NULL DEFAULT '', "
    "stage TEXT NOT NULL, confidence REAL NOT NULL, reason TEXT NOT NULL, signals_json TEXT NOT NULL, "
    "updated_at REAL NOT NULL, PRIMARY KEY(user_id, group_id))"
)


@dataclass(frozen=True, slots=True)
class AffectionSignals:
    interaction_count: int = 0
    reply_delay_s: float = 0.0
    register_consistency: float = 0.0
    consecutive_no_reply: int = 0


@dataclass(frozen=True, slots=True)
class AffectionDecision:
    stage: AffectionStage
    confidence: float
    reason: str
    signals: AffectionSignals = AffectionSignals()

    def to_state_value(self, *, user_id: str, group_id: str = "") -> dict[str, Any]:
        return {
            "user_id": user_id,
            "group_id": group_id,
            "stage": self.stage,
            "confidence": self.confidence,
            "reason": self.reason,
            "signals": asdict(self.signals),
            "ttl_s": _ROLLING_WINDOW_S,
        }


class AffectionStageStore:
    def __init__(self, path: str | Path = "storage/affection_stage.db") -> None:
        self.path = Path(path)

    def upsert(
        self, user_id: str, decision: AffectionDecision, *, group_id: str = "", now: float | None = None
    ) -> None:
        if not user_id:
            return
        payload = (
            user_id,
            group_id,
            decision.stage,
            decision.confidence,
            decision.reason,
            json.dumps(asdict(decision.signals), ensure_ascii=False),
            float(now if now is not None else time.time()),
        )
        with self._connect() as db:
            db.execute(_UPSERT_SQL, payload)

    def load_recent(
        self, user_id: str, *, group_id: str = "", now: float | None = None, max_age_s: int = _ROLLING_WINDOW_S
    ) -> AffectionDecision | None:
        if not user_id:
            return None
        with self._connect() as db:
            row = db.execute(_SELECT_SQL, (user_id, group_id)).fetchone()
        if row is None or float(now if now is not None else time.time()) - float(row[4]) > max_age_s:
            return None
        signals = AffectionSignals(**json.loads(row[3] or "{}"))
        return AffectionDecision(stage=row[0], confidence=float(row[1]), reason=row[2], signals=signals)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(_SCHEMA_SQL)
        return db


class AffectionClassifier:
    def __init__(self, store: AffectionStageStore | None = None) -> None:
        self.store = store

    async def classify(self, signals: AffectionSignals) -> AffectionDecision:
        stage, confidence, reason = _classify_stage(signals)
        return AffectionDecision(stage=stage, confidence=confidence, reason=reason, signals=signals)

    async def stage_for_user(self, user_id: str, *, group_id: str = "") -> AffectionDecision:
        recent = self.store.load_recent(user_id, group_id=group_id) if self.store is not None else None
        if recent is not None:
            return recent
        return AffectionDecision("acquaint", 0.4, "no recent affection stage")

    async def classify_and_write(
        self, user_id: str, signals: AffectionSignals, *, bus: RuntimeStateBus, scope: Scope, group_id: str = ""
    ) -> AffectionDecision:
        decision = await self.classify(signals)
        if self.store is not None:
            self.store.upsert(user_id, decision, group_id=group_id)
        bus.set(
            AFFECTION_STAGE_SLOT,
            decision.to_state_value(user_id=user_id, group_id=group_id),
            scope=scope,
            source=humanization_source("affection_classifier:classify"),
            confidence=decision.confidence,
            decay_at=datetime.now() + timedelta(seconds=_ROLLING_WINDOW_S),
        )
        return decision


def _classify_stage(signals: AffectionSignals) -> tuple[AffectionStage, float, str]:
    delay = max(0.0, signals.reply_delay_s)
    consistency = max(0.0, min(1.0, signals.register_consistency))
    count = max(0, signals.interaction_count)
    if signals.consecutive_no_reply >= 5 or delay >= 3600:
        return "withdraw", 0.78, "long silence or repeated no-reply"
    if count <= 1 and consistency < 0.4:
        return "stranger", 0.7, "cold start"
    if count >= 100 and delay <= 30 and consistency >= 0.75:
        return "close", 0.82, "dense fast interactions"
    if count >= 30 and delay <= 180 and consistency >= 0.55:
        return "familiar", 0.74, "stable recurring interactions"
    return "acquaint", 0.6, "some interaction but not close"
