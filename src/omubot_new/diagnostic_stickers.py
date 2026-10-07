"""Explicit debug save/send preparation on the existing managed sticker owner."""
from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from time import monotonic
from typing import cast

from .diagnostic_commands import DiagnosticCommand
from .rich_messages import ImageSegment, ReplySegment, TextSegment
from .runtime import DecisionBinding, Turn
from .sticker_store import StickerMetadata, StickerOperationReceipt, StickerStore
from .stickers import ResolvedStickerAsset, StickerEntry, sticker_scope_key
from .store import StoreConnection, request_digest
from .types import (
    MAX_STICKER_BYTES,
    Event,
    OperationError,
    QuotedImageLease,
    QuotedVisualSource,
    Scope,
    StickerMediaType,
    VisualOwner,
)


@dataclass(frozen=True, slots=True)
class DiagnosticImage:
    """Current-source raw asset bytes; only StickerStore decodes their format."""

    owner: VisualOwner
    data: bytes = field(repr=False)
    media_type: StickerMediaType

    def __post_init__(self) -> None:
        if type(self.owner) is not VisualOwner or type(self.data) is not bytes:
            raise TypeError("diagnostic image requires typed owner and bytes")
        if not self.data or len(self.data) > MAX_STICKER_BYTES:
            raise ValueError("diagnostic image exceeds its byte budget")
        if self.media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValueError("diagnostic image media type unsupported")


@dataclass(frozen=True, slots=True)
class DiagnosticSaveResult:
    receipts: tuple[StickerOperationReceipt, ...]
    failed_code: str | None = None

    @property
    def text(self) -> str:
        saved = '、'.join(receipt.sticker_id for receipt in self.receipts)
        text = f'已保存到本群表情库，待审核：{saved}。'
        return text + f' 后续图片未保存（{self.failed_code}）。' if self.failed_code else text


@dataclass(frozen=True, slots=True)
class DiagnosticStickerSend:
    entry: StickerEntry
    catalog_revision: int
    asset: ResolvedStickerAsset
    source_digest: str
    binding: DecisionBinding


