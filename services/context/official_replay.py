"""Offline replay helpers for official long-memory dataset shapes."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.context.service import ContextService
from services.context.sources import MemoryContextSource
from services.memory.card_store import CardStore, NewCard

OFFICIAL_REPLAY_VERSION = "longmemeval_raw_turn_replay_v1"
LONGMEMEVAL_UPSTREAM_REPO = "xiaowu0162/LongMemEval"
LONGMEMEVAL_UPSTREAM_COMMIT = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
LONGMEMEVAL_QUESTION_TYPES = frozenset(
    {
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
        "temporal-reasoning",
        "knowledge-update",
        "multi-session",
    }
)
LONGMEMEVAL_TURN_ROLES = frozenset({"user", "assistant"})


@dataclass(frozen=True, slots=True)
class LongMemEvalTurn:
    role: str
    content: str
    has_answer: bool = False


@dataclass(frozen=True, slots=True)
class LongMemEvalSession:
    session_id: str
    date: str
    turns: tuple[LongMemEvalTurn, ...]


@dataclass(frozen=True, slots=True)
class LongMemEvalReplayCase:
    question_id: str
    question_type: str
    question: str
    sessions: tuple[LongMemEvalSession, ...]
    answer_session_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_any: float
    recall_all: float
    ndcg: float


@dataclass(frozen=True, slots=True)
class ReplayTurn:
    session_id: str
    turn_ref: str
    source_message_id: str
    captured_at: str
    content: str
    search_content: str
    has_answer: bool


@dataclass(frozen=True, slots=True)
class ReplayCaseResult:
    question_id: str
    question_type: str
    is_abstention: bool
    indexed_turn_count: int
    packed_hit_count: int
    pack_chars: int
    omitted_count: int
    ranked_session_ids: tuple[str, ...]
    ranked_turn_refs: tuple[str, ...]
    session_metrics: RetrievalMetrics | None
    turn_metrics: RetrievalMetrics | None


@dataclass(frozen=True, slots=True)
class ReplayReport:
    dataset_sha256: str
    total_cases: int
    selected_cases: int
    results: tuple[ReplayCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        session_metrics = [result.session_metrics for result in self.results if result.session_metrics is not None]
        turn_metrics = [result.turn_metrics for result in self.results if result.turn_metrics is not None]
        return {
            "version": OFFICIAL_REPLAY_VERSION,
            "upstream_repo": LONGMEMEVAL_UPSTREAM_REPO,
            "upstream_commit": LONGMEMEVAL_UPSTREAM_COMMIT,
            "dataset_sha256": self.dataset_sha256,
            "total_cases": self.total_cases,
            "selected_cases": self.selected_cases,
            "benchmark_parity": False,
            "deterministic_provenance": True,
            "ranking_tie_break_deterministic": False,
            "memory_extraction_scored": False,
            "answer_generation_scored": False,
            "upstream_turn_to_session_equivalent": False,
            "session_ranking_scope": "packed_unique_sessions_at_turn_top_k",
            "turn_ranking_scope": "packed_turns_at_turn_top_k",
            "evaluation_scope": "raw_turn_cardstore_context_pack",
            "aggregates": {
                "session_scored_cases": len(session_metrics),
                "turn_scored_cases": len(turn_metrics),
                "abstention_cases": sum(1 for result in self.results if result.is_abstention),
                "session_recall_any": _mean_metric(session_metrics, "recall_any"),
                "session_recall_all": _mean_metric(session_metrics, "recall_all"),
                "session_ndcg": _mean_metric(session_metrics, "ndcg"),
                "turn_recall_any": _mean_metric(turn_metrics, "recall_any"),
                "turn_recall_all": _mean_metric(turn_metrics, "recall_all"),
                "turn_ndcg": _mean_metric(turn_metrics, "ndcg"),
                "mean_pack_chars": _mean([float(result.pack_chars) for result in self.results]),
                "total_indexed_turns": sum(result.indexed_turn_count for result in self.results),
                "total_omitted_hits": sum(result.omitted_count for result in self.results),
            },
            "results": [_case_result_to_dict(result) for result in self.results],
        }


async def run_longmemeval_dataset(
    dataset_path: str | Path,
    *,
    limit: int | None = None,
    question_types: set[str] | None = None,
    top_k: int = 10,
    max_chars: int = 2400,
) -> ReplayReport:
    """Run an isolated raw-turn replay over a user-supplied dataset file."""
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("limit must be a positive integer or null")
    _validate_replay_options(top_k=top_k, max_chars=max_chars)
    path = Path(dataset_path)
    raw = path.read_bytes()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise TypeError("LongMemEval dataset must be a JSON list")
    cases = load_longmemeval_cases(parsed)
    selected = cases
    if question_types is not None:
        allowed_types = {str(value).strip() for value in question_types if str(value).strip()}
        selected = [case for case in selected if case.question_type in allowed_types]
    if limit is not None:
        selected = selected[:limit]

    results: list[ReplayCaseResult] = []
    with tempfile.TemporaryDirectory(prefix="omubot-longmemeval-") as temp_dir:
        root = Path(temp_dir)
        for index, case in enumerate(selected):
            results.append(
                await run_longmemeval_case(
                    case,
                    db_path=root / f"case-{index:05d}.db",
                    top_k=top_k,
                    max_chars=max_chars,
                )
            )
    return ReplayReport(
        dataset_sha256=hashlib.sha256(raw).hexdigest(),
        total_cases=len(cases),
        selected_cases=len(selected),
        results=tuple(results),
    )


def _metric_to_dict(metrics: RetrievalMetrics | None) -> dict[str, float] | None:
    if metrics is None:
        return None
    return {
        "recall_any": metrics.recall_any,
        "recall_all": metrics.recall_all,
        "ndcg": metrics.ndcg,
    }


def _case_result_to_dict(result: ReplayCaseResult) -> dict[str, Any]:
    return {
        "question_id": _opaque_report_id("case", result.question_id),
        "question_type": result.question_type,
        "is_abstention": result.is_abstention,
        "indexed_turn_count": result.indexed_turn_count,
        "packed_hit_count": result.packed_hit_count,
        "pack_chars": result.pack_chars,
        "omitted_count": result.omitted_count,
        "ranked_session_ids": [
            _opaque_report_id("session", result.question_id, value)
            for value in result.ranked_session_ids
        ],
        "ranked_turn_refs": [
            _opaque_report_id("turn", result.question_id, value)
            for value in result.ranked_turn_refs
        ],
        "session_metrics": _metric_to_dict(result.session_metrics),
        "turn_metrics": _metric_to_dict(result.turn_metrics),
    }


def _opaque_report_id(kind: str, *parts: str) -> str:
    identity = "\0".join((kind, *parts))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{kind}_{digest}"


def _mean_metric(metrics: Sequence[RetrievalMetrics], field: str) -> float | None:
    return _mean([float(getattr(item, field)) for item in metrics])


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


async def run_longmemeval_case(
    case: LongMemEvalReplayCase,
    *,
    db_path: str | Path,
    top_k: int = 10,
    max_chars: int = 2400,
) -> ReplayCaseResult:
    """Replay one LongMemEval case through the real Omubot card/context stack."""
    _validate_replay_options(top_k=top_k, max_chars=max_chars)

    turns = build_replay_turns(case)
    by_source_message_id = {turn.source_message_id: turn for turn in turns}
    scope_id = "lme_" + hashlib.sha256(case.question_id.encode("utf-8")).hexdigest()[:20]
    store = CardStore(str(db_path))
    try:
        await store.init()
        for turn in turns:
            await store.add_card(
                NewCard(
                    category="fact",
                    scope="user",
                    scope_id=scope_id,
                    content=turn.search_content,
                    confidence=1.0,
                    source="longmemeval_official_raw_turn",
                ),
                source_msg_id=turn.source_message_id,
                captured_at=turn.captured_at,
                captured_by="longmemeval_official_replay",
            )
        service = ContextService([MemoryContextSource(store)])
        pack = await service.build_prompt_context(
            case.question,
            user_id=scope_id,
            top_k=top_k,
            max_chars=max_chars,
            mode="fact",
        )
        selected_turns: list[ReplayTurn] = []
        for hit in pack.hits:
            provenance = hit.provenance
            if provenance is None:
                continue
            source_message_id = str(provenance.source_message_id or "")
            turn = by_source_message_id.get(source_message_id)
            if turn is not None:
                selected_turns.append(turn)

        ranked_turn_refs = tuple(turn.turn_ref for turn in selected_turns)
        ranked_session_ids = tuple(dict.fromkeys(turn.session_id for turn in selected_turns))
        is_abstention = case.question_id.endswith("_abs")
        relevant_turn_refs = tuple(turn.turn_ref for turn in turns if turn.has_answer)
        session_metrics = (
            None
            if is_abstention or not case.answer_session_ids
            else score_ranked_refs(
                ranked_session_ids,
                case.answer_session_ids,
                k=top_k,
            )
        )
        turn_metrics = (
            None
            if is_abstention or not relevant_turn_refs
            else score_ranked_refs(
                ranked_turn_refs,
                relevant_turn_refs,
                k=top_k,
            )
        )
        return ReplayCaseResult(
            question_id=case.question_id,
            question_type=case.question_type,
            is_abstention=is_abstention,
            indexed_turn_count=len(turns),
            packed_hit_count=len(pack.hits),
            pack_chars=len(pack.text),
            omitted_count=int(pack.omitted_count),
            ranked_session_ids=ranked_session_ids,
            ranked_turn_refs=ranked_turn_refs,
            session_metrics=session_metrics,
            turn_metrics=turn_metrics,
        )
    finally:
        await store.close()


def _validate_replay_options(*, top_k: int, max_chars: int) -> None:
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    if type(max_chars) is not int or max_chars < 0:
        raise ValueError("max_chars must be a non-negative integer")


def build_replay_turns(case: LongMemEvalReplayCase) -> tuple[ReplayTurn, ...]:
    """Flatten one official case into deterministic raw-turn replay rows."""
    rows: list[ReplayTurn] = []
    for session in case.sessions:
        for turn_index, turn in enumerate(session.turns):
            identity = "\0".join((case.question_id, session.session_id, str(turn_index)))
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            rows.append(
                ReplayTurn(
                    session_id=session.session_id,
                    turn_ref=f"{session.session_id}_{turn_index}",
                    source_message_id=f"longmemeval:{digest}",
                    captured_at=session.date,
                    content=f"[{session.date}] {turn.role}: {turn.content}",
                    search_content=f"[{session.date}] {turn.content}",
                    has_answer=turn.has_answer,
                )
            )
    return tuple(rows)


def score_ranked_refs(
    ranked_refs: Sequence[str],
    relevant_refs: Sequence[str],
    *,
    k: int,
) -> RetrievalMetrics:
    """Score ranked references with the LongMemEval retrieval metrics."""
    if type(k) is not int or k <= 0:
        raise ValueError("retrieval metric k must be a positive integer")
    relevant = {str(value) for value in relevant_refs if str(value)}
    top = [str(value) for value in ranked_refs[:k]]
    if len(top) != len(set(top)):
        raise ValueError("duplicate ranked references are not valid retrieval results")
    recalled = set(top)
    recall_any = float(bool(relevant & recalled))
    recall_all = float(relevant.issubset(recalled))

    relevances = [1 if value in relevant else 0 for value in top]
    ideal = [1] * min(k, len(relevant))
    ideal.extend([0] * (k - len(ideal)))
    ideal_dcg = _dcg(ideal)
    ndcg = _dcg(relevances) / ideal_dcg if ideal_dcg > 0 else 0.0
    return RetrievalMetrics(
        recall_any=recall_any,
        recall_all=recall_all,
        ndcg=ndcg,
    )


def _dcg(relevances: Sequence[int]) -> float:
    if not relevances:
        return 0.0
    score = float(relevances[0])
    # Match LongMemEval's pinned eval_utils.py exactly, including its log2(2..n) discount.
    for index, relevance in enumerate(relevances[1:], start=2):
        score += float(relevance) / math.log2(index)
    return score


def _require_nonempty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def load_longmemeval_cases(payload: Sequence[Mapping[str, Any]]) -> list[LongMemEvalReplayCase]:
    """Parse LongMemEval v1 rows into strict replay cases."""
    cases: list[LongMemEvalReplayCase] = []
    question_ids: set[str] = set()
    for raw_case in payload:
        if not isinstance(raw_case, Mapping):
            raise TypeError("LongMemEval case must be an object")
        question_id = _require_nonempty_string(raw_case.get("question_id"), field="question_id")
        question_type = _require_nonempty_string(raw_case.get("question_type"), field="question_type")
        question = _require_nonempty_string(raw_case.get("question"), field="question")
        _require_nonempty_string(raw_case.get("answer"), field="answer")
        _require_nonempty_string(raw_case.get("question_date"), field="question_date")
        if question_type not in LONGMEMEVAL_QUESTION_TYPES:
            raise ValueError("question_type must be an official LongMemEval v1 type")
        if question_id in question_ids:
            raise ValueError(f"duplicate question_id: {question_id}")
        question_ids.add(question_id)
        session_ids = raw_case.get("haystack_session_ids")
        session_dates = raw_case.get("haystack_dates")
        session_rows = raw_case.get("haystack_sessions")
        if not isinstance(session_ids, list):
            raise TypeError("haystack_session_ids must be a list")
        if not isinstance(session_dates, list):
            raise TypeError("haystack_dates must be a list")
        if not isinstance(session_rows, list):
            raise TypeError("haystack_sessions must be a list")
        if len({len(session_ids), len(session_dates), len(session_rows)}) != 1:
            raise ValueError("haystack arrays must have equal lengths")
        if not session_ids:
            raise ValueError("haystack arrays must not be empty")
        normalized_session_ids = [
            _require_nonempty_string(value, field="haystack_session_ids item") for value in session_ids
        ]
        normalized_session_dates = [
            _require_nonempty_string(value, field="haystack_dates item") for value in session_dates
        ]
        if len(normalized_session_ids) != len(set(normalized_session_ids)):
            raise ValueError("duplicate haystack session_id")

        sessions: list[LongMemEvalSession] = []
        for session_id, date, raw_turns in zip(
            normalized_session_ids,
            normalized_session_dates,
            session_rows,
            strict=True,
        ):
            if not isinstance(raw_turns, list):
                raise TypeError("LongMemEval session must be a list of turns")
            if not raw_turns:
                raise ValueError("LongMemEval session turns must not be empty")
            turns: list[LongMemEvalTurn] = []
            for raw_turn in raw_turns:
                if not isinstance(raw_turn, Mapping):
                    raise TypeError("LongMemEval turn must be an object")
                has_answer = raw_turn.get("has_answer", False)
                if type(has_answer) is not bool:
                    raise TypeError("LongMemEval has_answer must be a boolean")
                role = _require_nonempty_string(
                    raw_turn.get("role"),
                    field="LongMemEval turn role",
                )
                if role not in LONGMEMEVAL_TURN_ROLES:
                    raise ValueError("LongMemEval turn role must be user or assistant")
                turns.append(
                    LongMemEvalTurn(
                        role=role,
                        content=_require_nonempty_string(raw_turn.get("content"), field="LongMemEval turn content"),
                        has_answer=has_answer,
                    )
                )
            sessions.append(
                LongMemEvalSession(
                    session_id=session_id,
                    date=date,
                    turns=tuple(turns),
                )
            )

        answer_session_ids = raw_case.get("answer_session_ids")
        if not isinstance(answer_session_ids, list):
            raise TypeError("answer_session_ids must be a list")
        answer_ids = tuple(
            _require_nonempty_string(value, field="answer_session_ids item") for value in answer_session_ids
        )
        if len(answer_ids) != len(set(answer_ids)):
            raise ValueError("duplicate answer_session_ids")
        known_session_ids = {session.session_id for session in sessions}
        if any(answer_id not in known_session_ids for answer_id in answer_ids):
            raise ValueError("answer_session_ids must reference haystack sessions")
        cases.append(
            LongMemEvalReplayCase(
                question_id=question_id,
                question_type=question_type,
                question=question,
                sessions=tuple(sessions),
                answer_session_ids=answer_ids,
            )
        )
    return cases
