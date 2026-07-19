"""Governance service for the lightweight knowledge graph."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from services.context.types import ContextHit
from services.knowledge_graph.extractor import KnowledgeGraphExtractor
from services.knowledge_graph.llm_extractor import LLMGraphExtractor
from services.knowledge_graph.provenance import (
    GraphProvenanceError,
    normalize_graph_evidence,
    primary_evidence_from_rows,
)
from services.knowledge_graph.store import KnowledgeGraphStore
from services.knowledge_graph.types import GraphCandidate, GraphFact
from services.memory.entity_identity import entity_ref_from_surface, parse_entity_key

_L = logger.bind(channel="knowledge_graph")

FactListener = Callable[[GraphFact, dict[str, Any]], Awaitable[None]]


class KnowledgeGraphService:
    def __init__(
        self,
        db_path: str | Path = "storage/knowledge_graph.db",
        *,
        llm_client: Any = None,
        provenance_gate_enabled: bool = True,
        observability_enabled: bool = True,
    ) -> None:
        self._db_path = Path(db_path)
        self.provenance_gate_enabled = bool(provenance_gate_enabled)
        self.observability_enabled = bool(observability_enabled)
        self._store = KnowledgeGraphStore(
            self._db_path,
            provenance_gate_enabled=self.provenance_gate_enabled,
            observability_enabled=self.observability_enabled,
        )
        # Regex baseline retained only for offline evaluation; never invoked
        # by the production extract path after PR2 (2026-05-21).
        self._regex_baseline = KnowledgeGraphExtractor()
        self._llm_extractor = LLMGraphExtractor(llm_client=llm_client)
        self._fact_listeners: list[FactListener] = []

    @property
    def db_path(self) -> Path:
        """Read-only path of the underlying knowledge_graph database."""
        return self._db_path

    def attach_llm_client(self, llm_client: Any) -> None:
        """Late-bind LLM client when ChatPlugin builds it after kg init."""
        self._llm_extractor = LLMGraphExtractor(llm_client=llm_client)

    async def init(self) -> None:
        await self._store.init()

    async def close(self) -> None:
        await self._store.close()

    async def health_snapshot(self) -> dict[str, Any]:
        """Read-only graph population / evidence-quality health (gpo_v1).

        Public service API for admin ``graph_health``. Delegates to the store;
        never exposes private DB handles to routes.
        """
        return await self._store.health_snapshot()

    def add_fact_listener(self, listener: FactListener) -> None:
        """Register a coroutine fired after every successful ``add_fact``.

        Listener signature: ``async (fact, evidence) -> None``.
        Used by the Phase E.4 graph bridge to mirror doc-backed facts as
        ``doc_supports_fact`` edges. Listener exceptions are swallowed
        and logged WARN — they must NEVER roll back the SQL commit.
        """
        self._fact_listeners.append(listener)

    async def _fire_fact_listeners(
        self, fact: GraphFact, evidence: dict[str, Any],
    ) -> None:
        for listener in self._fact_listeners:
            try:
                await listener(fact, evidence)
            except Exception as exc:
                _L.warning(
                    "knowledge graph fact listener failed | "
                    "fact={} listener={} err={}",
                    fact.fact_id,
                    getattr(listener, "__qualname__", repr(listener)),
                    exc,
                )

    async def submit_fact_candidate(
        self,
        *,
        subject: str,
        predicate: str,
        object: str,
        confidence: float,
        source: str,
        evidence: dict[str, Any],
        scope: str | None = None,
        scope_id: str | None = None,
        promote_directly: bool = False,
    ) -> GraphFact | GraphCandidate | None:
        """Apply governance thresholds.

        After PR2 (2026-05-21) extracted facts always require human review:
        any confidence in [0.60, 1.0] enters the pending candidate queue,
        below 0.60 is discarded. The legacy ``confidence >= 0.85`` direct
        active fast-path is gone — it bypassed candidate audit and let
        regex misextractions land in ``graph_facts.active`` undetected.

        ``promote_directly`` is a privileged override for admin-driven
        flows (manual ``approve_candidate``, supersede). Auto-extraction
        callers MUST leave it False.

        When ``provenance_gate_enabled`` (gpg_v1 default), evidence is
        normalized before persistence/listeners. Invalid automated/manual
        input returns None with a closed-reason log (no raw evidence/query/
        content) and writes no pending/active row.
        """
        subject = _clean_field(subject)
        predicate = _clean_field(predicate)
        object = _clean_field(object)
        scope = _clean_field(scope or str(evidence.get("scope") or "global")) or "global"
        scope_id = _clean_field(scope_id or str(evidence.get("scope_id") or "global")) or "global"
        if not subject or not predicate or not object:
            return None

        try:
            prepared_evidence = self._prepare_evidence_for_write(evidence)
        except GraphProvenanceError as exc:
            _L.info(
                "graph provenance gate rejected submit | code={} promote_directly={}",
                exc.code,
                promote_directly,
            )
            return None

        existing_fact = await self._store.find_fact(
            subject=subject,
            predicate=predicate,
            object=object,
            scope=scope,
            scope_id=scope_id,
            statuses=("active",),
        )
        if existing_fact is not None:
            existing_fact.evidence = await self._store.list_evidence(existing_fact.fact_id)
            return existing_fact

        try:
            if promote_directly:
                fact = await self._store.add_fact(
                    subject=subject,
                    predicate=predicate,
                    object=object,
                    confidence=confidence,
                    source=source,
                    evidence=prepared_evidence,
                    status="active",
                    scope=scope,
                    scope_id=scope_id,
                    metadata=_fact_entity_metadata(
                        subject=subject,
                        object_=object,
                        scope=scope,
                        scope_id=scope_id,
                    ),
                )
                await self._fire_fact_listeners(fact, prepared_evidence)
                return fact
            if confidence >= 0.60:
                existing_candidate = await self._store.find_candidate(
                    subject=subject,
                    predicate=predicate,
                    object=object,
                    scope=scope,
                    scope_id=scope_id,
                    statuses=("pending",),
                )
                if existing_candidate is not None:
                    return existing_candidate
                return await self._store.add_candidate(
                    subject=subject,
                    predicate=predicate,
                    object=object,
                    confidence=confidence,
                    source=source,
                    evidence=prepared_evidence,
                    status="pending",
                    scope=scope,
                    scope_id=scope_id,
                )
        except GraphProvenanceError as exc:
            _L.info(
                "graph provenance gate rejected submit | code={} promote_directly={}",
                exc.code,
                promote_directly,
            )
            return None
        except ValueError:
            # Legacy store path (gate disabled) may still raise ValueError.
            if self.provenance_gate_enabled:
                _L.info(
                    "graph provenance gate rejected submit | code=legacy_value_error"
                )
                return None
            raise
        return None

    def _prepare_evidence_for_write(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Normalize when gate enabled; otherwise pass through for legacy store."""
        if not self.provenance_gate_enabled:
            return dict(evidence or {})
        return normalize_graph_evidence(evidence)

    async def find_fact_ids_by_evidence_refs(
        self,
        evidence_ids: Any,
        *,
        allowed_scopes: set[tuple[str, str]] | None = None,
    ) -> list[str]:
        """Reverse-lookup active fact ids by evidence_id refs (scoped)."""
        return await self._store.find_fact_ids_by_evidence_refs(
            evidence_ids,
            allowed_scopes=allowed_scopes,
        )

    async def extract_from_context_hits(self, hits: list[ContextHit]) -> dict[str, Any]:
        """Extract graph candidates from context hits without affecting this prompt turn."""
        _L.info("graph extract called | hits={}", len(hits))
        extracted = await self._llm_extractor.extract_from_hits(hits)
        accepted = 0
        pending = 0
        ignored = 0
        for item in extracted:
            result = await self.submit_fact_candidate(
                subject=item.subject,
                predicate=item.predicate,
                object=item.object,
                confidence=item.confidence,
                source=item.source,
                evidence=item.evidence,
            )
            if isinstance(result, GraphFact):
                accepted += 1
            elif isinstance(result, GraphCandidate):
                pending += 1
            else:
                ignored += 1
        return {
            "extracted": len(extracted),
            "accepted": accepted,
            "pending": pending,
            "ignored": ignored,
        }

    async def approve_candidate(
        self,
        candidate_id: str,
        *,
        review_note: str | None = None,
        allow_legacy_approved: bool = False,
    ) -> GraphFact | None:
        """Promote a candidate to an active graph fact.

        Default admin/human path accepts only ``status='pending'``.
        ``allow_legacy_approved=True`` is reserved for AI reviewer repair of
        invalid legacy ``status='approved'`` rows (pre-promotion-loop bug);
        it never expands the admin HTTP surface by itself.

        Materialization is a single store-owned transaction (compare-and-set
        on the candidate row + one fact + one evidence). Listeners fire only
        after that commit succeeds. Concurrent approvals of the same
        candidate yield exactly one non-None result.

        Legacy invalid pending evidence: after store rollback of a
        ``GraphProvenanceError``, re-queue the candidate as pending with a
        closed ``provenance_gate:<code>`` review_note and return None — never
        leave a partial fact/evidence. Cancellation propagates unchanged.
        """
        allowed: set[str] = {"pending"}
        if allow_legacy_approved:
            allowed.add("approved")
        # Lightweight pre-check for metadata stamping; authoritative CAS is
        # inside promote_candidate (re-loads under BEGIN IMMEDIATE).
        candidate = await self._store.get_candidate(candidate_id)
        if candidate is None or candidate.status not in allowed:
            return None
        note = "approved" if review_note is None else review_note
        metadata = _fact_entity_metadata(
            subject=candidate.subject,
            object_=candidate.object,
            scope=candidate.scope,
            scope_id=candidate.scope_id,
        )
        try:
            promoted = await self._store.promote_candidate(
                candidate_id,
                review_note=note,
                allowed_statuses=allowed,
                metadata=metadata,
            )
        except GraphProvenanceError as exc:
            closed = f"provenance_gate:{exc.code}"
            await self._store.transition_candidate_status(
                candidate_id,
                to_status="pending",
                allowed_statuses=tuple(allowed),
                review_note=closed,
            )
            _L.info(
                "graph provenance gate blocked promote | code={} candidate={}",
                exc.code,
                candidate_id,
            )
            return None
        except BaseException:
            # Cancellation and unexpected errors: store already rolled back;
            # do not requeue or swallow CancelledError.
            raise
        if promoted is None:
            return None
        fact, evidence = promoted
        await self._fire_fact_listeners(fact, evidence)
        return fact

    async def reject_candidate(self, candidate_id: str, *, note: str = "") -> bool:
        """CAS reject: only from pending or intentional legacy approved.

        Never overwrites ``active`` (already promoted) or ``rejected``.
        Single store UPDATE — no pre-read TOCTOU.
        """
        return await self._store.transition_candidate_status(
            candidate_id,
            to_status="rejected",
            allowed_statuses=("pending", "approved"),
            review_note=note,
        )

    async def keep_candidate(self, candidate_id: str, *, note: str = "") -> bool:
        """CAS re-queue to pending from pending or legacy approved only.

        Used by AI reviewers for low-confidence verdicts and for fail-closed
        repair of malformed legacy ``approved`` rows. Single store UPDATE with
        allowed_statuses CAS — never pre-reads, never overwrites ``active`` or
        ``rejected``.
        """
        return await self._store.transition_candidate_status(
            candidate_id,
            to_status="pending",
            allowed_statuses=("pending", "approved"),
            review_note=note,
        )

    async def list_entities(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await self._store.list_entities(limit=limit)

    async def list_relationships(self, *, limit: int = 100) -> list[dict[str, Any]]:
        facts = await self._store.list_facts(status="active", limit=limit)
        evidence_by_fact = await self._store.list_evidence_for_facts(
            [fact.fact_id for fact in facts]
        )
        for fact in facts:
            fact.evidence = evidence_by_fact.get(fact.fact_id, [])
        return [fact.to_dict() for fact in facts]

    async def list_relationships_for_scopes(
        self,
        *,
        allowed_scopes: list[tuple[str, str]],
        limit_per_scope: int = 200,
    ) -> list[dict[str, Any]]:
        """List active relationships without one scope crowding out another."""
        facts = await self._store.list_facts_by_scopes(
            allowed_scopes=allowed_scopes,
            status="active",
            limit_per_scope=limit_per_scope,
        )
        evidence_by_fact = await self._store.list_evidence_for_facts(
            [fact.fact_id for fact in facts]
        )
        for fact in facts:
            fact.evidence = evidence_by_fact.get(fact.fact_id, [])
        return [fact.to_dict() for fact in facts]

    async def list_scope_risks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return legacy global facts that may need owner review after scope migration."""
        facts = await self._store.list_scope_risk_facts(limit=limit)
        for fact in facts:
            fact.evidence = await self._store.list_evidence(fact.fact_id)
        return [fact.to_dict() for fact in facts]

    async def get_relationship(self, fact_id: str) -> dict[str, Any] | None:
        fact = await self._store.get_fact(fact_id)
        if fact is None:
            return None
        fact.evidence = await self._store.list_evidence(fact.fact_id)
        return fact.to_dict()

    async def reject_relationship(self, fact_id: str, *, note: str = "") -> bool:
        return await self._store.set_fact_status(
            fact_id,
            "rejected",
            metadata_update={"review_note": note, "review_action": "rejected"},
        )

    async def rollback_relationship(self, fact_id: str, *, note: str = "") -> bool:
        fact = await self._store.get_fact(fact_id)
        if fact is None:
            return False
        ok = await self._store.set_fact_status(
            fact_id,
            "rejected",
            metadata_update={"rollback_note": note, "review_action": "rollback"},
        )
        if ok and fact.supersedes:
            await self._store.set_fact_status(
                fact.supersedes,
                "active",
                metadata_update={"restored_by": fact_id},
            )
        return ok

    async def supersede_relationship(
        self,
        fact_id: str,
        *,
        subject: str,
        predicate: str,
        object: str,
        confidence: float,
        source: str = "admin",
        evidence: dict[str, Any] | None = None,
        note: str = "",
    ) -> GraphFact | None:
        old = await self._store.get_fact(fact_id)
        if old is None or old.status != "active":
            return None
        if evidence is None:
            old_evidence = await self._store.list_evidence(fact_id)
            copied = primary_evidence_from_rows(old_evidence)
            if copied is None:
                _L.info(
                    "graph supersede refused | reason=no_primary_evidence fact={}",
                    fact_id,
                )
                return None
            evidence = dict(copied)
            evidence["supersedes_fact_id"] = fact_id
        metadata = _fact_entity_metadata(
            subject=subject,
            object_=object,
            scope=old.scope,
            scope_id=old.scope_id,
        )
        old_subject_ref = parse_entity_key(
            str(old.metadata.get("subject_entity_key") or "").strip()
        )
        if old_subject_ref is None:
            old_subject_ref = entity_ref_from_surface(
                subject=old.subject,
                scope=old.scope,
                scope_id=old.scope_id,
            )
        new_subject_ref = parse_entity_key(str(metadata["subject_entity_key"]))
        is_explicit_platform_reassignment = (
            new_subject_ref is not None
            and new_subject_ref.kind in {"user", "group"}
            and new_subject_ref.entity_key != old_subject_ref.entity_key
        )
        if not is_explicit_platform_reassignment:
            metadata["subject_entity_key"] = old_subject_ref.entity_key
        metadata["supersede_note"] = note
        try:
            new_fact = await self._store.add_fact(
                subject=_clean_field(subject),
                predicate=_clean_field(predicate),
                object=_clean_field(object),
                confidence=confidence,
                source=source,
                evidence=evidence,
                status="active",
                scope=old.scope,
                scope_id=old.scope_id,
                supersedes=fact_id,
                metadata=metadata,
            )
        except GraphProvenanceError as exc:
            _L.info(
                "graph supersede refused | code={} fact={}",
                exc.code,
                fact_id,
            )
            return None
        await self._store.set_fact_status(
            fact_id,
            "superseded",
            metadata_update={"superseded_by": new_fact.fact_id, "review_action": "superseded"},
        )
        new_fact.evidence = await self._store.list_evidence(new_fact.fact_id)
        return new_fact

    async def list_candidates(self, *, status: str = "pending", limit: int = 100) -> list[dict[str, Any]]:
        candidates = await self._store.list_candidates(status=status, limit=limit)
        return [candidate.to_dict() for candidate in candidates]


def _clean_field(value: str) -> str:
    return str(value or "").strip()


def _fact_entity_metadata(
    *,
    subject: str,
    object_: str,
    scope: str,
    scope_id: str,
) -> dict[str, Any]:
    return {
        "entity_identity_version": 1,
        "subject_entity_key": entity_ref_from_surface(
            subject=subject,
            scope=scope,
            scope_id=scope_id,
        ).entity_key,
        "object_entity_key": entity_ref_from_surface(
            subject=object_,
            scope=scope,
            scope_id=scope_id,
        ).entity_key,
    }
