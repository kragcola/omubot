"""Explicit, bounded execution of encrypted N6 memory extraction decisions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import unicodedata
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal, cast
from zoneinfo import ZoneInfo

from .actions import Actions
from .archive import ArchiveService, ArchiveSourceRecord
from .domain_learning import (
    DomainLearningResult,
    DomainLearningService,
    EpisodeValue,
    LearningDomain,
    LearningValue,
    SlangValue,
    StyleValue,
)
from .learning_autopilot import AutoApplyReport, AutoCandidateRef
from .memory import MemoryCandidate, MemoryService, MemorySuggestionReason
from .memory_extractor import (
    DEFAULT_EXTRACTION_DOMAINS,
    CorrectionTarget,
    MemoryExtractionBundle,
    MemoryExtractionDomain,
    MemoryExtractionInput,
    MemoryExtractor,
    MemorySuggestion,
)
from .store import MemoryExtractionRun
from .types import ActionCall, ModelPort, ModelReply, ModelRequest, OperationError, Scope

_MAX_BATCH = 16
_MAX_ATTEMPTS = 3
_FACT_EXTRACTOR_VERSION = "memory-facts-v1"
_LEGACY_DECISION_SCHEMA = 2
_DECISION_SCHEMA = 3
_TERMINAL_ATTEMPT_ERRORS = frozenset(
    {
        "attempt_limit",
        "memory_extractor_model_failed",
        "invalid_memory_extractor_input",
        "invalid_memory_extractor_reply",
        "invalid_memory_extractor_json",
        "invalid_memory_extractor_output",
        "invalid_memory_extractor_schema",
        "invalid_memory_extractor_evidence",
        "invalid_memory_extractor_privacy",
        "invalid_memory_extractor_episode_copy",
    }
)
_ALLOWED_REASONS = frozenset(
    {
        "stable_preference",
        "time_bounded_plan",
        "communication_boundary",
        "explicit_correction",
    }
)
_PUBLIC_ERRORS = frozenset(
    {
        "denied",
        "source_revoked",
        "archive_source_changed",
        "archive_source_unavailable",
        "archive_source_not_found",
        "archive_text_unavailable",
        "archive_text_spool_unavailable",
        "archive_text_cleanup_failed",
        "archive_text_delete_failed",
        "memory_extractor_model_failed",
        "invalid_memory_extractor_input",
        "invalid_memory_extractor_reply",
        "invalid_memory_extractor_json",
        "invalid_memory_extractor_output",
        "invalid_memory_extractor_schema",
        "invalid_memory_extractor_evidence",
        "invalid_memory_extractor_privacy",
        "invalid_memory_extractor_episode_copy",
        "correction_target_not_found",
        "invalid_extraction_decision",
        "attempt_limit",
        "duplicate_action",
        "idempotency_conflict",
        "offline",
        "closed",
        "transport_cancel_failed",
        "target_required",
        "target_mismatch",
        "target_not_current",
        "target_suppressed",
        "duplicate_active_fact",
        "correction_stale",
        "payload_conflict",
        "revision_conflict",
        "slang_key_collision",
        "slang_stoplisted",
        "invalid_memory_timestamp",
        "invalid_memory_validity",
        "operation_failed",
        "timeout",
    }
)
_CONTROLLED_ACTION_ERRORS = frozenset(
    {
        "denied",
        "source_revoked",
        "archive_source_changed",
        "archive_source_unavailable",
        "archive_source_not_found",
        "offline",
        "closed",
        "duplicate_action",
        "idempotency_conflict",
        "transport_cancel_failed",
    }
)


@dataclass(frozen=True, slots=True)
class MemoryExtractionReport:
    """A content-free summary of one explicit runner pass."""

    sources: int
    recovered_decisions: int
    model_attempts: int
    candidates: int
    empty_decisions: int
    group_excluded: int
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _SealedSuggestion:
    action: Literal["add", "supersede"]
    reason: MemorySuggestionReason
    predicate: str
    value: str
    valid_from: date | None
    valid_to: date | None
    correction_target: CorrectionTarget | None
    target_fact_id: str | None
    immediate_correction_eligible: bool


@dataclass(frozen=True, slots=True)
class _SealedDecision:
    source_id: str | None
    source_revision: int | None
    suggestions: tuple[_SealedSuggestion, ...]
    slang: tuple[SlangValue, ...]
    styles: tuple[StyleValue, ...]
    episodes: tuple[EpisodeValue, ...]
    legacy: bool


class MemoryExtractionRunner:
    """Run extraction only when explicitly asked, with Archive and Actions gates."""

    def __init__(
        self,
        archive: ArchiveService,
        memory: MemoryService,
        actions: Actions,
        model_client: ModelPort,
        *,
        actor: str,
        provider: str,
        model_name: str,
        timeout: float,
        timezone: ZoneInfo,
        domain_learning: DomainLearningService | None = None,
        allowed_groups: Collection[str] = (),
        extraction_domains: Collection[MemoryExtractionDomain] | None = None,
        max_sources: int = _MAX_BATCH,
        max_attempts_per_source: int = _MAX_ATTEMPTS,
        before_seal: Callable[[ArchiveSourceRecord, MemoryExtractionBundle], Awaitable[None]] | None = None,
        after_source: Callable[[str], None] | None = None,
        after_candidates: Callable[
            [ArchiveSourceRecord, tuple[AutoCandidateRef, ...]], Awaitable[AutoApplyReport]
        ] | None = None,
    ) -> None:
        store = archive.store
        policy = archive.policy
        if (
            memory.store is not store
            or actions.store is not store
            or memory.policy is not policy
            or actions.policy is not policy
            or (
                domain_learning is not None
                and (domain_learning.store is not store or domain_learning.policy is not policy)
            )
            or policy.store is not store
        ):
            raise OperationError("memory_runtime_dependency_mismatch")
        if (
            type(actor) is not str
            or not actor
            or actor != actor.strip()
            or len(actor) > 64
            or any(unicodedata.category(char).startswith("C") for char in actor)
        ):
            raise OperationError("invalid_memory_runtime_config")
        if (
            not self._valid_destination(provider)
            or not self._valid_destination(model_name)
            or len(model_name) > 64
        ):
            raise OperationError("invalid_memory_runtime_config")
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(float(timeout))
            or not 0 < float(timeout) <= 120
        ):
            raise OperationError("invalid_memory_runtime_config")
        if type(timezone) is not ZoneInfo:
            raise OperationError("invalid_memory_runtime_config")
        if type(max_sources) is not int or not 1 <= max_sources <= _MAX_BATCH:
            raise OperationError("invalid_memory_runtime_config")
        if type(max_attempts_per_source) is not int or not 1 <= max_attempts_per_source <= _MAX_ATTEMPTS:
            raise OperationError("invalid_memory_runtime_config")
        if isinstance(allowed_groups, str):
            raise OperationError("invalid_memory_runtime_config")
        groups = frozenset(allowed_groups)
        if any(
            type(group) is not str
            or not group
            or group != group.strip()
            or len(group) > 64
            or any(unicodedata.category(char).startswith("C") for char in group)
            for group in groups
        ):
            raise OperationError("invalid_memory_runtime_config")
        if type(model_client.is_external) is not bool or not callable(model_client.request):
            raise OperationError("invalid_memory_runtime_config")
        requested_domains: Collection[MemoryExtractionDomain] = (
            extraction_domains if extraction_domains is not None
            else DEFAULT_EXTRACTION_DOMAINS if domain_learning is not None else ("fact",)
        )
        if (
            not requested_domains
            or len(set(requested_domains)) != len(requested_domains)
            or not set(requested_domains) <= set(DEFAULT_EXTRACTION_DOMAINS)
            or (domain_learning is None and set(requested_domains) != {"fact"})
        ):
            raise OperationError("invalid_memory_runtime_config")

        self.archive = archive
        self.memory = memory
        self.domain_learning = domain_learning
        self.actions = actions
        self.model_client = model_client
        self.actor = actor
        self.provider = provider
        self.model_name = model_name
        self.timeout = float(timeout)
        self.timezone = timezone
        self.allowed_groups = groups
        self.extraction_domains: tuple[MemoryExtractionDomain, ...] = tuple(
            domain for domain in DEFAULT_EXTRACTION_DOMAINS if domain in requested_domains
        )
        self.max_sources = max_sources
        self.max_attempts_per_source = max_attempts_per_source
        self.after_candidates = after_candidates
        self.before_seal = before_seal
        self.after_source = after_source
        self._pending_after_source_id: str | None = None
        self._run_lock = asyncio.Lock()

    @staticmethod
    def _valid_destination(value: object) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and value == value.strip()
            and len(value) <= 128
            and not any(unicodedata.category(char).startswith("C") for char in value)
        )

    async def run_once(self) -> MemoryExtractionReport:
        """Process one bounded batch; cancellation leaves unexpired decisions recoverable."""
        async with self._run_lock:
            batch = await self.archive.pending_extraction_sources(
                self.actor,
                provider=self.provider,
                model=self.model_name,
                limit=self.max_sources,
                after_source_id=self._pending_after_source_id,
            )
            sources = batch.sources
            if batch.last_scanned_source_id is not None:
                # Skipped purpose sources and failures must not pin the cursor.
                self._pending_after_source_id = batch.last_scanned_source_id
            recovered = 0
            model_attempts = 0
            candidates = 0
            empty = 0
            excluded = 0
            errors: list[str] = []

            for source in sources:
                extraction_run: MemoryExtractionRun | None = None
                if source.scope.group_id not in self.allowed_groups:
                    excluded += 1
                    try:
                        await self.archive.delete_text(self.actor, source.source_id, source.scope)
                    except OperationError as exc:
                        errors.append(self._public_error(exc.code))
                    except Exception:
                        errors.append("operation_failed")
                    continue

                try:
                    if self.domain_learning is not None:
                        settled_failure = await self.domain_learning.failure_source_is_settled(
                            actor=self.actor,
                            scope=source.scope,
                            source_id=source.source_id,
                            expected_source_revision=source.source_revision,
                        )
                        if settled_failure:
                            continue
                    extraction_run = await self.archive.store.memory_extraction_run_begin(
                        bot_id=source.scope.bot_id,
                        group_id=source.scope.group_id,
                        source_id=source.source_id,
                        source_revision=source.source_revision,
                    )
                    decision = await self.archive.read_extraction_decision(
                        self.actor,
                        source.source_id,
                        source.scope,
                        provider=self.provider,
                        model=self.model_name,
                    )
                    if decision is None:
                        before_attempts = await self._attempt_count(source.source_id)
                        try:
                            decision = await self._extract_and_seal(source)
                        finally:
                            after_attempts = await self._attempt_count(source.source_id)
                            model_attempts += max(0, after_attempts - before_attempts)
                    else:
                        recovered += 1

                    sealed = self._decode_decision(decision, source)
                    facts = sealed.suggestions if "fact" in self.extraction_domains else ()
                    available_domain_values: tuple[tuple[LearningDomain, Sequence[LearningValue]], ...] = (
                        ("slang", sealed.slang),
                        ("style", sealed.styles),
                        ("episode", sealed.episodes),
                    )
                    domain_values: tuple[tuple[LearningDomain, Sequence[LearningValue]], ...] = tuple(
                        (domain, values) for domain, values in available_domain_values
                        if domain in self.extraction_domains
                    )
                    if not (facts or any(values for _, values in domain_values)):
                        empty += 1
                    if not sealed.legacy and self.domain_learning is None:
                        raise OperationError("domain_learning_unavailable")
                    fact_candidate_ids: list[str] = []
                    auto_candidates: list[AutoCandidateRef] = []
                    if "fact" in self.extraction_domains:
                        extraction_run = await self.archive.store.memory_extraction_run_stage(
                            extraction_run, "fact"
                        )
                        for item in facts:
                            candidate = await self._propose(source, item)
                            fact_candidate_ids.append(candidate.candidate_id)
                            auto_candidates.append(AutoCandidateRef(
                                "fact", candidate.candidate_id, candidate.candidate_revision,
                            ))
                            candidates += 1
                        await self.memory.record_fact_extraction_result(
                            actor=self.actor,
                            scope=source.scope,
                            source_id=source.source_id,
                            subject_id=source.speaker_id,
                            extractor_version=_FACT_EXTRACTOR_VERSION,
                            expected_source_revision=source.source_revision,
                            candidate_ids=tuple(fact_candidate_ids),
                        )
                    if not sealed.legacy:
                        assert self.domain_learning is not None
                        domain_failure = False
                        for domain, values in domain_values:
                            extraction_run = await self.archive.store.memory_extraction_run_stage(
                                extraction_run, domain
                            )
                            stage = "dry_run" if domain == "episode" else "candidate"
                            result = await self.domain_learning.record_result(
                                actor=self.actor,
                                scope=source.scope,
                                source_id=source.source_id,
                                domain=domain,
                                extractor_version="memory-domains-v1",
                                expected_source_revision=source.source_revision,
                                values=values,
                                episode_stage=stage,
                            )
                            if result.result_status == "failed":
                                if result.error_code is None or result.failure_revision is None:
                                    raise OperationError("invalid_domain_learning_result")
                                domain_failure = True
                                errors.append(self._public_error(result.error_code))
                            elif result.error_code is not None or result.failure_revision is not None:
                                raise OperationError("invalid_domain_learning_result")
                            candidates += len(result.candidate_ids)
                            auto_candidates.extend(
                                AutoCandidateRef(domain, candidate_id, 1)
                                for candidate_id in result.candidate_ids
                            )
                        if self.after_candidates is not None:
                            await self.after_candidates(source, tuple(auto_candidates))
                        if domain_failure:
                            await self.domain_learning.settle_failure_source(
                                actor=self.actor,
                                scope=source.scope,
                                source_id=source.source_id,
                                expected_source_revision=source.source_revision,
                            )
                        else:
                            await self.archive.delete_text(self.actor, source.source_id, source.scope)
                    else:
                        if self.after_candidates is not None:
                            await self.after_candidates(source, tuple(auto_candidates))
                        await self.archive.delete_text(self.actor, source.source_id, source.scope)
                    await self.archive.store.memory_extraction_run_finish(
                        extraction_run,
                        status="complete",
                        require_domains=not sealed.legacy,
                        required_domains=tuple(
                            domain for domain in self.extraction_domains
                            if domain == "fact" or not sealed.legacy
                        ),
                    )
                except asyncio.CancelledError as cancelled:
                    if extraction_run is not None:
                        try:
                            await self._record_cancelled_run(extraction_run)
                        except Exception as persistence_error:
                            error_code = (
                                persistence_error.code
                                if isinstance(persistence_error, OperationError)
                                else type(persistence_error).__name__
                            )
                            cancelled.add_note(
                                f"Could not persist cancelled extraction status: {error_code}"
                            )
                            raise cancelled from persistence_error
                    raise
                except OperationError as exc:
                    errors.append(self._public_error(exc.code))
                    if extraction_run is not None:
                        await self.archive.store.memory_extraction_run_finish(
                            extraction_run,
                            status="unknown",
                            error_code=self._public_error(exc.code),
                        )
                    if exc.code in _TERMINAL_ATTEMPT_ERRORS:
                        await self._delete_exhausted_raw_text(source, errors)
                except TimeoutError:
                    errors.append("timeout")
                    if extraction_run is not None:
                        await self.archive.store.memory_extraction_run_finish(
                            extraction_run,
                            status="unknown",
                            error_code="timeout",
                        )
                    await self._delete_exhausted_raw_text(source, errors)
                except Exception:
                    errors.append("operation_failed")
                    if extraction_run is not None:
                        await self.archive.store.memory_extraction_run_finish(
                            extraction_run,
                            status="unknown",
                            error_code="operation_failed",
                        )

                finally:
                    if self.after_source is not None:
                        self.after_source(source.source_id)

            return MemoryExtractionReport(
                sources=len(sources),
                recovered_decisions=recovered,
                model_attempts=model_attempts,
                candidates=candidates,
                empty_decisions=empty,
                group_excluded=excluded,
                errors=tuple(errors),
            )

    async def _record_cancelled_run(self, run: MemoryExtractionRun) -> None:
        """Drain the cancellation receipt without hiding a Store write failure."""
        task = asyncio.create_task(
            self.archive.store.memory_extraction_run_finish(
                run, status="cancelled", error_code="cancelled"
            )
        )
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        await task

    async def retry_slang_failure(
        self,
        *,
        review_actor: str,
        scope: Scope,
        result_id: str,
        expected_failure_revision: int,
        reason: str,
    ) -> DomainLearningResult:
        """Retry one reviewed slang failure from its still-valid sealed decision."""
        learning = self.domain_learning
        if (
            learning is None
            or scope.group_id not in self.allowed_groups
            or "slang" not in self.extraction_domains
        ):
            raise OperationError("domain_learning_retry_unavailable")
        async with self._run_lock:
            target = await learning.read_slang_retry_target(
                result_id,
                actor=review_actor,
                scope=scope,
                expected_failure_revision=expected_failure_revision,
                reason=reason,
            )
            if target.result_status == "candidates":
                return target
            if target.result_status != "failed" or target.domain != "slang":
                raise OperationError("invalid_domain_learning_result")
            if target.extractor_version != "memory-domains-v1":
                raise OperationError("domain_learning_retry_unavailable")

            source_status = await self.archive.source_status(
                self.actor, target.source_id, scope
            )
            source = source_status.source
            if source_status.status != "active" or source is None:
                if source_status.status in {"revoked", "deleted"}:
                    raise OperationError("source_revoked")
                raise OperationError("domain_learning_retry_source_unavailable")
            if source.source_revision != target.source_revision:
                raise OperationError("source_revision_conflict")

            decision = await self.archive.read_extraction_decision(
                self.actor,
                target.source_id,
                scope,
                provider=self.provider,
                model=self.model_name,
            )
            if decision is None:
                raise OperationError("domain_learning_retry_source_unavailable")
            sealed = self._decode_decision(decision, source)
            if sealed.legacy or not sealed.slang:
                raise OperationError("domain_learning_retry_decision_mismatch")

            result = await learning.record_result(
                actor=self.actor,
                review_actor=review_actor,
                retry_reason=reason,
                scope=scope,
                source_id=target.source_id,
                domain="slang",
                extractor_version=target.extractor_version,
                expected_source_revision=target.source_revision,
                expected_failure_revision=expected_failure_revision,
                values=sealed.slang,
            )
            if result.result_status == "candidates":
                await self.archive.delete_text(self.actor, target.source_id, scope)
            return result

    async def _delete_exhausted_raw_text(self, source: ArchiveSourceRecord, errors: list[str]) -> None:
        """Drop exhausted raw input only; Archive rechecks permission and revision."""
        try:
            if await self._attempt_count(source.source_id) < self.max_attempts_per_source:
                return
            if await self._has_pending_attempt(source.source_id):
                return
            await self.archive.delete_exhausted_raw_text(
                self.actor,
                source.source_id,
                source.scope,
                expected_revision=source.source_revision,
            )
        except OperationError as exc:
            errors.append(self._public_error(exc.code))
        except Exception:
            errors.append("operation_failed")

    async def _has_pending_attempt(self, source_id: str) -> bool:
        prefix = f"n6-memory-extract:{source_id}:"
        return await self.archive.store.transaction(
            lambda db: (
                db.execute(
                    "SELECT 1 FROM actions WHERE action='model.invoke' AND state='dispatching' "
                    "AND substr(id,1,?)=? LIMIT 1",
                    (len(prefix), prefix),
                ).fetchone()
                is not None
            )
        )

    async def _attempt_count(self, source_id: str) -> int:
        prefix = f"n6-memory-extract:{source_id}:"
        return await self.archive.store.transaction(
            lambda db: int(
                db.execute(
                    "SELECT count(*) FROM actions WHERE action='model.invoke' AND substr(id,1,?)=?",
                    (len(prefix), prefix),
                ).fetchone()[0]
            )
        )

    async def _extract_and_seal(self, source: ArchiveSourceRecord) -> str:
        attempt_count = await self._attempt_count(source.source_id)
        if attempt_count >= self.max_attempts_per_source:
            raise OperationError("attempt_limit")

        # Always re-read through Archive immediately before creating model intent.
        body = await self.archive.read_text(
            self.actor,
            source.source_id,
            source.scope,
            provider=self.provider,
            model=self.model_name,
        )
        if body is None:
            raise OperationError("archive_text_unavailable")

        attempt = attempt_count + 1
        action_key = f"n6-memory-extract:{source.source_id}:{attempt}"
        payload_hash = hashlib.sha256(
            (f"{source.source_id}\0{source.source_revision}\0{self.provider}\0{self.model_name}").encode()
        ).hexdigest()
        call = ActionCall(
            key=action_key,
            request_id=action_key,
            subject=self.actor,
            scope=source.scope,
            action="model.invoke",
            payload_hash=payload_hash,
            provider=self.provider,
            model=self.model_name,
            includes_history=True,
            history_subjects=(source.speaker_id,),
        )

        action_error: str | None = None

        async def invoke(request: ModelRequest) -> ModelReply:
            nonlocal action_error
            try:
                return await self.actions.execute(
                    call,
                    lambda: self.model_client.request(request),
                    external=self.model_client.is_external,
                    model_task="memory",
                    preflight_transaction=lambda db: self.archive.assert_text_send_transaction(
                        db,
                        self.actor,
                        source.source_id,
                        source.scope,
                        provider=self.provider,
                        model=self.model_name,
                        expected_revision=source.source_revision,
                    ),
                    timeout=self.timeout,
                )
            except OperationError as exc:
                if exc.code in _CONTROLLED_ACTION_ERRORS:
                    action_error = exc.code
                raise

        extraction = MemoryExtractionInput(
            scope=source.scope,
            source_id=source.source_id,
            subject_id=source.speaker_id,
            body=body,
            authorized=True,
            source_kind=source.source_kind,
            speaker_kind=source.speaker_kind,
        )
        extractor = MemoryExtractor(invoke, model=self.model_name)
        if self.domain_learning is None:
            try:
                suggestions = await extractor.extract(extraction)
            except OperationError as exc:
                if action_error is not None:
                    raise OperationError(action_error) from None
                if exc.code != "memory_extractor_source_rejected":
                    raise
                suggestions = ()
            decision = await self._encode_legacy_decision(source, suggestions)
            bundle = MemoryExtractionBundle(tuple(suggestions), (), (), ())
        else:
            try:
                bundle = await extractor.extract_bundle(extraction, domains=self.extraction_domains)
            except OperationError as exc:
                if action_error is not None:
                    raise OperationError(action_error) from None
                if exc.code != "memory_extractor_source_rejected":
                    raise
                bundle = MemoryExtractionBundle((), (), (), ())
            decision = await self._encode_decision(source, bundle)
        if self.before_seal is not None:
            await self.before_seal(source, bundle)
        await self.archive.seal_extraction_decision(
            self.actor,
            source.source_id,
            source.scope,
            decision,
            provider=self.provider,
            model=self.model_name,
        )
        sealed = await self.archive.read_extraction_decision(
            self.actor,
            source.source_id,
            source.scope,
            provider=self.provider,
            model=self.model_name,
        )
        if sealed is None:
            raise OperationError("archive_text_unavailable")
        return sealed

    async def _encode_legacy_decision(
        self,
        source: ArchiveSourceRecord,
        suggestions: tuple[MemorySuggestion, ...],
    ) -> str:
        entries = await self._encode_fact_entries(source, suggestions)
        return json.dumps(
            {"schema": _LEGACY_DECISION_SCHEMA, "suggestions": entries},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    async def _encode_decision(
        self,
        source: ArchiveSourceRecord,
        bundle: MemoryExtractionBundle,
    ) -> str:
        facts = await self._encode_fact_entries(source, bundle.suggestions)
        return json.dumps(
            {
                "schema": _DECISION_SCHEMA,
                "source_id": source.source_id,
                "source_revision": source.source_revision,
                "suggestions": facts,
                "slang": [
                    {"term": item.term, "meaning": item.meaning, "aliases": list(item.aliases)}
                    for item in bundle.slang
                ],
                "style": [
                    {
                        "situation": item.situation,
                        "style": item.style,
                        "output_policy": item.output_policy,
                        "risk_tags": list(item.risk_tags),
                    }
                    for item in bundle.styles
                ],
                "episodes": [
                    {
                        "situation": item.situation,
                        "observed_context": item.observed_context,
                        "action_taken": item.action_taken,
                        "outcome_signal": item.outcome_signal,
                        "reflection": item.reflection,
                    }
                    for item in bundle.episodes
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    async def _encode_fact_entries(
        self,
        source: ArchiveSourceRecord,
        suggestions: tuple[MemorySuggestion, ...],
    ) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for suggestion in suggestions:
            if (
                suggestion.source_id != source.source_id
                or suggestion.scope != source.scope
                or suggestion.subject_id != source.speaker_id
            ):
                raise OperationError("invalid_memory_extractor_output")
            target_fact_id: str | None = None
            target = suggestion.correction_target
            if suggestion.action == "supersede":
                if target is None:
                    raise OperationError("invalid_memory_extractor_output")
                current = await self.memory.find_exact_current_fact(
                    actor=self.actor,
                    scope=source.scope,
                    subject_id=source.speaker_id,
                    predicate=target.predicate,
                    value=target.value,
                )
                if current is None:
                    raise OperationError("correction_target_not_found")
                if (
                    current.scope != source.scope
                    or current.subject_id != source.speaker_id
                    or current.predicate != target.predicate
                    or current.value != target.value
                    or current.status != "active"
                    or not current.fact_id
                ):
                    raise OperationError("correction_target_not_found")
                target_fact_id = current.fact_id
            elif target is not None:
                raise OperationError("invalid_memory_extractor_output")

            entries.append(
                {
                    "action": suggestion.action,
                    "correction_target": (
                        None if target is None else {"predicate": target.predicate, "value": target.value}
                    ),
                    "predicate": suggestion.predicate,
                    "reason": suggestion.reason,
                    "target_fact_id": target_fact_id,
                    "immediate_correction_eligible": suggestion.immediate_correction_eligible,
                    "valid_from": (
                        None if suggestion.valid_from is None else suggestion.valid_from.isoformat()
                    ),
                    "valid_to": (None if suggestion.valid_to is None else suggestion.valid_to.isoformat()),
                    "value": suggestion.value,
                }
            )
        return entries

    @staticmethod
    def _decode_decision(value: str, source: ArchiveSourceRecord | None = None) -> _SealedDecision:
        try:
            decoded_raw: object = json.loads(value)
        except (TypeError, ValueError):
            raise OperationError("invalid_extraction_decision") from None
        if not isinstance(decoded_raw, dict):
            raise OperationError("invalid_extraction_decision")
        decoded = cast(dict[str, object], decoded_raw)
        schema = decoded.get("schema")
        legacy = type(schema) is int and schema == _LEGACY_DECISION_SCHEMA
        if legacy:
            if set(decoded) != {"schema", "suggestions"}:
                raise OperationError("invalid_extraction_decision")
        elif (
            type(schema) is not int
            or schema != _DECISION_SCHEMA
            or set(decoded)
            != {"schema", "source_id", "source_revision", "suggestions", "slang", "style", "episodes"}
            or type(decoded.get("source_id")) is not str
            or type(decoded.get("source_revision")) is not int
            or source is None
            or decoded.get("source_id") != source.source_id
            or decoded.get("source_revision") != source.source_revision
        ):
            raise OperationError("invalid_extraction_decision")
        raw_suggestions = decoded.get("suggestions")
        if not isinstance(raw_suggestions, list):
            raise OperationError("invalid_extraction_decision")
        suggestion_list = cast(list[object], raw_suggestions)
        if len(suggestion_list) > 3:
            raise OperationError("invalid_extraction_decision")
        result: list[_SealedSuggestion] = []
        item_keys = {
            "action",
            "correction_target",
            "predicate",
            "reason",
            "target_fact_id",
            "immediate_correction_eligible",
            "valid_from",
            "valid_to",
            "value",
        }
        for raw_item in suggestion_list:
            if not isinstance(raw_item, dict):
                raise OperationError("invalid_extraction_decision")
            raw = cast(dict[str, object], raw_item)
            if set(raw) != item_keys:
                raise OperationError("invalid_extraction_decision")
            action = raw["action"]
            reason = raw["reason"]
            predicate = raw["predicate"]
            fact_value = raw["value"]
            if (
                not isinstance(action, str)
                or action not in {"add", "supersede"}
                or not isinstance(reason, str)
                or reason not in _ALLOWED_REASONS
                or not isinstance(predicate, str)
                or not isinstance(fact_value, str)
                or not predicate
                or not fact_value
            ):
                raise OperationError("invalid_extraction_decision")
            valid_from = MemoryExtractionRunner._parse_date(raw["valid_from"])
            valid_to = MemoryExtractionRunner._parse_date(raw["valid_to"])
            if valid_from is not None and valid_to is not None and valid_to <= valid_from:
                raise OperationError("invalid_extraction_decision")

            correction_raw = raw["correction_target"]
            correction: CorrectionTarget | None
            if correction_raw is None:
                correction = None
            elif isinstance(correction_raw, dict):
                correction_values = cast(dict[str, object], correction_raw)
                if (
                    set(correction_values) != {"predicate", "value"}
                    or not isinstance(correction_values["predicate"], str)
                    or not isinstance(correction_values["value"], str)
                    or correction_values["predicate"] != predicate
                    or not correction_values["value"]
                ):
                    raise OperationError("invalid_extraction_decision")
                correction = CorrectionTarget(
                    predicate=correction_values["predicate"],
                    value=correction_values["value"],
                )
            else:
                raise OperationError("invalid_extraction_decision")

            fact_id = raw["target_fact_id"]
            immediate_correction_eligible = raw["immediate_correction_eligible"]
            if type(immediate_correction_eligible) is not bool:
                raise OperationError("invalid_extraction_decision")
            if action == "supersede":
                if (
                    reason != "explicit_correction"
                    or correction is None
                    or type(fact_id) is not str
                    or not fact_id
                    or len(fact_id) > 128
                ):
                    raise OperationError("invalid_extraction_decision")
            elif (
                reason == "explicit_correction"
                or correction is not None
                or fact_id is not None
                or immediate_correction_eligible
            ):
                raise OperationError("invalid_extraction_decision")
            result.append(
                _SealedSuggestion(
                    action=cast(Literal["add", "supersede"], action),
                    reason=cast(MemorySuggestionReason, reason),
                    predicate=predicate,
                    value=fact_value,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    correction_target=correction,
                    target_fact_id=fact_id if type(fact_id) is str else None,
                    immediate_correction_eligible=immediate_correction_eligible,
                )
            )
        if legacy:
            return _SealedDecision(
                source_id=None,
                source_revision=None,
                suggestions=tuple(result),
                slang=(),
                styles=(),
                episodes=(),
                legacy=True,
            )

        def text_field(item: dict[str, object], key: str, limit: int) -> str:
            field_value = item.get(key)
            if (
                type(field_value) is not str
                or not field_value
                or field_value != field_value.strip()
                or len(field_value) > limit
                or unicodedata.normalize("NFC", field_value) != field_value
                or any(unicodedata.category(char).startswith("C") for char in field_value)
            ):
                raise OperationError("invalid_extraction_decision")
            return field_value

        def domain_items(key: str, maximum: int = 3) -> list[object]:
            items = decoded.get(key)
            if not isinstance(items, list):
                raise OperationError("invalid_extraction_decision")
            checked_items = cast(list[object], items)
            if len(checked_items) > maximum:
                raise OperationError("invalid_extraction_decision")
            return checked_items

        slang: list[SlangValue] = []
        for item in domain_items("slang"):
            if not isinstance(item, dict):
                raise OperationError("invalid_extraction_decision")
            raw = cast(dict[str, object], item)
            if set(raw) != {"term", "meaning", "aliases"}:
                raise OperationError("invalid_extraction_decision")
            aliases = raw["aliases"]
            if not isinstance(aliases, list):
                raise OperationError("invalid_extraction_decision")
            alias_items = cast(list[object], aliases)
            if len(alias_items) > 16:
                raise OperationError("invalid_extraction_decision")
            slang.append(
                SlangValue(
                    term=text_field(raw, "term", 128),
                    meaning=text_field(raw, "meaning", 512),
                    aliases=tuple(text_field({"value": alias}, "value", 128) for alias in alias_items),
                )
            )

        styles: list[StyleValue] = []
        for item in domain_items("style"):
            if not isinstance(item, dict):
                raise OperationError("invalid_extraction_decision")
            raw = cast(dict[str, object], item)
            if set(raw) != {"situation", "style", "output_policy", "risk_tags"}:
                raise OperationError("invalid_extraction_decision")
            output_policy = raw["output_policy"]
            risk_tags = raw["risk_tags"]
            if output_policy not in {"allow_use", "transform", "observe_only"} or not isinstance(
                risk_tags, list
            ):
                raise OperationError("invalid_extraction_decision")
            risk_items = cast(list[object], risk_tags)
            if len(risk_items) > 16:
                raise OperationError("invalid_extraction_decision")
            styles.append(
                StyleValue(
                    situation=text_field(raw, "situation", 256),
                    style=text_field(raw, "style", 512),
                    output_policy=cast(Literal["allow_use", "transform", "observe_only"], output_policy),
                    risk_tags=tuple(text_field({"value": tag}, "value", 64) for tag in risk_items),
                )
            )

        episodes: list[EpisodeValue] = []
        for item in domain_items("episodes"):
            if not isinstance(item, dict):
                raise OperationError("invalid_extraction_decision")
            raw = cast(dict[str, object], item)
            if set(raw) != {"situation", "observed_context", "action_taken", "outcome_signal", "reflection"}:
                raise OperationError("invalid_extraction_decision")
            episodes.append(
                EpisodeValue(
                    situation=text_field(raw, "situation", 256),
                    observed_context=text_field(raw, "observed_context", 256),
                    action_taken=text_field(raw, "action_taken", 256),
                    outcome_signal=text_field(raw, "outcome_signal", 256),
                    reflection=text_field(raw, "reflection", 512),
                )
            )
        return _SealedDecision(
            source_id=str(decoded["source_id"]),
            source_revision=cast(int, decoded["source_revision"]),
            suggestions=tuple(result),
            slang=tuple(slang),
            styles=tuple(styles),
            episodes=tuple(episodes),
            legacy=False,
        )

    @staticmethod
    def _parse_date(value: object) -> date | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise OperationError("invalid_extraction_decision")
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise OperationError("invalid_extraction_decision") from None
        if parsed.isoformat() != value:
            raise OperationError("invalid_extraction_decision")
        return parsed

    def _date_timestamp(self, value: date | None) -> float | None:
        if value is None:
            return None
        local_midnight = datetime.combine(value, time.min, tzinfo=self.timezone)
        timestamp = local_midnight.timestamp()
        round_trip = datetime.fromtimestamp(timestamp, self.timezone)
        if round_trip.date() != value or round_trip.timetz().replace(tzinfo=None) != time.min:
            raise OperationError("invalid_memory_timestamp")
        return timestamp

    async def _propose(self, source: ArchiveSourceRecord, suggestion: _SealedSuggestion) -> MemoryCandidate:
        target_fact_id = suggestion.target_fact_id
        current_correction = (
            suggestion.action == "supersede"
            and suggestion.reason == "explicit_correction"
            and suggestion.immediate_correction_eligible
        )
        return await self.memory.propose(
            actor=self.actor,
            scope=source.scope,
            source_id=source.source_id,
            subject_id=source.speaker_id,
            predicate=suggestion.predicate,
            value=suggestion.value,
            action=suggestion.action,
            target_fact_id=target_fact_id,
            reliable_update=current_correction,
            current_self_correction=current_correction,
            suggestion_reason=suggestion.reason,
            valid_from=self._date_timestamp(suggestion.valid_from),
            valid_to=self._date_timestamp(suggestion.valid_to),
        )

    @staticmethod
    def _public_error(code: str) -> str:
        return code if code in _PUBLIC_ERRORS else "operation_failed"


__all__ = ["MemoryExtractionReport", "MemoryExtractionRunner"]
