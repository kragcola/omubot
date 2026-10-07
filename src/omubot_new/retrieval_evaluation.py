"""Explicit offline typed-human-fact fixtures through the current pack owners.

No raw-turn extraction, model, production enrollment or answer scoring occurs.
Every case owns a temporary Store and uses approved Memory transitions. The
report contains only counts, fixed codes and case-scoped opaque pointers.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from pydantic import Field, ValidationError, model_validator

from .archive import ArchiveService, ArchiveSourceInput
from .memory import MemoryService
from .policy import Policy
from .retrieval import MemoryContextPack, RetrievalService
from .store import Store
from .types import Grant, OperationError, Scope, StrictModel

EVALUATION_VERSION = "typed-human-fact-pack-v1"
UPSTREAM_COMMIT = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
# Current Memory visible-fact/search window and Retrieval pack ceilings. This
# limits each fixture case, not the total production fact corpus.
MAX_CASE_CARDS = 256
MAX_PACK_HITS = 64
MAX_PACK_CHARS = 32_000
CardCategory = Literal["preference", "boundary", "relationship", "event", "promise", "fact", "status"]


class EvaluationCard(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    source_user_id: str = Field(min_length=1, max_length=64)
    observed_at: float = Field(allow_inf_nan=False)
    predicate: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=256)
    category: CardCategory | None
    valid_from: float | None = Field(default=None, allow_inf_nan=False)
    valid_to: float | None = Field(default=None, allow_inf_nan=False)


class EvaluationCase(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    scope: Scope
    query: str = Field(min_length=1, max_length=8192)
    now: float = Field(allow_inf_nan=False)
    cards: tuple[EvaluationCard, ...] = Field(max_length=MAX_CASE_CARDS)
    relevant_card_ids: tuple[str, ...]

    @model_validator(mode="after")
    def exact_current_snapshot(self) -> EvaluationCase:
        ids = {card.id for card in self.cards}
        if len(ids) != len(self.cards):
            raise ValueError("duplicate_card_id")
        if len({(card.source_user_id, card.predicate) for card in self.cards}) != len(self.cards):
            raise ValueError("duplicate_current_head")
        relevant = set(self.relevant_card_ids)
        if len(relevant) != len(self.relevant_card_ids) or not relevant.issubset(ids):
            raise ValueError("invalid_ground_truth_pointer")
        if not self.query.strip():
            raise ValueError("empty_query")
        return self


class EvaluationFixture(StrictModel):
    schema_version: Literal[1]
    input_kind: Literal["synthetic", "user_supplied"]
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> EvaluationFixture:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate_case_id")
        return self


class EvaluationInputError(ValueError):
    """Safe schema/option diagnostics, with no parser text or input values."""
    def __init__(self, code: str, case_indexes: tuple[int, ...] = ()) -> None:
        self.code, self.case_indexes = code, case_indexes
        super().__init__(code)


def _opaque(kind: str, *parts: str) -> str:
    digest = hashlib.sha256(json.dumps([kind, *parts], ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()
    return kind + "_" + digest


def _metrics(ranked: tuple[str, ...], relevant: frozenset[str], top_k: int) -> dict[str, float] | None:
    if len(set(ranked)) != len(ranked):
        raise OperationError("duplicate_evaluation_rank")
    if not relevant:
        return None
    selected = ranked[:top_k]
    gains = [float(pointer in relevant) for pointer in selected]
    # Exact pinned LongMemEval dcg: rank one is undiscounted, and rank two
    # divides by log2(2), not the usual alternate log2(rank + 1) formula.
    actual = sum(gain if index == 0 else gain / math.log2(index + 1)
                 for index, gain in enumerate(gains))
    ideal = sum(1.0 if index == 0 else 1.0 / math.log2(index + 1)
                for index in range(min(len(relevant), top_k)))
    return {"recall_any": float(bool(set(selected) & relevant)),
            "recall_all": float(relevant.issubset(selected)),
            "official_ndcg_compat": actual / ideal}


def _pack_report(case: EvaluationCase, pack: MemoryContextPack, *,
                 indexed: dict[str, tuple[str, str]], relevant: frozenset[str],
                 top_k: int) -> dict[str, object]:
    ranked = tuple(hit.object_id for hit in pack.cold.hits)
    pointers: list[dict[str, object]] = []
    for hit in pack.cold.hits:
        if hit.object_id not in indexed:
            raise OperationError("evaluation_provenance_mismatch")
        card_id, source_id = indexed[hit.object_id]
        if (hit.source_scope != case.scope or hit.visibility_scope != case.scope
                or hit.source_refs != (source_id,) or hit.evidence_refs != (source_id,)):
            raise OperationError("evaluation_provenance_mismatch")
        pointers.append({"card_ref": _opaque("card", case.id, card_id),
            "fact_ref": _opaque("fact", case.id, hit.object_id),
            "source_refs": [_opaque("source", case.id, ref) for ref in hit.source_refs],
            "fact_revision": hit.fact_revision,
            "relevant": hit.object_id in relevant,
            "source_kind": "memory_card" if hit.card_projection is not None else "memory_fact"})
    plan = pack.retrieval_plan
    assert plan is not None
    return {"packed_hit_count": len(ranked), "pack_chars": pack.total_budget_used,
        "pack_state": pack.cold.pack_state, "metrics": _metrics(ranked, relevant, top_k),
        "pointers": pointers, "card_total_active": pack.cold.card_total_active,
        "card_matched_active": pack.cold.card_matched_active,
        "query_planner_enabled": plan.query_planner_enabled,
        "plan_needs": list(plan.needs), "reason_codes": list(pack.cold.reason_codes)}


def _aggregates(results: list[dict[str, object]]) -> dict[str, object]:
    summaries: dict[str, object] = {}
    completed = [result for result in results if result["status"] == "completed"]
    for mode in ("baseline", "query_planner_rrf"):
        packs = [cast(dict[str, dict[str, object]], result["modes"])[mode] for result in completed]
        scored = [cast(dict[str, float], pack["metrics"]) for pack in packs if pack["metrics"] is not None]
        summaries[mode] = {"completed_cases": len(packs), "scored_cases": len(scored),
            **{metric: sum(item[metric] for item in scored) / len(scored) if scored else None
               for metric in ("recall_any", "recall_all", "official_ndcg_compat")},
            "total_packed_hits": sum(cast(int, pack["packed_hit_count"]) for pack in packs),
            "total_pack_chars": sum(cast(int, pack["pack_chars"]) for pack in packs)}
    return summaries


async def _run_case(case: EvaluationCase, *, top_k: int, pack_chars: int) -> dict[str, object]:
    # No caller-supplied DB path can reach Store. Both modes share this one
    # approved snapshot; each case has its own temporary files and permissions.
    with TemporaryDirectory(prefix="omubot-retrieval-evaluation-") as directory:
        store = Store(Path(directory) / "case.sqlite3")
        try:
            await store.open()
            policy = Policy(store, case.scope.bot_id, "live")
            actor = "evaluation-operator"
            expiry = time.time() + 3600
            grants = [Grant(subject=author, scope=case.scope,
                actions=["message.read", "memory.archive", "memory.learn"], expires_at=expiry)
                for author in sorted({card.source_user_id for card in case.cards} - {actor})]
            grants.append(Grant(subject=actor, scope=case.scope,
                actions=["message.read", "memory.archive", "memory.learn", "memory.review",
                         "memory.apply", "memory.retrieve"], expires_at=expiry))
            await policy.replace(grants, 0, actor)
            archive = ArchiveService(store, policy, clock=lambda: case.now)
            memory = MemoryService(store, policy, clock=lambda: case.now)
            indexed: dict[str, tuple[str, str]] = {}
            by_card: dict[str, str] = {}
            for card in case.cards:
                source = await archive.archive_source(actor, ArchiveSourceInput(
                    scope=case.scope, event_id=_opaque("event", case.id, card.id),
                    source_kind="human_message", speaker_kind="human",
                    speaker_id=card.source_user_id, observed_at=card.observed_at))
                candidate = await memory.propose(actor=actor, scope=case.scope,
                    source_id=source.source_id, subject_id=card.source_user_id,
                    predicate=card.predicate, value=card.value, action="add",
                    valid_from=card.valid_from, valid_to=card.valid_to)
                approved = await memory.review(candidate.candidate_id, actor=actor,
                    expected_revision=candidate.candidate_revision, decision="approved")
                applied = await memory.apply(approved.candidate_id, actor=actor,
                    expected_revision=approved.candidate_revision)
                assert applied.applied_fact_id is not None
                fact = await memory.read_fact(applied.applied_fact_id, actor=actor, scope=case.scope)
                if fact is None:
                    raise OperationError("evaluation_fact_not_current")
                if card.category is not None:
                    await memory.classify_card(actor=actor, scope=case.scope, fact_id=fact.fact_id,
                        expected_fact_revision=fact.fact_revision, expected_classification_revision=0,
                        category=card.category)
                indexed[fact.fact_id] = (card.id, source.source_id)
                by_card[card.id] = fact.fact_id
            relevant = frozenset(by_card[ref] for ref in case.relevant_card_ids)
            active = await memory.query_cards(actor=actor, scope=case.scope)
            modes: dict[str, object] = {}
            for name, enabled in (("baseline", False), ("query_planner_rrf", True)):
                service = RetrievalService(memory, query_planner_enabled=enabled)
                pack = await service.retrieve_context(actor, case.scope, retrieve_mode="fact",
                    query=case.query, max_hits=top_k, total_budget=pack_chars, hot_budget=0)
                await store.transaction(partial(service.assert_current_transaction,
                    actor=actor, scope=case.scope, result=pack))
                modes[name] = _pack_report(case, pack, indexed=indexed, relevant=relevant, top_k=top_k)
            return {"case_ref": _opaque("case", case.id), "status": "completed",
                "indexed_fact_count": len(indexed), "active_card_count": active.total_active,
                "input_card_count": len(case.cards), "relevant_fact_count": len(relevant),
                "modes": modes}
        finally:
            # Store drains its one owned SQLite close operation on cancellation.
            # TemporaryDirectory runs only after connections/owner locks close.
            await store.close()


async def evaluate_retrieval(payload: bytes, *, limit: int | None = None,
                             top_k: int = 8, pack_chars: int = 4096) -> dict[str, object]:
    """Compare the real current baseline and planner/RRF on explicit JSON bytes."""
    if (limit is not None and (type(limit) is not int or limit <= 0)
            or type(top_k) is not int or not 1 <= top_k <= MAX_PACK_HITS
            or type(pack_chars) is not int or not 0 <= pack_chars <= MAX_PACK_CHARS):
        raise EvaluationInputError("invalid_evaluation_options")
    try:
        fixture = EvaluationFixture.model_validate_json(payload)
    except ValidationError as exc:
        indexes = tuple(sorted({error["loc"][1] for error in exc.errors(include_input=False,
            include_context=False) if len(error["loc"]) > 1 and error["loc"][0] == "cases"
            and type(error["loc"][1]) is int}))
        raise EvaluationInputError("invalid_evaluation_schema", indexes) from None
    selected = fixture.cases if limit is None else fixture.cases[:limit]
    results: list[dict[str, object]] = []
    for case in selected:
        try:
            results.append(await _run_case(case, top_k=top_k, pack_chars=pack_chars))
        except OperationError as exc:
            results.append({"case_ref": _opaque("case", case.id), "status": "failed",
                            "error_code": exc.code})
    failed = sum(result["status"] == "failed" for result in results)
    return {"version": EVALUATION_VERSION, "status": "partial_failure" if failed else "completed",
        "input_sha256": hashlib.sha256(payload).hexdigest(), "input_kind": fixture.input_kind,
        "input_license_verified": False, "total_cases": len(fixture.cases),
        "selected_cases": len(selected), "failed_cases": failed, "top_k": top_k,
        "pack_chars": pack_chars, "benchmark_parity": False, "memory_extraction_scored": False,
        "answer_generation_scored": False, "raw_turn_adapter": False,
        "historical_card_lifecycle_replayed": False,
        "upstream_commit": UPSTREAM_COMMIT, "metric_scope": "packed_current_fact_ids_at_top_k",
        "official_ndcg_compat_formula": "rank1 + sum(rank_i/log2(i), i>=2)",
        "source_time_rewritten": False, "production_ranking_overridden": False,
        "aggregates": _aggregates(results), "cases": results}
