"""Finite explicit offline reply comparisons and plans for the existing style owner.

This module never collects conversation bodies, calls a model, mutates profiles,
or approves deployment. Source and judgment declarations stay visible as such.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from .domain_learning import StyleProfile
from .types import Scope, StrictModel

Split = Literal["development", "holdout"]
ReviewSource = Literal["human", "model", "fixture"]
BlindLabel = Literal["A", "B"]
Verdict = Literal["win", "loss", "tie", "missing"]
CostMetric = Literal["model_calls", "input_tokens", "output_tokens", "latency_ms"]


def _input_material_sha(value: str) -> str:
    """Ignore NFC/whitespace presentation changes; replies never define input independence."""
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    return hashlib.sha256(normalized.encode()).hexdigest()


class ReplyEvaluationError(ValueError):
    """Fixed error codes do not reveal caller-supplied dialogue or paths."""


class ReplyCost(StrictModel):
    model_calls: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class FixedReply(StrictModel):
    text: str = Field(min_length=1, max_length=16000)
    cost: ReplyCost = ReplyCost()


class ReplyComparisonCase(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    input: str = Field(min_length=1, max_length=8192)
    baseline: FixedReply
    candidate: FixedReply


class StyleItemPin(StrictModel):
    object_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    source_id: str = Field(min_length=1, max_length=128)
    source_revision: int = Field(ge=1)
    subject_id: str = Field(min_length=1, max_length=64)


class StyleProfilePin(StrictModel):
    profile_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    revision: int = Field(ge=1)
    status: Literal["draft", "enabled", "disabled"]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[StyleItemPin, ...] = Field(min_length=1, max_length=8)


class StyleReleaseTarget(StrictModel):
    actor: str = Field(min_length=1, max_length=64)
    scope: Scope
    expected_revision: int = Field(ge=0)
    baseline_profile: StyleProfilePin
    candidate_profile: StyleProfilePin

    @model_validator(mode="after")
    def existing_profile_lifecycle(self) -> Self:
        if (self.baseline_profile.status != "enabled" or self.candidate_profile.status != "draft"
                or self.baseline_profile.profile_id == self.candidate_profile.profile_id):
            raise ValueError("invalid_native_profile_pair")
        return self


def style_profile_pin(profile: StyleProfile) -> StyleProfilePin:
    """Record a snapshot obtained from the native owner, without granting its use."""
    try:
        return StyleProfilePin.model_validate_json(json.dumps({
            "profile_id": profile.profile_id, "version": profile.version, "revision": profile.revision,
            "status": profile.status, "content_sha256": hashlib.sha256(profile.content.encode()).hexdigest(),
            "items": [{"object_id": item.object_id, "revision": item.revision, "source_id": item.source_id,
                "source_revision": item.source_revision, "subject_id": item.subject_id}
                for item in profile.items],
        }))
    except ValidationError:
        raise ReplyEvaluationError("unusable_native_profile") from None


class ReplyEvaluationFixture(StrictModel):
    schema_version: Literal[1]
    input_kind: Literal["synthetic", "user_supplied"]
    purpose: Literal["offline_reply_evaluation"]
    source_declaration: str = Field(min_length=1, max_length=500)
    development_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    holdout_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    cases: tuple[ReplyComparisonCase, ...] = Field(min_length=2, max_length=64)
    cost_limits: ReplyCost
    target: StyleReleaseTarget

    @model_validator(mode="after")
    def frozen_disjoint_cases(self) -> Self:
        ids = {case.id for case in self.cases}
        development, holdout = set(self.development_ids), set(self.holdout_ids)
        if (len(ids) != len(self.cases) or len(development) != len(self.development_ids)
                or len(holdout) != len(self.holdout_ids) or development & holdout
                or development | holdout != ids):
            raise ValueError("invalid_disjoint_split")
        materials = {case.id: _input_material_sha(case.input) for case in self.cases}
        if {materials[case_id] for case_id in development} & {materials[case_id] for case_id in holdout}:
            raise ValueError("overlapping_input_material")
        return self


class BlindedReplyCase(StrictModel):
    review_id: str
    split: Split
    input: str
    A: str
    B: str


class BlindedReplyBundle(StrictModel):
    schema_version: Literal[1] = 1
    bundle_id: str
    cases: tuple[BlindedReplyCase, ...]


class LockedReplyCase(StrictModel):
    review_id: str
    case_id: str
    split: Split
    baseline_label: BlindLabel
    input_sha256: str
    input_material_sha256: str
    baseline_sha256: str
    candidate_sha256: str


class ReplyReviewManifest(StrictModel):
    schema_version: Literal[1] = 1
    input_material_standard: Literal["nfc_collapsed_unicode_whitespace"] = "nfc_collapsed_unicode_whitespace"
    input_artifact_sha256: str
    fixture_sha256: str
    review_bundle_sha256: str
    fixture: ReplyEvaluationFixture
    cases: tuple[LockedReplyCase, ...]


@dataclass(frozen=True, slots=True)
class PreparedReplyReview:
    reviewer_bundle: BlindedReplyBundle
    private_manifest: ReplyReviewManifest


class ReplyJudgment(StrictModel):
    review_id: str
    winner: Literal["A", "B", "tie"] | None = None
    rater_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason_summary: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def named_declared_judge(self) -> Self:
        if self.winner is not None and self.rater_id is None:
            raise ValueError("judgment_requires_rater")
        return self


class ReplyJudgmentFile(StrictModel):
    schema_version: Literal[1] = 1
    source: ReviewSource
    review_bundle_sha256: str
    judgments: tuple[ReplyJudgment, ...] = Field(max_length=64)


class CaseJudgment(StrictModel):
    outcome: Verdict
    rater_id: str | None = None
    reason_summary: str = ""


class ReplyCostDelta(StrictModel):
    model_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float | None


class MissingReplyCost(StrictModel):
    case_id: str
    metric: CostMetric


class ReplyCostViolation(MissingReplyCost):
    value: int | float
    limit: int | float


class ReplyCostEvidence(StrictModel):
    basis: Literal["per_case_candidate_limits"] = "per_case_candidate_limits"
    status: Literal["missing", "violated", "within_limits"]
    missing_limits: tuple[CostMetric, ...]
    missing: tuple[MissingReplyCost, ...]
    violations: tuple[ReplyCostViolation, ...]


class ReplyCaseEvaluation(StrictModel):
    case_id: str
    review_id: str
    split: Split
    input_sha256: str
    input_material_sha256: str
    baseline_sha256: str
    candidate_sha256: str
    judgments: dict[ReviewSource, CaseJudgment]
    baseline_cost: ReplyCost
    candidate_cost: ReplyCost
    cost_delta: ReplyCostDelta


class ReplyScore(StrictModel):
    wins: int
    losses: int
    ties: int
    missing: int


class NativeStyleChange(StrictModel):
    actor: str
    scope: Scope
    expected_revision: int
    action: Literal["enable"] = "enable"
    profile_id: str


class NativeStyleReleasePlan(StrictModel):
    state: Literal["review_only"] = "review_only"
    owner: Literal["DomainLearningService.change_style_profile"] = (
        "DomainLearningService.change_style_profile"
    )
    qualification: Literal["provided_snapshot_requires_current_owner_checks"] = (
        "provided_snapshot_requires_current_owner_checks"
    )
    candidate_profile: StyleProfilePin
    rollback_profile: StyleProfilePin
    release: NativeStyleChange
    rollback: NativeStyleChange


class ReplyEvaluationReport(StrictModel):
    schema_version: Literal[1] = 1
    input_material_standard: Literal["nfc_collapsed_unicode_whitespace"] = "nfc_collapsed_unicode_whitespace"
    status: Literal["evaluated"] = "evaluated"
    input_kind: Literal["synthetic", "user_supplied"]
    source_declaration: str
    fixture_sha256: str
    review_bundle_sha256: str
    quality_claim_scope: Literal["explicit_fixture_comparison"] = "explicit_fixture_comparison"
    cost_basis: Literal["fixture_declared_unknown_preserved"] = "fixture_declared_unknown_preserved"
    pipeline_model_calls: Literal[0] = 0
    cases: tuple[ReplyCaseEvaluation, ...]
    by_source: dict[ReviewSource, dict[Split, ReplyScore]]
    human_evidence_status: Literal["missing", "partial", "declared_complete"]
    degraded_holdout_ids: tuple[str, ...]
    cost_limits: ReplyCost
    cost_evidence: ReplyCostEvidence
    release_eligibility: bool
    release_blockers: tuple[str, ...]
    native_release_plan: NativeStyleReleasePlan


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _model_sha(value: StrictModel) -> str:
    return _sha(json.dumps(value.model_dump(mode="json"), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode())


def prepare_reply_review(payload: bytes, *, blinding_key: bytes | None = None) -> PreparedReplyReview:
    """Lock a preselected finite split and hide variant/target/cost identity from reviewers."""
    try:
        fixture = ReplyEvaluationFixture.model_validate_json(payload)
    except ValidationError:
        raise ReplyEvaluationError("invalid_fixture") from None
    key = secrets.token_bytes(32) if blinding_key is None else blinding_key
    bundle_id = uuid.uuid4().hex
    public: list[BlindedReplyCase] = []
    locked: list[LockedReplyCase] = []
    for case in fixture.cases:
        identity = (bundle_id + "\0" + case.id).encode()
        hidden = hmac.digest(key, identity, "sha256")
        review_id = hidden.hex()
        label: BlindLabel = "A" if hidden[0] & 1 else "B"
        split: Split = "development" if case.id in fixture.development_ids else "holdout"
        public.append(BlindedReplyCase(review_id=review_id, split=split, input=case.input,
            A=case.baseline.text if label == "A" else case.candidate.text,
            B=case.candidate.text if label == "A" else case.baseline.text))
        locked.append(LockedReplyCase(review_id=review_id, case_id=case.id, split=split,
            baseline_label=label, input_sha256=_sha(case.input.encode()),
            input_material_sha256=_input_material_sha(case.input),
            baseline_sha256=_sha(case.baseline.text.encode()),
            candidate_sha256=_sha(case.candidate.text.encode())))
    reviewer = BlindedReplyBundle(bundle_id=bundle_id, cases=tuple(public))
    manifest = ReplyReviewManifest(input_artifact_sha256=_sha(payload), fixture_sha256=_model_sha(fixture),
        review_bundle_sha256=_model_sha(reviewer), fixture=fixture, cases=tuple(locked))
    return PreparedReplyReview(reviewer, manifest)


def judgment_template(prepared: PreparedReplyReview, *, source: ReviewSource = "human") -> ReplyJudgmentFile:
    return ReplyJudgmentFile(source=source,
        review_bundle_sha256=prepared.private_manifest.review_bundle_sha256,
        judgments=tuple(ReplyJudgment(review_id=case.review_id) for case in prepared.reviewer_bundle.cases))


def _verify_prepared(prepared: PreparedReplyReview) -> None:
    manifest, public = prepared.private_manifest, prepared.reviewer_bundle
    if _model_sha(manifest.fixture) != manifest.fixture_sha256:
        raise ReplyEvaluationError("fixture_changed")
    if _model_sha(public) != manifest.review_bundle_sha256:
        raise ReplyEvaluationError("review_bundle_changed")
    if (tuple(case.id for case in manifest.fixture.cases) != tuple(case.case_id for case in manifest.cases)
            or len(public.cases) != len(manifest.cases)
            or len({case.review_id for case in public.cases}) != len(public.cases)):
        raise ReplyEvaluationError("case_mapping_changed")
    for original, locked, shown in zip(manifest.fixture.cases, manifest.cases, public.cases, strict=True):
        split = "development" if original.id in manifest.fixture.development_ids else "holdout"
        baseline = shown.A if locked.baseline_label == "A" else shown.B
        candidate = shown.B if locked.baseline_label == "A" else shown.A
        if (shown.review_id != locked.review_id or shown.split != split or locked.split != split
                or shown.input != original.input or baseline != original.baseline.text
                or candidate != original.candidate.text
                or locked.input_sha256 != _sha(original.input.encode())
                or locked.input_material_sha256 != _input_material_sha(original.input)
                or locked.baseline_sha256 != _sha(baseline.encode())
                or locked.candidate_sha256 != _sha(candidate.encode())):
            raise ReplyEvaluationError("case_mapping_changed")


def _load_judgments(payload: bytes | None, source: ReviewSource,
                    prepared: PreparedReplyReview) -> dict[str, ReplyJudgment]:
    if payload is None:
        return {}
    try:
        file = ReplyJudgmentFile.model_validate_json(payload)
    except ValidationError:
        raise ReplyEvaluationError("invalid_judgments") from None
    if file.source != source:
        raise ReplyEvaluationError("judgment_source_mismatch")
    if file.review_bundle_sha256 != prepared.private_manifest.review_bundle_sha256:
        raise ReplyEvaluationError("judgment_bundle_mismatch")
    ids = {case.review_id for case in prepared.reviewer_bundle.cases}
    rows = {row.review_id: row for row in file.judgments}
    if len(rows) != len(file.judgments) or not rows.keys() <= ids:
        raise ReplyEvaluationError("invalid_judgment_case")
    return rows


def _cost_delta(old: ReplyCost, new: ReplyCost) -> ReplyCostDelta:
    return ReplyCostDelta(
        model_calls=None if old.model_calls is None or new.model_calls is None
        else new.model_calls - old.model_calls,
        input_tokens=None if old.input_tokens is None or new.input_tokens is None
        else new.input_tokens - old.input_tokens,
        output_tokens=None if old.output_tokens is None or new.output_tokens is None
        else new.output_tokens - old.output_tokens,
        latency_ms=None if old.latency_ms is None or new.latency_ms is None
        else new.latency_ms - old.latency_ms)


def _cost_evidence(fixture: ReplyEvaluationFixture) -> ReplyCostEvidence:
    metrics: tuple[CostMetric, ...] = ("model_calls", "input_tokens", "output_tokens", "latency_ms")
    missing_limits: tuple[CostMetric, ...] = (
        ("model_calls",) if fixture.cost_limits.model_calls is None else ()
    )
    missing: list[MissingReplyCost] = []
    violations: list[ReplyCostViolation] = []
    for metric in metrics:
        limit = getattr(fixture.cost_limits, metric)
        if limit is None:
            continue
        for case in fixture.cases:
            value = getattr(case.candidate.cost, metric)
            if value is None:
                missing.append(MissingReplyCost(case_id=case.id, metric=metric))
            elif value > limit:
                violations.append(ReplyCostViolation(
                    case_id=case.id, metric=metric, value=value, limit=limit))
    return ReplyCostEvidence(
        status="missing" if missing_limits or missing else "violated" if violations else "within_limits",
        missing_limits=missing_limits, missing=tuple(missing), violations=tuple(violations),
    )


def evaluate_reply_review(prepared: PreparedReplyReview, *, human_reviews: bytes | None = None,
                          model_reviews: bytes | None = None,
                          fixture_reviews: bytes | None = None) -> ReplyEvaluationReport:
    """Evaluate imported declarations separately; human absence can never be model-filled."""
    _verify_prepared(prepared)
    sources: tuple[ReviewSource, ...] = ("human", "model", "fixture")
    imported = {source: _load_judgments(payload, source, prepared) for source, payload in zip(
        sources, (human_reviews, model_reviews, fixture_reviews), strict=True)}
    cases: list[ReplyCaseEvaluation] = []
    for original, locked in zip(prepared.private_manifest.fixture.cases,
                                prepared.private_manifest.cases, strict=True):
        judgments: dict[ReviewSource, CaseJudgment] = {}
        for source in sources:
            row = imported[source].get(locked.review_id)
            if row is None or row.winner is None:
                judgments[source] = CaseJudgment(outcome="missing")
            else:
                outcome: Verdict = ("tie" if row.winner == "tie" else "loss"
                                    if row.winner == locked.baseline_label else "win")
                judgments[source] = CaseJudgment(outcome=outcome, rater_id=row.rater_id,
                                                 reason_summary=row.reason_summary)
        cases.append(ReplyCaseEvaluation(case_id=locked.case_id, review_id=locked.review_id,
            split=locked.split, input_sha256=locked.input_sha256, baseline_sha256=locked.baseline_sha256,
            input_material_sha256=locked.input_material_sha256,
            candidate_sha256=locked.candidate_sha256, judgments=judgments,
            baseline_cost=original.baseline.cost, candidate_cost=original.candidate.cost,
            cost_delta=_cost_delta(original.baseline.cost, original.candidate.cost)))

    def score(source: ReviewSource, split: Split) -> ReplyScore:
        verdicts = [case.judgments[source].outcome for case in cases if case.split == split]
        return ReplyScore(wins=verdicts.count("win"), losses=verdicts.count("loss"),
                          ties=verdicts.count("tie"), missing=verdicts.count("missing"))

    by_source: dict[ReviewSource, dict[Split, ReplyScore]] = {
        source: {"development": score(source, "development"), "holdout": score(source, "holdout")}
        for source in sources
    }
    human = score("human", "holdout")
    cost_evidence = _cost_evidence(prepared.private_manifest.fixture)
    blockers = tuple(code for code, applies in (
        ("missing_human_holdout_review", human.missing > 0), ("human_holdout_regression", human.losses > 0),
        ("no_human_holdout_gain", human.wins == 0),
        ("missing_model_call_limit", bool(cost_evidence.missing_limits)),
        ("missing_candidate_cost", bool(cost_evidence.missing)),
        ("candidate_cost_limit_exceeded", bool(cost_evidence.violations)),
    ) if applies)
    target = prepared.private_manifest.fixture.target
    plan = NativeStyleReleasePlan(candidate_profile=target.candidate_profile,
        rollback_profile=target.baseline_profile,
        release=NativeStyleChange(actor=target.actor, scope=target.scope,
            expected_revision=target.expected_revision, profile_id=target.candidate_profile.profile_id),
        rollback=NativeStyleChange(actor=target.actor, scope=target.scope,
            expected_revision=target.expected_revision + 1, profile_id=target.baseline_profile.profile_id))
    return ReplyEvaluationReport(input_kind=prepared.private_manifest.fixture.input_kind,
        source_declaration=prepared.private_manifest.fixture.source_declaration,
        fixture_sha256=prepared.private_manifest.fixture_sha256,
        review_bundle_sha256=prepared.private_manifest.review_bundle_sha256, cases=tuple(cases),
        by_source=by_source, human_evidence_status="missing"
        if human.missing == len(prepared.private_manifest.fixture.holdout_ids) else
        "partial" if human.missing else "declared_complete",
        degraded_holdout_ids=tuple(case.case_id for case in cases
            if case.split == "holdout" and case.judgments["human"].outcome == "loss"),
        cost_limits=prepared.private_manifest.fixture.cost_limits, cost_evidence=cost_evidence,
        release_eligibility=not blockers, release_blockers=blockers, native_release_plan=plan)
