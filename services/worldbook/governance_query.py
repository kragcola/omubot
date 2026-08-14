"""Bounded redacted Admin query projection for governed Worldbook facts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from services.worldbook.governance_contracts import WorldbookEventProposalV1
from services.worldbook.governance_store import WorldbookGovernanceStore

_CONTRACT_VERSION = "worldbook_governance_admin_query.v1"
_ADMIN_SCHEMA_VERSION = 1
_SOURCE_SCHEMA_VERSION = 1
_MODE = "offline_dark"
_CURSOR_KEY = secrets.token_bytes(32)
_SOURCE_KINDS = frozenset({"schedule", "social_evidence"})
_STATUSES = frozenset({"pending", "approved", "rejected", "committed"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _base(*, available: bool) -> dict[str, Any]:
    return {
        "available": available,
        "mode": _MODE,
        "reason": "" if available else "source_unavailable",
        "contract_version": _CONTRACT_VERSION,
        "admin_schema_version": _ADMIN_SCHEMA_VERSION,
        "source_schema_version": _SOURCE_SCHEMA_VERSION,
        "snapshot_at": _now_iso(),
    }


def _bounded_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("limit must be an integer")
    if not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _filter_values(
    *,
    world_id: str | None,
    source_kind: str | None,
    status: str | None,
) -> dict[str, str]:
    values = {
        "world_id": str(world_id or "").strip(),
        "source_kind": str(source_kind or "").strip(),
        "status": str(status or "").strip(),
    }
    if values["source_kind"] and values["source_kind"] not in _SOURCE_KINDS:
        raise ValueError("invalid Worldbook source_kind filter")
    if values["status"] and values["status"] not in _STATUSES:
        raise ValueError("invalid Worldbook status filter")
    if len(values["world_id"]) > 120:
        raise ValueError("invalid Worldbook world_id filter")
    return values


def _filter_digest(filters: Mapping[str, str]) -> str:
    raw = json.dumps(filters, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _encode_cursor(
    *,
    filters: Mapping[str, str],
    proposal_snapshot: int,
    decision_snapshot: int,
    receipt_snapshot: int,
    after: int,
) -> str:
    payload = _b64encode(
        json.dumps(
            {
                "v": 2,
                "filter": _filter_digest(filters),
                "proposal_snapshot": proposal_snapshot,
                "decision_snapshot": decision_snapshot,
                "receipt_snapshot": receipt_snapshot,
                "after": after,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    signature = hmac.new(_CURSOR_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"wgc2_{payload}.{signature}"


def _decode_cursor(
    value: str | None,
    *,
    filters: Mapping[str, str],
) -> tuple[int, int, int, int] | None:
    if value in (None, ""):
        return None
    raw = str(value)
    prefix, separator, signed = raw.partition("_")
    payload, dot, signature = signed.partition(".")
    expected = hmac.new(_CURSOR_KEY, payload.encode(), hashlib.sha256).hexdigest()
    if (
        prefix != "wgc2"
        or not separator
        or not dot
        or not hmac.compare_digest(signature, expected)
    ):
        raise ValueError("invalid or tampered Worldbook cursor")
    try:
        decoded = json.loads(_b64decode(payload))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Worldbook cursor payload") from exc
    if not isinstance(decoded, dict):
        raise ValueError("invalid Worldbook cursor payload")
    proposal_snapshot = decoded.get("proposal_snapshot")
    decision_snapshot = decoded.get("decision_snapshot")
    receipt_snapshot = decoded.get("receipt_snapshot")
    after = decoded.get("after")
    if (
        decoded.get("v") != 2
        or decoded.get("filter") != _filter_digest(filters)
        or isinstance(proposal_snapshot, bool)
        or not isinstance(proposal_snapshot, int)
        or isinstance(decision_snapshot, bool)
        or not isinstance(decision_snapshot, int)
        or isinstance(receipt_snapshot, bool)
        or not isinstance(receipt_snapshot, int)
        or isinstance(after, bool)
        or not isinstance(after, int)
        or proposal_snapshot < 0
        or decision_snapshot < 0
        or receipt_snapshot < 0
        or after < 0
        or after > proposal_snapshot
    ):
        raise ValueError("Worldbook cursor does not match query filters")
    return proposal_snapshot, decision_snapshot, receipt_snapshot, after


def _opened(source: WorldbookGovernanceStore | None) -> tuple[Any, Any] | None:
    if source is None:
        return None
    db = getattr(source, "_db", None)
    lock = getattr(source, "_write_lock", None)
    if db is None or lock is None:
        return None
    return db, lock


class WorldbookGovernanceAdminQueryV1:
    """Read a caller-owned, already-open Worldbook governance store."""

    def __init__(self, source: WorldbookGovernanceStore | None) -> None:
        self._source = source

    async def summary(self) -> dict[str, Any]:
        opened = _opened(self._source)
        if opened is None:
            return {
                **_base(available=False),
                "proposal_count": None,
                "pending_count": None,
                "approved_count": None,
                "rejected_count": None,
                "committed_count": None,
            }
        db, lock = opened
        try:
            async with lock:
                cursor = await db.execute(
                    """
                    SELECT
                        COUNT(*) AS proposal_count,
                        SUM(CASE WHEN decisions.proposal_id IS NULL THEN 1 ELSE 0 END)
                            AS pending_count,
                        SUM(CASE WHEN decisions.decision = 'approve'
                                      AND receipts.proposal_id IS NULL
                                 THEN 1 ELSE 0 END) AS approved_count,
                        SUM(CASE WHEN decisions.decision = 'reject' THEN 1 ELSE 0 END)
                            AS rejected_count,
                        SUM(CASE WHEN receipts.proposal_id IS NOT NULL THEN 1 ELSE 0 END)
                            AS committed_count
                    FROM worldbook_governance_proposals AS proposals
                    LEFT JOIN worldbook_governance_decisions AS decisions
                      ON decisions.proposal_id = proposals.proposal_id
                    LEFT JOIN worldbook_governance_receipts AS receipts
                      ON receipts.proposal_id = proposals.proposal_id
                    """
                )
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
        except Exception:
            return {
                **_base(available=False),
                "proposal_count": None,
                "pending_count": None,
                "approved_count": None,
                "rejected_count": None,
                "committed_count": None,
            }
        return {
            **_base(available=True),
            "proposal_count": int(row["proposal_count"] or 0),
            "pending_count": int(row["pending_count"] or 0),
            "approved_count": int(row["approved_count"] or 0),
            "rejected_count": int(row["rejected_count"] or 0),
            "committed_count": int(row["committed_count"] or 0),
        }

    async def list_proposals(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        world_id: str | None = None,
        source_kind: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        page_limit = _bounded_limit(limit)
        filters = _filter_values(
            world_id=world_id,
            source_kind=source_kind,
            status=status,
        )
        keyset = _decode_cursor(cursor, filters=filters)
        opened = _opened(self._source)
        if opened is None:
            return {
                **_base(available=False),
                "items": [],
                "next_cursor": None,
            }
        db, lock = opened
        try:
            async with lock:
                if keyset is None:
                    max_cursor = await db.execute(
                        """
                        SELECT
                            COALESCE((
                                SELECT MAX(proposal_seq)
                                FROM worldbook_governance_proposals
                            ), 0) AS proposal_maximum,
                            COALESCE((
                                SELECT MAX(decision_seq)
                                FROM worldbook_governance_decisions
                            ), 0) AS decision_maximum,
                            COALESCE((
                                SELECT MAX(receipt_seq)
                                FROM worldbook_governance_receipts
                            ), 0) AS receipt_maximum
                        """
                    )
                    try:
                        maximum_row = await max_cursor.fetchone()
                    finally:
                        await max_cursor.close()
                    proposal_snapshot = int(maximum_row["proposal_maximum"] or 0)
                    decision_snapshot = int(maximum_row["decision_maximum"] or 0)
                    receipt_snapshot = int(maximum_row["receipt_maximum"] or 0)
                    after = 0
                else:
                    (
                        proposal_snapshot,
                        decision_snapshot,
                        receipt_snapshot,
                        after,
                    ) = keyset

                conditions = ["proposals.proposal_seq > ?", "proposals.proposal_seq <= ?"]
                parameters: list[Any] = [
                    decision_snapshot,
                    receipt_snapshot,
                    after,
                    proposal_snapshot,
                ]
                if filters["world_id"]:
                    conditions.append("proposals.world_id = ?")
                    parameters.append(filters["world_id"])
                if filters["source_kind"]:
                    conditions.append("proposals.source_kind = ?")
                    parameters.append(filters["source_kind"])
                if filters["status"]:
                    conditions.append(
                        "CASE "
                        "WHEN receipts.proposal_id IS NOT NULL THEN 'committed' "
                        "WHEN decisions.decision = 'approve' THEN 'approved' "
                        "WHEN decisions.decision = 'reject' THEN 'rejected' "
                        "ELSE 'pending' END = ?"
                    )
                    parameters.append(filters["status"])
                parameters.append(page_limit + 1)
                query_cursor = await db.execute(
                    f"""
                    SELECT
                        proposals.proposal_seq,
                        proposals.proposal_id,
                        CASE
                            WHEN receipts.proposal_id IS NOT NULL THEN 'committed'
                            WHEN decisions.decision = 'approve' THEN 'approved'
                            WHEN decisions.decision = 'reject' THEN 'rejected'
                            ELSE 'pending'
                        END AS snapshot_status,
                        CASE WHEN decisions.proposal_id IS NULL THEN 0 ELSE 1 END
                            AS decision_present,
                        CASE WHEN receipts.proposal_id IS NULL THEN 0 ELSE 1 END
                            AS receipt_present
                    FROM worldbook_governance_proposals AS proposals
                    LEFT JOIN worldbook_governance_decisions AS decisions
                      ON decisions.proposal_id = proposals.proposal_id
                     AND decisions.decision_seq <= ?
                    LEFT JOIN worldbook_governance_receipts AS receipts
                      ON receipts.proposal_id = proposals.proposal_id
                     AND receipts.receipt_seq <= ?
                    WHERE {' AND '.join(conditions)}
                    ORDER BY proposals.proposal_seq
                    LIMIT ?
                    """,
                    tuple(parameters),
                )
                try:
                    rows = await query_cursor.fetchall()
                finally:
                    await query_cursor.close()
        except Exception:
            return {
                **_base(available=False),
                "items": [],
                "next_cursor": None,
            }

        visible = rows[:page_limit]
        items: list[dict[str, Any]] = []
        try:
            for row in visible:
                item = await self._proposal_item(
                    str(row["proposal_id"]),
                    status_projection=(
                        str(row["snapshot_status"]),
                        bool(row["decision_present"]),
                        bool(row["receipt_present"]),
                    ),
                )
                if item is None:
                    raise RuntimeError("Worldbook proposal disappeared during query")
                items.append(item)
        except Exception:
            return {
                **_base(available=False),
                "items": [],
                "next_cursor": None,
            }
        next_cursor = None
        if len(rows) > page_limit and visible:
            next_cursor = _encode_cursor(
                filters=filters,
                proposal_snapshot=proposal_snapshot,
                decision_snapshot=decision_snapshot,
                receipt_snapshot=receipt_snapshot,
                after=int(visible[-1]["proposal_seq"]),
            )
        return {
            **_base(available=True),
            "items": items,
            "next_cursor": next_cursor,
        }

    async def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        clean_id = str(proposal_id or "").strip()
        if not clean_id or len(clean_id) > 120:
            raise ValueError("invalid Worldbook proposal_id")
        if _opened(self._source) is None:
            return {**_base(available=False), "item": None}
        try:
            item = await self._proposal_item(clean_id)
        except Exception:
            return {**_base(available=False), "item": None}
        return {**_base(available=True), "item": item}

    async def _proposal_item(
        self,
        proposal_id: str,
        *,
        status_projection: tuple[str, bool, bool] | None = None,
    ) -> dict[str, Any] | None:
        source = self._source
        if source is None:
            return None
        proposal = await source.get_proposal(proposal_id)
        if proposal is None:
            return None
        if status_projection is None:
            decision = await source.get_operator_decision(proposal_id)
            receipt = await source.get_commit_receipt(proposal_id)
            status = (
                "committed"
                if receipt is not None
                else decision.decision
                if decision is not None
                else "pending"
            )
            status = "approved" if status == "approve" else status
            status = "rejected" if status == "reject" else status
            decision_present = decision is not None
            receipt_present = receipt is not None
        else:
            status, decision_present, receipt_present = status_projection
        return _proposal_dto(
            proposal,
            status=status,
            decision_present=decision_present,
            receipt_present=receipt_present,
        )


def _proposal_dto(
    proposal: WorldbookEventProposalV1,
    *,
    status: str,
    decision_present: bool,
    receipt_present: bool,
) -> dict[str, Any]:
    occurred_at = proposal.proposed_at
    if proposal.evidence and proposal.evidence[0].occurred_at is not None:
        occurred_at = proposal.evidence[0].occurred_at
    return {
        "contract_version": proposal.contract_version,
        "proposal_id": proposal.proposal_id,
        "proposal_sha256": proposal.proposal_sha256,
        "event_id": proposal.event.event_id,
        "world_id": proposal.world_ref.world_id,
        "source_kind": proposal.source.value,
        "status": status,
        "decision_present": decision_present,
        "receipt_present": receipt_present,
        "occurred_at": _iso(occurred_at),
        "proposed_at": _iso(proposal.proposed_at),
        "provenance": {
            "source_binding_sha256": proposal.source_binding_sha256,
            "evidence_count": len(proposal.evidence),
            "time_basis": (
                "source_evidence" if proposal.evidence else "schedule_binding"
            ),
        },
    }


def _iso(value: datetime | str) -> str:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Worldbook query timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["WorldbookGovernanceAdminQueryV1"]
