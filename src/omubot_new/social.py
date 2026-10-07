"""Content-free factual SocialExperience capture over N6 source evidence."""

from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Literal

from .domain_learning import EpisodeValue, episode_decay_is_current
from .policy import Policy
from .store import Store, StoreConnection, StoreRow
from .types import OperationError, Scope

_SOURCE_AUTHOR_ACTIONS = ("message.read", "memory.archive", "memory.learn")
_INVALID_CURRENT_CODES = frozenset(
    {
        "social_episode_not_found",
        "social_episode_not_current",
        "source_revoked",
        "source_kind_forbidden",
        "source_revision_conflict",
        "social_source_metadata_missing",
        "social_reply_not_found",
        "social_reply_not_succeeded",
        "social_receipt_missing",
        "social_receipt_conflict",
        "social_delivery_incomplete",
    }
)


_TEMPORARILY_UNAVAILABLE_CODES = frozenset({"social_episode_prompt_unavailable", "social_episode_expired"})
_SOCIAL_CJK_RUN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002fa1f\U00030000-\U000323af]+"
)
_SOCIAL_WORD = re.compile(r"[a-z0-9]{4,}", re.IGNORECASE)


def _social_topic_tokens(text: str) -> frozenset[str]:
    bounded = text[:4096].casefold()
    tokens = {"word:" + match.group(0) for match in _SOCIAL_WORD.finditer(bounded)}
    for run in _SOCIAL_CJK_RUN.findall(bounded):
        tokens.update("cjk:" + run[index:index + 4] for index in range(len(run) - 3))
    return frozenset(tokens)


def social_topic_matches(text: str, value: tuple[str, ...]) -> bool:
    if not value:
        return False
    shared = _social_topic_tokens(text) & _social_topic_tokens(" ".join(value))
    return any(token.startswith("cjk:") for token in shared) or sum(
        token.startswith("word:") for token in shared) >= 2


def _episode_query_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    normalized = re.sub(r"https?://\S+", "", normalized)
    normalized = re.sub(r"\[[^\]]+\]\([^)]+\)", "", normalized)
    normalized = re.sub(r"^[#>\-\s*`_~]+", "", normalized)
    return re.sub(r"[\s`*_~#>\[\](){}《》<>:：,，。.!！?？;；\"'“”‘’|/\\]+", "", normalized)


def _episode_relevance(query: str, value: EpisodeValue) -> float:
    left = _episode_query_key(query)
    right = _episode_query_key("\n".join((
        value.situation, value.observed_context, value.action_taken,
        value.outcome_signal, value.reflection))[:1200])
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.82
    size = 2 if len(left) > 2 and len(right) > 2 else 1
    lhs = {left[index:index + size] for index in range(len(left) - size + 1)}
    rhs = {right[index:index + size] for index in range(len(right) - size + 1)}
    return len(lhs & rhs) / len(lhs | rhs)


def _identity(value: object, code: str, *, limit: int = 128) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > limit
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise OperationError(code)
    return value


def _scope(value: object, policy: Policy) -> Scope:
    if type(value) is not Scope or value.bot_id != policy.bot_id:
        raise OperationError("denied")
    return value


def _experience_id(scope: Scope, episode_id: str) -> str:
    material = "\0".join((scope.bot_id, scope.group_id, episode_id)).encode("utf-8")
    return f"social_{hashlib.sha256(material).hexdigest()}"


def _experience(row: StoreRow) -> SocialExperience:
    status = row["status"]
    if status not in {"active", "invalidated"}:
        raise OperationError("invalid_social_experience")
    try:
        observed_at = float(row["observed_at"])
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_social_experience") from exc
    if not math.isfinite(observed_at):
        raise OperationError("invalid_social_experience")
    return SocialExperience(
        experience_id=str(row["experience_id"]),
        episode_id=str(row["episode_id"]),
        source_id=str(row["source_id"]),
        source_revision=int(row["source_revision"]),
        bot_id=str(row["bot_id"]),
        group_id=str(row["group_id"]),
        user_id=str(row["user_id"]),
        platform_message_id=str(row["platform_message_id"]),
        observed_at=observed_at,
        reply_action_id=str(row["reply_action_id"]),
        receipt=str(row["receipt"]),
        status=status,
    )


