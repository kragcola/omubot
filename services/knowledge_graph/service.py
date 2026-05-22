"""Governance service for the lightweight knowledge graph."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from services.context.types import ContextHit
from services.knowledge_graph.extractor import KnowledgeGraphExtractor
from services.knowledge_graph.llm_extractor import LLMGraphExtractor
from services.knowledge_graph.store import KnowledgeGraphStore
from services.knowledge_graph.types import GraphCandidate, GraphFact

_L = logger.bind(channel="knowledge_graph")

FactListener = Callable[[GraphFact, dict[str, Any]], Awaitable[None]]


class KnowledgeGraphService:
    def __init__(
        self,
        db_path: str | Path = "storage/knowledge_graph.db",
        *,
        llm_client: Any = None,
    ) -> None:
        self._store = KnowledgeGraphStore(db_path)
        # Regex baseline retained only for offline evaluation; never invoked
        # by the production extract path after PR2 (2026-05-21).
        self._regex_baseline = KnowledgeGraphExtractor()
        self._llm_extractor = LLMGraphExtractor(llm_client=llm_client)
        self._fact_listeners: list[FactListener] = []

    def attach_llm_client(self, llm_client: Any) -> None:
        """Late-bind LLM client when ChatPlugin builds it after kg init."""
        self._llm_extractor = LLMGraphExtractor(llm_client=llm_client)

    async def init(self) -> None:
        await self._store.init()

    async def close(self) -> None:
        await self._store.close()

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
        """
        subject = _clean_field(subject)
        predicate = _clean_field(predicate)
        object = _clean_field(object)
        scope = _clean_field(scope or str(evidence.get("scope") or "global")) or "global"
        scope_id = _clean_field(scope_id or str(evidence.get("scope_id") or "global")) or "global"
        if not subject or not predicate or not object:
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

        if promote_directly:
            fact = await self._store.add_fact(
                subject=subject,
                predicate=predicate,
                object=object,
                confidence=confidence,
                source=source,
                evidence=evidence,
                status="active",
                scope=scope,
                scope_id=scope_id,
            )
            await self._fire_fact_listeners(fact, evidence)
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
                evidence=evidence,
                status="pending",
                scope=scope,
                scope_id=scope_id,
            )
        return None

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

    async def approve_candidate(self, candidate_id: str) -> GraphFact | None:
        candidate = await self._store.get_candidate(candidate_id)
        if candidate is None or candidate.status != "pending":
            return None
        fact = await self._store.add_fact(
            subject=candidate.subject,
            predicate=candidate.predicate,
            object=candidate.object,
            confidence=candidate.confidence,
            source=candidate.source,
            evidence=candidate.evidence,
            status="active",
            scope=candidate.scope,
            scope_id=candidate.scope_id,
        )
        await self._store.set_candidate_status(candidate_id, "active", review_note="approved")
        await self._fire_fact_listeners(fact, candidate.evidence)
        fact.evidence = await self._store.list_evidence(fact.fact_id)
        return fact

    async def reject_candidate(self, candidate_id: str, *, note: str = "") -> bool:
        return await self._store.set_candidate_status(candidate_id, "rejected", review_note=note)

    async def list_entities(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await self._store.list_entities(limit=limit)

    async def list_relationships(self, *, limit: int = 100) -> list[dict[str, Any]]:
        facts = await self._store.list_facts(status="active", limit=limit)
        for fact in facts:
            fact.evidence = await self._store.list_evidence(fact.fact_id)
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
            evidence = _evidence_from_existing(fact_id, old_evidence)
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
            metadata={"supersede_note": note},
        )
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


def _evidence_from_existing(fact_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if rows:
        first = rows[0]
        evidence: dict[str, Any] = {
            "type": first.get("type") or "graph_fact",
            "id": first.get("id") or fact_id,
            "quote": first.get("quote") or "",
            "supersedes_fact_id": fact_id,
        }
        if first.get("type") == "memory_card":
            evidence["card_id"] = first.get("id") or ""
        elif first.get("type") == "doc_chunk":
            evidence["chunk_id"] = first.get("id") or ""
        return evidence
    return {"type": "graph_fact", "id": fact_id, "quote": "", "supersedes_fact_id": fact_id}
