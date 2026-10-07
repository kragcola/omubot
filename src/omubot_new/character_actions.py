"""Current-image recognition through the one durable, revocable Actions exit."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TypeVar

from .actions import Actions
from .character_assets import CharacterAssetOwner, CharacterAssetReceipt
from .character_pack_client import CcipPackClient, CharacterPackBuildRequest, prepare_character_batch
from .character_recognition import (
    AnimeTraceCandidate,
    AnimeTraceClient,
    CcipEmbedding,
    CharacterRecognition,
    CharacterRecognitionService,
)
from .character_reference import CharacterReferenceOwner
from .runtime import Turn
from .store import request_digest
from .types import ActionCall, Event, OperationError, Scope
from .visual_transport import ImageBytes

T = TypeVar("T")


class CharacterActions:
    """Bind direct current pixels, author, request, destination and operation intent.

    Reply-image attribution is deliberately unavailable until a trusted original
    author can be carried by the media owner. Results remain application reference
    identities or AnimeTrace candidates; this owner performs no fact writes.
    """

    def __init__(self, actions: Actions, *, recognition: CharacterRecognitionService | None = None,
                 animetrace: AnimeTraceClient | None = None, pack_client: CcipPackClient | None = None,
                 pack_assets: CharacterAssetOwner | None = None,
                 reference_owner: CharacterReferenceOwner | None = None) -> None:
        self.actions, self.recognition, self.animetrace = actions, recognition, animetrace
        self.pack_client, self.pack_assets, self.reference_owner = pack_client, pack_assets, reference_owner

    async def build_reference_pack(
        self, *, actor: str, scope: Scope, request_id: str, request: CharacterPackBuildRequest,
        images: dict[str, bytes], expected_revision: str | None,
    ) -> CharacterAssetReceipt:
        """Explicit admin caller: reviewed public inputs -> Actions -> verified saved artifact.

        Public-source review is required independently of the exact upload grant.
        No QQ image attribution, per-bot relation or model permission is reused.
        The caller authenticates management access; Policy owns service upload.
        """
        client, assets, reference_owner = self.pack_client, self.pack_assets, self.reference_owner
        if client is None or not client.available or assets is None or reference_owner is None:
            raise OperationError("character_service_unavailable")
        if scope.bot_id != reference_owner.bot_id:
            raise OperationError("character_reference_bot_mismatch")
        reference = await asyncio.to_thread(reference_owner.read)
        batch = await asyncio.to_thread(prepare_character_batch, request, images, reference, actor=actor)
        existing = await asyncio.to_thread(assets.reuse, request_sha256=batch.request_sha256,
            reference_owner=reference_owner, reference_revision=batch.reference_revision,
            expected_revision=expected_revision)
        if existing is not None:
            return existing
        destination = client.destination
        assert destination is not None
        call = ActionCall(key=f"{request_id}:character-build:{batch.request_sha256[:24]}",
            request_id=request_id, subject=actor, scope=scope, action="tool.invoke:http.post",
            payload_hash=batch.request_sha256, provider=destination, model="ccip.build-series-pack",
            includes_images=True, image_subjects=(actor,))

        def current_reference(_: object) -> None:
            current = reference_owner.read()
            if current is None or current.revision != batch.reference_revision:
                raise OperationError("revision_conflict")

        artifact = await self.actions.execute(call, lambda: client.build(batch), external=True,
            preflight_transaction=current_reference, timeout=client.timeout)
        return await assets.install_async(artifact, reference_owner=reference_owner,
                                          expected_revision=expected_revision)

    async def _execute(
        self, *, event: Event, image: ImageBytes, turn: Turn, action: str,
        destination: str, model: str, parameters: dict[str, object],
        operation: Callable[[], Awaitable[T]], timeout: float,  # noqa: ASYNC109 - Actions owns this budget
    ) -> tuple[T, str]:
        payload = json.dumps({"event": request_digest(event), "owner": image.owner.model_dump(mode="json"),
                              "image_sha256": hashlib.sha256(image.data).hexdigest(),
                              "parameters": parameters}, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        key = f"{event.event_id}:character:{action.removeprefix('tool.invoke:')}:{digest[:24]}"
        call = ActionCall(key=key,
                          request_id=event.event_id, subject=event.user_id, scope=event.scope,
                          action=action, payload_hash=digest, provider=destination, model=model,
                          includes_images=True, image_subjects=(event.user_id,))
        result = await self.actions.execute(call, operation, external=True, turn=turn, timeout=timeout,
                                            source_event=event, image_owner=image.owner)
        return result, key

    async def recognize(
        self, *, event: Event, image: ImageBytes, turn: Turn, multi: bool = True,
        timeout: float = 10,  # noqa: ASYNC109 - Actions owns this budget
    ) -> CharacterRecognition:
        service = self.recognition
        if service is None or not service.available:
            raise OperationError("character_service_unavailable")
        destination = service.identify_destination(multi=multi)
        assert destination is not None
        result, key = await self._execute(event=event, image=image, turn=turn,
            action="tool.invoke:ccip.identify", destination=destination, model="ccip.identify",
            parameters={"multi": multi, "pack_revision": service.reference_revision},
            operation=lambda: service.recognize(image, multi=multi), timeout=timeout)
        return replace(result, action_key=key)

    async def embed_reference(
        self, *, event: Event, image: ImageBytes, turn: Turn,
        crop: tuple[int, int, int, int] | None = None,
        timeout: float = 10,  # noqa: ASYNC109 - Actions owns this budget
    ) -> CcipEmbedding:
        service = self.recognition
        if service is None or not service.available:
            raise OperationError("character_service_unavailable")
        destination = service.embed_destination
        assert destination is not None
        result, _ = await self._execute(event=event, image=image, turn=turn,
            action="tool.invoke:ccip.embed", destination=destination, model="ccip.embed",
            parameters={"crop": crop, "pack_revision": service.reference_revision},
            operation=lambda: service.embed_reference(image, crop=crop), timeout=timeout)
        return result

    async def identify_anime(
        self, *, event: Event, image: ImageBytes, turn: Turn,
        timeout: float = 8,  # noqa: ASYNC109 - Actions owns this budget
    ) -> tuple[AnimeTraceCandidate, ...]:
        client = self.animetrace
        if client is None or not client.available:
            raise OperationError("character_service_unavailable")
        destination, model = client.destination, client.model
        assert destination is not None and model is not None
        result, _ = await self._execute(event=event, image=image, turn=turn,
            action="tool.invoke:animetrace.identify", destination=destination, model=model,
            parameters={}, operation=lambda: client.identify(image), timeout=timeout)
        return result
