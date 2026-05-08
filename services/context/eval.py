"""Repeatable evaluation helpers for ContextService retrieval quality."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.context.packing import pack_context_hits
from services.context.types import ContextHit, ContextPack


@dataclass(slots=True)
class ContextHitExpectation:
    """A small, human-writable matcher for expected or forbidden context hits."""

    type: str = ""
    id: str = ""
    contains: str = ""
    source: str = ""
    title_contains: str = ""
    retriever: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextHitExpectation:
        return cls(
            type=str(data.get("type", "") or ""),
            id=str(data.get("id", "") or ""),
            contains=str(data.get("contains", "") or ""),
            source=str(data.get("source", "") or ""),
            title_contains=str(data.get("title_contains", "") or ""),
            retriever=str(data.get("retriever", "") or ""),
        )

    def matches(self, hit: ContextHit) -> bool:
        if self.type and hit.type != self.type:
            return False
        if self.id and hit.id != self.id:
            return False
        if self.contains and self.contains not in hit.content:
            return False
        if self.source and hit.source != self.source:
            return False
        if self.title_contains and self.title_contains not in hit.title:
            return False
        return not (self.retriever and hit.retriever != self.retriever)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "type": self.type,
                "id": self.id,
                "contains": self.contains,
                "source": self.source,
                "title_contains": self.title_contains,
                "retriever": self.retriever,
            }.items()
            if value
        }


@dataclass(slots=True)
class ContextEvalCase:
    """One retrieval evaluation query and its expected context behavior."""

    id: str
    query: str
    user_id: str = ""
    group_id: str | None = None
    top_k: int = 10
    max_chars: int = 2400
    max_pack_chars: int | None = None
    required_hits: list[ContextHitExpectation] = field(default_factory=list)
    forbidden_hits: list[ContextHitExpectation] = field(default_factory=list)
    required_recall: float = 1.0
    max_duplicate_hits: int = 0
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextEvalCase:
        return cls(
            id=str(data["id"]),
            query=str(data["query"]),
            user_id=str(data.get("user_id", "") or ""),
            group_id=str(data["group_id"]) if data.get("group_id") is not None else None,
            top_k=int(data.get("top_k", 10) or 10),
            max_chars=int(data.get("max_chars", 2400) or 2400),
            max_pack_chars=(
                int(data["max_pack_chars"])
                if data.get("max_pack_chars") is not None
                else None
            ),
            required_hits=[
                ContextHitExpectation.from_dict(item)
                for item in data.get("required_hits", [])
                if isinstance(item, dict)
            ],
            forbidden_hits=[
                ContextHitExpectation.from_dict(item)
                for item in data.get("forbidden_hits", [])
                if isinstance(item, dict)
            ],
            required_recall=float(data.get("required_recall", 1.0)),
            max_duplicate_hits=int(data.get("max_duplicate_hits", 0) or 0),
            notes=str(data.get("notes", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "user_id": self.user_id,
            "group_id": self.group_id,
            "top_k": self.top_k,
            "max_chars": self.max_chars,
            "max_pack_chars": self.max_pack_chars,
            "required_hits": [item.to_dict() for item in self.required_hits],
            "forbidden_hits": [item.to_dict() for item in self.forbidden_hits],
            "required_recall": self.required_recall,
            "max_duplicate_hits": self.max_duplicate_hits,
            "notes": self.notes,
        }


@dataclass(slots=True)
class ContextEvalResult:
    """Evaluation outcome for one case."""

    case_id: str
    query: str
    passed: bool
    hit_count: int
    pack_chars: int
    omitted_count: int
    required_total: int
    required_matched: int
    required_recall: float
    missing_required: list[dict[str, Any]] = field(default_factory=list)
    forbidden_violations: list[dict[str, Any]] = field(default_factory=list)
    duplicate_count: int = 0
    max_pack_chars: int | None = None
    pack_budget_exceeded: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "query": self.query,
            "passed": self.passed,
            "hit_count": self.hit_count,
            "pack_chars": self.pack_chars,
            "omitted_count": self.omitted_count,
            "required_total": self.required_total,
            "required_matched": self.required_matched,
            "required_recall": self.required_recall,
            "missing_required": self.missing_required,
            "forbidden_violations": self.forbidden_violations,
            "duplicate_count": self.duplicate_count,
            "max_pack_chars": self.max_pack_chars,
            "pack_budget_exceeded": self.pack_budget_exceeded,
            "error": self.error,
        }


@dataclass(slots=True)
class ContextEvalSummary:
    """Aggregated metrics for a context eval run."""

    total_cases: int
    passed_cases: int
    required_total: int
    required_matched: int
    forbidden_violations: int
    duplicate_hits: int
    pack_budget_violations: int
    avg_pack_chars: float
    results: list[ContextEvalResult]

    @property
    def pass_rate(self) -> float:
        if self.total_cases <= 0:
            return 1.0
        return self.passed_cases / self.total_cases

    @property
    def required_hit_recall(self) -> float:
        if self.required_total <= 0:
            return 1.0
        return self.required_matched / self.required_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "pass_rate": self.pass_rate,
            "required_total": self.required_total,
            "required_matched": self.required_matched,
            "required_hit_recall": self.required_hit_recall,
            "forbidden_violations": self.forbidden_violations,
            "duplicate_hits": self.duplicate_hits,
            "pack_budget_violations": self.pack_budget_violations,
            "avg_pack_chars": self.avg_pack_chars,
            "results": [item.to_dict() for item in self.results],
        }


def load_context_eval_cases(path: str | Path) -> list[ContextEvalCase]:
    """Load eval cases from a JSON file.

    Supported shapes:
    - {"cases": [{...}, {...}]}
    - [{...}, {...}]
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("cases", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("context eval fixture must be a list or an object with a cases list")
    return [ContextEvalCase.from_dict(item) for item in items if isinstance(item, dict)]


async def evaluate_context_cases(service: Any, cases: list[ContextEvalCase]) -> ContextEvalSummary:
    """Run a set of cases against a ContextService-compatible object."""
    results: list[ContextEvalResult] = []
    for case in cases:
        results.append(await evaluate_context_case(service, case))

    total_pack_chars = sum(result.pack_chars for result in results)
    return ContextEvalSummary(
        total_cases=len(results),
        passed_cases=sum(1 for result in results if result.passed),
        required_total=sum(result.required_total for result in results),
        required_matched=sum(result.required_matched for result in results),
        forbidden_violations=sum(len(result.forbidden_violations) for result in results),
        duplicate_hits=sum(result.duplicate_count for result in results),
        pack_budget_violations=sum(1 for result in results if result.pack_budget_exceeded),
        avg_pack_chars=(total_pack_chars / len(results)) if results else 0.0,
        results=results,
    )


async def evaluate_context_case(service: Any, case: ContextEvalCase) -> ContextEvalResult:
    """Run one case and compute retrieval safety metrics."""
    try:
        hits = await service.search(
            case.query,
            user_id=case.user_id,
            group_id=case.group_id,
            top_k=case.top_k,
        )
        pack = pack_context_hits(hits, max_chars=case.max_chars)
    except Exception as exc:  # pragma: no cover - exercised by integration callers
        return ContextEvalResult(
            case_id=case.id,
            query=case.query,
            passed=False,
            hit_count=0,
            pack_chars=0,
            omitted_count=0,
            required_total=len(case.required_hits),
            required_matched=0,
            required_recall=0.0,
            missing_required=[item.to_dict() for item in case.required_hits],
            max_pack_chars=case.max_pack_chars,
            error=type(exc).__name__,
        )

    return _score_case(case, hits, pack)


def _score_case(case: ContextEvalCase, hits: list[ContextHit], pack: ContextPack) -> ContextEvalResult:
    missing_required: list[dict[str, Any]] = []
    required_matched = 0
    for expectation in case.required_hits:
        if any(expectation.matches(hit) for hit in hits):
            required_matched += 1
        else:
            missing_required.append(expectation.to_dict())

    forbidden_violations: list[dict[str, Any]] = []
    for expectation in case.forbidden_hits:
        matched = [hit.id for hit in hits if expectation.matches(hit)]
        if matched:
            forbidden_violations.append({
                "expectation": expectation.to_dict(),
                "matched_hit_ids": matched,
            })

    duplicate_count = _count_duplicate_hits(pack.hits)
    required_total = len(case.required_hits)
    required_recall = (required_matched / required_total) if required_total else 1.0
    pack_budget_exceeded = (
        case.max_pack_chars is not None
        and len(pack.text) > case.max_pack_chars
    )
    passed = (
        required_recall >= case.required_recall
        and not forbidden_violations
        and duplicate_count <= case.max_duplicate_hits
        and not pack_budget_exceeded
    )

    return ContextEvalResult(
        case_id=case.id,
        query=case.query,
        passed=passed,
        hit_count=len(hits),
        pack_chars=len(pack.text),
        omitted_count=pack.omitted_count,
        required_total=required_total,
        required_matched=required_matched,
        required_recall=required_recall,
        missing_required=missing_required,
        forbidden_violations=forbidden_violations,
        duplicate_count=duplicate_count,
        max_pack_chars=case.max_pack_chars,
        pack_budget_exceeded=pack_budget_exceeded,
    )


def _count_duplicate_hits(hits: list[ContextHit]) -> int:
    identity_seen: set[tuple[str, str]] = set()
    content_seen: set[tuple[str, str]] = set()
    duplicates = 0
    for hit in hits:
        identity_key = (hit.type, hit.id)
        content_key = (hit.type, hit.content.strip())
        if identity_key in identity_seen or content_key in content_seen:
            duplicates += 1
        identity_seen.add(identity_key)
        content_seen.add(content_key)
    return duplicates
