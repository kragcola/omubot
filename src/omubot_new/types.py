"""Small boundary values shared by the actual first-use-case components."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PrivateAttr, field_validator, model_validator

from .rich_messages import Segment


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


ConversationKey = tuple[str, Literal["group", "private"], str]


def _exact_scope_identifier(value: str) -> str:
    if value != value.strip() or "*" in value:
        raise ValueError("scope must be an exact nonblank identifier")
    return value


class Scope(StrictModel):
    bot_id: str = Field(min_length=1, max_length=64)
    kind: Literal["group"] = "group"
    group_id: str = Field(min_length=1, max_length=64)

    @field_validator("bot_id", "group_id")
    @classmethod
    def exact_identifier(cls, value: str) -> str:
        return _exact_scope_identifier(value)

    @property
    def key(self) -> ConversationKey:
        return self.bot_id, self.kind, self.group_id


class PrivateScope(StrictModel):
    bot_id: str = Field(min_length=1, max_length=64)
    kind: Literal["private"]
    private_user_id: str = Field(min_length=1, max_length=64)

    @field_validator("bot_id", "private_user_id")
    @classmethod
    def exact_identifier(cls, value: str) -> str:
        return _exact_scope_identifier(value)

    @property
    def key(self) -> ConversationKey:
        return self.bot_id, self.kind, self.private_user_id

    @property
    def group_id(self) -> str:
        raise OperationError("unsupported_scope")


ConversationScope = Scope | PrivateScope


ContactPurpose = Literal["autonomous_chat", "role_life_broadcast"]


@dataclass(frozen=True, slots=True)
class ContactAuthorityProof:
    """Policy's frozen two-consent authority; ordinary action grants are separate."""

    policy_revision: int
    scope: Scope
    target_user_id: str
    purpose: ContactPurpose
    user_generation: str
    group_generation: str


@dataclass(frozen=True, slots=True)
class ReplyOwner:
    """The actual request and permission subject shared by two reply origins."""

    request_id: str
    scope: ConversationScope
    permission_subject_id: str
    origin: Literal["inbound_event", "bot_contact"]


@dataclass(frozen=True, slots=True)
class BotContactInput:
    """A real Bot intent, with no human message or fabricated ingress identity."""

    request_id: str
    scope: Scope
    target_user_id: str
    purpose: ContactPurpose
    authority: ContactAuthorityProof
    cause_kind: Literal["conversation_context", "role_life"]
    cause_id: str
    cause_version: str
    configuration_version: str
    created_at: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class ContactActionProof:
    """The exact native intent and lifecycle revision consumed by Actions."""

    contact: BotContactInput
    revision: int
    phase: Literal["timing", "reply", "tool", "send", "followup"]


