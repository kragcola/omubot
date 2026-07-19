#!/usr/bin/env python3
"""One-shot QZone live publish capture boundary.

The full CLI is intentionally assembled in TDD slices.  This module's first
slice creates a schema-validated, secret-scanned fixture from an explicitly
successful HTTP exchange without persisting request values.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import inspect
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, quote, quote_plus

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.qzone_journal.delivery import (  # noqa: E402
    BUILTIN_WIRE_PROFILE,
    DeliveryConfig,
    JournalDelivery,
)
from plugins.qzone_journal.fixture_conformance import (  # noqa: E402
    run_conformance,
    validate_fixture_dict,
)
from plugins.qzone_journal.response_parser import parse_qzone_publish_response  # noqa: E402
from plugins.qzone_journal.store import JournalStore  # noqa: E402
from plugins.qzone_journal.transport import (  # noqa: E402
    CredentialBundle,
    NapCatCredentialSource,
    QZoneRequestBuilder,
    WireProfile,
)
from plugins.qzone_journal.wire_profiles import (  # noqa: E402
    DEFAULT_ATTESTATION_SECRET_PATH,
    attestation_path_for,
    build_capture_attestation,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_LIVE_CONFIRMATION = "PUBLISH_EXISTING_APPROVED_DRAFT_ONCE"


class CapturePreflightError(RuntimeError):
    """One-shot live publication preconditions did not match exactly."""


class CaptureSecretEchoError(RuntimeError):
    """A success response echoed request-sensitive material."""


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    draft_id: str
    expected_content_sha256: str
    expected_uin_sha256: str
    profile_id: str

    def __post_init__(self) -> None:
        if not self.draft_id.strip():
            raise ValueError("draft_id is required")
        if _SHA256_RE.fullmatch(self.expected_content_sha256) is None:
            raise ValueError("expected_content_sha256 must be lowercase SHA-256")
        if _SHA256_RE.fullmatch(self.expected_uin_sha256) is None:
            raise ValueError("expected_uin_sha256 must be lowercase SHA-256")
        if _PROFILE_ID_RE.fullmatch(self.profile_id) is None:
            raise ValueError("profile_id is invalid")
        if self.profile_id == BUILTIN_WIRE_PROFILE.profile_id:
            raise ValueError("capture profile must be distinct from the built-in profile")


def build_real_sanitized_fixture(
    *,
    request: httpx.Request,
    response: httpx.Response,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate a names-only fixture for one explicit success."""

    if str(parsed.get("status", "") or "") != "published":
        raise ValueError("QZone capture requires an explicit published outcome")
    remote_id = str(parsed.get("remote_id", "") or "").strip()
    if not remote_id:
        raise ValueError("QZone capture requires a non-empty remote id")

    query_names = sorted({key for key, _value in request.url.params.multi_items()})
    try:
        encoded_form = request.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("QZone request form is not UTF-8") from exc
    form_names = sorted({key for key, _value in parse_qsl(encoded_form)})
    header_names = sorted(
        name
        for name in ("Cookie", "Origin", "Referer")
        if name in request.headers
    )
    content_type = str(response.headers.get("content-type", "") or "").strip()
    headers = {"content-type": content_type} if content_type else {}
    try:
        body = response.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("QZone response body is not UTF-8") from exc

    payload: dict[str, Any] = {
        "schema_version": 1,
        "origin": "real_sanitized",
        "note": "Genuine QZone live publish response; request values omitted and response secret-scanned.",
        "request_fingerprint": {
            "method": request.method,
            "scheme": request.url.scheme,
            "host": request.url.host,
            "path": request.url.path,
            "query_field_names": query_names,
            "form_field_names": form_names,
            "header_names": header_names,
            "follow_redirects": False,
        },
        "response": {
            "status_code": response.status_code,
            "headers": headers,
            "body": body,
        },
        "expected": {
            "status": "published",
            "remote_id": remote_id,
        },
    }
    validate_fixture_dict(payload)
    return payload


