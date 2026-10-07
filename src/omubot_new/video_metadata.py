"""Current-event video metadata through the existing Tools/Actions HTTP owner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from time import monotonic
from typing import Literal, cast
from urllib.parse import urlencode, urljoin

from pydantic import JsonValue

from .actions import Actions
from .bilibili_sources import (
    bili_endpoint,
    decode_bili_metadata,
    extended_bilibili_text,
    extended_bilibili_url,
    resolve_bangumi_video,
)
from .rich_messages import JsonSegment, TextSegment, VideoRef
from .runtime import DecisionBinding, Turn
from .store import StoreConnection, request_digest
from .tools import Tools
from .types import ActionCall, ConversationScope, Event, OperationError
from .video_refs import parse_video_text

MetadataStatus = Literal[
    "available", "absent", "user_supplied_card_metadata", "unsupported",
    "unavailable", "timeout", "budget_exhausted", "already_attempted",
]


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    ref: VideoRef
    status: MetadataStatus
    title: str | None = None
    partial: bool = False
    untrusted: Literal[True] = True
    endpoint: str | None = None
    action_key: str | None = None
    reads: tuple[tuple[str, str], ...] = ()
    source_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class QuotedVideoSource:
    """Finite refs from one actual retained human entry, without copied body."""

    scope: ConversationScope
    event_id: str
    message_id: str
    author_id: str
    source_digest: str
    refs: tuple[VideoRef, ...]
    expires_at: float

    def __post_init__(self) -> None:
        if (self.scope.kind != "group" or self.author_id == self.scope.bot_id
                or not 1 <= len(self.refs) <= 2 or type(self.refs) is not tuple
                or not math.isfinite(self.expires_at)
                or any(not value or value != value.strip()
                       for value in (self.event_id, self.message_id, self.author_id, self.source_digest))):
            raise ValueError("quoted video requires one exact retained human source")

    def assert_current(self, event: Event) -> None:
        if (event.scope != self.scope or event.reply_to != self.message_id
                or monotonic() >= self.expires_at):
            raise OperationError("video_metadata_quoted_source_changed")


@dataclass(frozen=True, slots=True)
class VideoMetadataBatch:
    event_id: str
    user_id: str
    scope: ConversationScope
    source_digest: str
    binding: DecisionBinding
    enabled: bool
    results: tuple[VideoMetadata, ...]
    quoted_source: QuotedVideoSource | None = None

    @property
    def history_subjects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(result.source_user_id for result in self.results
                                   if result.source_user_id is not None))


def current_video_refs(event: Event) -> tuple[VideoRef, ...]:
    """Read direct segments only; rendered replies/forwards have different authors."""
    refs: dict[tuple[str, str], VideoRef] = {}
    segments = event.rich_segments or (TextSegment(event.text),)
    for segment in segments:
        values = ((*parse_video_text(segment.text), *extended_bilibili_text(segment.text))
                  if isinstance(segment, TextSegment)
                  else segment.video_refs if isinstance(segment, JsonSegment) else ())
        for ref in values:
            key = (ref.platform, ref.video_id)
            if key in refs:
                if ref.title and not refs[key].title:
                    refs[key] = ref
            elif len(refs) < 2:
                refs[key] = ref
    return tuple(refs.values())


def youtube_oembed_url(ref: VideoRef) -> str:
    return "https://www.youtube.com/oembed?" + urlencode({"format": "json", "url": ref.url})


def _title(output: dict[str, JsonValue], endpoint: str) -> tuple[str | None, bool]:
    # Tools owns the HttpApiOutput schema; these checks bind its semantics to this fetch.
    if output["method"] != "GET" or output["url"] != endpoint or output["untrusted"] is not True:
        raise OperationError("video_metadata_protocol")
    if output["truncated"] is True:
        raise OperationError("video_metadata_truncated")
    try:
        payload = cast(JsonValue, json.loads(cast(str, output["text"])))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise OperationError("video_metadata_protocol") from exc
    if (not isinstance(payload, dict) or payload.get("version") != "1.0"
            or payload.get("type") != "video"):
        raise OperationError("video_metadata_protocol")
    if "title" not in payload:
        return None, False
    value = payload["title"]
    if not isinstance(value, str):
        raise OperationError("video_metadata_protocol")
    title = " ".join(value.split())
    return title[:160] if title else None, len(title) > 160


class VideoMetadataRunner:
    """Default-off, finite enrichment; owns no client, background task or cache."""

    def __init__(self, actions: Actions, tools: Tools, *, bot_id: str,
                 enabled: bool = False, allowed_groups: Iterable[str] = ()) -> None:
        self.actions, self.tools, self.bot_id = actions, tools, bot_id
        self.enabled, self.allowed_groups = enabled, frozenset(allowed_groups)

    def _active(self, event: Event) -> bool:
        return bool(self.enabled and event.scope.kind == "group"
                    and event.scope.bot_id == self.bot_id
                    and event.scope.group_id in self.allowed_groups)

    def assert_current_transaction(
        self, db: StoreConnection, batch: VideoMetadataBatch, *, event: Event, turn: Turn,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
        source_preflight_transaction: Callable[[StoreConnection], None],
        quoted_source: QuotedVideoSource | None = None,
    ) -> None:
        turn.check()
        if (batch.event_id != event.event_id or batch.user_id != event.user_id
                or batch.scope != event.scope or batch.source_digest != request_digest(event)
                or batch.enabled != self._active(event) or batch.binding != current_binding()
                or batch.binding.turn_id != turn.id or batch.binding.generation != turn.generation
                or batch.quoted_source != quoted_source):
            raise OperationError("video_metadata_source_changed")
        source_preflight()
        source_preflight_transaction(db)
        if quoted_source is not None:
            quoted_source.assert_current(event)
            row = db.execute("SELECT digest FROM requests WHERE id=?", (quoted_source.event_id,)).fetchone()
            if row is not None and row["digest"] != quoted_source.source_digest:
                raise OperationError("video_metadata_quoted_source_changed")
            self.actions.policy.check_transaction(db, quoted_source.author_id, event.scope,
                                                  "message.read", "", "", False, False)
        self.actions.policy.check_transaction(db, event.user_id, event.scope, "message.read",
                                              "", "", False, False)
        for result in batch.results:
            if result.reads:
                for endpoint, tool in result.reads:
                    self.actions.policy.check_transaction(
                        db, event.user_id, event.scope, "tool.invoke:http.get", endpoint, tool,
                        result.source_user_id is not None, False)
                    if result.source_user_id is not None:
                        self.actions.policy.check_transaction(
                            db, result.source_user_id, event.scope, "tool.invoke:http.get", endpoint, tool,
                            True, False)
            elif result.endpoint is not None:
                self.actions.policy.check_transaction(db, event.user_id, event.scope, "tool.invoke:http.get",
                                                      result.endpoint, "http.get", False, False)

    async def enrich(
        self, event: Event, *, turn: Turn, binding: DecisionBinding,
        current_binding: Callable[[], DecisionBinding], source_preflight: Callable[[], None],
        source_preflight_transaction: Callable[[StoreConnection], None],
        capabilities: frozenset[str], deadline: float | None = None,
        quoted_source: QuotedVideoSource | None = None,
    ) -> VideoMetadataBatch:
        deadline = monotonic() + 0.5 if deadline is None else deadline
        digest = request_digest(event)

        def check() -> None:
            turn.check()
            if (binding.turn_id != turn.id or binding.generation != turn.generation
                    or current_binding() != binding):
                raise OperationError("video_metadata_source_changed")
            source_preflight()
            if quoted_source is not None:
                quoted_source.assert_current(event)

        check()
        active = self._active(event)
        results: list[VideoMetadata] = []
        if active:
            direct = current_video_refs(event)
            refs: list[tuple[VideoRef, QuotedVideoSource | None]] = [(ref, None) for ref in direct]
            if quoted_source is not None:
                seen = {(ref.platform, ref.video_id) for ref in direct}
                refs.extend((ref, quoted_source) for ref in quoted_source.refs
                            if (ref.platform, ref.video_id) not in seen)
            for ordinal, (ref, quote) in enumerate(refs[:2]):
                check()
                if ref.platform == "bilibili":
                    try:
                        self.tools.spec("video.get")
                    except OperationError as exc:
                        if exc.code != "unknown_tool":
                            raise
                        result = (VideoMetadata(ref, "user_supplied_card_metadata", ref.title)
                                  if ref.title else VideoMetadata(ref, "unsupported"))
                        results.append(replace(result, source_user_id=quote.author_id) if quote else result)
                        continue
                    result = await self._bilibili(
                        ref, event, ordinal=ordinal, digest=digest, turn=turn, binding=binding,
                        check=check, source_transaction=source_preflight_transaction,
                        capabilities=capabilities, deadline=deadline,
                        quote=quote,
                    )
                    results.append(replace(result, source_user_id=quote.author_id) if quote else result)
                    if result.status == "already_attempted":
                        break
                    continue
                if quote is not None:
                    # The quoted-card contract is finite Bilibili metadata;
                    # no arbitrary/YouTube display content is promoted here.
                    results.append(VideoMetadata(ref, "unsupported", source_user_id=quote.author_id))
                    continue
                remaining = deadline - monotonic()
                if remaining <= 0:
                    results.append(VideoMetadata(ref, "budget_exhausted"))
                    continue
                args: dict[str, JsonValue] = {
                    "url": youtube_oembed_url(ref), "headers": {"Accept": "application/json"},
                }
                spec = self.tools.spec("http.get")
                endpoint = self.tools.destination("http.get", args)
                if endpoint is None:
                    raise OperationError("video_metadata_missing_destination")
                payload = json.dumps({
                    "event": digest, "scope": event.scope.model_dump(), "user": event.user_id,
                    "binding": binding.model_dump(), "ref": [ref.platform, ref.video_id, ref.url],
                    "ordinal": ordinal, "phase": "video_metadata", "arguments": args,
                    "endpoint": endpoint,
                    "tool": [spec.id, spec.version, spec.api_version],
                    "requested_capabilities": sorted(spec.requested_capabilities),
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                identity = json.dumps([event.scope.model_dump(), event.event_id, digest,
                                       ordinal, ref.platform, ref.video_id], sort_keys=True)
                key = "video-metadata:" + hashlib.sha256(identity.encode()).hexdigest()
                call = ActionCall(key, event.event_id, event.user_id, event.scope,
                                  "tool.invoke:http.get", hashlib.sha256(payload.encode()).hexdigest(),
                                  provider=endpoint, model="http.get")

                def transaction(db: StoreConnection) -> None:
                    check()
                    source_preflight_transaction(db)

                def postflight(db: StoreConnection, frozen_call: ActionCall = call) -> None:
                    transaction(db)
                    for action in ("message.read", "tool.invoke:http.get"):
                        self.actions.policy.check_transaction(
                            db, frozen_call.subject, frozen_call.scope, action,
                            frozen_call.provider, frozen_call.model, False, False,
                        )

                async def operation(
                    arguments: dict[str, JsonValue] = args, frozen_ref: VideoRef = ref,
                    frozen_endpoint: str = endpoint, frozen_key: str = key,
                    verify: Callable[[StoreConnection], None] = postflight,
                ) -> VideoMetadata:
                    # Intent/queue time is part of this round's absolute budget, too.
                    async with asyncio.timeout_at(deadline):
                        await self.actions.store.transaction(verify)
                        check()
                        output = await self.tools.invoke("http.get", arguments,
                                                         capabilities=capabilities)
                        check()
                        await self.actions.store.transaction(verify)
                        check()
                        if not 200 <= cast(int, output["status_code"]) < 300:
                            return VideoMetadata(frozen_ref, "unavailable", endpoint=frozen_endpoint,
                                                 action_key=frozen_key)
                        title, partial = _title(output, frozen_endpoint)
                        return VideoMetadata(
                            frozen_ref, "available" if title is not None else "absent",
                            title, partial, endpoint=frozen_endpoint, action_key=frozen_key,
                        )

                try:
                    result = await self.actions.execute(
                        call, operation, external=True, turn=turn, before_intent=check,
                        before_operation=check, preflight_transaction=transaction,
                        timeout=max(0.0, deadline - monotonic()),
                    )
                except TimeoutError:
                    if monotonic() < deadline:
                        # A cleanup/provider TimeoutError is not our local budget expiry.
                        raise
                    check()
                    await self.actions.store.transaction(postflight)
                    check()
                    results.append(VideoMetadata(ref, "timeout", endpoint=endpoint, action_key=key))
                    continue
                except OperationError as exc:
                    check()
                    if exc.code == "duplicate":
                        status: MetadataStatus = "already_attempted"
                    elif exc.code == "http_api_timeout":
                        status = "timeout"
                    elif (exc.code == "http_api_transport_failed"
                          or exc.code.startswith("http_api_http_")
                          and exc.code.removeprefix("http_api_http_").isdecimal()):
                        status = "unavailable"
                    else:
                        raise
                    await self.actions.store.transaction(postflight)
                    check()
                    results.append(VideoMetadata(ref, status, endpoint=endpoint, action_key=key))
                    if status == "already_attempted":
                        # A repeated round cannot resume the tail of an uncertain round.
                        break
                    continue
                check()
                results.append(result)
        check()
        return VideoMetadataBatch(event.event_id, event.user_id, event.scope, digest, binding,
                                  active, tuple(results), quoted_source)

    async def _bilibili(
        self, original: VideoRef, event: Event, *, ordinal: int, digest: str, turn: Turn,
        binding: DecisionBinding, check: Callable[[], None],
        source_transaction: Callable[[StoreConnection], None], capabilities: frozenset[str],
        deadline: float,
        quote: QuotedVideoSource | None = None,
    ) -> VideoMetadata:
        ref = original
        reads: list[tuple[str, str]] = []
        visited: set[str] = set()
        for step in range(4):
            check()
            if monotonic() >= deadline:
                return VideoMetadata(original, "budget_exhausted", reads=tuple(reads))
            args: dict[str, JsonValue] = {
                "url": bili_endpoint(ref), "headers": {"Accept": "application/json"},
            }
            destination = self.tools.destination("video.get", args)
            if destination is None:
                raise OperationError("video_metadata_missing_destination")
            endpoint = destination
            if endpoint in visited:
                return VideoMetadata(original, "unavailable", reads=tuple(reads))
            visited.add(endpoint)
            spec = self.tools.spec("video.get")
            payload = json.dumps({
                "purpose": "video_metadata", "source": digest, "binding": binding.model_dump(),
                "quoted_source": ([quote.event_id, quote.message_id, quote.author_id,
                                   quote.source_digest, quote.expires_at] if quote else None),
                "original": [original.video_id, original.url, original.reference_kind],
                "step": step, "args": args, "endpoint": endpoint,
                "tool": [spec.id, spec.version, spec.api_version],
                "capabilities": sorted(spec.requested_capabilities),
            }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            key = "video-metadata:" + hashlib.sha256(json.dumps(
                [event.scope.model_dump(), event.event_id, digest, ordinal, original.video_id, step],
                sort_keys=True).encode()).hexdigest()
            call = ActionCall(key, event.event_id, event.user_id, event.scope, "tool.invoke:http.get",
                              hashlib.sha256(payload.encode()).hexdigest(),
                              provider=endpoint, model="video.get", includes_history=quote is not None,
                              history_subjects=(quote.author_id,) if quote else ())

            def transaction(db: StoreConnection, *, frozen_endpoint: str = endpoint,
                            frozen_reads: tuple[tuple[str, str], ...] = tuple(reads)) -> None:
                check()
                source_transaction(db)
                if quote is not None:
                    quote.assert_current(event)
                    row = db.execute("SELECT digest FROM requests WHERE id=?", (quote.event_id,)).fetchone()
                    if row is not None and row["digest"] != quote.source_digest:
                        raise OperationError("video_metadata_quoted_source_changed")
                    self.actions.policy.check_transaction(db, quote.author_id, event.scope,
                                                          "message.read", "", "", False, False)
                self.actions.policy.check_transaction(db, event.user_id, event.scope, "message.read",
                                                      "", "", False, False)
                for url, tool in (*frozen_reads, (frozen_endpoint, "video.get")):
                    self.actions.policy.check_transaction(db, event.user_id, event.scope,
                                                          "tool.invoke:http.get", url, tool,
                                                          quote is not None, False)
                    if quote is not None:
                        self.actions.policy.check_transaction(db, quote.author_id, event.scope,
                                                              "tool.invoke:http.get", url, tool, True, False)

            async def operation(
                arguments: dict[str, JsonValue] = args,
                verify: Callable[[StoreConnection], None] = transaction,
            ) -> dict[str, JsonValue]:
                async with asyncio.timeout_at(deadline):
                    await self.actions.store.transaction(verify)
                    check()
                    output = await self.tools.invoke("video.get", arguments, capabilities=capabilities)
                    check()
                    await self.actions.store.transaction(verify)
                    return output

            status: MetadataStatus = "unavailable"
            try:
                output = await self.actions.execute(
                    call, operation, external=True, turn=turn, before_intent=check,
                    before_operation=check, preflight_transaction=transaction,
                    timeout=max(0.0, deadline - monotonic()),
                )
            except TimeoutError:
                if monotonic() < deadline:
                    raise
                status = "timeout"
                output = None
            except OperationError as exc:
                if exc.code == "duplicate":
                    status = "already_attempted"
                elif exc.code == "http_api_timeout":
                    status = "timeout"
                elif (exc.code in {"http_api_transport_failed", "video_step_location_missing"}
                      or exc.code.startswith("http_api_http_")):
                    status = "unavailable"
                else:
                    raise
                output = None
            check()
            await self.actions.store.transaction(transaction)
            reads.append((endpoint, "video.get"))
            if output is None:
                return VideoMetadata(original, status, endpoint=endpoint, action_key=key, reads=tuple(reads))
            if (output["method"] != "GET" or output["url"] != endpoint
                    or output["untrusted"] is not True or output["truncated"] is True):
                raise OperationError("video_metadata_protocol")
            if 300 <= cast(int, output["status_code"]) < 400:
                location = output["location"]
                if not isinstance(location, str) or not location:
                    return VideoMetadata(original, "unavailable", endpoint=endpoint,
                                         action_key=key, reads=tuple(reads))
                next_ref = extended_bilibili_url(urljoin(endpoint, location), title=original.title, card=True)
                if next_ref is None:
                    return VideoMetadata(original, "unsupported", endpoint=endpoint,
                                         action_key=key, reads=tuple(reads))
                ref = next_ref
                continue
            if ref.reference_kind in {"short", "qqdoc"}:
                # No Location means no inferred redirect, HTML/script scraping or title guessing.
                return VideoMetadata(original, "absent", endpoint=endpoint,
                                     action_key=key, reads=tuple(reads))
            try:
                if ref.reference_kind in {"episode", "season"}:
                    resolved = resolve_bangumi_video(ref, cast(str, output["text"]))
                    if resolved is None:
                        return VideoMetadata(original, "absent", endpoint=endpoint,
                                             action_key=key, reads=tuple(reads))
                    ref = resolved
                    continue
                title, partial = decode_bili_metadata(ref, cast(str, output["text"]))
            except OperationError as exc:
                if exc.code != "video_metadata_unavailable":
                    raise
                return VideoMetadata(original, "unavailable", endpoint=endpoint,
                                     action_key=key, reads=tuple(reads))
            return VideoMetadata(original, "available" if title else "absent", title, partial,
                                 endpoint=endpoint, action_key=key, reads=tuple(reads))
        return replace(VideoMetadata(original, "budget_exhausted"), reads=tuple(reads))