class VisualOwner(StrictModel):
    """The complete current-turn identity carried with validated image bytes."""

    scope: ConversationScope
    event_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=64)
    segment_index: int = Field(ge=0, le=127)
    # admin_asset uses the HTTP operation ID and sticker ID, never QQ message
    # identity; it cannot authorize a direct/reply image source.
    source_kind: Literal["direct", "reply", "admin_asset"]

    @field_validator("event_id", "turn_id", "message_id")
    @classmethod
    def exact_owner_identifier(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("owner identifiers must be exact")
        return value


# ``ImageOwner`` is a descriptive alias for callers that name the value by its
# payload rather than its visual role.  There is still one owner model.
ImageOwner = VisualOwner


@dataclass(frozen=True, slots=True)
class RetainedHumanImageSource:
    """Conversation-minted identity; digests retain no token, URL or pixels."""

    scope: Scope
    event_id: str
    message_id: str
    author_id: str
    request_digest: str
    image_bindings: tuple[tuple[int, str], ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class QuotedImageLease:
    """The real retained owner supplies the mandatory current-source gate."""

    owner: VisualOwner
    source: RetainedHumanImageSource
    deadline: float
    assert_current: Callable[[], Awaitable[None]] = dataclass_field(repr=False, compare=False)
    assert_current_sync: Callable[[], None] = dataclass_field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class QuotedImageProof:
    source: RetainedHumanImageSource
    source_segment_index: int
    token_sha256: str
    pixel_sha256: str


_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_BASE64 = ((_MAX_IMAGE_BYTES + 2) // 3) * 4
MAX_STICKER_BYTES: Final = 8 * 1024 * 1024
StickerMediaType = Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
_STICKER_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})


class ImagePart(StrictModel):
    """Validated, request-local image data; it carries no URL or filesystem path."""

    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: str = Field(min_length=4, max_length=_MAX_IMAGE_BASE64, repr=False)
    owner: VisualOwner

    @field_validator("data")
    @classmethod
    def canonical_base64(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image data must be canonical base64") from exc
        if not decoded or len(decoded) > _MAX_IMAGE_BYTES:
            raise ValueError("image data exceeds the byte budget")
        if base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("image data must be canonical base64")
        return value

    @model_validator(mode="after")
    def matching_magic(self) -> ImagePart:
        raw = base64.b64decode(self.data, validate=True)
        signatures = {
            "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
            "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/webp": len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP",
        }
        if not signatures[self.media_type]:
            raise ValueError("image data does not match media type")
        return self


@dataclass(frozen=True, slots=True)
class VisualSource:
    """Unvalidated bytes returned by an injected current-turn resolver."""

    owner: VisualOwner
    data: bytes = dataclass_field(repr=False)
    content_type: str
    source_subject: str

    def __post_init__(self) -> None:
        if type(self.owner) is not VisualOwner:
            raise TypeError("visual source owner must be VisualOwner")
        if type(self.data) is not bytes or not self.data:
            raise ValueError("visual source data must be non-empty bytes")
        if type(self.content_type) is not str or not self.content_type.strip():
            raise ValueError("visual source content type must be non-empty")
        if (
            type(self.source_subject) is not str
            or not self.source_subject
            or self.source_subject != self.source_subject.strip()
            or len(self.source_subject) > 64
        ):
            raise ValueError("visual source subject must be exact")


@dataclass(frozen=True, slots=True)
class QuotedVisualSource:
    """Outer reply owner and independent original image proof stay separate."""

    source: VisualSource
    proof: QuotedImageProof


@dataclass(frozen=True, slots=True)
class StickerImage:
    """Validated application media for one outgoing sticker action.

    The core value has no path, URL, catalog lookup, or provider-specific wire
    field.  OneBot ``base64://`` encoding belongs to the payload boundary.
    """

    data: bytes = dataclass_field(repr=False)
    media_type: StickerMediaType

    def __post_init__(self) -> None:
        if type(self.data) is not bytes:
            raise TypeError("sticker data must be bytes")
        if not self.data or len(self.data) > MAX_STICKER_BYTES:
            raise ValueError("sticker data exceeds the byte budget")
        if self.media_type not in _STICKER_MEDIA_TYPES:
            raise ValueError("sticker media type is unsupported")
        if not _sticker_matches_magic(self.data, self.media_type):
            raise ValueError("sticker bytes do not match media type")


def _sticker_matches_magic(data: bytes, media_type: StickerMediaType) -> bool:
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if media_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    return False


class Event(StrictModel):
    # Authenticated parser-owned request hints: never serialized, logged or archived.
    _current_image_urls: tuple[tuple[int, str], ...] = PrivateAttr(default=())

    def bind_current_image_urls(self, hints: tuple[tuple[int, str], ...]) -> None:
        """Authenticated adapter binds transient hints before publishing this event."""
        self._current_image_urls = hints

    @property
    def current_image_urls(self) -> tuple[tuple[int, str], ...]:
        return self._current_image_urls

    event_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=64)
    scope: ConversationScope
    text: str = Field(min_length=1, max_length=8000)
    # Authenticated OneBot envelope time. None means the source time is unknown;
    # receipt time must never stand in for it in temporal memory decisions.
    event_time: int | None = Field(default=None, ge=946684800, le=4102444800)
    message_id: str = Field(default="", max_length=64)
    reply_to: str = Field(default="", max_length=64)
    mentioned: bool = False
    # Exact OneBot @ targets, in wire order. This is local routing evidence;
    # the rendered text is never parsed to infer identities.
    mention_targets: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    # The parser keeps the bounded local rich structure beside the legacy text
    # projection. Segment is owned by the core rich-message module; this DTO
    # does not define a second wire or raw-dict model.
    rich_segments: tuple[Segment, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def matching_private_peer(self) -> Event:
        if self.scope.kind == "private" and self.scope.private_user_id != self.user_id:
            raise ValueError("private event sender must match its scope peer")
        return self

    @field_validator("mention_targets")
    @classmethod
    def exact_mention_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not target or target != target.strip() or len(target) > 64 for target in value):
            raise ValueError("mention targets must be exact identifiers")
        if len(value) != len(set(value)):
            raise ValueError("mention targets must be unique")
        return value


class VisualResolverPort(Protocol):
    """Optional, request-scoped source for current event image bytes."""

    async def resolve(
        self,
        event: Event,
        owners: tuple[VisualOwner, ...],
        *,
        turn_id: str,
        revision: int,
    ) -> tuple[VisualSource, ...]: ...


class QuotedVisualResolverPort(Protocol):
    """Read retained human quote pixels through the same native visual owner."""

    async def resolve_quote(self, lease: QuotedImageLease) -> tuple[QuotedVisualSource, ...]: ...


@runtime_checkable
class ManagedImageResolverPort(Protocol):
    """Bounded raw bytes for explicit managed import, outside Model image input."""

    async def resolve_assets(
        self, event: Event, owners: tuple[VisualOwner, ...], *, turn_id: str, revision: int,
    ) -> tuple[VisualSource, ...]: ...


@runtime_checkable
class ManagedQuotedImageResolverPort(Protocol):
    """Purpose-minted retained source lease, consumed by the same native owner."""

    async def resolve_quote_assets(self, lease: QuotedImageLease) -> tuple[QuotedVisualSource, ...]: ...


class Grant(StrictModel):
    subject: str = Field(min_length=1, max_length=64)
    scope: ConversationScope
    actions: list[str]
    effect: Literal["allow", "deny"] = "allow"
    provider: str = "anthropic"
    model: str = "offline-model"
    allow_history: bool = False
    allow_images: bool = False
    expires_at: float = Field(gt=0)


VisibilityMaterial = Literal["knowledge", "slang", "style"]


class KnowledgeVisibilityRef(StrictModel):
    """An explicitly selected current document; no document content is copied."""

    material_type: Literal["knowledge"] = "knowledge"
    source_id: str = Field(min_length=1, max_length=128)
    source_revision: int = Field(ge=1)
    content_revision: int = Field(ge=1)
    index_version: str = Field(min_length=1, max_length=64)

    @field_validator("source_id", "index_version")
    @classmethod
    def exact_reference(cls, value: str) -> str:
        return _exact_scope_identifier(value)


class LearningVisibilityRef(StrictModel):
    """An applied source-owned projection, never an approved candidate."""

    material_type: Literal["slang", "style"]
    object_id: str = Field(min_length=1, max_length=128)
    object_revision: int = Field(ge=1)
    source_id: str = Field(min_length=1, max_length=128)
    source_revision: int = Field(ge=1)
    applied_event_id: str = Field(min_length=1, max_length=128)

    @field_validator("object_id", "source_id", "applied_event_id")
    @classmethod
    def exact_reference(cls, value: str) -> str:
        return _exact_scope_identifier(value)


VisibilityObjectRef = KnowledgeVisibilityRef | LearningVisibilityRef


class VisibilityGrant(StrictModel):
    """Policy-owned, reviewed visibility for exact nonpersonal source revisions."""

    grant_id: str = Field(min_length=1, max_length=64)
    grant_revision: int = Field(ge=1)
    source_scope: Scope
    target_scope: Scope
    material_type: VisibilityMaterial
    object_refs: tuple[VisibilityObjectRef, ...] = Field(min_length=1, max_length=64)
    non_personal_projection_confirmed: Literal[True]
    actor: str = Field(min_length=1, max_length=64)
    issued_at: float = Field(ge=0, allow_inf_nan=False)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    status: Literal["active", "revoked"] = "active"

    @field_validator("grant_id", "actor")
    @classmethod
    def exact_identity(cls, value: str) -> str:
        return _exact_scope_identifier(value)

    @field_validator("non_personal_projection_confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit nonpersonal review is required")
        return value

    @model_validator(mode="after")
    def exact_group_projection(self) -> VisibilityGrant:
        if (self.source_scope.bot_id != self.target_scope.bot_id
                or self.source_scope == self.target_scope
                or self.expires_at <= self.issued_at
                or any(ref.material_type != self.material_type for ref in self.object_refs)
                or len(set(self.object_refs)) != len(self.object_refs)):
            raise ValueError("visibility requires exact distinct groups and selected current revisions")
        return self


class VisibilityReceipt(StrictModel):
    """A frozen visibility check; authorization still requires current Policy lookup."""

    policy_revision: int = Field(ge=1)
    grant_id: str = Field(min_length=1, max_length=64)
    grant_revision: int = Field(ge=1)
    reader_id: str = Field(min_length=1, max_length=64)
    source_scope: Scope
    target_scope: Scope
    material_type: VisibilityMaterial
    object_refs: tuple[VisibilityObjectRef, ...] = Field(min_length=1, max_length=64)
    expires_at: float = Field(gt=0, allow_inf_nan=False)


class Message(StrictModel):
    role: Literal["user", "assistant"]
    content: str
    source_ids: list[str] = Field(default_factory=list, max_length=8)


class ToolCall(StrictModel):
    id: str
    name: str
    arguments: dict[str, JsonValue]


class ModelUsage(StrictModel):
    input_tokens: int = Field(ge=0, le=1_000_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000_000)
    cached_input_tokens: int = Field(default=0, ge=0, le=1_000_000_000)
    cache_write_tokens: int = Field(default=0, ge=0, le=1_000_000_000)


class ModelReply(StrictModel):
    usage: ModelUsage | None = None
    text: str = ""
    tool_call: ToolCall | None = None
    continuation: list[dict[str, JsonValue]] = Field(
        default_factory=lambda: list[dict[str, JsonValue]](), repr=False
    )


class ModelRequest(StrictModel):
    system: str = Field(default="", max_length=6200)
    messages: list[Message]
    # Images belong to the current final user message only.  Historical
    # Message values deliberately remain text-only and therefore serializable
    # without provider/media data.
    current_images: tuple[ImagePart, ...] = Field(
        default_factory=tuple, max_length=2, repr=False
    )
    model: str
    max_output_tokens: int | None = Field(default=None, gt=0)
    # Local visible-text budget; providers receive only their token parameter.
    max_output_chars: int = Field(default=2000, gt=0, le=32768)
    tools: list[dict[str, JsonValue]]
    previous_tool: ToolCall | None = None
    tool_result: dict[str, JsonValue] | None = None
    continuation: list[dict[str, JsonValue]] = Field(
        default_factory=lambda: list[dict[str, JsonValue]](), repr=False
    )

    @model_validator(mode="after")
    def current_images_require_user_tail(self) -> ModelRequest:
        if self.current_images and (not self.messages or self.messages[-1].role != "user"):
            raise ValueError("current images require a final user message")
        return self

    @model_validator(mode="after")
    def current_images_share_owner_context(self) -> ModelRequest:
        if len(self.current_images) > 1:
            first = self.current_images[0].owner
            context = (first.scope, first.event_id, first.turn_id)
            if any(
                (image.owner.scope, image.owner.event_id, image.owner.turn_id) != context
                for image in self.current_images[1:]
            ):
                raise ValueError("current images must share the current owner context")
        return self


class SendReceipt(StrictModel):
    message_id: str


class ReplyTarget(StrictModel):
    scope: ConversationScope
    message_id: str = Field(min_length=1, max_length=64)

    @field_validator("message_id")
    @classmethod
    def exact_message_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reply target must be exact")
        return value


class ContactAddressee(StrictModel):
    """An actual directed group recipient, independently of quoted message identity."""

    scope: Scope
    user_id: str = Field(min_length=1, max_length=64)

    @field_validator("user_id")
    @classmethod
    def exact_recipient(cls, value: str) -> str:
        return _exact_scope_identifier(value)


@dataclass(frozen=True)
class ActionCall:
    key: str
    request_id: str
    subject: str
    scope: ConversationScope
    action: str
    payload_hash: str
    provider: str = "anthropic"
    model: str = "offline-model"
    includes_history: bool = False
    history_subjects: tuple[str, ...] = ()
    includes_images: bool = False
    image_subjects: tuple[str, ...] = ()
    sticker_id: str = ""
    catalog_revision: int = 0
    reply_target_status: Literal["none", "anchored", "invalid", "continuation"] = "none"
    contact: ContactActionProof | None = None
    contact_addressee: ContactAddressee | None = None


class OperationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


QQScopeKey = ConversationKey


def qq_params_hash(params: dict[str, JsonValue]) -> str:
    """Digest exactly the JSON parameters used by the protocol encoder."""
    encoded = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class QQWriteSpec:
    api: str
    account_id: str
    scope_key: QQScopeKey
    params_hash: str
    cost: Literal[1] = 1
    # Native encoder reads actual structured wire segments. None means the
    # wire form does not expose reliable recipients (for example raw CQ text).
    mention_targets: tuple[str, ...] | None = ()


@dataclass(frozen=True, slots=True)
class QQAdmissionBinding:
    action_key: str
    candidate_generation: int
    candidate_revision: str = ""
    connection_generation: int = 0


@dataclass(frozen=True, slots=True)
class QQAdmission:
    token: str
    spec: QQWriteSpec
    binding: QQAdmissionBinding
    governor_revision: int
    deadline: float


@dataclass(frozen=True, slots=True)
class QQWriteGrant:
    """Single committed write; consume is synchronous at the actual I/O boundary."""

    spec: QQWriteSpec
    consume: Callable[[QQWriteSpec, int], None] = dataclass_field(repr=False, compare=False)


class QQDeliveryLimits(StrictModel):
    account_min_interval: float = Field(default=5.0, gt=0)
    account_hour_limit: int = Field(default=60, ge=1)
    account_day_limit: int = Field(default=180, ge=1)
    target_min_interval: float = Field(default=8.0, gt=0)
    target_hour_limit: int = Field(default=30, ge=1)
    target_day_limit: int = Field(default=90, ge=1)
    admission_wait_seconds: float = Field(default=30.0, gt=0, le=30)
    account_queue_limit: int = Field(default=8, ge=1, le=8)
    target_queue_limit: int = Field(default=2, ge=1, le=2)


@dataclass(frozen=True, slots=True)
class QQStateRow:
    account_id: str
    scope_key: QQScopeKey | None
    held: bool
    revision: int
    reason: str
    occurred_at: float
    last_settled_at: float | None
    action_id: str
    audit_id: int | None


@dataclass(frozen=True, slots=True)
class QQQuotaAttempt:
    action_id: str
    budget_anchor: float
    cost: int


@dataclass(frozen=True, slots=True)
class QQQuotaProjection:
    observed_at: float
    account_hour_used: int
    account_day_used: int
    target_hour_used: int
    target_day_used: int
    account_attempts: tuple[QQQuotaAttempt, ...]
    target_attempts: tuple[QQQuotaAttempt, ...]


@dataclass(frozen=True, slots=True)
class QQDeliverySnapshot:
    account: QQStateRow
    target: QQStateRow | None
    targets: tuple[QQStateRow, ...]
    quota: QQQuotaProjection
    unsettled_action_ids: tuple[str, ...]
    unreviewed_unknown_action_ids: tuple[str, ...]
    clock_inconsistent: bool


@dataclass(frozen=True, slots=True)
class QQTransportEvidence:
    transport: Literal["http", "ws"]
    phase: Literal["not_started", "may_have_started", "acknowledged"]
    http_status: int | None = None
    onebot_status: str | None = None
    retcode: int | None = None
    platform_result: int | str | None = None
    reliable_code: str | None = None
    bridge_version: str | None = None

    def safe_dump(self) -> dict[str, JsonValue]:
        return {
            "transport": self.transport, "phase": self.phase, "http_status": self.http_status,
            "onebot_status": self.onebot_status, "retcode": self.retcode,
            "platform_result": self.platform_result, "reliable_code": self.reliable_code,
            "bridge_version": self.bridge_version,
        }


class QQTransportError(OperationError):
    def __init__(self, code: str, evidence: QQTransportEvidence) -> None:
        self.evidence = evidence
        super().__init__(code)

    def safe_dump(self) -> dict[str, JsonValue]:
        return {"code": self.code, "evidence": self.evidence.safe_dump()}


@runtime_checkable
class QQSenderPort(Protocol):
    is_external: bool

    @property
    def connection_generation(self) -> int: ...

    def describe_send(
        self, scope: ConversationScope, text: str, *, target: ReplyTarget | None = None,
        addressee: ContactAddressee | None = None,
    ) -> QQWriteSpec: ...

    async def send(
        self, scope: ConversationScope, text: str, *, grant: QQWriteGrant,
        target: ReplyTarget | None = None,
        addressee: ContactAddressee | None = None,
    ) -> SendReceipt: ...


@runtime_checkable
class QQStickerSenderPort(Protocol):
    is_external: bool

    @property
    def connection_generation(self) -> int: ...

    def describe_sticker(
        self, scope: ConversationScope, image: StickerImage, *, target: ReplyTarget | None = None,
    ) -> QQWriteSpec: ...

    async def send_sticker(
        self, scope: ConversationScope, image: StickerImage, *, grant: QQWriteGrant,
        target: ReplyTarget | None = None,
    ) -> SendReceipt: ...


class ModelPort(Protocol):
    is_external: bool

    async def request(self, request: ModelRequest) -> ModelReply: ...

    async def close(self) -> None: ...


@runtime_checkable
class StreamingTextModelPort(ModelPort, Protocol):
    """Optional tool-free text delta capability of the reply model."""

    async def request_stream(
        self, request: ModelRequest, on_text_delta: Callable[[str], Awaitable[None]]
    ) -> ModelReply: ...


class SenderPort(Protocol):
    is_external: bool

    async def send(
        self, scope: ConversationScope, text: str, *, target: ReplyTarget | None = None,
        addressee: ContactAddressee | None = None,
    ) -> SendReceipt: ...

    async def close(self) -> None: ...


class StickerSenderPort(Protocol):
    """Typed outgoing sticker boundary owned by the OneBot adapter."""

    is_external: bool

    async def send_sticker(
        self, scope: ConversationScope, image: StickerImage, *, target: ReplyTarget | None = None
    ) -> SendReceipt: ...

    async def close(self) -> None: ...




ModelOperation = Callable[[], Awaitable[ModelReply]]
