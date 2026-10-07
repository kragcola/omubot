"""Finite current-message URL titles through the existing Tools/Actions owner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import JsonValue

from .actions import Actions
from .rich_messages import TextSegment
from .runtime import DecisionBinding, Turn
from .store import StoreConnection, request_digest
from .tools import Tools
from .types import ActionCall, ConversationScope, Event, OperationError
from .video_refs import parse_video_text

UrlTitleStatus = Literal[
    "available", "absent", "unavailable", "timeout", "budget_exhausted", "already_attempted",
]
_URLS = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
# Unsupported platform links remain in the video lane; Generic is not its fallback.
_VIDEO_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
    "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv",
})


@dataclass(frozen=True, slots=True)
class UrlTitleRef:
    url: str
    segment_index: int
    url_position: int


@dataclass(frozen=True, slots=True)
class UrlTitle:
    ref: UrlTitleRef
    status: UrlTitleStatus
    title: str | None = None
    untrusted: Literal[True] = True
    endpoint: str | None = None
    action_key: str | None = None


@dataclass(frozen=True, slots=True)
class UrlTitleBatch:
    event_id: str
    user_id: str
    scope: ConversationScope
    source_digest: str
    binding: DecisionBinding
    enabled: bool
    results: tuple[UrlTitle, ...]


_AUTO_BLOCKED_HOST_LABELS = frozenset({
    "admin", "auth", "bank", "banking", "finance", "login", "pay", "payment", "wallet",
})
_AUTO_PRIVATE_SUFFIXES = (".corp", ".home.arpa", ".internal", ".lan", ".local")



def _direct_refs(event: Event) -> Iterable[UrlTitleRef]:
    segments = event.rich_segments or (TextSegment(event.text),)
    for index, segment in enumerate(segments):
        if not isinstance(segment, TextSegment):
            continue
        for match in _URLS.finditer(segment.text):
            value = match.group().rstrip("。，、！？.,!?)］】")
            if len(value) > 2048:
                continue
            try:
                host = urlsplit(value).hostname
            except ValueError:
                # The existing WebFetch boundary will reject this candidate.
                host = None
            if parse_video_text(value) or host in _VIDEO_HOSTS:
                continue
            if host is not None:
                hostname = host.casefold().rstrip(".")
                if (hostname == "localhost" or hostname.endswith(_AUTO_PRIVATE_SUFFIXES)
                        or set(hostname.split(".")) & _AUTO_BLOCKED_HOST_LABELS):
                    continue
            yield UrlTitleRef(value, index, match.start())


class UrlTitleRunner:
    """Default-off attachment producer; no client, cache or background lifecycle."""

    def __init__(self, actions: Actions, tools: Tools, *, bot_id: str,
                 enabled: bool = False, allowed_groups: Iterable[str] = ()) -> None:
        self.actions, self.tools, self.bot_id = actions, tools, bot_id
        self.enabled, self.allowed_groups = enabled, frozenset(allowed_groups)

    def _active(self, event: Event) -> bool:
        return bool(self.enabled and event.scope.kind == "group"
                    and event.scope.bot_id == self.bot_id
                    and event.scope.group_id in self.allowed_groups)

    @staticmethod
    def _check_source(
        event: Event, digest: str, binding: DecisionBinding, turn: Turn,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
    ) -> None:
        turn.check()
        if (binding.turn_id != turn.id or binding.generation != turn.generation
                or current_binding() != binding or request_digest(event) != digest):
            raise OperationError("url_title_source_changed")
        source_preflight()

    def assert_current_transaction(
        self, db: StoreConnection, batch: UrlTitleBatch, *, event: Event, turn: Turn,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
        source_preflight_transaction: Callable[[StoreConnection], None],
    ) -> None:
        """Current source/exact fetch grants; consumer also gates its Model destination.

        Pass the original owner callbacks, including the original observed-time TTL.
        This method does not mint or refresh a source, URL permission or upload grant.
        """
        if (batch.event_id != event.event_id or batch.user_id != event.user_id
                or batch.scope != event.scope or batch.enabled != self._active(event)):
            raise OperationError("url_title_source_changed")
        self._check_source(event, batch.source_digest, batch.binding, turn,
                           current_binding, source_preflight)
        source_preflight_transaction(db)
        self.actions.policy.check_transaction(db, batch.user_id, batch.scope, "message.read",
                                              "", "", False, False)
        for result in batch.results:
            if result.endpoint is not None:
                self.actions.policy.check_transaction(
                    db, batch.user_id, batch.scope, "tool.invoke:web.fetch",
                    result.endpoint, "web.fetch", False, False,
                )

    async def enrich(
        self, event: Event, *, turn: Turn, binding: DecisionBinding,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
        source_preflight_transaction: Callable[[StoreConnection], None],
        capabilities: frozenset[str], deadline: float,
    ) -> UrlTitleBatch:
        """Use the caller's one absolute round deadline, including intent/queue time."""
        digest = request_digest(event)

        def check() -> None:
            self._check_source(event, digest, binding, turn, current_binding, source_preflight)

        check()
        active = self._active(event)
        results: list[UrlTitle] = []
        seen: set[str] = set()
        if active:
            for ref in _direct_refs(event):
                check()
                args: dict[str, JsonValue] = {"url": ref.url}
                try:
                    spec = self.tools.spec("web.fetch")
                    endpoint = self.tools.destination("web.fetch", args)
                except OperationError as exc:
                    if exc.code not in {"unknown_tool", "web_fetch_unavailable",
                                        "web_fetch_invalid_url", "web_fetch_host_denied"}:
                        raise
                    endpoint = None
                    spec = None
                identity = endpoint if endpoint is not None else ref.url
                if identity in seen:
                    continue
                seen.add(identity)
                if len(seen) > 3:
                    break
                if endpoint is None or spec is None or not spec.requested_capabilities <= capabilities:
                    results.append(UrlTitle(ref, "unavailable"))
                    continue
                if monotonic() >= deadline:
                    results.append(UrlTitle(ref, "budget_exhausted", endpoint=endpoint))
                    continue
                payload = json.dumps({
                    "event": digest, "scope": event.scope.model_dump(), "user": event.user_id,
                    "binding": binding.model_dump(), "ref": [ref.url, ref.segment_index, ref.url_position],
                    "phase": "url_title", "arguments": args, "endpoint": endpoint,
                    "tool": [spec.id, spec.version, spec.api_version],
                    "requested_capabilities": sorted(spec.requested_capabilities),
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                identity_json = json.dumps([event.scope.model_dump(), event.event_id, digest,
                                            ref.segment_index, ref.url_position, endpoint], sort_keys=True)
                key = "url-title:" + hashlib.sha256(identity_json.encode()).hexdigest()
                call = ActionCall(key, event.event_id, event.user_id, event.scope,
                                  "tool.invoke:web.fetch", hashlib.sha256(payload.encode()).hexdigest(),
                                  provider=endpoint, model="web.fetch")

                def transaction(db: StoreConnection) -> None:
                    check()
                    source_preflight_transaction(db)

                def postflight(db: StoreConnection, frozen_call: ActionCall = call) -> None:
                    transaction(db)
                    for action in ("message.read", "tool.invoke:web.fetch"):
                        self.actions.policy.check_transaction(
                            db, frozen_call.subject, frozen_call.scope, action,
                            frozen_call.provider, frozen_call.model, False, False,
                        )

                async def operation(
                    arguments: dict[str, JsonValue] = args, frozen_ref: UrlTitleRef = ref,
                    frozen_endpoint: str = endpoint, frozen_key: str = key,
                    verify: Callable[[StoreConnection], None] = postflight,
                ) -> UrlTitle:
                    async with asyncio.timeout_at(deadline):
                        # Recheck after durable intent, immediately before the actual tool callback.
                        await self.actions.store.transaction(verify)
                        check()
                        output = await self.tools.invoke("web.fetch", arguments, capabilities=capabilities)
                        check()
                        await self.actions.store.transaction(verify)
                        check()
                        if output["url"] != frozen_endpoint or output["untrusted"] is not True:
                            raise OperationError("url_title_protocol")
                        title = " ".join(cast(str, output["title"]).split())[:120]
                        return UrlTitle(frozen_ref, "available" if title else "absent", title or None,
                                        endpoint=frozen_endpoint, action_key=frozen_key)

                try:
                    result = await self.actions.execute(
                        call, operation, external=True, turn=turn, before_intent=check,
                        before_operation=check, preflight_transaction=transaction,
                        timeout=max(0.0, deadline - monotonic()),
                    )
                except TimeoutError:
                    if monotonic() < deadline:
                        raise
                    check()
                    await self.actions.store.transaction(postflight)
                    check()
                    results.append(UrlTitle(ref, "timeout", endpoint=endpoint, action_key=key))
                    continue
                except OperationError as exc:
                    check()
                    if exc.code == "duplicate":
                        status: UrlTitleStatus = "already_attempted"
                    elif exc.code in {"web_fetch_timeout", "tool_timeout"}:
                        status = "timeout"
                    elif (exc.code in {"web_fetch_dns_failed", "web_fetch_transport_failed"}
                          or exc.code.startswith("web_fetch_http_")
                          and exc.code.removeprefix("web_fetch_http_").isdecimal()):
                        status = "unavailable"
                    else:
                        raise
                    await self.actions.store.transaction(postflight)
                    check()
                    results.append(UrlTitle(ref, status, endpoint=endpoint, action_key=key))
                    if status == "already_attempted":
                        break
                    continue
                check()
                results.append(result)
        check()
        batch = UrlTitleBatch(event.event_id, event.user_id, event.scope, digest, binding,
                              active, tuple(results))
        if active:
            await self.actions.store.transaction(lambda db: self.assert_current_transaction(
                db, batch, event=event, turn=turn, current_binding=current_binding,
                source_preflight=source_preflight,
                source_preflight_transaction=source_preflight_transaction,
            ))
            check()
        return batch
