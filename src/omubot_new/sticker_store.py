"""Admin-controlled durable sticker catalog on the one Store executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from io import BytesIO
from typing import Literal, cast

from PIL import Image, UnidentifiedImageError

from .policy import Policy
from .stickers import (
    MAX_CATALOG_ENTRIES,
    MAX_STICKER_BYTES,
    SUPPORTED_MIME_TYPES,
    ResolvedStickerAsset,
    StickerCatalog,
    StickerEntry,
    StickerStatus,
    _validate_id,  # pyright: ignore[reportPrivateUsage] - reuse the catalog ID boundary.
    sticker_scope_key,
)
from .store import Store, StoreConnection, StoreRow, drain_on_cancel
from .types import ConversationScope, OperationError, Scope, StickerImage, StickerMediaType
from .visual_transport import VisualLimits, _decode_image  # pyright: ignore[reportPrivateUsage]

MAX_STICKER_GIF_FRAMES = 128


def _decode_sticker_gif(data: bytes) -> tuple[int, int] | str:
    """Decode every bounded outgoing GIF frame; Model GIF input stays rejected."""
    if not data.startswith((b"GIF87a", b"GIF89a")) or not data.endswith(b";"):
        return "invalid_gif"
    limits = VisualLimits(max_bytes=MAX_STICKER_BYTES)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=["GIF"]) as image:
                dimensions = image.size
                total_pixels = 0
                for frame in range(MAX_STICKER_GIF_FRAMES + 1):
                    try:
                        image.seek(frame)
                    except EOFError:
                        return dimensions
                    if frame == MAX_STICKER_GIF_FRAMES:
                        return "gif_frame_limit"
                    width, height = image.size
                    if not 1 <= width <= limits.max_dimension or not 1 <= height <= limits.max_dimension:
                        return "dimension_limit"
                    total_pixels += width * height
                    if total_pixels > limits.max_pixels:
                        return "gif_pixel_limit"
                    image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        return "invalid_gif"
    raise AssertionError("GIF frame loop must terminate")


@dataclass(frozen=True, slots=True)
class StickerMetadata:
    description: str = ""
    usage_hint: str = ""
    ocr_text: str = ""
    intent_tags: tuple[str, ...] = ()
    affect_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StickerListing:
    revision: int
    entries: tuple[StickerEntry, ...]


@dataclass(frozen=True, slots=True)
class StickerOperationReceipt:
    operation_id: str
    catalog_revision: int
    sticker_id: str
    entry_revision: int
    status: StickerStatus


def _head(db: StoreConnection) -> int:
    row = db.execute("SELECT revision FROM sticker_catalog_head WHERE id=1").fetchone()
    if row is None or type(row[0]) is not int or row[0] < 0:
        raise OperationError("invalid_sticker_catalog")
    return row[0]


def _entry(row: StoreRow) -> StickerEntry:
    return StickerEntry(
        sticker_id=str(row["sticker_id"]), content_hash=str(row["content_hash"]),
        mime_type=str(row["mime_type"]), byte_size=int(row["byte_size"]),
        description=str(row["description"]), usage_hint=str(row["usage_hint"]),
        ocr_text=str(row["ocr_text"]),
        intent_tags=tuple(json.loads(row["intent_tags"])),
        affect_tags=tuple(json.loads(row["affect_tags"])),
        allowed_scopes=(sticker_scope_key(Scope(bot_id=str(row["bot_id"]),
                                              group_id=str(row["group_id"]))),),
        status=cast(StickerStatus, row["status"]), catalog_revision=int(row["revision"]),
    )


def _snapshot(db: StoreConnection) -> StickerListing:
    return StickerListing(_head(db), tuple(_entry(row) for row in db.execute(
        "SELECT * FROM sticker_assets ORDER BY sticker_id")))


def _image(row: StoreRow) -> StickerImage:
    data = cast(bytes, row["image_data"])
    if len(data) != row["byte_size"] or hashlib.sha256(data).hexdigest() != row["content_hash"]:
        raise OperationError("sticker_asset_corrupt")
    return StickerImage(data=data, media_type=cast(StickerMediaType, row["mime_type"]))


def _receipt(row: StoreRow) -> StickerOperationReceipt:
    return StickerOperationReceipt(str(row["operation_id"]), int(row["catalog_revision"]),
                                   str(row["sticker_id"]), int(row["entry_revision"]),
                                   cast(StickerStatus, row["status"]))


class StickerStore:
    """Own the durable assets and the stable Catalog projection/resolver.

    All administration is serialized with revocation and authorized in the
    same Store transaction. No media path, model call or sending outlet exists.
    A started commit and projection publication are drained together on cancel.
    """

    def __init__(self, store: Store, policy: Policy) -> None:
        if policy.store is not store:
            raise OperationError("sticker_owner_mismatch")
        self.store, self.policy = store, policy
        self.catalog = StickerCatalog()
        self._closed = False
        self._opened = False

    def _scope(self, scope: ConversationScope) -> None:
        if self._closed or not self._opened:
            raise OperationError("sticker_closed")
        if not isinstance(scope, Scope):
            raise OperationError("unsupported_scope")

    def _authorize(self, db: StoreConnection, actor: str, scope: Scope) -> int:
        self._scope(scope)
        return self.policy.check_transaction(db, actor, scope, "sticker.manage", "", "", False, False)

    async def open(self) -> None:
        async with self.policy.dispatch_boundary:
            if self._closed or self._opened:
                raise OperationError("sticker_lifecycle")
            snapshot = await self.store.transaction(_snapshot)
            self.catalog.restore_snapshot(snapshot.entries, revision=snapshot.revision)
            self._opened = True

    async def close(self) -> None:
        async with self.policy.dispatch_boundary:
            self._closed = True
            self._opened = False
            self.catalog.restore_snapshot((), revision=self.catalog.revision)

    async def list_entries(self, *, actor: str, scope: Scope) -> StickerListing:
        async with self.policy.dispatch_boundary:
            def read(db: StoreConnection) -> StickerListing:
                self._authorize(db, actor, scope)
                rows = db.execute("SELECT * FROM sticker_assets WHERE bot_id=? AND group_id=? "
                                  "ORDER BY sticker_id", (scope.bot_id, scope.group_id))
                return StickerListing(_head(db), tuple(_entry(row) for row in rows))
            return await self.store.transaction(read)

    async def approved_send_candidates(
        self, *, actor: str, scope: Scope, preflight_transaction: Callable[[StoreConnection], None],
    ) -> StickerListing:
        """Bounded send metadata; send authority never implies asset management."""
        self._scope(scope)
        async with self.policy.dispatch_boundary:
            def read(db: StoreConnection) -> StickerListing:
                preflight_transaction(db)
                for action in ("message.read", "message.sticker", "media.send"):
                    self.policy.check_transaction(db, actor, scope, action, "", "", False, False)
                rows = db.execute("SELECT sticker_id,bot_id,group_id,content_hash,mime_type,byte_size,"
                                  "description,usage_hint,ocr_text,intent_tags,affect_tags,status,revision "
                                  "FROM sticker_assets WHERE bot_id=? AND group_id=? "
                                  "AND status='approved' ORDER BY sticker_id LIMIT ?",
                                  (scope.bot_id, scope.group_id, MAX_CATALOG_ENTRIES))
                return StickerListing(_head(db), tuple(_entry(row) for row in rows))
            return await self.store.transaction(read)

    async def read_asset(self, *, actor: str, scope: Scope, sticker_id: str,
                         expected_revision: int) -> tuple[StickerEntry, StickerImage]:
        """Read a current managed asset, including pending assets, without decoding again."""
        try:
            _validate_id(sticker_id)
        except ValueError as exc:
            raise OperationError("invalid_sticker_identifier") from exc
        async with self.policy.dispatch_boundary:
            def read(db: StoreConnection) -> tuple[StickerEntry, StickerImage]:
                self._authorize(db, actor, scope)
                entry = self._current(db, scope, sticker_id)
                if (_head(db) != expected_revision or self.catalog.revision != expected_revision
                        or entry.status == "revoked"):
                    raise OperationError("stale_sticker_asset")
                row = db.execute("SELECT * FROM sticker_assets WHERE sticker_id=?",
                                 (sticker_id,)).fetchone()
                return entry, _image(row)
            return await self.store.transaction(read)

    def assert_description_asset_transaction(self, db: StoreConnection, *, actor: str,
                                             scope: Scope, entry: StickerEntry,
                                             expected_revision: int, image: StickerImage) -> None:
        """Management authority and source lifetime check for a manual model draft."""
        self._authorize(db, actor, scope)
        current = self._current(db, scope, entry.sticker_id)
        if (_head(db) != expected_revision or self.catalog.revision != expected_revision
                or current != entry or current.status not in {"pending", "approved"}
                or image.media_type != entry.mime_type or len(image.data) != entry.byte_size
                or hashlib.sha256(image.data).hexdigest() != entry.content_hash):
            raise OperationError("stale_sticker_asset")

    async def _mutate(self, *, actor: str, scope: Scope, operation_id: str,
                      expected_revision: int, operation: str, sticker_id: str,
                      payload: dict[str, object],
                      apply: Callable[[StoreConnection, int], StickerEntry],
                      preflight_transaction: Callable[[StoreConnection], None] | None = None,
                      ) -> StickerOperationReceipt:
        try:
            _validate_id(operation_id, "operation_id")
            _validate_id(sticker_id)
        except ValueError as exc:
            raise OperationError("invalid_sticker_identifier") from exc
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperationError("invalid_sticker_revision")
        self._scope(scope)
        digest = hashlib.sha256(json.dumps(
            {"actor": actor, "scope": scope.key, "operation": operation,
             "sticker_id": sticker_id, "expected_revision": expected_revision, "payload": payload},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        async with self.policy.dispatch_boundary:
            async def commit() -> StickerOperationReceipt:
                def write(db: StoreConnection) -> tuple[StickerOperationReceipt, StickerListing]:
                    policy_revision = self._authorize(db, actor, scope)
                    if preflight_transaction is not None:
                        preflight_transaction(db)
                    previous = db.execute("SELECT * FROM sticker_operations WHERE operation_id=?",
                                          (operation_id,)).fetchone()
                    if previous is not None:
                        if previous["digest"] != digest:
                            raise OperationError("sticker_operation_conflict")
                        return _receipt(previous), _snapshot(db)
                    revision = _head(db)
                    if revision != expected_revision:
                        raise OperationError("stale_sticker_revision")
                    result = apply(db, revision + 1)
                    db.execute("UPDATE sticker_catalog_head SET revision=? WHERE id=1", (revision + 1,))
                    db.execute("INSERT INTO sticker_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (operation_id, scope.bot_id, scope.group_id, actor, operation, digest,
                                revision + 1, sticker_id, result.catalog_revision, result.status,
                                policy_revision, time.time()))
                    db.execute("INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
                               ("sticker", operation_id, policy_revision, "sticker_" + operation,
                                json.dumps({"sticker_id": sticker_id, "catalog_revision": revision + 1,
                                            "digest": digest}, separators=(",", ":"))))
                    receipt = StickerOperationReceipt(operation_id, revision + 1, sticker_id,
                                                       result.catalog_revision, result.status)
                    return receipt, _snapshot(db)
                receipt, snapshot = await self.store.transaction(write)
                self.catalog.restore_snapshot(snapshot.entries, revision=snapshot.revision)
                return receipt
            return await drain_on_cancel(asyncio.create_task(commit()))

    async def import_asset(self, *, actor: str, scope: Scope, operation_id: str,
                           expected_revision: int, sticker_id: str, data: bytes,
                           content_type: str, metadata: StickerMetadata,
                           preflight_transaction: Callable[[StoreConnection], None] | None = None,
                           ) -> StickerOperationReceipt:
        self._scope(scope)
        if type(data) is not bytes or not data or len(data) > MAX_STICKER_BYTES:
            raise OperationError("invalid_sticker_bytes")
        if content_type not in SUPPORTED_MIME_TYPES:
            raise OperationError("invalid_sticker_media_type")
        dimensions = (await asyncio.to_thread(_decode_sticker_gif, data)
                      if content_type == "image/gif" else
                      _decode_image(data, content_type, VisualLimits(max_bytes=MAX_STICKER_BYTES)))
        if isinstance(dimensions, str):
            raise OperationError("invalid_sticker_media_" + dimensions)
        width, height = dimensions
        try:
            entry = StickerEntry(sticker_id=sticker_id, content_hash=hashlib.sha256(data).hexdigest(),
                                 mime_type=content_type, byte_size=len(data),
                                 allowed_scopes=(sticker_scope_key(scope),), status="pending",
                                 **asdict(metadata))
        except (ValueError, TypeError) as exc:
            raise OperationError("invalid_sticker_input") from exc
        def apply(db: StoreConnection, revision: int) -> StickerEntry:
            old = db.execute("SELECT bot_id,group_id FROM sticker_assets WHERE sticker_id=?",
                             (sticker_id,)).fetchone()
            if old is not None:
                if tuple(old) != (scope.bot_id, scope.group_id):
                    raise OperationError("sticker_scope_mismatch")
                raise OperationError("sticker_id_already_exists")
            if db.execute("SELECT count(*) FROM sticker_assets").fetchone()[0] >= MAX_CATALOG_ENTRIES:
                raise OperationError("sticker_catalog_full")
            result = replace(entry, catalog_revision=revision)
            db.execute("INSERT INTO sticker_assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (sticker_id, scope.bot_id, scope.group_id, entry.content_hash, entry.mime_type,
                        entry.byte_size, width, height, data, entry.description, entry.usage_hint,
                        entry.ocr_text, json.dumps(entry.intent_tags), json.dumps(entry.affect_tags),
                        result.status, revision))
            return result
        return await self._mutate(actor=actor, scope=scope, operation_id=operation_id,
                                  expected_revision=expected_revision, operation="imported",
                                  sticker_id=sticker_id,
                                  payload={"hash": entry.content_hash, "mime": content_type,
                                           "width": width, "height": height, **asdict(metadata)},
                                  apply=apply, preflight_transaction=preflight_transaction)

    @staticmethod
    def _current(db: StoreConnection, scope: Scope, sticker_id: str) -> StickerEntry:
        row = db.execute("SELECT * FROM sticker_assets WHERE sticker_id=?", (sticker_id,)).fetchone()
        if row is None:
            raise OperationError("unknown_sticker")
        if (row["bot_id"], row["group_id"]) != (scope.bot_id, scope.group_id):
            raise OperationError("sticker_scope_mismatch")
        return _entry(row)

    async def _change(self, *, actor: str, scope: Scope, operation_id: str,
                      expected_revision: int, sticker_id: str,
                      operation: Literal["approved", "revoked", "updated"],
                      metadata: StickerMetadata | None = None) -> StickerOperationReceipt:
        def apply(db: StoreConnection, revision: int) -> StickerEntry:
            old = self._current(db, scope, sticker_id)
            if old.status == "revoked" or (operation == "approved" and old.status != "pending"):
                raise OperationError("invalid_sticker_state")
            changed = {} if metadata is None else asdict(metadata)
            status: StickerStatus = old.status if operation == "updated" else operation
            try:
                entry = replace(old, status=status, catalog_revision=revision, **changed)
            except (ValueError, TypeError) as exc:
                raise OperationError("invalid_sticker_input") from exc
            db.execute("UPDATE sticker_assets SET description=?,usage_hint=?,ocr_text=?,intent_tags=?,"
                       "affect_tags=?,status=?,revision=? WHERE sticker_id=?",
                       (entry.description, entry.usage_hint, entry.ocr_text, json.dumps(entry.intent_tags),
                        json.dumps(entry.affect_tags), status, revision, sticker_id))
            return entry
        return await self._mutate(actor=actor, scope=scope, operation_id=operation_id,
                                  expected_revision=expected_revision, operation=operation,
                                  sticker_id=sticker_id,
                                  payload={} if metadata is None else asdict(metadata), apply=apply)

    async def approve(self, *, actor: str, scope: Scope, operation_id: str,
                      expected_revision: int, sticker_id: str) -> StickerOperationReceipt:
        return await self._change(actor=actor, scope=scope, operation_id=operation_id,
                                  expected_revision=expected_revision, sticker_id=sticker_id,
                                  operation="approved")

    async def revoke(self, *, actor: str, scope: Scope, operation_id: str,
                     expected_revision: int, sticker_id: str) -> StickerOperationReceipt:
        return await self._change(actor=actor, scope=scope, operation_id=operation_id,
                                  expected_revision=expected_revision, sticker_id=sticker_id,
                                  operation="revoked")

    async def update(self, *, actor: str, scope: Scope, operation_id: str,
                     expected_revision: int, sticker_id: str,
                     metadata: StickerMetadata) -> StickerOperationReceipt:
        return await self._change(actor=actor, scope=scope, operation_id=operation_id,
                                  expected_revision=expected_revision, sticker_id=sticker_id,
                                  operation="updated", metadata=metadata)

    def assert_current_asset_transaction(self, db: StoreConnection, *, scope: Scope,
                                         entry: StickerEntry, expected_revision: int,
                                         asset: ResolvedStickerAsset) -> None:
        """Current send preflight; sender authority remains solely with Actions."""
        self._scope(scope)
        current = self._current(db, scope, entry.sticker_id)
        if (_head(db) != expected_revision or self.catalog.revision != expected_revision
                or current != entry or current.status != "approved"
                or asset.sticker_id != entry.sticker_id or asset.catalog_revision != entry.catalog_revision
                or asset.image.media_type != entry.mime_type or len(asset.image.data) != entry.byte_size
                or hashlib.sha256(asset.image.data).hexdigest() != entry.content_hash):
            raise OperationError("stale_sticker_asset")

    async def resolve(self, sticker_id: str, catalog_revision: int) -> ResolvedStickerAsset | None:
        async with self.policy.dispatch_boundary:
            if self._closed or not self._opened:
                raise OperationError("sticker_closed")
            def read(db: StoreConnection) -> ResolvedStickerAsset | None:
                row = db.execute("SELECT * FROM sticker_assets WHERE sticker_id=?", (sticker_id,)).fetchone()
                if row is None or row["status"] != "approved" or row["revision"] != catalog_revision:
                    return None
                return ResolvedStickerAsset(sticker_id, catalog_revision, _image(row))
            return await self.store.transaction(read)
