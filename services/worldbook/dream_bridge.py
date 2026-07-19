"""Dream proposal bridge — proposal-only submit + deterministic lifecycle."""

from __future__ import annotations

from typing import Any

from services.worldbook.domain import (
    CanonMutationError,
    EventProposal,
    FictionCommitRecord,
    ProposalDecision,
    ProposalProcessResult,
    utc_now_iso,
)
from services.worldbook.store import ProposalStore, forbidden_proposal_key


class DreamProposalBridge:
    """Accept Dream reflection outputs as proposals; optional validate/commit.

    EventProposal remains status=proposal forever. Validation and commit use
    independent decision/commit stores via :class:`ProposalLifecycle`.
    """

    def __init__(
        self,
        proposal_store: ProposalStore,
        *,
        lifecycle: Any | None = None,
        enabled: bool = False,
    ) -> None:
        self._store = proposal_store
        self._lifecycle = lifecycle
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def submit(
        self,
        *,
        proposal_id: str,
        kind: str,
        summary: str,
        arc_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> EventProposal:
        if not self._enabled:
            raise RuntimeError("dream_proposal_enabled is false")
        payload = dict(payload or {})
        # Fail closed on any attempt to smuggle factual / canon writes.
        banned = forbidden_proposal_key(payload)
        if banned is not None:
            raise ValueError(
                f"Dream proposal cannot carry {banned!r}; "
                "factual and Canon stores are write-forbidden"
            )
        if kind in {"factual", "canon", "persona_canon", "native_canon"}:
            raise ValueError(f"invalid dream proposal kind: {kind!r}")
        proposal = EventProposal(
            proposal_id=proposal_id,
            kind=kind,  # type: ignore[arg-type]
            summary=summary,
            status="proposal",
            arc_id=arc_id,
            payload=payload,
            created_at=utc_now_iso(),
            source="dream_proposal",
        )
        return self._store.save_proposal(proposal)

    def validate(self, proposal_id: str) -> ProposalDecision:
        if self._lifecycle is None:
            raise RuntimeError("proposal lifecycle not configured")
        return self._lifecycle.validate(proposal_id)

    def commit(self, proposal_id: str) -> FictionCommitRecord:
        if self._lifecycle is None:
            raise RuntimeError("proposal lifecycle not configured")
        return self._lifecycle.commit(proposal_id)

    def process(self, proposal_id: str) -> ProposalProcessResult:
        """Submit-time companion: validate then commit when possible."""
        if self._lifecycle is None:
            raise RuntimeError("proposal lifecycle not configured")
        return self._lifecycle.process(proposal_id)

    def validate_and_commit(self, proposal_id: str) -> ProposalProcessResult:
        return self.process(proposal_id)

    def submit_and_process(
        self,
        *,
        proposal_id: str,
        kind: str,
        summary: str,
        arc_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> ProposalProcessResult:
        """Persist proposal then deterministically validate/commit."""
        self.submit(
            proposal_id=proposal_id,
            kind=kind,
            summary=summary,
            arc_id=arc_id,
            payload=payload,
        )
        return self.process(proposal_id)

    def promote_factual(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("Dream cannot promote factual social evidence")

    def write_persona_canon(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Dream cannot write Persona Canon")

    def write_native_canon(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Dream cannot write Native World Canon")
