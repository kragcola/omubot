"""Focused tests for shared learning_autopilot LLM verdict parser.

Contract (fail-closed, type-safe ReviewVerdict):
- decision accepted only when exactly approved|rejected|kept (no case-fold)
- confidence accepted only for exact int/float (not bool), finite, in [0, 1]
- invalid decision or confidence → decision kept, confidence 0.0, useful reason
- never retains a raw non-float confidence on ReviewVerdict
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from services.learning_autopilot.base import AggressivenessConfig
from services.learning_autopilot.episode_reviewer import EpisodeAIReviewer
from services.learning_autopilot.llm_assess import _parse_verdict
from services.learning_autopilot.style_reviewer import StyleAIReviewer


def _assert_float_confidence(conf: Any) -> float:
    """Confidence on ReviewVerdict must be a real float, never bool/str/raw junk."""
    assert not isinstance(conf, bool), f"bool must not leak as confidence: {conf!r}"
    assert isinstance(conf, (int, float)), (
        f"confidence must be numeric, got {type(conf).__name__}: {conf!r}"
    )
    conf_f = float(conf)
    assert math.isfinite(conf_f)
    return conf_f


def _verdict_from_payload(payload: dict[str, Any]) -> Any:
    """Build LLM result text; allow non-finite floats via CPython NaN JSON tokens."""
    conf = payload.get("confidence")
    if isinstance(conf, float) and not math.isfinite(conf):
        text = json.dumps(payload, allow_nan=True)
    else:
        text = json.dumps(payload, ensure_ascii=False)
    return _parse_verdict({"text": text})


# ---------------------------------------------------------------------------
# Shared parser: valid boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decision,confidence",
    [
        ("approved", 0.0),
        ("approved", 1.0),
        ("approved", 0.72),
        ("rejected", 0.0),
        ("rejected", 1),
        ("kept", 0.5),
        ("kept", 0),
        ("kept", 1),
    ],
)
def test_parse_verdict_accepts_exact_valid_decision_and_confidence(
    decision: str,
    confidence: float | int,
) -> None:
    payload = {"decision": decision, "confidence": confidence, "reason": "ok"}
    verdict = _verdict_from_payload(payload)
    assert verdict.decision == decision
    conf_f = _assert_float_confidence(verdict.confidence)
    assert conf_f == float(confidence)
    assert verdict.reason == "ok"


def test_parse_verdict_accepts_string_result_shape() -> None:
    text = json.dumps({"decision": "approved", "confidence": 0.9, "reason": "string shape"})
    verdict = _parse_verdict(text)
    assert verdict.decision == "approved"
    assert _assert_float_confidence(verdict.confidence) == 0.9


def test_parse_verdict_accepts_dict_content_key() -> None:
    payload = {"decision": "rejected", "confidence": 0.8, "reason": "content key"}
    verdict = _parse_verdict({"content": json.dumps(payload)})
    assert verdict.decision == "rejected"
    assert _assert_float_confidence(verdict.confidence) == 0.8


def test_parse_verdict_extracts_json_embedded_in_prose() -> None:
    payload = {"decision": "kept", "confidence": 0.4, "reason": "embedded"}
    text = f"here is my answer:\n{json.dumps(payload)}\nthanks"
    verdict = _parse_verdict(text)
    assert verdict.decision == "kept"
    assert _assert_float_confidence(verdict.confidence) == 0.4


# ---------------------------------------------------------------------------
# Shared parser: invalid decision / confidence → fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,reason_substr",
    [
        (
            {"decision": "approve", "confidence": 0.99, "reason": "unknown decision"},
            "invalid decision",
        ),
        (
            {"decision": "APPROVED", "confidence": 0.99, "reason": "case folded"},
            "invalid decision",
        ),
        (
            {"decision": "Approved", "confidence": 0.99, "reason": "title case"},
            "invalid decision",
        ),
        (
            {"decision": "maybe", "confidence": 0.99, "reason": "unknown"},
            "invalid decision",
        ),
        (
            {"decision": 1, "confidence": 0.99, "reason": "non-string decision"},
            "invalid decision",
        ),
        (
            {"decision": "approved", "confidence": True, "reason": "bool conf"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": False, "reason": "bool false conf"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": "0.99", "reason": "string conf"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": float("nan"), "reason": "nan"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": float("inf"), "reason": "inf"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": float("-inf"), "reason": "-inf"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": -0.1, "reason": "below"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": 1.01, "reason": "above"},
            "invalid confidence",
        ),
        (
            {"decision": "rejected", "confidence": True, "reason": "bool reject"},
            "invalid confidence",
        ),
        (
            {"decision": "rejected", "confidence": "0.9", "reason": "string reject"},
            "invalid confidence",
        ),
        (
            {"decision": "APPROVED", "confidence": True, "reason": "both bad"},
            "invalid",
        ),
        (
            {"decision": "approved", "confidence": None, "reason": "null conf"},
            "invalid confidence",
        ),
        (
            {"decision": "approved", "confidence": [0.9], "reason": "list conf"},
            "invalid confidence",
        ),
    ],
)
def test_parse_verdict_invalid_decision_or_confidence_fail_closed(
    payload: dict[str, Any],
    reason_substr: str,
) -> None:
    verdict = _verdict_from_payload(payload)
    assert verdict.decision == "kept", f"must not promote: {payload!r} -> {verdict}"
    conf_f = _assert_float_confidence(verdict.confidence)
    assert conf_f == 0.0
    assert reason_substr in verdict.reason


def test_parse_verdict_malformed_json_fail_closed() -> None:
    verdict = _parse_verdict({"text": "not json at all {{"})
    assert verdict.decision == "kept"
    assert _assert_float_confidence(verdict.confidence) == 0.5
    assert "Failed to parse" in verdict.reason


def test_parse_verdict_empty_braces_object_defaults_kept() -> None:
    # empty object: decision defaults to kept, confidence defaults to 0.5 (valid)
    verdict = _parse_verdict({"text": "{}"})
    assert verdict.decision == "kept"
    assert _assert_float_confidence(verdict.confidence) == 0.5


def test_parse_verdict_non_object_json_root_fail_closed() -> None:
    # No object braces → cannot extract verdict JSON; fail closed.
    verdict = _parse_verdict({"text": '["approved", 0.9]'})
    assert verdict.decision == "kept"
    conf_f = _assert_float_confidence(verdict.confidence)
    assert conf_f == 0.5
    assert "Failed to parse" in verdict.reason


def test_parse_verdict_never_retains_raw_string_confidence() -> None:
    verdict = _parse_verdict(
        {"text": json.dumps({"decision": "approved", "confidence": "0.99", "reason": "x"})}
    )
    assert verdict.decision == "kept"
    assert type(verdict.confidence) is float
    assert verdict.confidence == 0.0


# ---------------------------------------------------------------------------
# Style / Episode regression: malformed confidence cannot TypeError or promote
# ---------------------------------------------------------------------------


async def _seed_style_pending(db_path: Path, expression_id: str = "s1") -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS style_expressions (
                expression_id TEXT PRIMARY KEY,
                situation TEXT NOT NULL DEFAULT '',
                style TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                confidence REAL NOT NULL DEFAULT 0.5,
                meta_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        await db.execute(
            """INSERT INTO style_expressions
               (expression_id, situation, style, scope, group_id, status, confidence, meta_json, updated_at)
               VALUES (?, 'greeting', 'casual', 'group', 'g1', 'pending', 0.8, '{}', '2026-07-16T00:00:00+08:00')""",
            (expression_id,),
        )
        await db.commit()


async def _seed_episode_candidate(db_path: Path, episode_id: str = "e1") -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                situation TEXT NOT NULL DEFAULT '',
                reflection TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.5,
                episode_state TEXT NOT NULL DEFAULT 'candidate',
                meta_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        await db.execute(
            """INSERT INTO episodes
               (episode_id, situation, reflection, group_id, confidence, episode_state, meta_json, updated_at)
               VALUES (?, 'met user', 'remember this', 'g1', 0.8, 'candidate', '{}', '2026-07-16T00:00:00+08:00')""",
            (episode_id,),
        )
        await db.commit()


class _ScriptedLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    async def _call(self, request: Any) -> dict[str, Any]:
        self.calls += 1
        conf = self.payload.get("confidence")
        if isinstance(conf, float) and not math.isfinite(conf):
            text = json.dumps(self.payload, allow_nan=True)
        else:
            text = json.dumps(self.payload, ensure_ascii=False)
        return {"text": text}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_payload",
    [
        {"decision": "approved", "confidence": True, "reason": "bool"},
        {"decision": "approved", "confidence": "0.99", "reason": "string"},
        {"decision": "approved", "confidence": float("nan"), "reason": "nan"},
        {"decision": "APPROVED", "confidence": 0.99, "reason": "case"},
        {"decision": "rejected", "confidence": "0.9", "reason": "string reject"},
    ],
)
async def test_style_reviewer_malformed_confidence_stays_pending(
    tmp_path: Path,
    bad_payload: dict[str, Any],
) -> None:
    db_path = tmp_path / "style.db"
    await _seed_style_pending(db_path)
    reviewer = StyleAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM(bad_payload)

    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert llm.calls == 1

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT status, meta_json FROM style_expressions WHERE expression_id = 's1'"
        )
        row = await cur.fetchone()
        assert row is not None
        status, meta_json = row[0], row[1]
        assert status == "pending", f"must not approve/reject on bad conf, got {status}"
        meta = json.loads(meta_json)
        ai = meta.get("ai_review") or {}
        assert ai.get("decision") == "kept"
        conf = ai.get("confidence")
        assert not isinstance(conf, bool)
        assert isinstance(conf, (int, float))
        assert math.isfinite(float(conf))
        assert float(conf) == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_payload",
    [
        {"decision": "approved", "confidence": True, "reason": "bool"},
        {"decision": "approved", "confidence": "0.99", "reason": "string"},
        {"decision": "approved", "confidence": float("nan"), "reason": "nan"},
        {"decision": "APPROVED", "confidence": 0.99, "reason": "case"},
        {"decision": "rejected", "confidence": "0.9", "reason": "string reject"},
    ],
)
async def test_episode_reviewer_malformed_confidence_stays_candidate(
    tmp_path: Path,
    bad_payload: dict[str, Any],
) -> None:
    db_path = tmp_path / "episode.db"
    await _seed_episode_candidate(db_path)
    reviewer = EpisodeAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM(bad_payload)

    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert llm.calls == 1

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT episode_state, meta_json FROM episodes WHERE episode_id = 'e1'"
        )
        row = await cur.fetchone()
        assert row is not None
        state, meta_json = row[0], row[1]
        assert state == "candidate", f"must not promote on bad conf, got {state}"
        meta = json.loads(meta_json)
        ai = meta.get("ai_review") or {}
        assert ai.get("decision") == "kept"
        conf = ai.get("confidence")
        assert not isinstance(conf, bool)
        assert isinstance(conf, (int, float))
        assert math.isfinite(float(conf))
        assert float(conf) == 0.0


# ---------------------------------------------------------------------------
# Applied-outcome contract: counters reflect state after thresholds, not LLM raw
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_style_low_confidence_approve_counts_as_kept(tmp_path: Path) -> None:
    """Valid approved decision below auto_approve threshold → applied kept."""
    db_path = tmp_path / "style-low.db"
    await _seed_style_pending(db_path, "s_low")
    reviewer = StyleAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM({
        "decision": "approved",
        "confidence": 0.60,
        "reason": "borderline style",
    })
    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.approved_in_batch == 0
    assert result.rejected_in_batch == 0
    assert result.kept_in_batch == 1
    assert result.processed_in_batch == 1
    assert result.remaining == 1
    assert result.completed is False

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT status FROM style_expressions WHERE expression_id = 's_low'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == "pending"

    state = await reviewer.get_state()
    assert state.kept == 1
    assert state.approved == 0
    assert state.rejected == 0


@pytest.mark.asyncio
async def test_style_low_confidence_reject_counts_as_kept(tmp_path: Path) -> None:
    """Valid rejected decision below auto_reject threshold → applied kept."""
    db_path = tmp_path / "style-low-rej.db"
    await _seed_style_pending(db_path, "s_rej")
    reviewer = StyleAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM({
        "decision": "rejected",
        "confidence": 0.30,
        "reason": "unsure reject",
    })
    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.approved_in_batch == 0
    assert result.rejected_in_batch == 0
    assert result.kept_in_batch == 1
    assert result.remaining == 1
    assert result.completed is False

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT status FROM style_expressions WHERE expression_id = 's_rej'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == "pending"


@pytest.mark.asyncio
async def test_style_high_confidence_approve_and_reject_applied_counters(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "style-high.db"
    await _seed_style_pending(db_path, "s_a")
    await _seed_style_pending(db_path, "s_r")
    reviewer = StyleAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
        concurrency=1,
    )

    class _SeqLLM:
        def __init__(self) -> None:
            self.queue = [
                {"decision": "approved", "confidence": 0.95, "reason": "good"},
                {"decision": "rejected", "confidence": 0.90, "reason": "noise"},
            ]
            self.calls = 0

        async def _call(self, request: Any) -> dict[str, Any]:
            self.calls += 1
            payload = self.queue.pop(0)
            return {"text": json.dumps(payload, ensure_ascii=False)}

    llm = _SeqLLM()
    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert llm.calls == 2
    assert result.approved_in_batch == 1
    assert result.rejected_in_batch == 1
    assert result.kept_in_batch == 0
    assert result.remaining == 0
    assert result.completed is True

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT expression_id, status FROM style_expressions ORDER BY expression_id"
        )
        rows = {r[0]: r[1] for r in await cur.fetchall()}
        assert rows["s_a"] == "approved"
        assert rows["s_r"] == "rejected"


@pytest.mark.asyncio
async def test_style_empty_cursor_sticky_kept_reports_true_remaining(
    tmp_path: Path,
) -> None:
    """batch_size=2 over 3 sticky kept: empty page never claims remaining=0/completed."""
    db_path = tmp_path / "style-cursor.db"
    for i in range(3):
        await _seed_style_pending(db_path, f"s{i}")
    reviewer = StyleAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.90,
        auto_reject_max_confidence=0.20,
    )

    class _KeepLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def _call(self, request: Any) -> dict[str, Any]:
            self.calls += 1
            return {
                "text": json.dumps({
                    "decision": "kept",
                    "confidence": 0.4,
                    "reason": f"keep{self.calls}",
                }),
            }

    llm = _KeepLLM()
    r1 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r1.ok is True
    assert r1.processed_in_batch == 2
    assert r1.kept_in_batch == 2
    assert r1.completed is False
    assert r1.remaining == 3

    r2 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r2.ok is True
    assert r2.processed_in_batch == 1
    assert r2.kept_in_batch == 1
    assert r2.completed is False
    assert r2.remaining == 3

    r3 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r3.ok is True
    assert r3.processed_in_batch == 0
    assert r3.remaining == 3
    assert r3.completed is False

    state = await reviewer.get_state()
    assert state.active is False
    assert state.last_done_at == ""

    r4 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r4.ok is True
    assert r4.processed_in_batch == 2
    assert r4.completed is False
    assert r4.remaining == 3


@pytest.mark.asyncio
async def test_episode_low_confidence_approve_counts_as_kept(tmp_path: Path) -> None:
    db_path = tmp_path / "ep-low.db"
    await _seed_episode_candidate(db_path, "e_low")
    reviewer = EpisodeAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM({
        "decision": "approved",
        "confidence": 0.55,
        "reason": "borderline episode",
    })
    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.approved_in_batch == 0
    assert result.rejected_in_batch == 0
    assert result.kept_in_batch == 1
    assert result.remaining == 1
    assert result.completed is False

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT episode_state FROM episodes WHERE episode_id = 'e_low'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == "candidate"


@pytest.mark.asyncio
async def test_episode_low_confidence_reject_counts_as_kept(tmp_path: Path) -> None:
    db_path = tmp_path / "ep-low-rej.db"
    await _seed_episode_candidate(db_path, "e_rej")
    reviewer = EpisodeAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM({
        "decision": "rejected",
        "confidence": 0.25,
        "reason": "unsure",
    })
    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.kept_in_batch == 1
    assert result.rejected_in_batch == 0
    assert result.remaining == 1
    assert result.completed is False

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT episode_state FROM episodes WHERE episode_id = 'e_rej'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == "candidate"


@pytest.mark.asyncio
async def test_episode_high_confidence_maps_enabled_and_disabled(
    tmp_path: Path,
) -> None:
    """Applied approved → enabled_for_prompt; rejected → disabled; counters match."""
    db_path = tmp_path / "ep-high.db"
    await _seed_episode_candidate(db_path, "e_a")
    await _seed_episode_candidate(db_path, "e_r")
    reviewer = EpisodeAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
        concurrency=1,
    )

    class _SeqLLM:
        def __init__(self) -> None:
            self.queue = [
                {"decision": "approved", "confidence": 0.95, "reason": "good"},
                {"decision": "rejected", "confidence": 0.88, "reason": "noise"},
            ]
            self.calls = 0

        async def _call(self, request: Any) -> dict[str, Any]:
            self.calls += 1
            payload = self.queue.pop(0)
            return {"text": json.dumps(payload, ensure_ascii=False)}

    llm = _SeqLLM()
    result = await reviewer.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.approved_in_batch == 1
    assert result.rejected_in_batch == 1
    assert result.kept_in_batch == 0
    assert result.remaining == 0
    assert result.completed is True

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT episode_id, episode_state FROM episodes ORDER BY episode_id"
        )
        rows = {r[0]: r[1] for r in await cur.fetchall()}
        assert rows["e_a"] == "enabled_for_prompt"
        assert rows["e_r"] == "disabled"


@pytest.mark.asyncio
async def test_episode_empty_cursor_sticky_kept_reports_true_remaining(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ep-cursor.db"
    for i in range(3):
        await _seed_episode_candidate(db_path, f"e{i}")
    reviewer = EpisodeAIReviewer(db_path)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.90,
        auto_reject_max_confidence=0.20,
    )

    class _KeepLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def _call(self, request: Any) -> dict[str, Any]:
            self.calls += 1
            return {
                "text": json.dumps({
                    "decision": "kept",
                    "confidence": 0.4,
                    "reason": f"keep{self.calls}",
                }),
            }

    llm = _KeepLLM()
    r1 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r1.processed_in_batch == 2
    assert r1.kept_in_batch == 2
    assert r1.remaining == 3
    assert r1.completed is False

    r2 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r2.processed_in_batch == 1
    assert r2.kept_in_batch == 1
    assert r2.remaining == 3
    assert r2.completed is False

    r3 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r3.processed_in_batch == 0
    assert r3.remaining == 3
    assert r3.completed is False

    state = await reviewer.get_state()
    assert state.active is False
    assert state.last_done_at == ""

    r4 = await reviewer.run_one_batch(batch_size=2, config=config, llm_client=llm)
    assert r4.processed_in_batch == 2
    assert r4.remaining == 3
    assert r4.completed is False


# ---------------------------------------------------------------------------
# SlangReviewerAdapter: applied outcomes + honest remaining via count_pending
# ---------------------------------------------------------------------------


class _FakeSlangTerm:
    def __init__(
        self,
        term_id: str,
        *,
        term: str = "梗",
        meaning: str = "意思",
        confidence: float = 0.8,
        meta: dict[str, Any] | None = None,
        status: str = "candidate",
    ) -> None:
        self.term_id = term_id
        self.term = term
        self.meaning = meaning
        self.confidence = confidence
        self.meta = dict(meta or {})
        self.status = status
        self.notes = ""
        self.group_id = "g1"
        self.usage_count = 5


class _FakeSlangStore:
    """Minimal store surface for SlangReviewerAdapter unit tests."""

    def __init__(self, terms: list[_FakeSlangTerm]) -> None:
        self.terms = {t.term_id: t for t in terms}
        self.meta: dict[str, Any] = {}
        self.promotions: list[tuple[str, str]] = []
        self.meta_updates: list[str] = []

    def _require_db(self) -> Any:
        return self

    async def execute(self, sql: str, params: Any = ()) -> Any:
        # count_pending path: SELECT COUNT(*) ... ai_reviewed_at IS NULL
        if "COUNT(*)" in sql and "ai_reviewed_at" in sql:
            n = sum(
                1
                for t in self.terms.values()
                if t.status == "candidate" and not (t.meta or {}).get("ai_reviewed_at")
            )

            class _Cur:
                async def fetchone(self_inner: Any) -> tuple[int]:
                    return (n,)

            return _Cur()
        raise AssertionError(f"unexpected SQL: {sql}")

    async def list_backlog_candidates(
        self,
        *,
        after_term_id: str = "",
        min_confidence: float = 0.0,
        min_usage_count: int = 0,
        limit: int = 50,
        gated_by_threshold: bool = False,
    ) -> list[_FakeSlangTerm]:
        items = [
            t
            for t in self.terms.values()
            if t.status == "candidate" and t.confidence >= min_confidence
        ]
        items.sort(key=lambda t: t.term_id)
        if after_term_id:
            items = [t for t in items if t.term_id > after_term_id]
        return items[:limit]

    async def count_backlog_candidates(self, **kwargs: Any) -> int:
        return sum(1 for t in self.terms.values() if t.status == "candidate")

    async def set_meta(self, key: str, value: Any) -> None:
        self.meta[key] = value

    async def get_meta(self, key: str, default: Any = None) -> Any:
        return self.meta.get(key, default)

    async def update_term(self, term_id: str, **fields: Any) -> bool:
        t = self.terms[term_id]
        if "meta" in fields:
            t.meta = dict(fields["meta"] or {})
        self.meta_updates.append(term_id)
        return True

    async def set_status(self, term_id: str, status: str, actor: str = "") -> bool:
        self.terms[term_id].status = status
        self.promotions.append((term_id, status))
        return True


class _FakeBacklogReviewer:
    async def get_state(self, store: Any) -> dict[str, Any]:
        raw = store.meta.get("backlog_review_state", {})
        return dict(raw) if isinstance(raw, dict) else {}

    async def reset(self, store: Any) -> dict[str, Any]:
        cleared = {"active": False, "last_term_id": "", "processed": 0}
        store.meta["backlog_review_state"] = cleared
        return cleared

    async def status(self, store: Any, settings: Any = None) -> dict[str, Any]:
        st = await self.get_state(store)
        return {
            "active": bool(st.get("active", False)),
            "processed": int(st.get("processed", 0)),
            "approved": int(st.get("approved", 0)),
            "muted": int(st.get("muted", 0)),
            "kept": int(st.get("kept", 0)),
            "total_at_start": int(st.get("total_at_start", 0)),
            "remaining": 0,
            "started_at": "",
            "last_progress_at": "",
            "last_done_at": "",
        }


def _make_slang_adapter(store: _FakeSlangStore) -> Any:
    from services.learning_autopilot.slang_adapter import SlangReviewerAdapter

    return SlangReviewerAdapter(
        backlog_reviewer=_FakeBacklogReviewer(),
        store=store,
        message_log=None,
        settings_loader=lambda: None,
    )


@pytest.mark.asyncio
async def test_slang_adapter_low_confidence_approve_counts_kept() -> None:
    store = _FakeSlangStore([_FakeSlangTerm("t1")])
    adapter = _make_slang_adapter(store)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM({
        "decision": "approved",
        "confidence": 0.60,
        "reason": "borderline slang",
    })
    result = await adapter.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.approved_in_batch == 0
    assert result.rejected_in_batch == 0
    assert result.kept_in_batch == 1
    assert result.processed_in_batch == 1
    # Sticky kept still candidate with ai_reviewed_at → not actionable remaining
    assert result.remaining == 0
    assert result.completed is True
    assert store.terms["t1"].status == "candidate"
    assert store.terms["t1"].meta.get("ai_reviewed_at")
    assert store.promotions == []


@pytest.mark.asyncio
async def test_slang_adapter_high_confidence_approve_and_mute_applied() -> None:
    store = _FakeSlangStore([
        _FakeSlangTerm("t_a", term="好梗"),
        _FakeSlangTerm("t_r", term="烂梗"),
    ])
    adapter = _make_slang_adapter(store)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
        concurrency=1,
    )

    class _SeqLLM:
        def __init__(self) -> None:
            self.queue = [
                {"decision": "approved", "confidence": 0.95, "reason": "good"},
                {"decision": "rejected", "confidence": 0.90, "reason": "noise"},
            ]
            self.calls = 0

        async def _call(self, request: Any) -> dict[str, Any]:
            self.calls += 1
            payload = self.queue.pop(0)
            return {"text": json.dumps(payload, ensure_ascii=False)}

    llm = _SeqLLM()
    result = await adapter.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert llm.calls == 2
    assert result.approved_in_batch == 1
    assert result.rejected_in_batch == 1
    assert result.kept_in_batch == 0
    assert result.remaining == 0
    assert result.completed is True
    assert store.terms["t_a"].status == "approved"
    assert store.terms["t_r"].status == "muted"
    assert ("t_a", "approved") in store.promotions
    assert ("t_r", "muted") in store.promotions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_payload",
    [
        {"decision": "approved", "confidence": True, "reason": "bool"},
        {"decision": "approved", "confidence": "0.99", "reason": "string"},
        {"decision": "approved", "confidence": float("nan"), "reason": "nan"},
        {"decision": "APPROVED", "confidence": 0.99, "reason": "case"},
        {"decision": "rejected", "confidence": "0.9", "reason": "string reject"},
    ],
)
async def test_slang_adapter_malformed_confidence_kept_no_promote_mute(
    bad_payload: dict[str, Any],
) -> None:
    store = _FakeSlangStore([_FakeSlangTerm("t_bad")])
    adapter = _make_slang_adapter(store)
    config = AggressivenessConfig(
        auto_approve_min_confidence=0.72,
        auto_reject_max_confidence=0.50,
    )
    llm = _ScriptedLLM(bad_payload)
    result = await adapter.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.approved_in_batch == 0
    assert result.rejected_in_batch == 0
    assert result.kept_in_batch == 1
    assert store.terms["t_bad"].status == "candidate"
    assert store.promotions == []
    assert store.terms["t_bad"].meta.get("ai_review_decision") == "kept"
    conf = store.terms["t_bad"].meta.get("ai_review_confidence")
    assert not isinstance(conf, bool)
    assert isinstance(conf, (int, float))
    assert math.isfinite(float(conf))
    assert float(conf) == 0.0


@pytest.mark.asyncio
async def test_slang_adapter_already_reviewed_page_honest_remaining() -> None:
    """Page of only ai_reviewed sticky candidates: no LLM; remaining=0 when none actionable."""
    sticky = _FakeSlangTerm(
        "t_sticky",
        meta={"ai_reviewed_at": "2026-07-16T00:00:00+08:00", "ai_review_decision": "kept"},
    )
    # Only sticky candidates → page has no unreviewed items; remaining is honest 0.
    store = _FakeSlangStore([sticky])
    adapter = _make_slang_adapter(store)
    config = AggressivenessConfig()
    llm = _ScriptedLLM({
        "decision": "approved", "confidence": 0.99, "reason": "should not call",
    })
    result = await adapter.run_one_batch(batch_size=10, config=config, llm_client=llm)
    assert result.ok is True
    assert result.processed_in_batch == 0
    assert result.remaining == 0  # no unreviewed actionable
    assert result.completed is True
    assert llm.calls == 0  # unreviewed empty short-circuits before LLM
