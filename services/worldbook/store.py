"""Atomic JSON loaders and stores for worldbook registries and mutable state."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from services.worldbook.domain import (
    CanonEntry,
    CanonMutationError,
    EventProposal,
    FictionCommitRecord,
    LifeState,
    LifeStateItem,
    ProposalDecision,
    SourceMeta,
    Storylet,
    find_forbidden_proposal_marker,
    proposal_semantic_fields,
    utc_now_iso,
    validate_life_state_meta,
)

_L = logger.bind(channel="worldbook.store")
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_FORBIDDEN_PROPOSAL_KEYS = frozenset({
    "persona_canon",
    "native_canon",
    "factual",
    "factual_commit",
    "canon_write",
    "social_fact",
})


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def forbidden_proposal_key(value: Any) -> str | None:
    """Return the first forbidden factual/Canon key found recursively.

    Uses the closed domain scanner (keys + control values) and maps the
    result to a short key token for existing callers.
    """
    marker = find_forbidden_proposal_marker(value)
    if marker is None:
        # Backward-compatible exact-key scan for legacy tests.
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).strip().lower()
                if normalized in _FORBIDDEN_PROPOSAL_KEYS:
                    return normalized
                nested = forbidden_proposal_key(item)
                if nested is not None:
                    return nested
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                nested = forbidden_proposal_key(item)
                if nested is not None:
                    return nested
        return None
    # marker shapes: forbidden_key:X@path | forbidden_control:k=v@path
    if marker.startswith("forbidden_key:"):
        body = marker.split(":", 1)[1]
        return body.split("@", 1)[0]
    if marker.startswith("forbidden_control:"):
        body = marker.split(":", 1)[1]
        return body.split("@", 1)[0]
    return marker


class CanonRegistry:
    """Read-only Native World Canon registry loaded from versioned JSON files.

    Mutation APIs raise :class:`CanonMutationError`. Empty directory is safe.
    """

    def __init__(self, canon_dir: str | Path) -> None:
        self._dir = Path(canon_dir)
        self._entries: dict[str, CanonEntry] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> list[CanonEntry]:
        entries: dict[str, CanonEntry] = {}
        for path in _iter_json_files(self._dir):
            try:
                raw = _read_json(path)
            except (json.JSONDecodeError, OSError) as exc:
                _L.warning("canon load skip | path={} err={}", path, exc)
                continue
            items: list[Any] = raw if isinstance(raw, list) else [raw]
            if isinstance(raw, dict) and "entries" in raw:
                raw_entries = raw.get("entries")
                items = raw_entries if isinstance(raw_entries, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    entry = CanonEntry.from_dict(item)
                except (TypeError, ValueError) as exc:
                    _L.warning("canon entry invalid | path={} err={}", path, exc)
                    continue
                entries[entry.entry_id] = entry
        self._entries = entries
        self._loaded = True
        return self.list_entries()

    def list_entries(self) -> list[CanonEntry]:
        return sorted(self._entries.values(), key=lambda e: (-e.priority, e.entry_id))

    def get(self, entry_id: str) -> CanonEntry | None:
        return self._entries.get(str(entry_id))

    def save(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Native World Canon is immutable at runtime")

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Native World Canon is immutable at runtime")

    def delete(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Native World Canon is immutable at runtime")

    def upsert(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Native World Canon is immutable at runtime")


class PersonaCanonRef:
    """Read-only reference to Persona v2 identity (no second persona store)."""

    def __init__(self, identity_text: str = "", persona_id: str = "") -> None:
        self._identity_text = str(identity_text or "")
        self._persona_id = str(persona_id or "")

    @property
    def persona_id(self) -> str:
        return self._persona_id

    @property
    def identity_text(self) -> str:
        return self._identity_text

    def mutate(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Persona Canon is immutable at runtime")

    def write(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("Persona Canon is immutable at runtime")


class StoryletRegistry:
    """Versioned Storylet configuration registry (empty → no candidates)."""

    def __init__(self, storylet_dir: str | Path) -> None:
        self._dir = Path(storylet_dir)
        self._items: dict[str, Storylet] = {}
        self._loaded = False

    def load(self) -> list[Storylet]:
        items: dict[str, Storylet] = {}
        for path in _iter_json_files(self._dir):
            try:
                raw = _read_json(path)
            except (json.JSONDecodeError, OSError) as exc:
                _L.warning("storylet load skip | path={} err={}", path, exc)
                continue
            entries: list[Any] = raw if isinstance(raw, list) else [raw]
            if isinstance(raw, dict) and "storylets" in raw:
                raw_storylets = raw.get("storylets")
                entries = raw_storylets if isinstance(raw_storylets, list) else []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                try:
                    storylet = Storylet.from_dict(item)
                except (TypeError, ValueError) as exc:
                    _L.warning("storylet invalid | path={} err={}", path, exc)
                    continue
                items[storylet.storylet_id] = storylet
        self._items = items
        self._loaded = True
        return self.list_storylets()

    def list_storylets(self) -> list[Storylet]:
        return sorted(
            self._items.values(),
            key=lambda s: (-s.saliency, -s.priority, s.storylet_id),
        )

    def get(self, storylet_id: str) -> Storylet | None:
        return self._items.get(str(storylet_id))


class LifeStateStore:
    """Atomic JSON life-state persistence with revision CAS."""

    def __init__(self, state_dir: str | Path) -> None:
        self._dir = Path(state_dir)
        self._path = self._dir / "life_state.json"
        self._lock_path = self._dir / ".life_state.lock"
        self._thread_lock = _thread_lock(self._dir)

    def load(self) -> LifeState:
        with self._locked():
            if not self._path.exists():
                return LifeState()
            try:
                raw = _read_json(self._path)
                if not isinstance(raw, dict):
                    raise TypeError("life state root must be object")
                return LifeState.from_dict(raw)
            except (json.JSONDecodeError, TypeError, ValueError, OSError) as exc:
                _L.error("life state load failed | path={} err={}", self._path, exc)
                return LifeState()

    def load_readonly(self) -> LifeState:
        """Read the atomic JSON snapshot without creating lock files/directories.

        Writers publish with ``os.replace``, so an unlocked reader observes the
        previous or next complete file. This port is intended for GET-only
        observability paths that must not mutate the filesystem.
        """
        if not self._path.exists():
            return LifeState()
        try:
            raw = _read_json(self._path)
            if not isinstance(raw, dict):
                raise TypeError("life state root must be object")
            return LifeState.from_dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError, OSError) as exc:
            _L.error("life state readonly load failed | path={} err={}", self._path, exc)
            return LifeState()

    def save(self, state: LifeState) -> LifeState:
        with self._locked():
            current = self._load_unlocked()
            expected = max(0, int(state.revision))
            actual = max(0, int(current.revision))
            if expected != actual:
                raise RuntimeError(
                    f"life state revision conflict expected={expected} actual={actual}"
                )
            state.revision = actual + 1
            if not state.updated_at:
                state.updated_at = utc_now_iso()
            _atomic_write_json(self._path, state.to_dict())
            return state

    def update(self, mutator: Callable[[LifeState], None]) -> LifeState:
        with self._locked():
            current = self._load_unlocked()
            before = current.to_dict()
            mutator(current)
            if current.to_dict() == before:
                return current
            current.revision = max(0, int(current.revision)) + 1
            current.updated_at = utc_now_iso()
            _atomic_write_json(self._path, current.to_dict())
            return current

    def set_item(
        self,
        key: str,
        value: str,
        *,
        source: str = "life_state",
        scope: str = "self",
        confidence: str = "medium",
        privacy: str = "private",
        decay_at: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        updated_at: str | None = None,
    ) -> LifeState:
        meta = SourceMeta(
            source=source,  # type: ignore[arg-type]
            scope=scope,
            confidence=confidence,  # type: ignore[arg-type]
            privacy=privacy,  # type: ignore[arg-type]
            updated_at=str(updated_at or utc_now_iso()),
            decay_at=decay_at,
            revision=0,
            evidence_refs=evidence_refs,
        )
        # Fail closed before mutate: TTL + trusted self/private labeling.
        validate_life_state_meta(meta, require_ttl=True)

        def apply(state: LifeState) -> None:
            state.items[str(key)] = LifeStateItem(
                key=str(key),
                value=str(value),
                meta=SourceMeta(
                    source=meta.source,
                    scope=meta.scope,
                    confidence=meta.confidence,
                    privacy=meta.privacy,
                    updated_at=meta.updated_at,
                    decay_at=meta.decay_at,
                    revision=state.revision + 1,
                    evidence_refs=meta.evidence_refs,
                ),
            )

        return self.update(apply)

    def _load_unlocked(self) -> LifeState:
        if not self._path.exists():
            return LifeState()
        raw = _read_json(self._path)
        if not isinstance(raw, dict):
            raise TypeError("life state root must be object")
        return LifeState.from_dict(raw)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._thread_lock.acquire()
        handle = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            handle = self._lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self._thread_lock.release()


class ProposalStore:
    """Dream proposal store — proposal-only; never factual or Canon."""

    def __init__(self, state_dir: str | Path) -> None:
        self._dir = Path(state_dir) / "proposals"
        self._lock_path = Path(state_dir) / ".proposals.lock"
        self._thread_lock = _thread_lock(Path(state_dir))

    def list_proposals(self) -> list[EventProposal]:
        if not self._dir.exists():
            return []
        out: list[EventProposal] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                raw = _read_json(path)
                if isinstance(raw, dict):
                    out.append(EventProposal.from_dict(raw))
            except (json.JSONDecodeError, TypeError, ValueError, OSError):
                continue
        return out

    def save_proposal(self, proposal: EventProposal) -> EventProposal:
        # Domain already enforces proposal-only; re-check at store boundary.
        if str(proposal.status) != "proposal":
            raise ValueError(
                f"ProposalStore only accepts status='proposal', got {proposal.status!r}"
            )
        if str(proposal.source) not in {"dream_proposal", "system"}:
            raise ValueError(
                f"ProposalStore forbids source={proposal.source!r}"
            )
        # Reject any payload that attempts Canon / factual promotion fields.
        forbidden = forbidden_proposal_key(proposal.payload)
        if forbidden is not None:
            raise ValueError(
                f"proposal payload may not contain {forbidden!r}; "
                "Dream cannot promote factual or Canon data"
            )
        with self._locked():
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{proposal.proposal_id}.json"
            if path.exists():
                try:
                    existing_raw = _read_json(path)
                except (json.JSONDecodeError, OSError) as exc:
                    raise RuntimeError(
                        f"proposal immutable conflict id={proposal.proposal_id!r}: "
                        f"corrupt existing record ({type(exc).__name__})"
                    ) from exc
                if not isinstance(existing_raw, dict):
                    raise RuntimeError(
                        f"proposal immutable conflict id={proposal.proposal_id!r}: "
                        "corrupt existing record"
                    )
                try:
                    existing = EventProposal.from_dict(existing_raw)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"proposal immutable conflict id={proposal.proposal_id!r}: "
                        f"unreadable existing record ({type(exc).__name__})"
                    ) from exc
                # Semantic idempotency: same identity fields → keep original
                # created_at and never overwrite JSON content.
                if proposal_semantic_fields(existing) == proposal_semantic_fields(
                    proposal
                ):
                    return existing
                raise RuntimeError(
                    f"proposal immutable conflict id={proposal.proposal_id!r}: "
                    "existing proposal content differs"
                )
            _atomic_write_json(path, proposal.to_dict())
            return proposal

    def load(self, proposal_id: str) -> EventProposal | None:
        path = self._dir / f"{proposal_id}.json"
        if not path.exists():
            return None
        try:
            raw = _read_json(path)
            if not isinstance(raw, dict):
                return None
            return EventProposal.from_dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError, OSError):
            return None

    def commit_factual(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermissionError(
            "ProposalStore cannot commit factual social evidence"
        )

    def write_canon(self, *_args: Any, **_kwargs: Any) -> None:
        raise CanonMutationError("ProposalStore cannot write Canon")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._thread_lock.acquire()
        handle = None
        try:
            Path(self._lock_path).parent.mkdir(parents=True, exist_ok=True)
            handle = self._lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self._thread_lock.release()


class _ExactJsonRecordStore:
    """Atomic JSON records with exact-payload idempotent save + conflict."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        subdir: str,
        lock_name: str,
    ) -> None:
        self._dir = Path(state_dir) / subdir
        self._lock_path = Path(state_dir) / lock_name
        self._thread_lock = _thread_lock(Path(state_dir) / subdir)

    def _path_for(self, record_id: str) -> Path:
        return self._dir / f"{record_id}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._thread_lock.acquire()
        handle = None
        try:
            Path(self._lock_path).parent.mkdir(parents=True, exist_ok=True)
            handle = self._lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if handle is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self._thread_lock.release()

    def _save_exact(
        self,
        record_id: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        path = self._path_for(record_id)
        with self._locked():
            self._dir.mkdir(parents=True, exist_ok=True)
            if path.exists():
                existing = _read_json(path)
                if not isinstance(existing, dict):
                    raise RuntimeError(f"corrupt record at {path}")
                # Exact idempotent: same payload is a no-op; different conflicts.
                if existing == dict(payload):
                    return existing
                raise RuntimeError(
                    f"record conflict id={record_id!r}: existing payload differs"
                )
            _atomic_write_json(path, payload)
            return dict(payload)

    def _load_raw(self, record_id: str) -> dict[str, Any] | None:
        path = self._path_for(record_id)
        if not path.exists():
            return None
        try:
            raw = _read_json(path)
            return raw if isinstance(raw, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _list_raw(self) -> list[dict[str, Any]]:
        if not self._dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                raw = _read_json(path)
                if isinstance(raw, dict):
                    out.append(raw)
            except (json.JSONDecodeError, OSError):
                continue
        return out


class DecisionStore(_ExactJsonRecordStore):
    """Persisted ProposalDecision records under state_dir/decisions/."""

    def __init__(self, state_dir: str | Path) -> None:
        super().__init__(
            state_dir,
            subdir="decisions",
            lock_name=".decisions.lock",
        )

    def save_decision(self, decision: ProposalDecision) -> ProposalDecision:
        if str(decision.status) not in {"validated", "rejected"}:
            raise ValueError(
                f"DecisionStore only accepts validated|rejected, got {decision.status!r}"
            )
        payload = decision.to_dict()
        # Secret-free: never accept raw social text fields.
        banned = find_forbidden_proposal_marker(payload)
        if banned is not None:
            raise ValueError(f"decision payload forbidden: {banned}")
        self._save_exact(decision.decision_id, payload)
        return decision

    def load(self, decision_id: str) -> ProposalDecision | None:
        raw = self._load_raw(decision_id)
        if raw is None:
            return None
        try:
            return ProposalDecision.from_dict(raw)
        except (TypeError, ValueError):
            return None

    def load_for_proposal(self, proposal_id: str) -> ProposalDecision | None:
        """Load decision by deterministic id derived from proposal_id."""
        from services.worldbook.domain import deterministic_decision_id

        try:
            return self.load(deterministic_decision_id(proposal_id))
        except ValueError:
            return None

    def list_decisions(self) -> list[ProposalDecision]:
        out: list[ProposalDecision] = []
        for raw in self._list_raw():
            try:
                out.append(ProposalDecision.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return out


class CommitStore(_ExactJsonRecordStore):
    """Persisted FictionCommitRecord under state_dir/commits/."""

    def __init__(self, state_dir: str | Path) -> None:
        super().__init__(
            state_dir,
            subdir="commits",
            lock_name=".commits.lock",
        )

    def save_commit(self, record: FictionCommitRecord) -> FictionCommitRecord:
        if str(record.status) != "committed":
            raise ValueError(
                f"CommitStore only accepts status='committed', got {record.status!r}"
            )
        payload = record.to_dict()
        banned = find_forbidden_proposal_marker(payload)
        if banned is not None:
            raise ValueError(f"commit payload forbidden: {banned}")
        self._save_exact(record.commit_id, payload)
        return record

    def load(self, commit_id: str) -> FictionCommitRecord | None:
        raw = self._load_raw(commit_id)
        if raw is None:
            return None
        try:
            return FictionCommitRecord.from_dict(raw)
        except (TypeError, ValueError):
            return None

    def load_for_proposal(self, proposal_id: str) -> FictionCommitRecord | None:
        """Load commit for a proposal.

        Prefers the deterministic commit_id derived from proposal + decision.
        Falls back to scanning stored records by proposal_id so orphan or
        legacy commit records (non-deterministic commit_id) remain visible to
        fail-closed lifecycle checks.
        """
        from services.worldbook.domain import (
            deterministic_commit_id,
            deterministic_decision_id,
        )

        pid = str(proposal_id or "").strip()
        if not pid:
            return None
        try:
            decision_id = deterministic_decision_id(pid)
            found = self.load(deterministic_commit_id(pid, decision_id))
            if found is not None:
                return found
        except ValueError:
            pass
        for record in self.list_commits():
            if str(record.proposal_id or "").strip() == pid:
                return record
        return None

    def list_commits(self) -> list[FictionCommitRecord]:
        out: list[FictionCommitRecord] = []
        for raw in self._list_raw():
            try:
                out.append(FictionCommitRecord.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return out