def _episode_source(
    db: StoreConnection, *, scope: Scope, episode_id: str, now: float
) -> StoreRow:
    row = db.execute(
        "SELECT e.object_id AS episode_id,e.bot_id,e.group_id,e.source_id,e.state AS episode_state,"
        "e.object_revision,e.decay_at,e.situation,e.observed_context,e.action_taken,e.outcome_signal,e.reflection,"
        "c.candidate_id,c.source_id AS candidate_source_id,c.review_status,c.application_status,"
        "c.episode_state AS candidate_episode_state,c.applied_object_id,"
        "r.source_id AS result_source_id,r.source_revision,"
        "s.origin_event_id,s.platform_message_id,s.observed_at,s.speaker_id,s.source_revision "
        "AS archive_source_revision,s.status AS source_status,s.source_kind,s.speaker_kind "
        "FROM domain_learning_episodes AS e "
        "JOIN domain_learning_candidates AS c ON c.candidate_id=e.candidate_id "
        "AND c.bot_id=e.bot_id AND c.group_id=e.group_id "
        "JOIN domain_learning_results AS r ON r.result_id=c.result_id "
        "AND r.bot_id=c.bot_id AND r.group_id=c.group_id "
        "JOIN archive_sources AS s ON s.source_id=e.source_id "
        "AND s.bot_id=e.bot_id AND s.group_id=e.group_id "
        "WHERE e.object_id=? AND e.bot_id=? AND e.group_id=? AND c.domain='episode' "
        "AND r.domain='episode'",
        (episode_id, scope.bot_id, scope.group_id),
    ).fetchone()
    if row is None:
        raise OperationError("social_episode_not_found")
    if (
        row["episode_state"] != row["candidate_episode_state"]
        or row["review_status"] != "approved"
        or row["application_status"] not in {"applied", "disabled"}
        or row["applied_object_id"] != episode_id
        or row["candidate_source_id"] != row["source_id"]
        or row["result_source_id"] != row["source_id"]
    ):
        raise OperationError("social_episode_not_current")

    tombstone = db.execute(
        "SELECT bot_id,group_id FROM archive_source_tombstones WHERE source_id=?",
        (row["source_id"],),
    ).fetchone()
    if tombstone is not None and (
        tombstone["bot_id"] != scope.bot_id or tombstone["group_id"] != scope.group_id
    ):
        raise OperationError("denied")
    if tombstone is not None or row["source_status"] != "active":
        raise OperationError("source_revoked")
    if row["source_kind"] != "human_message" or row["speaker_kind"] != "human":
        raise OperationError("source_kind_forbidden")
    if int(row["source_revision"]) != int(row["archive_source_revision"]):
        raise OperationError("source_revision_conflict")
    platform_message_id = row["platform_message_id"]
    speaker_id = row["speaker_id"]
    try:
        observed_at = float(row["observed_at"])
    except (TypeError, ValueError) as exc:
        raise OperationError("social_source_metadata_missing") from exc
    if (
        not isinstance(platform_message_id, str)
        or not platform_message_id.strip()
        or not isinstance(speaker_id, str)
        or not speaker_id.strip()
        or not math.isfinite(observed_at)
    ):
        raise OperationError("social_source_metadata_missing")
    if row["episode_state"] != "enabled_for_prompt" or row["application_status"] != "applied":
        raise OperationError("social_episode_prompt_unavailable")
    if not episode_decay_is_current(str(row["decay_at"]), now):
        raise OperationError("social_episode_expired")
    return row


def _episode_value(evidence: StoreRow) -> EpisodeValue:
    return EpisodeValue(
        situation=str(evidence["situation"]),
        observed_context=str(evidence["observed_context"]),
        action_taken=str(evidence["action_taken"]),
        outcome_signal=str(evidence["outcome_signal"]),
        reflection=str(evidence["reflection"]),
    )


