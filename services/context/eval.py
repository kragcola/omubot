"""Repeatable evaluation helpers for ContextService retrieval quality."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.context.packing import pack_context_hits
from services.context.types import ContextHit, ContextPack

_TRACE_STRING_LIST_FIELDS = (
    "required_current_contains",
    "forbidden_current_contains",
    "required_earlier_contains",
    "forbidden_earlier_contains",
    "required_trajectory_contains",
    "forbidden_trajectory_contains",
    "required_text_contains",
    "forbidden_text_contains",
    "required_evidence_refs",
    "required_evidence_ref_prefixes",
    "forbidden_evidence_refs",
    "forbidden_evidence_ref_prefixes",
)
_TRACE_REQUIRED_FIELDS = (
    "required_current_contains",
    "required_earlier_contains",
    "required_trajectory_contains",
    "required_text_contains",
    "required_evidence_refs",
    "required_evidence_ref_prefixes",
)
_TRACE_EXPECTATION_FIELDS = frozenset({
    "expected_present",
    "reason",
    "max_knowledge_points",
    "max_trace_chars",
    *_TRACE_STRING_LIST_FIELDS,
})


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
class TemporalTraceExpectation:
    """Structured expectation over TemporalTrace / KnowledgePointTrace fields.

    Scoring never parses rendered prose to invent current/earlier/trajectory —
    those dimensions are read from typed DTO fields (or dict equivalents).
    ``required_text_contains`` / ``forbidden_text_contains`` only check the
    assembler's ``text`` field when provided.
    """

    expected_present: bool | None = None
    reason: str = ""
    required_current_contains: list[str] = field(default_factory=list)
    forbidden_current_contains: list[str] = field(default_factory=list)
    required_earlier_contains: list[str] = field(default_factory=list)
    forbidden_earlier_contains: list[str] = field(default_factory=list)
    required_trajectory_contains: list[str] = field(default_factory=list)
    forbidden_trajectory_contains: list[str] = field(default_factory=list)
    required_text_contains: list[str] = field(default_factory=list)
    forbidden_text_contains: list[str] = field(default_factory=list)
    required_evidence_refs: list[str] = field(default_factory=list)
    required_evidence_ref_prefixes: list[str] = field(default_factory=list)
    forbidden_evidence_refs: list[str] = field(default_factory=list)
    forbidden_evidence_ref_prefixes: list[str] = field(default_factory=list)
    max_knowledge_points: int | None = None
    max_trace_chars: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TemporalTraceExpectation | None:
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError("trace expectation must be a dict")
        if not data:
            return None
        unknown = sorted(set(data) - _TRACE_EXPECTATION_FIELDS)
        if unknown:
            raise ValueError(f"unknown trace expectation fields: {unknown}")

        expected_present = data.get("expected_present", None)
        if expected_present is not None and type(expected_present) is not bool:
            raise TypeError("trace expected_present must be a bool or null")

        reason = data.get("reason", "")
        if not isinstance(reason, str):
            raise TypeError("trace reason must be a string")

        string_lists = {
            key: _str_list(data[key], field_name=key) if key in data else []
            for key in _TRACE_STRING_LIST_FIELDS
        }
        expectation = cls(
            expected_present=expected_present,
            reason=reason,
            max_knowledge_points=_optional_non_negative_int(
                data,
                "max_knowledge_points",
            ),
            max_trace_chars=_optional_non_negative_int(data, "max_trace_chars"),
            **string_lists,
        )
        if expectation.expected_present is False and (
            expectation.reason
            or any(getattr(expectation, key) for key in _TRACE_REQUIRED_FIELDS)
        ):
            raise ValueError(
                "trace expected_present=false cannot declare reason or required evidence"
            )
        return expectation

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.expected_present is not None:
            out["expected_present"] = self.expected_present
        if self.reason:
            out["reason"] = self.reason
        for key in (
            "required_current_contains",
            "forbidden_current_contains",
            "required_earlier_contains",
            "forbidden_earlier_contains",
            "required_trajectory_contains",
            "forbidden_trajectory_contains",
            "required_text_contains",
            "forbidden_text_contains",
            "required_evidence_refs",
            "required_evidence_ref_prefixes",
            "forbidden_evidence_refs",
            "forbidden_evidence_ref_prefixes",
        ):
            value = getattr(self, key)
            if value:
                out[key] = list(value)
        if self.max_knowledge_points is not None:
            out["max_knowledge_points"] = self.max_knowledge_points
        if self.max_trace_chars is not None:
            out["max_trace_chars"] = self.max_trace_chars
        return out

    def is_active(self) -> bool:
        """True when the case declares any trace check (present/absent/fields)."""
        if self.expected_present is not None:
            return True
        if self.reason:
            return True
        if self.max_knowledge_points is not None or self.max_trace_chars is not None:
            return True
        for key in (
            "required_current_contains",
            "forbidden_current_contains",
            "required_earlier_contains",
            "forbidden_earlier_contains",
            "required_trajectory_contains",
            "forbidden_trajectory_contains",
            "required_text_contains",
            "forbidden_text_contains",
            "required_evidence_refs",
            "required_evidence_ref_prefixes",
            "forbidden_evidence_refs",
            "forbidden_evidence_ref_prefixes",
        ):
            if getattr(self, key):
                return True
        return False


@dataclass(slots=True)
class ContextEvalCase:
    """One retrieval evaluation query and its expected context behavior."""

    id: str
    query: str
    session_id: str = ""
    user_id: str = ""
    group_id: str | None = None
    top_k: int = 10
    max_chars: int = 2400
    max_pack_chars: int | None = None
    required_hits: list[ContextHitExpectation] = field(default_factory=list)
    forbidden_hits: list[ContextHitExpectation] = field(default_factory=list)
    required_recall: float = 1.0
    max_duplicate_hits: int = 0
    max_hit_count: int | None = None
    notes: str = ""
    current_message: str = ""
    rewritten_query: str = ""
    trace: TemporalTraceExpectation | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextEvalCase:
        trace_raw = data.get("trace")
        return cls(
            id=str(data["id"]),
            query=str(data["query"]),
            session_id=str(data.get("session_id", "") or ""),
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
            max_hit_count=(
                int(data["max_hit_count"])
                if data.get("max_hit_count") is not None
                else None
            ),
            notes=str(data.get("notes", "") or ""),
            current_message=str(data.get("current_message", "") or ""),
            rewritten_query=str(data.get("rewritten_query", "") or ""),
            trace=TemporalTraceExpectation.from_dict(trace_raw),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "query": self.query,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "group_id": self.group_id,
            "top_k": self.top_k,
            "max_chars": self.max_chars,
            "max_pack_chars": self.max_pack_chars,
            "required_hits": [item.to_dict() for item in self.required_hits],
            "forbidden_hits": [item.to_dict() for item in self.forbidden_hits],
            "required_recall": self.required_recall,
            "max_duplicate_hits": self.max_duplicate_hits,
            "max_hit_count": self.max_hit_count,
            "notes": self.notes,
            "current_message": self.current_message,
            "rewritten_query": self.rewritten_query,
        }
        if self.trace is not None:
            payload["trace"] = self.trace.to_dict()
        return payload


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
    max_hit_count: int | None = None
    hit_count_exceeded: bool = False
    error: str = ""
    # Additive temporal / evidence-use fields (default keeps legacy callers green).
    trace_checked: bool = False
    trace_present: bool = False
    trace_reason: str = ""
    trace_chars: int = 0
    trace_violations: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence_use: list[dict[str, Any]] = field(default_factory=list)
    # Opt-in euc_v1 pack-state only (absent from default payload keys when None).
    pack_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
            "max_hit_count": self.max_hit_count,
            "hit_count_exceeded": self.hit_count_exceeded,
            "error": self.error,
            "trace_checked": self.trace_checked,
            "trace_present": self.trace_present,
            "trace_reason": self.trace_reason,
            "trace_chars": self.trace_chars,
            "trace_violations": self.trace_violations,
            "missing_evidence_use": self.missing_evidence_use,
        }
        # Opt-in only: keep historical fixture payloads free of pack_state.
        if self.pack_state is not None:
            payload["pack_state"] = self.pack_state
        return payload


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
    hit_count_violations: int
    avg_pack_chars: float
    results: list[ContextEvalResult]
    trace_violations: int = 0

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
            "hit_count_violations": self.hit_count_violations,
            "avg_pack_chars": self.avg_pack_chars,
            "trace_violations": self.trace_violations,
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


async def evaluate_context_cases(
    service: Any,
    cases: list[ContextEvalCase],
    *,
    trace_assembler: Any | None = None,
    score_evidence_use_contract: bool = False,
) -> ContextEvalSummary:
    """Run a set of cases against a ContextService-compatible object."""
    results: list[ContextEvalResult] = []
    for case in cases:
        results.append(
            await evaluate_context_case(
                service,
                case,
                trace_assembler=trace_assembler,
                score_evidence_use_contract=score_evidence_use_contract,
            )
        )

    total_pack_chars = sum(result.pack_chars for result in results)
    return ContextEvalSummary(
        total_cases=len(results),
        passed_cases=sum(1 for result in results if result.passed),
        required_total=sum(result.required_total for result in results),
        required_matched=sum(result.required_matched for result in results),
        forbidden_violations=sum(len(result.forbidden_violations) for result in results),
        duplicate_hits=sum(result.duplicate_count for result in results),
        pack_budget_violations=sum(1 for result in results if result.pack_budget_exceeded),
        hit_count_violations=sum(1 for result in results if result.hit_count_exceeded),
        avg_pack_chars=(total_pack_chars / len(results)) if results else 0.0,
        results=results,
        trace_violations=sum(len(result.trace_violations) for result in results),
    )


async def evaluate_context_case(
    service: Any,
    case: ContextEvalCase,
    *,
    trace_assembler: Any | None = None,
    score_evidence_use_contract: bool = False,
) -> ContextEvalResult:
    """Run one case and compute retrieval + optional temporal-trace metrics.

    Default path remains ``search()`` + ``pack_context_hits()`` (historical
    fixtures unchanged). Opt-in ``score_evidence_use_contract`` records closed
    ``pack_state`` only — never answer grounding / answer_used_evidence.
    """
    try:
        hits = await service.search(
            case.query,
            session_id=case.session_id,
            user_id=case.user_id,
            group_id=case.group_id,
            top_k=case.top_k,
        )
        pack = pack_context_hits(hits, max_chars=case.max_chars)
    except asyncio.CancelledError:
        raise
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
            max_hit_count=case.max_hit_count,
            error=type(exc).__name__,
        )

    return await _score_case(
        case,
        hits,
        pack,
        trace_assembler=trace_assembler,
        score_evidence_use_contract=score_evidence_use_contract,
    )


async def _score_case(
    case: ContextEvalCase,
    hits: list[ContextHit],
    pack: ContextPack,
    *,
    trace_assembler: Any | None,
    score_evidence_use_contract: bool = False,
) -> ContextEvalResult:
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
    hit_count_exceeded = (
        case.max_hit_count is not None
        and len(hits) > case.max_hit_count
    )

    trace_checked = False
    trace_present = False
    trace_reason = ""
    trace_chars = 0
    trace_violations: list[dict[str, Any]] = []
    missing_evidence_use: list[dict[str, Any]] = []
    error = ""

    expectation = case.trace
    needs_trace = expectation is not None and expectation.is_active()
    if needs_trace:
        assert expectation is not None
        trace_expectation = expectation
        trace_checked = True
        if trace_assembler is None:
            error = "missing_trace_assembler"
            violation = {
                "code": "missing_trace_assembler",
                "message": (
                    "case declares temporal-trace expectations but no "
                    "trace_assembler was provided"
                ),
            }
            trace_violations.append(violation)
            missing_evidence_use.append(violation)
        else:
            try:
                current_message = case.current_message or case.query
                rewritten_query = case.rewritten_query or case.query
                # Ordinary hits remain independently scored; assembler receives them
                # as active_memory_hits for head seeding only.
                memory_hits = [hit for hit in hits if hit.type == "memory_card"]
                trace = await trace_assembler.assemble(
                    current_message=current_message,
                    rewritten_query=rewritten_query,
                    session_id=case.session_id,
                    user_id=case.user_id,
                    group_id=case.group_id,
                    active_memory_hits=memory_hits or hits,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = type(exc).__name__
                violation = {
                    "code": "trace_assembler_error",
                    "message": f"trace assembler raised {type(exc).__name__}",
                    "error_type": type(exc).__name__,
                }
                trace_violations.append(violation)
                missing_evidence_use.append(violation)
                trace = None
            else:
                scored = _score_temporal_trace(trace_expectation, trace)
                trace_present = scored["trace_present"]
                trace_reason = scored["trace_reason"]
                trace_chars = scored["trace_chars"]
                trace_violations.extend(scored["trace_violations"])
                missing_evidence_use.extend(scored["missing_evidence_use"])

    ordinary_passed = (
        required_recall >= case.required_recall
        and not forbidden_violations
        and duplicate_count <= case.max_duplicate_hits
        and not pack_budget_exceeded
        and not hit_count_exceeded
    )
    passed = ordinary_passed and not trace_violations and not error

    pack_state: str | None = None
    if score_evidence_use_contract:
        # Pack-state observability only (gate metrics unknown on default
        # search+pack path → fail-closed empty actions via sanitizer).
        from services.context.evidence_use_contract import derive_evidence_use_contract

        contract = derive_evidence_use_contract(
            pack_hits=pack.hits,
            omitted_count=int(pack.omitted_count),
            peg_metrics=None,
            retrieve_mode="hybrid",
            enabled=True,
            inject_constrained_instruction=False,
        )
        pack_state = str(contract.pack_state)

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
        max_hit_count=case.max_hit_count,
        hit_count_exceeded=hit_count_exceeded,
        error=error,
        trace_checked=trace_checked,
        trace_present=trace_present,
        trace_reason=trace_reason,
        trace_chars=trace_chars,
        trace_violations=trace_violations,
        missing_evidence_use=missing_evidence_use,
        pack_state=pack_state,
    )


def _score_temporal_trace(
    expectation: TemporalTraceExpectation,
    trace: Any,
) -> dict[str, Any]:
    """Score structured TemporalTrace fields. Never invents dims from prose."""
    violations: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    present = trace is not None
    shape_issues = _trace_shape_issues(trace) if present else []
    structurally_valid = present and not shape_issues
    reason = _trace_reason(trace) if structurally_valid else ""
    text = _trace_text(trace) if structurally_valid else ""
    chars = len(text)
    kps = _trace_kps(trace) if structurally_valid else []

    requires_presence = bool(
        expectation.reason
        or any(getattr(expectation, key) for key in _TRACE_REQUIRED_FIELDS)
    )
    if expectation.expected_present is False and requires_presence:
        item = {
            "code": "contradictory_trace_expectation",
            "message": (
                "expected_present=false cannot be combined with reason or "
                "required evidence"
            ),
        }
        violations.append(item)
        missing.append(item)

    if shape_issues:
        item = {
            "code": "trace_malformed",
            "message": "temporal trace does not satisfy the structured DTO contract",
            "issues": shape_issues,
        }
        violations.append(item)
        missing.append(item)

    if (expectation.expected_present is True or requires_presence) and not present:
        item = {
            "code": "trace_expected_present",
            "message": "expected temporal trace to be present",
        }
        violations.append(item)
        missing.append(item)
    if expectation.expected_present is False and present:
        violations.append({
            "code": "trace_expected_absent",
            "message": "expected temporal trace to be absent",
            "actual_reason": reason,
        })

    # Field checks only apply when a trace object exists (or required fields force miss).
    if present:
        if expectation.reason and reason != expectation.reason:
            violations.append({
                "code": "trace_reason_mismatch",
                "message": "trace reason mismatch",
                "expected": expectation.reason,
                "actual": reason,
            })

        currents = [
            _node_content(
                kp.get("current")
                if isinstance(kp, dict)
                else getattr(kp, "current", None)
            )
            for kp in kps
        ]
        earliers = _flatten_earlier(kps)
        trajectories = [
            str(
                kp.get("trajectory")
                if isinstance(kp, dict)
                else getattr(kp, "trajectory", "") or ""
            )
            for kp in kps
        ]
        refs = _flatten_evidence_refs(kps)

        current_blob = "\n".join(currents)
        earlier_blob = "\n".join(earliers)
        traj_blob = "\n".join(trajectories)

        _check_required_substrings(
            violations,
            missing,
            field="required_current",
            blob=current_blob,
            needles=expectation.required_current_contains,
        )
        _check_forbidden_substrings(
            violations,
            field="forbidden_current",
            blob=current_blob,
            needles=expectation.forbidden_current_contains,
        )
        _check_required_substrings(
            violations,
            missing,
            field="required_earlier",
            blob=earlier_blob,
            needles=expectation.required_earlier_contains,
        )
        _check_forbidden_substrings(
            violations,
            field="forbidden_earlier",
            blob=earlier_blob,
            needles=expectation.forbidden_earlier_contains,
        )
        _check_required_substrings(
            violations,
            missing,
            field="required_trajectory",
            blob=traj_blob,
            needles=expectation.required_trajectory_contains,
        )
        _check_forbidden_substrings(
            violations,
            field="forbidden_trajectory",
            blob=traj_blob,
            needles=expectation.forbidden_trajectory_contains,
        )
        _check_required_substrings(
            violations,
            missing,
            field="required_text",
            blob=text,
            needles=expectation.required_text_contains,
        )
        _check_forbidden_substrings(
            violations,
            field="forbidden_text",
            blob=text,
            needles=expectation.forbidden_text_contains,
        )

        for ref in expectation.required_evidence_refs:
            if ref not in refs:
                item = {
                    "code": "missing_evidence_ref",
                    "message": f"missing exact evidence ref {ref!r}",
                    "expected": ref,
                }
                violations.append(item)
                missing.append(item)
        for prefix in expectation.required_evidence_ref_prefixes:
            if not any(r.startswith(prefix) for r in refs):
                item = {
                    "code": "missing_evidence_ref_prefix",
                    "message": f"missing evidence ref with prefix {prefix!r}",
                    "expected_prefix": prefix,
                    "actual_refs": list(refs),
                }
                violations.append(item)
                missing.append(item)
        for ref in expectation.forbidden_evidence_refs:
            if ref in refs:
                violations.append({
                    "code": "forbidden_evidence_ref",
                    "message": f"forbidden evidence ref present: {ref!r}",
                    "actual": ref,
                })
        for prefix in expectation.forbidden_evidence_ref_prefixes:
            matched = [r for r in refs if r.startswith(prefix)]
            if matched:
                violations.append({
                    "code": "forbidden_evidence_ref_prefix",
                    "message": f"forbidden evidence ref prefix {prefix!r}",
                    "matched": matched,
                })

        has_kp_requirements = any((
            expectation.required_current_contains,
            expectation.required_earlier_contains,
            expectation.required_trajectory_contains,
            expectation.required_evidence_refs,
            expectation.required_evidence_ref_prefixes,
        ))
        if (
            len(kps) > 1
            and has_kp_requirements
            and _kp_fields_match_requirements(
                expectation,
                current=current_blob,
                earlier=earlier_blob,
                trajectory=traj_blob,
                refs=refs,
            )
            and not any(
                _knowledge_point_matches_requirements(expectation, kp)
                for kp in kps
            )
        ):
            item = {
                "code": "trace_required_fields_split_across_knowledge_points",
                "message": (
                    "required current/earlier/trajectory/evidence fields must "
                    "co-locate in one knowledge point"
                ),
            }
            violations.append(item)
            missing.append(item)

        if (
            expectation.max_knowledge_points is not None
            and len(kps) > expectation.max_knowledge_points
        ):
            violations.append({
                "code": "max_knowledge_points",
                "message": "knowledge point budget exceeded",
                "max_knowledge_points": expectation.max_knowledge_points,
                "actual": len(kps),
            })
        if expectation.max_trace_chars is not None and chars > expectation.max_trace_chars:
            violations.append({
                "code": "max_trace_chars",
                "message": "trace character budget exceeded",
                "max_trace_chars": expectation.max_trace_chars,
                "actual": chars,
            })
    else:
        # Required field expectations without a present trace are missing evidence-use.
        for field_name, needles in (
            ("required_current", expectation.required_current_contains),
            ("required_earlier", expectation.required_earlier_contains),
            ("required_trajectory", expectation.required_trajectory_contains),
            ("required_text", expectation.required_text_contains),
        ):
            for needle in needles:
                item = {
                    "code": f"missing_{field_name}",
                    "message": f"missing {field_name} substring {needle!r} (trace absent)",
                    "expected": needle,
                }
                # Only count as violation when we expected presence or any field require.
                if expectation.expected_present is not False:
                    violations.append(item)
                    missing.append(item)
        needs_refs = (
            expectation.required_evidence_refs
            or expectation.required_evidence_ref_prefixes
        )
        if needs_refs and expectation.expected_present is not False:
            item = {
                "code": "missing_evidence_use",
                "message": "trace absent; required evidence refs not available",
            }
            violations.append(item)
            missing.append(item)

    return {
        "trace_present": present,
        "trace_reason": reason,
        "trace_chars": chars,
        "trace_violations": violations,
        "missing_evidence_use": missing,
    }


def _check_required_substrings(
    violations: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    *,
    field: str,
    blob: str,
    needles: list[str],
) -> None:
    for needle in needles:
        if needle not in blob:
            item = {
                "code": f"missing_{field}",
                "message": f"missing {field} substring {needle!r}",
                "expected": needle,
            }
            violations.append(item)
            missing.append(item)


def _check_forbidden_substrings(
    violations: list[dict[str, Any]],
    *,
    field: str,
    blob: str,
    needles: list[str],
) -> None:
    for needle in needles:
        if needle in blob:
            violations.append({
                "code": f"{field}_present",
                "message": f"forbidden {field} substring present: {needle!r}",
                "actual": needle,
            })


def _knowledge_point_matches_requirements(
    expectation: TemporalTraceExpectation,
    kp: Any,
) -> bool:
    current = _node_content(
        kp.get("current") if isinstance(kp, dict) else getattr(kp, "current", None)
    )
    earlier = "\n".join(_flatten_earlier([kp]))
    trajectory = str(
        kp.get("trajectory")
        if isinstance(kp, dict)
        else getattr(kp, "trajectory", "") or ""
    )
    refs = _flatten_evidence_refs([kp])
    return _kp_fields_match_requirements(
        expectation,
        current=current,
        earlier=earlier,
        trajectory=trajectory,
        refs=refs,
    )


def _kp_fields_match_requirements(
    expectation: TemporalTraceExpectation,
    *,
    current: str,
    earlier: str,
    trajectory: str,
    refs: list[str],
) -> bool:
    return (
        all(needle in current for needle in expectation.required_current_contains)
        and all(needle in earlier for needle in expectation.required_earlier_contains)
        and all(
            needle in trajectory
            for needle in expectation.required_trajectory_contains
        )
        and all(ref in refs for ref in expectation.required_evidence_refs)
        and all(
            any(ref.startswith(prefix) for ref in refs)
            for prefix in expectation.required_evidence_ref_prefixes
        )
    )


_MISSING_TRACE_FIELD = object()


def _trace_shape_issues(trace: Any) -> list[str]:
    issues: list[str] = []
    text = _trace_field(trace, "text")
    reason = _trace_field(trace, "reason")
    raw_kps = _trace_field(trace, "knowledge_points")

    if not isinstance(text, str) or not text.strip():
        issues.append("text must be a non-empty string")
    if not isinstance(reason, str) or not reason.strip():
        issues.append("reason must be a non-empty string")
    if not isinstance(raw_kps, (list, tuple)) or not raw_kps:
        issues.append("knowledge_points must be a non-empty list")
        return issues

    for index, kp in enumerate(raw_kps):
        current = _trace_field(kp, "current")
        earlier = _trace_field(kp, "earlier")
        trajectory = _trace_field(kp, "trajectory")
        refs = _trace_field(kp, "evidence_refs")
        kp_reason = _trace_field(kp, "reason")

        if not _has_non_empty_node_content(current):
            issues.append(f"knowledge_points[{index}].current must contain text")
        if (
            not isinstance(earlier, (list, tuple))
            or not earlier
            or any(not _has_non_empty_node_content(item) for item in earlier)
        ):
            issues.append(
                f"knowledge_points[{index}].earlier must be a non-empty node list"
            )
        if not isinstance(trajectory, str) or not trajectory.strip():
            issues.append(
                f"knowledge_points[{index}].trajectory must be a non-empty string"
            )
        if (
            not isinstance(refs, (list, tuple))
            or not refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
        ):
            issues.append(
                f"knowledge_points[{index}].evidence_refs must be non-empty strings"
            )
        if (
            not isinstance(kp_reason, str)
            or not kp_reason.strip()
            or not isinstance(reason, str)
            or kp_reason != reason
        ):
            issues.append(
                f"knowledge_points[{index}].reason must match the trace reason"
            )
    return issues


def _trace_field(value: Any, field_name: str) -> Any:
    if isinstance(value, dict):
        return value.get(field_name, _MISSING_TRACE_FIELD)
    try:
        return getattr(value, field_name, _MISSING_TRACE_FIELD)
    except Exception:
        return _MISSING_TRACE_FIELD


def _has_non_empty_node_content(node: Any) -> bool:
    if isinstance(node, dict):
        content = node.get("content", node.get("text", _MISSING_TRACE_FIELD))
    else:
        content = _trace_field(node, "content")
        if content is _MISSING_TRACE_FIELD:
            content = _trace_field(node, "text")
    return isinstance(content, str) and bool(content.strip())


def _trace_text(trace: Any) -> str:
    if trace is None:
        return ""
    text = getattr(trace, "text", None)
    if text is not None:
        return str(text)
    if isinstance(trace, dict):
        return str(trace.get("text") or "")
    return ""


def _trace_reason(trace: Any) -> str:
    if trace is None:
        return ""
    top = getattr(trace, "reason", None)
    if top:
        return str(top)
    if isinstance(trace, dict) and trace.get("reason"):
        return str(trace["reason"])
    kps = _trace_kps(trace)
    if not kps:
        return ""
    kp0 = kps[0]
    if isinstance(kp0, dict):
        return str(kp0.get("reason") or "")
    return str(getattr(kp0, "reason", "") or "")


def _trace_kps(trace: Any) -> list[Any]:
    if trace is None:
        return []
    if isinstance(trace, dict):
        kps = trace.get("knowledge_points") or []
        return list(kps)
    kps = getattr(trace, "knowledge_points", None) or []
    return list(kps)


def _node_content(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return str(node.get("content") or node.get("text") or "")
    return str(getattr(node, "content", None) or getattr(node, "text", None) or "")


def _flatten_earlier(kps: list[Any]) -> list[str]:
    out: list[str] = []
    for kp in kps:
        earlier = kp.get("earlier") if isinstance(kp, dict) else getattr(kp, "earlier", None)
        if earlier is None:
            continue
        if isinstance(earlier, str):
            out.append(earlier)
            continue
        if isinstance(earlier, dict):
            out.append(_node_content(earlier))
            continue
        if isinstance(earlier, (list, tuple)):
            for item in earlier:
                out.append(_node_content(item))
            continue
        out.append(_node_content(earlier))
    return out


def _flatten_evidence_refs(kps: list[Any]) -> list[str]:
    out: list[str] = []
    for kp in kps:
        refs = (
            kp.get("evidence_refs")
            if isinstance(kp, dict)
            else getattr(kp, "evidence_refs", None)
        )
        if not refs:
            continue
        for ref in refs:
            s = str(ref).strip()
            if s:
                out.append(s)
    return out


def _str_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"trace {field_name} must be a list of strings")
    if any(not isinstance(item, str) for item in value):
        raise TypeError(f"trace {field_name} must contain only strings")
    return [item for item in value if item]


def _optional_non_negative_int(data: dict[str, Any], field_name: str) -> int | None:
    if field_name not in data or data[field_name] is None:
        return None
    value = data[field_name]
    if type(value) is not int:
        raise TypeError(f"trace {field_name} must be an integer or null")
    if value < 0:
        raise ValueError(f"trace {field_name} must be non-negative")
    return value


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
