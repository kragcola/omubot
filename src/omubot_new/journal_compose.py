"""Bounded Journal selection and fiction composition; factual rendering is code-only."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from .actions import Actions
from .journal import JournalDraft, JournalOwner, journal_digest, journal_source_hash
from .model_budget import ModelBudget
from .store import StoreConnection, drain_on_cancel
from .types import ActionCall, Message, ModelPort, ModelRequest, OperationError, Scope

GENERIC_LABELS = frozenset(
    {
        "一位朋友",
        "一位伙伴",
        "一位同伴",
        "朋友甲",
        "朋友乙",
        "朋友丙",
        "伙伴甲",
        "伙伴乙",
        "伙伴丙",
        "同伴甲",
        "同伴乙",
        "同伴丙",
    }
)
TEMPLATES = {
    "social_public_event_solo_v1": (1, "今天和{0}一起参加了公开活动"),
    "social_public_event_duo_v1": (2, "{0}和{1}一起参加了公开活动"),
    "attendance_solo_v1": (1, "{0}到场了"),
    "attendance_duo_v1": (2, "{0}和{1}到场了"),
    "milestone_solo_v1": (1, "和{0}一起达成了一个里程碑"),
    "milestone_duo_v1": (2, "和{0}、{1}一起达成了一个里程碑"),
}


def render_public(template_id: str, labels: tuple[str, ...]) -> str:
    """No free text, private relationships, real nicknames or raw summaries."""
    if (
        type(template_id) is not str
        or template_id not in TEMPLATES
        or type(labels) is not tuple
        or len(labels) != TEMPLATES[template_id][0]
        or len(set(labels)) != len(labels)
        or any(type(label) is not str or label not in GENERIC_LABELS for label in labels)
    ):
        raise OperationError("journal_public_projection_invalid")
    return TEMPLATES[template_id][1].format(*labels)


@dataclass(frozen=True, slots=True)
class PublicStatement:
    template_id: str
    labels: tuple[str, ...]
    label_index: int


def public_statement(text: str) -> PublicStatement:
    """Literal source-author assertion and consent, never an Admin author-ID DTO.

    Exact grammar: 公开日志同意：<template>；代称：<comma-separated generic labels>；我的代称：<label>
    The same literal message both asserts the closed statement and permits public use.
    Each duo participant must independently issue the same statement for their own label.
    A final ；事实：<exact rendered statement> explicitly asserts the displayed claim.
    """
    if type(text) is not str or len(text) > 256:
        raise OperationError("journal_public_consent_invalid")
    parts = text.split("；")
    if len(parts) != 4 or not parts[0].startswith("公开日志同意：") or not parts[1].startswith("代称："):
        raise OperationError("journal_public_consent_invalid")
    template_id = parts[0].removeprefix("公开日志同意：")
    labels = tuple(parts[1].removeprefix("代称：").split("、"))
    statement = render_public(template_id, labels)
    if parts[3] != "事实：" + statement:
        raise OperationError("journal_public_consent_invalid")
    if not parts[2].startswith("我的代称：") or parts[2].removeprefix("我的代称：") not in labels:
        raise OperationError("journal_public_consent_invalid")
    return PublicStatement(template_id, labels, labels.index(parts[2].removeprefix("我的代称：")))


@dataclass(frozen=True, slots=True)
class JournalCandidate:
    source_event_id: str
    source_hash: str
    event_date: str
    salience: float
    summary: str
    content_kind: str = "fiction"


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    source_event_id: str
    reason: str
    score: float | None


def _tokens(value: str) -> frozenset[str]:
    text = value.casefold()
    # The old ranking uses Jaccard summary tokens. CJK bigrams retain meaningful
    # comparison without importing a tokenizer/model or copying private text.
    return frozenset(text[i : i + 2] for i in range(len(text) - 1) if not text[i : i + 2].isspace())


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a or b else 0.0


class JournalComposer:
    """Actual preview caller: current owner candidates -> reserve -> Actions -> draft.

    Bound to one model/owner, no tick daemon and no publisher. Root calls preview
    from the existing authenticated Journal application, then shows the stored draft.
    """

    def __init__(
        self,
        owner: JournalOwner,
        actions: Actions,
        model: ModelPort,
        *,
        provider: str,
        model_name: str,
        budget: ModelBudget,
        enabled: bool = False,
        threshold: float = 0.7,
    ) -> None:
        if actions.store is not owner.store or actions.policy is not owner.policy:
            raise OperationError("journal_store_mismatch")
        if type(threshold) not in {int, float} or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise OperationError("invalid_journal_threshold")
        self.owner, self.actions, self.model = owner, actions, model
        self.provider, self.model_name, self.enabled = provider, model_name, enabled
        self.budget = budget
        self.threshold = threshold

    async def preview(
        self, *, actor: str, scope: Scope, operation_id: str, day_narrative: str = ""
    ) -> tuple[JournalDraft | None, tuple[SelectionDecision, ...]]:
        if not self.enabled:
            raise OperationError("journal_disabled")
        actor = self.owner.validate_caller(actor, scope)
        if type(operation_id) is not str or not operation_id or len(operation_id) > 80:
            raise OperationError("invalid_journal_operation")
        if type(day_narrative) is not str or len(day_narrative) > 1200:
            raise OperationError("invalid_journal_narrative")
        today = datetime.fromtimestamp(self.owner.clock(), UTC).date().isoformat()

        def reserve(db: StoreConnection) -> tuple[JournalCandidate | None, tuple[SelectionDecision, ...]]:
            self.owner.authorize_transaction(db, actor, scope)
            story = self.owner.story
            if story is None:
                raise OperationError("journal_source_unavailable")
            if db.execute(
                "SELECT 1 FROM journal_compositions WHERE operation_id=?", (operation_id,)
            ).fetchone():
                raise OperationError("journal_composition_duplicate")
            rows = db.execute(
                "SELECT event_id FROM story_events WHERE bot_id=? AND group_id=? "
                "ORDER BY committed_at DESC,event_id LIMIT 64",
                (scope.bot_id, scope.group_id),
            ).fetchall()
            candidates: list[JournalCandidate] = []
            decisions: list[SelectionDecision] = []
            for row in rows:
                event = story.read_event_transaction(db, scope, str(row["event_id"]))
                if event is None or event.origin_kind == "social_experience":
                    decisions.append(
                        SelectionDecision(str(row["event_id"]), "reject_public_projection", None)
                    )
                    continue
                day = datetime.fromtimestamp(event.committed_at, UTC).date().isoformat()
                salience = (
                    0.95 if event.stage is not None or event.open_threads or event.resolve_threads else 0.35
                )
                # Only producer-owned fiction metadata, never factual raw narrative.
                summary = f"虚构剧情 {event.event_type} 阶段 {event.result_stage}"
                candidate = JournalCandidate(
                    event.event_id, journal_source_hash(event), day, salience, summary
                )
                if candidate.salience < self.threshold:
                    decisions.append(
                        SelectionDecision(candidate.source_event_id, "reject_below_threshold", None)
                    )
                    continue
                if db.execute(
                    "SELECT 1 FROM journal_drafts WHERE bot_id=? AND group_id=? AND source_event_id=? "
                    "UNION ALL SELECT 1 FROM journal_compositions WHERE bot_id=? AND group_id=? "
                    "AND source_event_id=? AND state!='failed'",
                    (
                        scope.bot_id,
                        scope.group_id,
                        event.event_id,
                        scope.bot_id,
                        scope.group_id,
                        event.event_id,
                    ),
                ).fetchone():
                    decisions.append(SelectionDecision(event.event_id, "reject_duplicate_dedupe", None))
                    continue
                candidates.append(candidate)
            recent = db.execute(
                "SELECT source_event_id FROM journal_drafts WHERE bot_id=? AND group_id=? "
                "ORDER BY created_at DESC LIMIT 32",
                (scope.bot_id, scope.group_id),
            ).fetchall()
            summaries: list[str] = []
            for row in recent:
                event = story.read_event_transaction(db, scope, str(row["source_event_id"]))
                if event is not None and event.origin_kind != "social_experience":
                    summaries.append(f"虚构剧情 {event.event_type} 阶段 {event.result_stage}")
            ranked: list[tuple[float, JournalCandidate]] = []
            for candidate in candidates:
                if (
                    self.owner.day_draft_count_transaction(db, scope, candidate.event_date)
                    >= self.owner.max_drafts_per_day
                ):
                    decisions.append(
                        SelectionDecision(candidate.source_event_id, "reject_day_draft_budget", None)
                    )
                    continue
                novelty = 1 - max((_similarity(candidate.summary, s) for s in summaries), default=0.0)
                relevance = _similarity(candidate.summary, day_narrative) if day_narrative else 0.5
                score = (
                    0.5 * candidate.salience
                    + 0.2 * (candidate.event_date == today)
                    + 0.2 * novelty
                    + 0.1 * relevance
                )
                ranked.append((score, candidate))
            ranked.sort(key=lambda pair: (-pair[0], -pair[1].salience, pair[1].source_event_id))
            for index, (score, candidate) in enumerate(ranked):
                decisions.append(
                    SelectionDecision(
                        candidate.source_event_id, "accept" if index == 0 else "reject_out_ranked", score
                    )
                )
            if not ranked:
                return None, tuple(decisions)
            chosen = ranked[0][1]
            db.execute(
                "DELETE FROM journal_compositions WHERE bot_id=? AND group_id=? "
                "AND source_event_id=? AND state='failed'",
                (scope.bot_id, scope.group_id, chosen.source_event_id),
            )
            db.execute(
                "INSERT INTO journal_compositions VALUES (?,?,?,?,?,?,'composing',?)",
                (
                    operation_id,
                    scope.bot_id,
                    scope.group_id,
                    chosen.source_event_id,
                    chosen.source_hash,
                    chosen.event_date,
                    self.owner.clock(),
                ),
            )
            return chosen, tuple(decisions)

        candidate, decisions = await self.owner.store.transaction(reserve)
        if candidate is None:
            return None, decisions
        request = ModelRequest(
            system="只写虚构故事日志，不把故事当成真实自我经历。",
            model=self.model_name,
            messages=[Message(role="user", content=candidate.summary)],
            tools=[],
            max_output_tokens=160,
        )

        def check(db: StoreConnection) -> None:
            self.owner.authorize_transaction(db, actor, scope)
            if (
                self.owner.source_hash_transaction(db, scope, candidate.source_event_id)
                != candidate.source_hash
            ):
                raise OperationError("journal_source_changed")

        async def invoke():
            async with self.budget.slot("reply"):
                await self.owner.store.transaction(check)
                return await self.model.request(request)

        try:
            reply = await self.actions.execute(
                ActionCall(
                    key="jc_" + journal_digest(operation_id),
                    request_id=operation_id,
                    subject=actor,
                    scope=scope,
                    action="model.invoke",
                    payload_hash=journal_digest(request.model_dump()),
                    provider=self.provider,
                    model=self.model_name,
                ),
                invoke,
                external=self.model.is_external,
                model_task="journal",
                preflight_transaction=check,
                timeout=30,
            )
            if reply.tool_call is not None:
                raise OperationError("journal_composition_tool_forbidden")
            draft = await self.owner.create(
                actor=actor,
                scope=scope,
                source_event_id=candidate.source_event_id,
                body=reply.text,
                operation_id=operation_id + ":draft",
                composition_operation_id=operation_id,
            )
        except BaseException:
            await drain_on_cancel(
                asyncio.create_task(
                    self.owner.store.transaction(
                        lambda db: db.execute(
                            "UPDATE journal_compositions SET state='failed' WHERE operation_id=?",
                            (operation_id,),
                        )
                    )
                )
            )
            raise
        return draft, decisions
