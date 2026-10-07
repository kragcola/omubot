"""Strict, explicitly supplied LongMemEval v1 raw turns in an offline index.

This adapter reuses current lexical query tokens and pinned retrieval metrics.
It never enrolls raw human/assistant turns in production Memory or Knowledge.
One case owns one temporary SQLite database; reports expose only opaque refs.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    RootModel,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

# This offline adapter deliberately shares existing pure helpers without
# extending production owners or changing their public interfaces.
from .retrieval import _bounded_query_tokens  # pyright: ignore[reportPrivateUsage]
from .retrieval_evaluation import (
    MAX_PACK_CHARS,
    MAX_PACK_HITS,
    UPSTREAM_COMMIT,
    _metrics,  # pyright: ignore[reportPrivateUsage]
    _opaque,  # pyright: ignore[reportPrivateUsage]
)

EVALUATION_VERSION = "longmemeval-v1-raw-turn-sqlite-lexical-pack-v1"
QuestionType = Literal[
    "single-session-user", "single-session-assistant", "single-session-preference",
    "temporal-reasoning", "knowledge-update", "multi-session",
]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class ReplayInputError(ValueError):
    """Fixed schema/option codes without parser messages or input values."""


class ReplayError(RuntimeError):
    """Fixed execution failure without owner messages or input values."""


class _InputModel(BaseModel):
    # Official metadata outside the replay fields is ignored, never serialized.
    model_config = ConfigDict(strict=True, extra="ignore")


class _TurnInput(_InputModel):
    role: Literal["user", "assistant"]
    content: NonEmpty
    has_answer: bool = False

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank_content")
        return value


class _CaseInput(_InputModel):
    question_id: NonEmpty
    question_type: QuestionType
    question: NonEmpty
    answer: NonEmpty
    question_date: NonEmpty
    haystack_session_ids: list[NonEmpty]
    haystack_dates: list[NonEmpty]
    haystack_sessions: list[list[_TurnInput]]
    answer_session_ids: list[NonEmpty]

    @field_validator("question_id", "question", "answer", "question_date")
    @classmethod
    def nonblank_field(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank_field")
        return value

    @model_validator(mode="after")
    def official_structure(self) -> _CaseInput:
        ids, dates, sessions, answers = (self.haystack_session_ids, self.haystack_dates,
                                        self.haystack_sessions, self.answer_session_ids)
        if (not sessions or not len(ids) == len(dates) == len(sessions)
                or any(not turns for turns in sessions)
                or any(not value.strip() for value in (*ids, *dates, *answers))
                or len(set(ids)) != len(ids) or len(set(answers)) != len(answers)
                or not set(answers).issubset(ids)):
            raise ValueError("invalid_haystack")
        return self


class _DatasetInput(RootModel[list[_CaseInput]]):
    model_config = ConfigDict(strict=True)

    @model_validator(mode="after")
    def unique_questions(self) -> _DatasetInput:
        if len({case.question_id for case in self.root}) != len(self.root):
            raise ValueError("duplicate_question_id")
        return self


@dataclass(frozen=True)
class _Turn:
    turn_ref: str
    role: str
    content: str
    has_answer: bool


@dataclass(frozen=True)
class _Session:
    session_ref: str
    date: str
    turns: tuple[_Turn, ...]


@dataclass(frozen=True)
class _Case:
    case_ref: str
    question_type: QuestionType
    question: str
    sessions: tuple[_Session, ...]
    relevant_sessions: frozenset[str]
    is_abstention: bool


@dataclass(frozen=True)
class _Hit:
    turn_ref: str
    session_ref: str
    score: float


@dataclass(frozen=True)
class _Pack:
    hits: tuple[_Hit, ...]
    candidate_count: int
    omitted_count: int
    chars: int
    query_token_truncated: bool


def _parse(payload: bytes) -> tuple[_Case, ...]:
    try:
        dataset = _DatasetInput.model_validate_json(payload)
    except (ValidationError, ValueError, TypeError):
        raise ReplayInputError("invalid_longmemeval_schema") from None
    # Required answer/question_date and original IDs do not survive this step.
    cases: list[_Case] = []
    for case in dataset.root:
        sessions = tuple(_Session(_opaque("session", case.question_id, session_id), date,
            tuple(_Turn(_opaque("turn", case.question_id, session_id, str(index)),
                turn.role, turn.content, turn.has_answer) for index, turn in enumerate(turns)))
            for session_id, date, turns in zip(case.haystack_session_ids, case.haystack_dates,
                                               case.haystack_sessions, strict=True))
        cases.append(_Case(_opaque("case", case.question_id), case.question_type, case.question,
            sessions, frozenset(_opaque("session", case.question_id, ref)
                               for ref in case.answer_session_ids), case.question_id.endswith("_abs")))
    return tuple(cases)


async def _search_pack(db: sqlite3.Connection, question: str, *, top_k: int, max_chars: int) -> _Pack:
    # The official question is unchanged. Role labels are metadata, not tokens.
    tokens, truncated = _bounded_query_tokens(question)
    if not tokens:
        return _Pack((), 0, 0, 0, truncated)
    score = " + ".join("CASE WHEN instr(search_text, ?) > 0 THEN 1 ELSE 0 END" for _ in tokens)
    # This raw index uses the same substring coverage calculation as current
    # lexical Memory scoring, but has no fact/source-ID matching or typed gates.
    rows = db.execute(f"""SELECT turn_ref, session_ref, date, role, content,
            round(({score}) * 1.0 / ?, 6) AS score
        FROM turns WHERE score > 0 ORDER BY score DESC, ordinal ASC LIMIT ?""",
        (*tokens, len(tokens), top_k)).fetchall()
    hits: list[_Hit] = []
    chars = 0
    for raw in rows:
        turn_ref, session_ref, date, role, content, value = cast(
            tuple[str, str, str, str, str, float], raw)
        rendered = f"[{date}] {role}: {content}"
        required = len(rendered) + int(bool(hits))  # One newline between whole turns.
        if chars + required <= max_chars:
            chars += required
            hits.append(_Hit(turn_ref, session_ref, value))
    await asyncio.sleep(0)
    return _Pack(tuple(hits), len(rows), len(rows) - len(hits), chars, truncated)


def _scored(ranked: tuple[str, ...], relevant: frozenset[str], *, top_k: int,
            is_abstention: bool) -> dict[str, float] | None:
    values = None if is_abstention else _metrics(ranked, relevant, top_k)
    if values is None:
        return None
    return {"recall_any": values["recall_any"], "recall_all": values["recall_all"],
            "ndcg": values["official_ndcg_compat"]}


async def _run_case(case: _Case, *, top_k: int, max_chars: int) -> dict[str, object]:
    with TemporaryDirectory(prefix="omubot-longmemeval-") as directory:
        path = Path(directory) / "case.sqlite3"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        db = sqlite3.connect(path)
        try:
            db.execute("""CREATE TABLE turns (ordinal INTEGER PRIMARY KEY, turn_ref TEXT UNIQUE,
                session_ref TEXT, date TEXT, role TEXT, content TEXT, search_text TEXT)""")
            relevant_turns: set[str] = set()
            indexed = 0
            for session in case.sessions:
                for turn in session.turns:
                    db.execute("INSERT INTO turns VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (indexed, turn.turn_ref, session.session_ref, session.date, turn.role,
                         turn.content, f"[{session.date}] {turn.content}".casefold()))
                    indexed += 1
                    if turn.has_answer:
                        relevant_turns.add(turn.turn_ref)
                    if indexed % 64 == 0:
                        await asyncio.sleep(0)
                await asyncio.sleep(0)
            db.commit()
            pack = await _search_pack(db, case.question, top_k=top_k, max_chars=max_chars)
            turn_ranked = tuple(hit.turn_ref for hit in pack.hits)
            session_ranked = tuple(dict.fromkeys(hit.session_ref for hit in pack.hits))
            return {"case_ref": case.case_ref, "question_type": case.question_type, "status": "completed",
                "is_abstention": case.is_abstention, "indexed_session_count": len(case.sessions),
                "indexed_turn_count": indexed, "candidate_count_at_turn_top_k": pack.candidate_count,
                "packed_hit_count": len(pack.hits), "packed_session_count": len(session_ranked),
                "pack_chars": pack.chars, "omitted_count": pack.omitted_count,
                "query_token_truncated": pack.query_token_truncated,
                "relevant_turn_count": len(relevant_turns),
                "relevant_session_count": len(case.relevant_sessions),
                "turn_metrics": _scored(turn_ranked, frozenset(relevant_turns),
                    top_k=top_k, is_abstention=case.is_abstention),
                "session_metrics": _scored(session_ranked, case.relevant_sessions,
                    top_k=top_k, is_abstention=case.is_abstention),
                "pointers": [{"turn_ref": hit.turn_ref, "session_ref": hit.session_ref,
                              "lexical_score": hit.score} for hit in pack.hits]}
        finally:
            # Synchronous close completes before TemporaryDirectory cleanup,
            # including cancellation from index/search/pack suspension points.
            db.close()


def _aggregates(results: list[dict[str, object]]) -> dict[str, object]:
    summaries: dict[str, object] = {}
    for kind in ("turn", "session"):
        scored = [cast(dict[str, float], row[kind + "_metrics"]) for row in results
                  if row[kind + "_metrics"] is not None]
        summaries[kind] = {"scored_cases": len(scored),
            **{metric: sum(values[metric] for values in scored) / len(scored) if scored else None
               for metric in ("recall_any", "recall_all", "ndcg")}}
    return summaries


async def evaluate_longmemeval(payload: bytes, *, limit: int | None = None,
                              top_k: int = 10, max_chars: int = 2400) -> dict[str, object]:
    """Replay only explicit local JSON bytes; validate the full file before limit."""
    if (limit is not None and (type(limit) is not int or limit <= 0)
            or type(top_k) is not int or not 1 <= top_k <= MAX_PACK_HITS
            or type(max_chars) is not int or not 0 <= max_chars <= MAX_PACK_CHARS):
        raise ReplayInputError("invalid_replay_options")
    cases = _parse(payload)
    selected = cases if limit is None else cases[:limit]
    results: list[dict[str, object]] = []
    try:
        for case in selected:
            results.append(await _run_case(case, top_k=top_k, max_chars=max_chars))
    except Exception:
        raise ReplayError("longmemeval_replay_failed") from None
    return {"status": "completed", "evaluation_version": EVALUATION_VERSION,
        "upstream_commit": UPSTREAM_COMMIT, "input_sha256": hashlib.sha256(payload).hexdigest(),
        "input_case_count": len(cases), "selected_case_count": len(selected),
        "top_k": top_k, "max_chars": max_chars, "raw_turn_adapter": True,
        "production_memory_owner_used": False, "production_knowledge_owner_used": False,
        "benchmark_parity": False, "memory_extraction_scored": False,
        "answer_generation_scored": False, "deterministic_provenance": True,
        "ranking_tie_break_deterministic": True,
        "ranking_tie_break_strategy": "dataset_session_turn_order",
        "upstream_turn_to_session_equivalent": False,
        "session_ranking_scope": "packed_unique_sessions_at_turn_top_k",
        "turn_ranking_scope": "packed_turns_at_turn_top_k",
        "query_planner_rrf_used": False, "historical_card_lifecycle_replayed": False,
        "input_license_verified": False,
        "production_retrieval_differences": [
            "isolated_raw_turn_index_without_memory_extraction_or_typed_fact_gates",
            "shared_current_query_tokens_with_date_content_substring_coverage_only",
            "no_memory_card_knowledge_graph_buckets_or_production_query_planner",
            "whole_raw_turn_pack_at_turn_top_k_with_dataset_order_score_ties",
            "legacy_cardstore_gate_context_owner_chain_not_replayed",
        ], "results": results, "aggregates": _aggregates(results)}