def _authorize_source_author(
    policy: Policy, db: StoreConnection, *, source_author: str, scope: Scope
) -> None:
    for action in _SOURCE_AUTHOR_ACTIONS:
        policy.check_transaction(db, source_author, scope, action, "", "", False, False)


def _reply_receipt(
    db: StoreConnection,
    *,
    scope: Scope,
    event_id: str,
    author_id: str,
    reply_action_id: str,
) -> tuple[str, str]:
    row = db.execute(
        "SELECT a.id,a.request_id,a.action,a.state,a.subject,a.bot_id,a.group_id,a.receipt "
        "FROM actions AS a "
        "JOIN requests AS owner ON owner.id=a.request_id "
        "JOIN request_sources AS rs ON rs.owner_request_id=a.request_id "
        "JOIN requests AS source_event ON source_event.id=rs.source_request_id "
        "WHERE a.id=? AND rs.source_request_id=? AND a.bot_id=? AND a.group_id=?",
        (reply_action_id, event_id, scope.bot_id, scope.group_id),
    ).fetchone()
    if row is None:
        raise OperationError("social_reply_not_found")
    if (
        row["action"] != "message.reply"
        or row["state"] != "succeeded"
        or row["subject"] != author_id
    ):
        raise OperationError("social_reply_not_succeeded")
    receipt = row["receipt"]
    if not isinstance(receipt, str) or not receipt.strip():
        raise OperationError("social_receipt_missing")
    delivery_row = db.execute(
        "SELECT total FROM deliveries WHERE request_id=?", (row["request_id"],)
    ).fetchone()
    if delivery_row is None:
        raise OperationError("social_delivery_incomplete")
    aggregate = Store.delivery_transaction(
        db, str(row["request_id"]), int(delivery_row["total"])
    )
    if aggregate["state"] != "complete":
        raise OperationError("social_delivery_incomplete")
    return str(row["request_id"]), receipt


def _first_complete_reply(
    db: StoreConnection, *, scope: Scope, event_id: str, author_id: str
) -> tuple[str, str]:
    rows = db.execute(
        "SELECT a.id FROM actions AS a "
        "JOIN request_sources AS rs ON rs.owner_request_id=a.request_id "
        "WHERE rs.source_request_id=? AND a.bot_id=? AND a.group_id=? "
        "AND a.action='message.reply' ORDER BY a.created_at,a.id",
        (event_id, scope.bot_id, scope.group_id),
    ).fetchall()
    if not rows:
        raise OperationError("social_reply_not_found")
    errors: list[str] = []
    for row in rows:
        action_id = _identity(row["id"], "invalid_social_reply_action")
        try:
            _, receipt = _reply_receipt(
                db,
                scope=scope,
                event_id=event_id,
                author_id=author_id,
                reply_action_id=action_id,
            )
        except OperationError as exc:
            if exc.code not in {
                "social_reply_not_found",
                "social_reply_not_succeeded",
                "social_receipt_missing",
                "social_delivery_incomplete",
            }:
                raise
            errors.append(exc.code)
            continue
        return action_id, receipt
    for code in (
        "social_delivery_incomplete",
        "social_receipt_missing",
        "social_reply_not_succeeded",
    ):
        if code in errors:
            raise OperationError(code)
    raise OperationError("social_reply_not_found")


def _same_capture(evidence: StoreRow, existing: StoreRow, *, reply_action_id: str, receipt: str) -> bool:
    return (
        str(existing["episode_id"]) == str(evidence["episode_id"])
        and str(existing["source_id"]) == str(evidence["source_id"])
        and int(existing["source_revision"]) == int(evidence["source_revision"])
        and str(existing["bot_id"]) == str(evidence["bot_id"])
        and str(existing["group_id"]) == str(evidence["group_id"])
        and str(existing["user_id"]) == str(evidence["speaker_id"])
        and str(existing["platform_message_id"]) == str(evidence["platform_message_id"])
        and float(existing["observed_at"]) == float(evidence["observed_at"])
        and str(existing["reply_action_id"]) == reply_action_id
        and str(existing["receipt"]) == receipt
    )