class DiagnosticStickerCommands:
    """No new store/sender/lifecycle: freeze source, consume one actual asset owner."""

    def __init__(self, owner: StickerStore, *, choose: Callable[[Sequence[StickerEntry]], StickerEntry] =
                 random.choice) -> None:
        self.owner = owner
        self._choose = choose

    def _gate(
        self, event: Event, *, turn: Turn, binding: DecisionBinding,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
        source_preflight_transaction: Callable[[StoreConnection], None], save: bool,
    ) -> tuple[Callable[[], None], Callable[[StoreConnection], None]]:
        if not isinstance(event.scope, Scope):
            raise OperationError('unsupported_scope')
        scope = event.scope
        digest = request_digest(event)

        def check() -> None:
            turn.check()
            if (binding.turn_id != turn.id or binding.generation != turn.generation
                    or current_binding() != binding):
                raise OperationError('diagnostic_source_changed')
            source_preflight()

        def transaction(db: StoreConnection) -> None:
            check()
            source_preflight_transaction(db)
            row = db.execute('SELECT digest FROM requests WHERE id=?', (event.event_id,)).fetchone()
            if row is None or row['digest'] != digest:
                raise OperationError('diagnostic_source_changed')
            for action in ('message.read', 'media.read', 'sticker.manage') if save else (
                'message.read', 'media.send', 'message.sticker',
            ):
                self.owner.policy.check_transaction(db, event.user_id, scope, action, '', '', False, False)

        return check, transaction

    async def save(
        self, event: Event, command: DiagnosticCommand, images: tuple[DiagnosticImage, ...], *, turn: Turn,
        binding: DecisionBinding, current_binding: Callable[[], DecisionBinding],
        source_preflight: Callable[[], None], source_preflight_transaction: Callable[[StoreConnection], None],
    ) -> DiagnosticSaveResult:
        if command.kind != 'save':
            raise OperationError('invalid_diagnostic_command')
        check, transaction = self._gate(
            event, turn=turn, binding=binding, current_binding=current_binding,
            source_preflight=source_preflight, source_preflight_transaction=source_preflight_transaction,
            save=True,
        )
        await self.owner.store.transaction(transaction)
        if not images or len(images) > 2:
            raise OperationError('diagnostic_no_current_image')
        expected = {index for index, segment in enumerate(event.rich_segments)
                    if isinstance(segment, ImageSegment) and not segment.is_flash}
        indices: set[int] = set()
        for image in images:
            owner = image.owner
            if (owner.scope != event.scope or owner.event_id != event.event_id
                    or owner.message_id != event.message_id or owner.turn_id != turn.id
                    or owner.source_kind != 'direct' or owner.segment_index not in expected
                    or owner.segment_index in indices):
                raise OperationError('diagnostic_invalid_image_owner')
            indices.add(owner.segment_index)
        if indices != expected:
            raise OperationError('diagnostic_image_missing')
        return await self._save_assets(event, command, tuple(
            (image.owner.segment_index, image, 'direct') for image in images),
            check=check, transaction=transaction)

    async def save_retained(
        self, event: Event, command: DiagnosticCommand, images: tuple[QuotedVisualSource, ...], *,
        lease: QuotedImageLease, turn: Turn, binding: DecisionBinding,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
        source_preflight_transaction: Callable[[StoreConnection], None],
    ) -> DiagnosticSaveResult:
        """Explicit save consumes exact retained pixels; this lease must be purpose-minted."""
        if command.kind != 'save':
            raise OperationError('invalid_diagnostic_command')
        outer_check, outer_transaction = self._gate(
            event, turn=turn, binding=binding, current_binding=current_binding,
            source_preflight=source_preflight, source_preflight_transaction=source_preflight_transaction,
            save=True,
        )
        source, owner = lease.source, lease.owner
        if (owner.scope != event.scope or source.scope != event.scope
                or owner.event_id != event.event_id or owner.message_id != event.message_id
                or owner.turn_id != turn.id or owner.source_kind != 'reply'
                or source.author_id == event.scope.bot_id or not source.author_id):
            raise OperationError('diagnostic_invalid_image_owner')
        if event.reply_to:
            reply_indices = [index for index, segment in enumerate(event.rich_segments)
                             if isinstance(segment, ReplySegment) and segment.message_id == event.reply_to]
            if (reply_indices != [owner.segment_index] or source.message_id != event.reply_to
                    or any(not isinstance(segment, (TextSegment, ReplySegment))
                           for segment in event.rich_segments)):
                raise OperationError('diagnostic_invalid_image_owner')
        elif (owner.segment_index >= len(event.rich_segments)
              or not isinstance(event.rich_segments[owner.segment_index], TextSegment)
              or any(not isinstance(segment, TextSegment) for segment in event.rich_segments)):
            raise OperationError('diagnostic_invalid_image_owner')

        def check() -> None:
            outer_check()
            lease.assert_current_sync()
            if monotonic() >= min(lease.deadline, source.expires_at):
                raise OperationError('diagnostic_source_changed')

        def transaction(db: StoreConnection) -> None:
            check()
            outer_transaction(db)
            row = db.execute('SELECT digest FROM requests WHERE id=?', (source.event_id,)).fetchone()
            if row is not None and row['digest'] != source.request_digest:
                raise OperationError('diagnostic_source_changed')
            if source.author_id != event.user_id:
                for action in ('message.read', 'media.read'):
                    self.owner.policy.check_transaction(
                        db, source.author_id, source.scope, action, '', '', False, False)

        check()
        await lease.assert_current()
        await self.owner.store.transaction(transaction)
        if not images or len(images) > 2:
            raise OperationError('diagnostic_no_current_image')
        assets: list[tuple[int, DiagnosticImage, str]] = []
        indices: set[int] = set()
        for result in images:
            image, proof = result.source, result.proof
            if (image.owner != owner or image.source_subject != source.author_id
                    or proof.source != source or proof.source_segment_index in indices
                    or (proof.source_segment_index, proof.token_sha256) not in source.image_bindings
                    or hashlib.sha256(image.data).hexdigest() != proof.pixel_sha256):
                raise OperationError('diagnostic_invalid_image_owner')
            indices.add(proof.source_segment_index)
            frozen_source = hashlib.sha256(json.dumps([
                source.scope.key, source.event_id, source.message_id, source.author_id,
                source.request_digest, proof.source_segment_index, proof.token_sha256,
                proof.pixel_sha256], separators=(',', ':')).encode()).hexdigest()
            assets.append((proof.source_segment_index,
                           DiagnosticImage(owner, image.data, cast(StickerMediaType, image.content_type)),
                           frozen_source))
        return await self._save_assets(event, command, tuple(assets), check=check, transaction=transaction)

    async def _save_assets(
        self, event: Event, command: DiagnosticCommand,
        assets: tuple[tuple[int, DiagnosticImage, str], ...], *,
        check: Callable[[], None], transaction: Callable[[StoreConnection], None],
    ) -> DiagnosticSaveResult:
        receipts: list[StickerOperationReceipt] = []
        for index, image, frozen_source in sorted(assets, key=lambda value: value[0]):
            check()
            identity = hashlib.sha256(json.dumps(
                [event.scope.key, event.event_id, request_digest(event), index, frozen_source,
                 hashlib.sha256(image.data).hexdigest()], separators=(',', ':')).encode()).hexdigest()
            try:
                assert isinstance(event.scope, Scope)
                receipt = await self.owner.import_asset(
                    actor=event.user_id, scope=event.scope, operation_id='debug-save-' + identity[:40],
                    expected_revision=self.owner.catalog.revision, sticker_id='stk_' + identity[:24],
                    data=image.data, content_type=image.media_type,
                    metadata=StickerMetadata(description=command.text, usage_hint='通用聊天表情'),
                    preflight_transaction=transaction,
                )
            except OperationError as exc:
                if not receipts:
                    raise
                return DiagnosticSaveResult(tuple(receipts), exc.code)
            receipts.append(receipt)
        return DiagnosticSaveResult(tuple(receipts))

    async def prepare_send(
        self, event: Event, command: DiagnosticCommand, *, turn: Turn, binding: DecisionBinding,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
        source_preflight_transaction: Callable[[StoreConnection], None],
    ) -> DiagnosticStickerSend:
        if command.kind != 'send':
            raise OperationError('invalid_diagnostic_command')
        check, transaction = self._gate(
            event, turn=turn, binding=binding, current_binding=current_binding,
            source_preflight=source_preflight, source_preflight_transaction=source_preflight_transaction,
            save=False,
        )
        await self.owner.store.transaction(transaction)
        animated = command.text in {'gif', '动图', '动态'}
        if command.text and not animated:
            revision = self.owner.catalog.revision
            entry = self.owner.catalog.get(command.text)
        else:
            assert isinstance(event.scope, Scope)
            listing = await self.owner.approved_send_candidates(
                actor=event.user_id, scope=event.scope, preflight_transaction=transaction)
            candidates = tuple(
                entry for entry in listing.entries if not animated or entry.mime_type == 'image/gif')
            if not candidates:
                raise OperationError('diagnostic_sticker_unavailable')
            revision = listing.revision
            entry = self._choose(candidates)
        if (entry is None or entry.status != 'approved'
                or sticker_scope_key(event.scope) not in entry.allowed_scopes):
            raise OperationError('diagnostic_sticker_unavailable')
        asset = await self.owner.resolve(entry.sticker_id, entry.catalog_revision)
        check()
        if asset is None:
            raise OperationError('diagnostic_sticker_unavailable')

        def frozen(db: StoreConnection) -> None:
            transaction(db)
            assert isinstance(event.scope, Scope)
            self.owner.assert_current_asset_transaction(db, scope=event.scope, entry=entry,
                                                       expected_revision=revision, asset=asset)
        await self.owner.store.transaction(frozen)
        return DiagnosticStickerSend(entry, revision, asset, request_digest(event), binding)