def _unvalidated_copy(profile: WireProfile) -> WireProfile:
    return WireProfile(
        profile_id=profile.profile_id,
        endpoint=profile.endpoint,
        validated=False,
        content_field=profile.content_field,
        uin_field=profile.uin_field,
        static_form_fields=dict(profile.static_form_fields),
        max_content_chars=profile.max_content_chars,
    )


def _encode_json(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_bytes_exclusive(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _probe_exclusive_write(path: Path) -> None:
    probe = path.parent / f".{path.name}.write-probe-{os.getpid()}"
    try:
        _write_bytes_exclusive(probe, b"qzone-capture-write-probe\n")
    finally:
        probe.unlink(missing_ok=True)


def _load_or_create_attestation_secret(path: Path) -> bytes:
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        existing = b""
    except OSError as exc:
        raise CapturePreflightError(
            "QZone capture attestation secret is unavailable"
        ) from exc
    if existing:
        if len(existing) < 32:
            raise CapturePreflightError(
                "QZone capture attestation secret is invalid"
            )
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        try:
            raced = path.read_bytes()
        except OSError as exc:
            raise CapturePreflightError(
                "QZone capture attestation secret is unavailable"
            ) from exc
        if len(raced) < 32:
            raise CapturePreflightError(
                "QZone capture attestation secret is invalid"
            ) from None
        return raced
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(generated)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return generated


def _sensitive_echo_values(
    *,
    credentials: CredentialBundle,
    text: str,
) -> tuple[str, ...]:
    values = {
        str(credentials.uin),
        str(credentials.cookie_header),
        str(credentials.p_skey),
        str(credentials.g_tk),
    }
    encoded_values: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if len(cleaned) < 4:
            continue
        encoded_values.add(cleaned)
        encoded_values.add(quote(cleaned, safe=""))
        encoded_values.add(quote_plus(cleaned, safe=""))
        encoded_values.add(json.dumps(cleaned, ensure_ascii=True)[1:-1])
    content = str(text)
    if content:
        encoded_values.add(content)
        encoded_values.add(quote(content, safe=""))
        encoded_values.add(quote_plus(content, safe=""))
        encoded_values.add(json.dumps(content, ensure_ascii=True)[1:-1])
    return tuple(sorted(encoded_values, key=len, reverse=True))


def _decoded_response_scalars(body: str) -> tuple[str, ...]:
    candidate = body.strip().rstrip(";").strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        open_paren = candidate.find("(")
        if open_paren <= 0 or not candidate.endswith(")"):
            return ()
        try:
            payload = json.loads(candidate[open_paren + 1 : -1].strip())
        except json.JSONDecodeError:
            return ()

    scalars: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            scalars.append(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            scalars.append(str(value))

    walk(payload)
    return tuple(scalars)


def _reject_sensitive_echo(body: str, *, sensitive_values: tuple[str, ...]) -> None:
    decoded_scalars = _decoded_response_scalars(body)
    if any(value and value in body for value in sensitive_values) or any(
        value and value in scalar
        for scalar in decoded_scalars
        for value in sensitive_values
    ):
        raise CaptureSecretEchoError(
            "QZone success response echoed request-sensitive material"
        )


class CaptureTransport:
    """Send once and persist evidence only for an explicit published result."""

    def __init__(
        self,
        *,
        fixture_path: str | Path,
        attestation_path: str | Path | None = None,
        attestation_secret: str | bytes | None = None,
        attestation_secret_path: str | Path = DEFAULT_ATTESTATION_SECRET_PATH,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._fixture_path = Path(fixture_path)
        self._attestation_path = (
            Path(attestation_path)
            if attestation_path is not None
            else attestation_path_for(
                self._fixture_path.stem,
                profile_dir=self._fixture_path.parent,
            )
        )
        self._attestation_secret = attestation_secret
        self._attestation_secret_path = Path(attestation_secret_path)
        self._resolved_attestation_secret: str | bytes | None = None
        self._http_client = http_client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = http_client is None
        self._prepared_profile_id: str | None = None
        self._pending_payload: dict[str, Any] | None = None

    async def prepare(self, *, profile_id: str) -> None:
        if self._fixture_path.exists() or self._attestation_path.exists():
            raise FileExistsError("QZone capture evidence already exists")
        resolved_secret = self._attestation_secret
        if resolved_secret is None:
            resolved_secret = _load_or_create_attestation_secret(
                self._attestation_secret_path
            )
        build_capture_attestation(
            profile_id=profile_id,
            fixture_bytes=b"qzone-capture-attestation-preflight",
            secret=resolved_secret,
        )
        _probe_exclusive_write(self._fixture_path)
        _probe_exclusive_write(self._attestation_path)
        self._prepared_profile_id = profile_id
        self._resolved_attestation_secret = resolved_secret

    async def publish(
        self,
        *,
        profile: WireProfile,
        credentials: CredentialBundle,
        text: str,
    ) -> dict[str, Any]:
        if self._prepared_profile_id != profile.profile_id:
            raise CapturePreflightError("QZone capture transport was not prepared")
        if self._pending_payload is not None:
            raise CapturePreflightError("QZone capture transport was already used")
        built = QZoneRequestBuilder(profile).build(
            credentials=credentials,
            text=text,
        )
        response = await self._http_client.send(
            built.request,
            follow_redirects=built.follow_redirects,
        )
        parsed = parse_qzone_publish_response(response)
        if parsed.get("status") != "published":
            return parsed

        try:
            response_body = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("QZone response body is not UTF-8") from exc
        _reject_sensitive_echo(
            response_body,
            sensitive_values=_sensitive_echo_values(
                credentials=credentials,
                text=text,
            ),
        )

        payload = build_real_sanitized_fixture(
            request=built.request,
            response=response,
            parsed=parsed,
        )
        fixture = validate_fixture_dict(payload)
        report = run_conformance(
            fixture,
            profile=_unvalidated_copy(profile),
        )
        if not report.profile_creation_eligible:
            raise RuntimeError("captured QZone response is not profile-eligible")
        self._pending_payload = payload
        return parsed

    async def commit_fixture(self) -> None:
        profile_id = self._prepared_profile_id
        payload = self._pending_payload
        if profile_id is None or payload is None:
            raise CapturePreflightError("QZone capture has no published evidence to commit")
        fixture_bytes = _encode_json(payload)
        attestation = build_capture_attestation(
            profile_id=profile_id,
            fixture_bytes=fixture_bytes,
            secret=self._resolved_attestation_secret,
        )
        _write_bytes_exclusive(self._fixture_path, fixture_bytes)
        try:
            _write_bytes_exclusive(
                self._attestation_path,
                _encode_json(attestation),
            )
        except BaseException:
            self._fixture_path.unlink(missing_ok=True)
            raise
        self._pending_payload = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()


def _bootstrap_capture_profile(profile_id: str) -> WireProfile:
    """Ephemeral one-shot profile; never exported or persisted as validated.

    The resulting real response must pass the sanitized fixture conformance
    gate before the normal runtime loader can create a validated profile.
    """

    if BUILTIN_WIRE_PROFILE.validated:
        raise CapturePreflightError("built-in QZone profile must remain unvalidated")
    return WireProfile(
        profile_id=profile_id,
        endpoint=BUILTIN_WIRE_PROFILE.endpoint,
        validated=True,
        content_field=BUILTIN_WIRE_PROFILE.content_field,
        uin_field=BUILTIN_WIRE_PROFILE.uin_field,
        static_form_fields=dict(BUILTIN_WIRE_PROFILE.static_form_fields),
        max_content_chars=BUILTIN_WIRE_PROFILE.max_content_chars,
    )


class _PinnedCredentialSource:
    def __init__(self, credentials: CredentialBundle) -> None:
        self._credentials = credentials

    async def acquire(self) -> CredentialBundle:
        return self._credentials


def _draft_content(draft: Any) -> str:
    return str(getattr(draft, "content", getattr(draft, "text", "")) or "")


def _draft_id(draft: Any) -> str:
    return str(getattr(draft, "draft_id", "") or "")


def _credential_uin(credentials: Any) -> str:
    if isinstance(credentials, dict):
        return str(credentials.get("uin", "") or "").strip()
    return str(getattr(credentials, "uin", "") or "").strip()


async def publish_once(
    *,
    request: CaptureRequest,
    store: Any,
    credential_source: Any,
    transport: Any,
) -> Any:
    """Publish exactly one pinned approved draft through JournalDelivery."""

    if BUILTIN_WIRE_PROFILE.validated:
        raise CapturePreflightError("built-in QZone profile must remain unvalidated")
    draft = await store.get(request.draft_id)
    if draft is None:
        raise CapturePreflightError("pinned QZone draft was not found")
    if str(getattr(draft, "status", "") or "") != "approved":
        raise CapturePreflightError("pinned QZone draft is not approved")
    if str(getattr(draft, "approval_scope", "") or "") != "live":
        raise CapturePreflightError(
            "pinned QZone draft lacks live approval scope"
        )
    actual_content_sha = hashlib.sha256(
        _draft_content(draft).encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(actual_content_sha, request.expected_content_sha256):
        raise CapturePreflightError("pinned QZone draft content hash mismatch")

    approved = await store.list(status="approved", limit=2, offset=0)
    if len(approved) != 1 or _draft_id(approved[0]) != request.draft_id:
        raise CapturePreflightError(
            "approved QZone queue is not exactly the pinned draft"
        )

    credentials = await credential_source.acquire()
    uin = _credential_uin(credentials)
    actual_uin_sha = hashlib.sha256(uin.encode("utf-8")).hexdigest()
    if not uin or not hmac.compare_digest(actual_uin_sha, request.expected_uin_sha256):
        raise CapturePreflightError("QZone credential UIN hash mismatch")

    prepare = getattr(transport, "prepare", None)
    if callable(prepare):
        prepared = prepare(profile_id=request.profile_id)
        if not inspect.isawaitable(prepared):
            raise CapturePreflightError("QZone capture prepare hook is not awaitable")
        await cast(Awaitable[Any], prepared)

    delivery = JournalDelivery(
        config=DeliveryConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=(uin,),
        ),
        store=store,
        credential_source=_PinnedCredentialSource(credentials),
        transport=transport,
        profile=_bootstrap_capture_profile(request.profile_id),
    )
    result = await delivery.deliver(request.draft_id)
    commit_fixture = getattr(transport, "commit_fixture", None)
    if callable(commit_fixture):
        committed = commit_fixture()
        if not inspect.isawaitable(committed):
            raise CapturePreflightError("QZone capture commit hook is not awaitable")
        await cast(Awaitable[Any], committed)
    return result


class OneBotHttpClient:
    """Minimal secret-retaining OneBot client for NapCat credential actions."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._http_client = http_client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=10.0,
        )
        self._owns_client = http_client is None

    async def call_api(self, action: str, **params: Any) -> Any:
        name = str(action or "").strip()
        if name not in {"get_login_info", "get_cookies"}:
            raise CapturePreflightError("unsupported OneBot credential action")
        response = await self._http_client.post(
            f"{self._base_url}/{name}",
            json=params,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise CapturePreflightError("OneBot credential response is not an object")
        if str(payload.get("status", "") or "") not in {"", "ok"}:
            raise CapturePreflightError("OneBot credential action was not successful")
        retcode = payload.get("retcode", 0)
        if isinstance(retcode, bool) or not isinstance(retcode, int) or retcode != 0:
            raise CapturePreflightError("OneBot credential action returned nonzero retcode")
        return payload

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Publish exactly one pinned approved QZone draft, capture a "
            "secret-scanned real fixture, and never retry automatically."
        )
    )
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--expected-content-sha256", required=True)
    parser.add_argument("--expected-uin-sha256", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("storage/qzone_journal.db"),
    )
    parser.add_argument(
        "--onebot-base-url",
        default="http://host.docker.internal:29300",
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must equal {_LIVE_CONFIRMATION}",
    )
    return parser


async def _close_resources_shielded(*resources: Any) -> tuple[str, ...]:
    async def close_one(resource: Any) -> BaseException | None:
        close = getattr(resource, "aclose", None)
        if not callable(close):
            close = getattr(resource, "close", None)
        if not callable(close):
            return None
        try:
            result = close()
            if inspect.isawaitable(result):
                await cast(Awaitable[Any], result)
        except BaseException as exc:
            return exc
        return None

    async def close_all() -> list[BaseException | None]:
        return list(await asyncio.gather(
            *(close_one(resource) for resource in resources if resource is not None),
            return_exceptions=False,
        ))

    task = asyncio.create_task(close_all())
    try:
        results = await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    return tuple(
        type(result).__name__
        for result in results
        if isinstance(result, BaseException)
    )


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    if args.confirm != _LIVE_CONFIRMATION:
        raise CapturePreflightError("live confirmation token did not match")
    request = CaptureRequest(
        draft_id=args.draft_id,
        expected_content_sha256=args.expected_content_sha256,
        expected_uin_sha256=args.expected_uin_sha256,
        profile_id=args.profile_id,
    )
    fixture_path = Path(args.fixture)
    if fixture_path.name != f"{request.profile_id}.json":
        raise CapturePreflightError(
            "fixture filename must match the requested profile id"
        )
    if fixture_path.exists():
        raise CapturePreflightError("capture fixture already exists")

    store: JournalStore | None = None
    onebot: OneBotHttpClient | None = None
    transport: CaptureTransport | None = None
    success_payload: dict[str, Any] | None = None
    try:
        store = JournalStore(
            Path(args.db),
            max_posts_per_day=1,
            max_drafts_per_day=1,
        )
        onebot = OneBotHttpClient(base_url=args.onebot_base_url)
        transport = CaptureTransport(fixture_path=fixture_path)
        await store.init()
        result = await publish_once(
            request=request,
            store=store,
            credential_source=NapCatCredentialSource(bot=onebot),
            transport=transport,
        )
        stored = await store.get(request.draft_id)
        status = str(getattr(stored, "status", "") or "")
        remote_id = str(getattr(stored, "external_post_id", "") or "")
        if status != "published" or not remote_id or result.status != "published":
            raise RuntimeError("published response did not persist a published draft")
        success_payload = {
            "ok": True,
            "draft_id": request.draft_id,
            "status": status,
            "profile_id": request.profile_id,
            "fixture": str(fixture_path),
            "attestation": str(
                attestation_path_for(
                    request.profile_id,
                    profile_dir=fixture_path.parent,
                )
            ),
            "external_post_id_sha256": hashlib.sha256(
                remote_id.encode("utf-8")
            ).hexdigest(),
        }
        return success_payload
    finally:
        cleanup_warnings = await _close_resources_shielded(
            transport,
            onebot,
            store,
        )
        if success_payload is not None and cleanup_warnings:
            success_payload["cleanup_warning_types"] = list(cleanup_warnings)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = asyncio.run(_run_cli(args))
    except CapturePreflightError as exc:
        payload = {
            "ok": False,
            "error": "preflight_failed",
            "reason": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2
    except BaseException as exc:
        payload = {
            "ok": False,
            "error": "publish_failed_or_ambiguous",
            "exception_type": type(exc).__name__,
            "automatic_retry": False,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


__all__ = [
    "CapturePreflightError",
    "CaptureRequest",
    "CaptureSecretEchoError",
    "CaptureTransport",
    "OneBotHttpClient",
    "build_real_sanitized_fixture",
    "main",
    "publish_once",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