@dataclass(frozen=True, slots=True)
class SocialExperience:
    experience_id: str
    episode_id: str
    source_id: str
    source_revision: int
    bot_id: str
    group_id: str
    user_id: str
    platform_message_id: str
    observed_at: float
    reply_action_id: str
    receipt: str
    status: Literal["active", "invalidated"]


@dataclass(frozen=True, slots=True)
class SocialChatItem:
    """Frozen current N6 value plus private identities needed for preflight."""

    experience_id: str
    episode_id: str
    episode_revision: int
    user_id: str
    value: EpisodeValue


@dataclass(frozen=True, slots=True)
class SocialChatProjection:
    """Bounded current group facts; only ``prompt_values`` belongs in a prompt."""

    scope: Scope
    items: tuple[SocialChatItem, ...]
    reader_id: str = ""
    speaker_id: str | None = None
    selected_at: float | None = None

    @property
    def prompt_values(self) -> tuple[EpisodeValue, ...]:
        return tuple(item.value for item in self.items)


SocialCaptureStatus = tuple[Literal["missing", "active", "invalidated"], str | None]
_MAX_SOCIAL_CHAT_ITEMS = 4
_MAX_SOCIAL_CANDIDATES = 256


def _current_experience(
    policy: Policy, db: StoreConnection, *, scope: Scope, row: StoreRow, now: float
) -> tuple[SocialExperience, StoreRow]:
    evidence = _episode_source(db, scope=scope, episode_id=str(row["episode_id"]), now=now)
    source_author = _identity(evidence["speaker_id"], "source_author_missing", limit=64)
    if not _same_capture(
        evidence,
        row,
        reply_action_id=str(row["reply_action_id"]),
        receipt=str(row["receipt"]),
    ):
        raise OperationError("social_episode_not_current")
    _authorize_source_author(policy, db, source_author=source_author, scope=scope)
    _, receipt = _reply_receipt(
        db,
        scope=scope,
        event_id=str(evidence["origin_event_id"]),
        author_id=source_author,
        reply_action_id=str(row["reply_action_id"]),
    )
    if receipt != str(row["receipt"]):
        raise OperationError("social_receipt_conflict")
    return _experience(row), evidence


class SocialExperienceService:
    def __init__(
        self,
        store: Store,
        policy: Policy,
        *,
        enabled: bool = False,
        social_evidence_enabled: bool = False,
        allowed_groups: Collection[str] = (),
        episode_query_rerank_enabled: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if type(enabled) is not bool or type(social_evidence_enabled) is not bool:
            raise OperationError("invalid_social_evidence_gate")
        if isinstance(allowed_groups, str):
            raise OperationError("invalid_social_group_allowlist")
        groups = frozenset(
            _identity(group_id, "invalid_social_group_allowlist", limit=64)
            for group_id in allowed_groups
        )
        if type(episode_query_rerank_enabled) is not bool:
            raise OperationError("invalid_episode_rerank_gate")
        self.episode_query_rerank_enabled = episode_query_rerank_enabled
        self._clock = clock
        self.store = store
        self.policy = policy
        self.enabled = enabled
        self.social_evidence_enabled = social_evidence_enabled
        self.allowed_groups = groups

    def _timestamp(self) -> float:
        value = self._clock()
        if type(value) not in {int, float} or not math.isfinite(value):
            raise OperationError("invalid_social_clock")
        return float(value)

    def _scope(self, value: object) -> Scope:
        return _scope(value, self.policy)

    def _is_enabled_for(self, scope: Scope) -> bool:
        return (
            self.enabled
            and self.social_evidence_enabled
            and scope.group_id in self.allowed_groups
        )

    def assert_current_transaction(
        self, db: StoreConnection, scope: Scope, experience_id: str
    ) -> SocialExperience:
        """Revalidate one experience while the caller holds the Store transaction."""

        scope = self._scope(scope)
        experience_id = _identity(experience_id, "invalid_social_experience_id")
        if not self._is_enabled_for(scope):
            raise OperationError("social_evidence_disabled")
        row = db.execute(
            "SELECT * FROM social_experiences WHERE experience_id=? AND bot_id=? "
            "AND group_id=? AND status='active'",
            (experience_id, scope.bot_id, scope.group_id),
        ).fetchone()
        if row is None:
            raise OperationError("social_experience_not_current")
        experience, _ = _current_experience(self.policy, db, scope=scope, row=row, now=self._timestamp())
        return experience

    def _capture_transaction(
        self,
        db: StoreConnection,
        *,
        actor: str,
        scope: Scope,
        episode_id: str,
        reply_action_id: str | None,
    ) -> SocialExperience:
        self.policy.check_transaction(db, actor, scope, "memory.apply", "", "", False, False)
        evidence = _episode_source(db, scope=scope, episode_id=episode_id, now=self._timestamp())
        source_author = _identity(evidence["speaker_id"], "source_author_missing", limit=64)
        _authorize_source_author(self.policy, db, source_author=source_author, scope=scope)
        existing = db.execute(
            "SELECT * FROM social_experiences WHERE bot_id=? AND group_id=? AND episode_id=?",
            (scope.bot_id, scope.group_id, episode_id),
        ).fetchone()
        if existing is not None:
            if existing["status"] != "active":
                raise OperationError("social_experience_invalidated")
            anchored_action = str(existing["reply_action_id"])
            if reply_action_id is not None and reply_action_id != anchored_action:
                raise OperationError("social_experience_action_conflict")
            reply_action_id = anchored_action
        if reply_action_id is None:
            reply_action_id, receipt = _first_complete_reply(
                db,
                scope=scope,
                event_id=str(evidence["origin_event_id"]),
                author_id=source_author,
            )
        else:
            reply_action_id = _identity(reply_action_id, "invalid_social_reply_action")
            _, receipt = _reply_receipt(
                db,
                scope=scope,
                event_id=str(evidence["origin_event_id"]),
                author_id=source_author,
                reply_action_id=reply_action_id,
            )

        if existing is not None:
            if not _same_capture(
                evidence, existing, reply_action_id=reply_action_id, receipt=receipt
            ):
                raise OperationError("social_experience_conflict")
            return _experience(existing)

        experience_id = _experience_id(scope, episode_id)
        db.execute(
            "INSERT INTO social_experiences(experience_id,episode_id,source_id,source_revision,"
            "bot_id,group_id,user_id,platform_message_id,observed_at,reply_action_id,receipt,status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'active')",
            (
                experience_id,
                episode_id,
                str(evidence["source_id"]),
                int(evidence["source_revision"]),
                scope.bot_id,
                scope.group_id,
                source_author,
                str(evidence["platform_message_id"]),
                float(evidence["observed_at"]),
                reply_action_id,
                receipt,
            ),
        )
        row = db.execute(
            "SELECT * FROM social_experiences WHERE experience_id=?", (experience_id,)
        ).fetchone()
        if row is None:
            raise OperationError("invalid_social_experience")
        return _experience(row)

    async def capture(
        self, *, actor: str, scope: Scope, episode_id: str, reply_action_id: str
    ) -> SocialExperience:
        actor = _identity(actor, "invalid_social_actor", limit=64)
        scope = self._scope(scope)
        episode_id = _identity(episode_id, "invalid_social_episode")
        reply_action_id = _identity(reply_action_id, "invalid_social_reply_action")
        if not self._is_enabled_for(scope):
            raise OperationError("social_evidence_disabled")
        return await self.store.transaction(
            lambda db: self._capture_transaction(
                db,
                actor=actor,
                scope=scope,
                episode_id=episode_id,
                reply_action_id=reply_action_id,
            )
        )

    async def capture_from_episode(
        self, *, actor: str, scope: Scope, episode_id: str
    ) -> SocialExperience:
        """Capture using the first complete successful text reply for this source."""

        actor = _identity(actor, "invalid_social_actor", limit=64)
        scope = self._scope(scope)
        episode_id = _identity(episode_id, "invalid_social_episode")
        if not self._is_enabled_for(scope):
            raise OperationError("social_evidence_disabled")
        return await self.store.transaction(
            lambda db: self._capture_transaction(
                db,
                actor=actor,
                scope=scope,
                episode_id=episode_id,
                reply_action_id=None,
            )
        )

    async def read_capture_status(
        self, *, actor: str, scope: Scope, episode_id: str
    ) -> SocialCaptureStatus:
        """Read one review-page capture status without returning source details."""

        actor = _identity(actor, "invalid_social_actor", limit=64)
        scope = self._scope(scope)
        episode_id = _identity(episode_id, "invalid_social_episode")

        def read(db: StoreConnection) -> SocialCaptureStatus:
            self.policy.check_transaction(db, actor, scope, "memory.retrieve", "", "", False, False)
            if not self._is_enabled_for(scope):
                return ("missing", None)
            row = db.execute(
                "SELECT * FROM social_experiences WHERE bot_id=? AND group_id=? AND episode_id=?",
                (scope.bot_id, scope.group_id, episode_id),
            ).fetchone()
            if row is None:
                return ("missing", None)
            experience_id = str(row["experience_id"])
            if row["status"] == "invalidated":
                return ("invalidated", experience_id)
            if row["status"] != "active":
                raise OperationError("invalid_social_experience")
            try:
                _current_experience(self.policy, db, scope=scope, row=row, now=self._timestamp())
            except OperationError as exc:
                if exc.code == "denied":
                    raise
                if exc.code in _TEMPORARILY_UNAVAILABLE_CODES:
                    return ("active", experience_id)
                if exc.code in _INVALID_CURRENT_CODES:
                    return ("invalidated", experience_id)
                raise
            return ("active", experience_id)

        return await self.store.transaction(read)

    async def read_current(self, *, actor: str, scope: Scope) -> tuple[SocialExperience, ...]:
        actor = _identity(actor, "invalid_social_actor", limit=64)
        scope = self._scope(scope)
        if not self._is_enabled_for(scope):
            return ()

        current = await self.store.transaction(
            lambda db: self._read_current_transaction(db, actor=actor, scope=scope)
        )
        return tuple(experience for experience, _ in current)

    async def read_chat_projection(
        self,
        *,
        actor: str,
        scope: Scope,
        limit: int = _MAX_SOCIAL_CHAT_ITEMS,
        speaker_id: str | None = None,
        query: str = "",
    ) -> SocialChatProjection:
        """Read a recent, bounded projection of current N6 episode values."""

        actor = _identity(actor, "invalid_social_actor", limit=64)
        scope = self._scope(scope)
        if speaker_id is not None:
            speaker_id = _identity(speaker_id, "invalid_social_speaker", limit=64)
        if type(query) is not str:
            raise OperationError("invalid_social_query")
        if type(limit) is not int or not 0 <= limit <= _MAX_SOCIAL_CHAT_ITEMS:
            raise OperationError("invalid_social_projection_limit")
        if not self._is_enabled_for(scope) or limit == 0:
            return SocialChatProjection(
                scope=scope, items=(), reader_id=actor, speaker_id=speaker_id
            )

        def read(db: StoreConnection) -> SocialChatProjection:
            current = self._read_current_transaction(db, actor=actor, scope=scope)
            recent = sorted(
                (
                    item
                    for item in current
                    if speaker_id is None or item[1]["speaker_id"] == speaker_id
                ),
                key=lambda item: (item[0].observed_at, item[0].experience_id),
                reverse=True,
            )
            if self.episode_query_rerank_enabled and _episode_query_key(query):
                recent = recent[:min(24, max(limit, limit * 3))]
                recent = [item for item in recent if social_topic_matches(query, (
                    item[1]["situation"], item[1]["observed_context"], item[1]["action_taken"],
                    item[1]["outcome_signal"], item[1]["reflection"]))]
                recent.sort(key=lambda item: _episode_relevance(query, _episode_value(item[1])), reverse=True)
            recent = recent[:limit]
            items = tuple(
                SocialChatItem(
                    experience_id=experience.experience_id,
                    episode_id=experience.episode_id,
                    episode_revision=int(evidence["object_revision"]),
                    user_id=experience.user_id,
                    value=_episode_value(evidence),
                )
                for experience, evidence in recent
            )
            return SocialChatProjection(
                scope=scope, items=items, reader_id=actor, speaker_id=speaker_id,
                selected_at=self._timestamp() if self.episode_query_rerank_enabled and items else None,
            )

        return await self.store.transaction(read)

    def assert_chat_projection_transaction(
        self,
        db: StoreConnection,
        *,
        actor: str,
        scope: Scope,
        frozen: SocialChatProjection,
    ) -> None:
        """Fail closed if a frozen chat projection changed before an action intent."""

        try:
            actor = _identity(actor, "invalid_social_actor", limit=64)
            scope = self._scope(scope)
            if (
                type(frozen) is not SocialChatProjection
                or frozen.scope != scope
                or len(frozen.items) > _MAX_SOCIAL_CHAT_ITEMS
                or frozen.reader_id != actor
                or not self._is_enabled_for(scope)
            ):
                raise OperationError("stale_social_context")
            self.policy.check_transaction(db, actor, scope, "memory.retrieve", "", "", False, False)
            for item in frozen.items:
                row = db.execute(
                    "SELECT * FROM social_experiences WHERE experience_id=? AND bot_id=? "
                    "AND group_id=? AND status='active'",
                    (item.experience_id, scope.bot_id, scope.group_id),
                ).fetchone()
                if row is None:
                    raise OperationError("stale_social_context")
                experience, evidence = _current_experience(
                    self.policy, db, scope=scope, row=row, now=self._timestamp()
                )
                if (
                    experience.user_id != item.user_id
                    or (frozen.speaker_id is not None and item.user_id != frozen.speaker_id)
                    or experience.episode_id != item.episode_id
                    or int(evidence["object_revision"]) != item.episode_revision
                    or _episode_value(evidence) != item.value
                ):
                    raise OperationError("stale_social_context")
        except OperationError as exc:
            if exc.code == "stale_social_context":
                raise
            raise OperationError("stale_social_context") from exc

    def stamp_chat_projection_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope, frozen: SocialChatProjection,
    ) -> None:
        """Stamp only the caller's final selected pack, without changing its proof revision."""
        self.assert_chat_projection_transaction(db, actor=actor, scope=scope, frozen=frozen)
        if frozen.selected_at is None:
            return
        for item in frozen.items:
            db.execute(
                "UPDATE domain_learning_episodes SET last_used_at=? WHERE object_id=? "
                "AND (last_used_at IS NULL OR last_used_at<?)",
                (frozen.selected_at, item.episode_id, frozen.selected_at),
            )

    def _read_current_transaction(
        self, db: StoreConnection, *, actor: str, scope: Scope
    ) -> tuple[tuple[SocialExperience, StoreRow], ...]:
        self.policy.check_transaction(db, actor, scope, "memory.retrieve", "", "", False, False)
        rows = db.execute(
            "SELECT * FROM social_experiences WHERE bot_id=? AND group_id=? AND status='active' "
            "ORDER BY observed_at,experience_id LIMIT 257",
            (scope.bot_id, scope.group_id),
        ).fetchall()
        if len(rows) > _MAX_SOCIAL_CANDIDATES:
            raise OperationError("social_experience_result_limit")
        current: list[tuple[SocialExperience, StoreRow]] = []
        for row in rows:
            experience_id = str(row["experience_id"])
            try:
                current.append(_current_experience(
                    self.policy, db, scope=scope, row=row, now=self._timestamp()))
            except OperationError as exc:
                if exc.code == "denied" or exc.code in _TEMPORARILY_UNAVAILABLE_CODES:
                    continue
                if exc.code not in _INVALID_CURRENT_CODES:
                    raise
                db.execute(
                    "UPDATE social_experiences SET status='invalidated' "
                    "WHERE experience_id=? AND status='active'",
                    (experience_id,),
                )
        return tuple(current)
