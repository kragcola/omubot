from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from plugins.qzone_journal.delivery import (
    BUILTIN_WIRE_PROFILE,
    DeliveryPostDispatchError,
)
from plugins.qzone_journal.fixture_conformance import (
    run_conformance,
    validate_fixture_dict,
)
from plugins.qzone_journal.transport import CredentialBundle, QZoneRequestBuilder, WireProfile

ROOT = Path(__file__).resolve().parents[1]
TEST_ATTESTATION_SECRET = "qzone-test-attestation-secret-v1"


def _api() -> Any:
    try:
        module = import_module("tools.publish_qzone_capture")
    except ModuleNotFoundError as exc:
        if exc.name != "tools.publish_qzone_capture":
            raise
        module = None
    assert module is not None, (
        "tools.publish_qzone_capture must implement the one-shot, "
        "secret-safe live capture boundary"
    )
    return module


def test_capture_cli_script_bootstraps_repo_import_path() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "publish_qzone_capture.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Publish exactly one pinned approved QZone draft" in result.stdout


def _profile(*, validated: bool) -> WireProfile:
    return WireProfile(
        profile_id="qzone-text-v1-live-dev",
        endpoint=BUILTIN_WIRE_PROFILE.endpoint,
        validated=validated,
        content_field=BUILTIN_WIRE_PROFILE.content_field,
        uin_field=BUILTIN_WIRE_PROFILE.uin_field,
        static_form_fields=dict(BUILTIN_WIRE_PROFILE.static_form_fields),
        max_content_chars=BUILTIN_WIRE_PROFILE.max_content_chars,
    )


def _built_request() -> httpx.Request:
    credentials = CredentialBundle(
        uin="123456789",
        cookie_header="uin=o0123456789; p_skey=never-persist-this",
        p_skey="never-persist-this",
        g_tk=1082113096,
    )
    # The exact token is irrelevant to fixture shape, but the builder verifies
    # it before creating the request.
    from plugins.qzone_journal.transport import compute_g_tk

    credentials = CredentialBundle(
        uin=credentials.uin,
        cookie_header=credentials.cookie_header,
        p_skey=credentials.p_skey,
        g_tk=compute_g_tk(credentials.p_skey),
    )
    return QZoneRequestBuilder(_profile(validated=True)).build(
        credentials=credentials,
        text="今天也认真记录了一件小事。",
    ).request


def test_success_capture_fixture_is_real_eligible_and_names_only(tmp_path: Path) -> None:
    api = _api()
    request = _built_request()
    response = httpx.Response(
        200,
        headers={"content-type": "application/json; charset=utf-8"},
        content=b'{"code":0,"tid":"realTid001"}',
        request=request,
    )

    payload = api.build_real_sanitized_fixture(
        request=request,
        response=response,
        parsed={"status": "published", "remote_id": "realTid001"},
    )

    fixture = validate_fixture_dict(payload)
    report = run_conformance(fixture, profile=_profile(validated=False))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert report.profile_creation_eligible is True
    assert payload["origin"] == "real_sanitized"
    assert payload["expected"] == {
        "status": "published",
        "remote_id": "realTid001",
    }
    assert "never-persist-this" not in serialized
    assert "今天也认真记录了一件小事" not in serialized
    assert "123456789" not in serialized
    assert BUILTIN_WIRE_PROFILE.validated is False


@pytest.mark.asyncio
async def test_capture_prepare_creates_dedicated_private_attestation_secret(
    tmp_path: Path,
) -> None:
    api = _api()
    fixture_path = tmp_path / "profiles" / "qzone-text-v1-live-dev.json"
    secret_path = tmp_path / "storage" / "qzone" / "attestation.secret"
    transport = api.CaptureTransport(
        fixture_path=fixture_path,
        attestation_secret_path=secret_path,
    )
    try:
        await transport.prepare(profile_id="qzone-text-v1-live-dev")
    finally:
        await transport.aclose()

    assert len(secret_path.read_bytes()) == 32
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_capture_transport_writes_fixture_only_for_explicit_success(
    tmp_path: Path,
) -> None:
    api = _api()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"code":0,"tid":"realTid002"}',
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fixture_path = tmp_path / "qzone-text-v1-live-dev.json"
    transport = api.CaptureTransport(
        fixture_path=fixture_path,
        attestation_secret=TEST_ATTESTATION_SECRET,
        http_client=client,
    )
    credentials = CredentialBundle(
        uin="123456789",
        cookie_header="uin=o0123456789; p_skey=never-persist-this",
        p_skey="never-persist-this",
        g_tk=0,
    )
    from plugins.qzone_journal.transport import compute_g_tk

    credentials = CredentialBundle(
        uin=credentials.uin,
        cookie_header=credentials.cookie_header,
        p_skey=credentials.p_skey,
        g_tk=compute_g_tk(credentials.p_skey),
    )

    try:
        await transport.prepare(profile_id="qzone-text-v1-live-dev")
        result = await transport.publish(
            profile=_profile(validated=True),
            credentials=credentials,
            text="今天也认真记录了一件小事。",
        )
        assert not fixture_path.exists()
        await transport.commit_fixture()
    finally:
        await client.aclose()

    assert result == {"status": "published", "remote_id": "realTid002"}
    fixture = validate_fixture_dict(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )
    assert fixture.origin == "real_sanitized"
    from plugins.qzone_journal.wire_profiles import load_wire_profile

    loaded_profile = load_wire_profile(
        "qzone-text-v1-live-dev",
        profile_dir=tmp_path,
        attestation_secret=TEST_ATTESTATION_SECRET,
    )
    assert loaded_profile.validated is True
    assert BUILTIN_WIRE_PROFILE.validated is False


@pytest.mark.asyncio
async def test_capture_transport_does_not_write_ambiguous_response(
    tmp_path: Path,
) -> None:
    api = _api()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>login required</html>",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fixture_path = tmp_path / "qzone-text-v1-live-dev.json"
    transport = api.CaptureTransport(
        fixture_path=fixture_path,
        attestation_secret=TEST_ATTESTATION_SECRET,
        http_client=client,
    )
    from plugins.qzone_journal.transport import compute_g_tk

    credentials = CredentialBundle(
        uin="123456789",
        cookie_header="uin=o0123456789; p_skey=never-persist-this",
        p_skey="never-persist-this",
        g_tk=compute_g_tk("never-persist-this"),
    )
    try:
        await transport.prepare(profile_id="qzone-text-v1-live-dev")
        result = await transport.publish(
            profile=_profile(validated=True),
            credentials=credentials,
            text="今天也认真记录了一件小事。",
        )
    finally:
        await client.aclose()

    assert result["status"] == "ambiguous"
    assert not fixture_path.exists()
    assert BUILTIN_WIRE_PROFILE.validated is False


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["今天也认真记录了一件小事。", "好"])
async def test_capture_transport_rejects_exact_sensitive_value_echo(
    tmp_path: Path,
    content: str,
) -> None:
    api = _api()

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps(
            {"code": 0, "tid": "realTidEcho", "echo": content},
            ensure_ascii=True,
        ).encode("utf-8")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fixture_path = tmp_path / "qzone-text-v1-live-dev.json"
    transport = api.CaptureTransport(
        fixture_path=fixture_path,
        attestation_secret=TEST_ATTESTATION_SECRET,
        http_client=client,
    )
    from plugins.qzone_journal.transport import compute_g_tk

    credentials = CredentialBundle(
        uin="123456789",
        cookie_header="uin=o0123456789; p_skey=never-persist-this",
        p_skey="never-persist-this",
        g_tk=compute_g_tk("never-persist-this"),
    )
    try:
        await transport.prepare(profile_id="qzone-text-v1-live-dev")
        with pytest.raises(api.CaptureSecretEchoError):
            await transport.publish(
                profile=_profile(validated=True),
                credentials=credentials,
                text=content,
            )
    finally:
        await client.aclose()

    assert not fixture_path.exists()
    assert BUILTIN_WIRE_PROFILE.validated is False


@pytest.mark.asyncio
async def test_publish_once_checks_draft_hash_before_credentials_or_transport() -> None:
    api = _api()

    class Store:
        async def get(self, draft_id: str) -> Any:
            assert draft_id == "qzd_expected"
            return SimpleNamespace(
                draft_id=draft_id,
                content="approved content",
                status="approved",
                approval_scope="live",
            )

        async def list(self, **_kwargs: Any) -> list[Any]:
            return [await self.get("qzd_expected")]

    class CredentialSource:
        called = False

        async def acquire(self) -> Any:
            self.called = True
            raise AssertionError("credentials must not be acquired on hash mismatch")

    class Transport:
        called = False

        async def publish(self, **_kwargs: Any) -> dict[str, Any]:
            self.called = True
            raise AssertionError("transport must not run on hash mismatch")

    credentials = CredentialSource()
    transport = Transport()
    request = api.CaptureRequest(
        draft_id="qzd_expected",
        expected_content_sha256=hashlib.sha256(b"different content").hexdigest(),
        expected_uin_sha256=hashlib.sha256(b"123456789").hexdigest(),
        profile_id="qzone-text-v1-live-dev",
    )

    with pytest.raises(api.CapturePreflightError, match="content hash"):
        await api.publish_once(
            request=request,
            store=Store(),
            credential_source=credentials,
            transport=transport,
        )

    assert credentials.called is False
    assert transport.called is False
    assert BUILTIN_WIRE_PROFILE.validated is False


@pytest.mark.asyncio
async def test_publish_once_reuses_delivery_state_machine_exactly_once() -> None:
    api = _api()
    content = "approved content"

    class Store:
        def __init__(self) -> None:
            self.draft = SimpleNamespace(
                draft_id="qzd_expected",
                content=content,
                status="approved",
                approval_scope="live",
                source="event_replan",
                subject_kind="self",
                privacy="public",
                source_summary=content,
                external_post_id=None,
            )
            self.claims = 0
            self.published = 0

        async def get(self, draft_id: str) -> Any:
            return self.draft if draft_id == self.draft.draft_id else None

        async def list(self, **_kwargs: Any) -> list[Any]:
            return [self.draft] if self.draft.status == "approved" else []

        async def is_lineage_tip(self, draft_id: str) -> bool:
            return draft_id == self.draft.draft_id

        async def claim_for_publish(self, draft_id: str) -> Any:
            assert draft_id == self.draft.draft_id
            assert self.draft.status == "approved"
            self.claims += 1
            self.draft.status = "dispatching"
            return self.draft

        async def mark_published(
            self,
            draft_id: str,
            *,
            external_post_id: str,
        ) -> Any:
            assert draft_id == self.draft.draft_id
            assert self.draft.status == "dispatching"
            self.published += 1
            self.draft.status = "published"
            self.draft.external_post_id = external_post_id
            return self.draft

        async def mark_unknown(self, draft_id: str, *, reason: str) -> Any:
            raise AssertionError(f"unexpected unknown transition: {draft_id} {reason}")

    class CredentialSource:
        async def acquire(self) -> CredentialBundle:
            return CredentialBundle(
                uin="123456789",
                cookie_header="uin=o0123456789; p_skey=never-persist-this",
                p_skey="never-persist-this",
                g_tk=0,
            )

    class Transport:
        def __init__(self) -> None:
            self.calls = 0

        async def publish(self, **kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            assert kwargs["profile"].validated is True
            assert kwargs["text"] == content
            return {"status": "published", "remote_id": "realTid003"}

    store = Store()
    transport = Transport()
    result = await api.publish_once(
        request=api.CaptureRequest(
            draft_id="qzd_expected",
            expected_content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            expected_uin_sha256=hashlib.sha256(b"123456789").hexdigest(),
            profile_id="qzone-text-v1-live-dev",
        ),
        store=store,
        credential_source=CredentialSource(),
        transport=transport,
    )

    assert result.status == "published"
    assert store.claims == 1
    assert store.published == 1
    assert transport.calls == 1
    assert BUILTIN_WIRE_PROFILE.validated is False


@pytest.mark.asyncio
async def test_db_publish_failure_cannot_leave_capture_fixture(
    tmp_path: Path,
) -> None:
    api = _api()
    content = "approved content"

    class Store:
        def __init__(self) -> None:
            self.draft = SimpleNamespace(
                draft_id="qzd_expected",
                content=content,
                status="approved",
                approval_scope="live",
                source="event_replan",
                subject_kind="self",
                privacy="public",
                source_summary=content,
            )

        async def get(self, draft_id: str) -> Any:
            return self.draft if draft_id == self.draft.draft_id else None

        async def list(self, **_kwargs: Any) -> list[Any]:
            return [self.draft]

        async def is_lineage_tip(self, draft_id: str) -> bool:
            return draft_id == self.draft.draft_id

        async def claim_for_publish(self, draft_id: str) -> Any:
            self.draft.status = "dispatching"
            return self.draft

        async def mark_published(self, draft_id: str, *, external_post_id: str) -> Any:
            raise RuntimeError("simulated DB commit failure")

        async def mark_unknown(self, draft_id: str, *, reason: str) -> Any:
            self.draft.status = "unknown"
            return self.draft

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"code":0,"tid":"realTid004"}',
            request=request,
        )

    from plugins.qzone_journal.transport import compute_g_tk

    credentials = CredentialBundle(
        uin="123456789",
        cookie_header="uin=o0123456789; p_skey=never-persist-this",
        p_skey="never-persist-this",
        g_tk=compute_g_tk("never-persist-this"),
    )

    class CredentialSource:
        async def acquire(self) -> CredentialBundle:
            return credentials

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fixture_path = tmp_path / "qzone-text-v1-live-dev.json"
    transport = api.CaptureTransport(
        fixture_path=fixture_path,
        attestation_secret=TEST_ATTESTATION_SECRET,
        http_client=client,
    )
    try:
        with pytest.raises(DeliveryPostDispatchError) as raised:
            await api.publish_once(
                request=api.CaptureRequest(
                    draft_id="qzd_expected",
                    expected_content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    expected_uin_sha256=hashlib.sha256(b"123456789").hexdigest(),
                    profile_id="qzone-text-v1-live-dev",
                ),
                store=Store(),
                credential_source=CredentialSource(),
                transport=transport,
            )
        assert isinstance(raised.value.__cause__, RuntimeError)
        assert "DB commit failure" in str(raised.value.__cause__)
    finally:
        await client.aclose()

    assert not fixture_path.exists()
    assert BUILTIN_WIRE_PROFILE.validated is False


def test_cli_requires_exact_live_confirmation_before_opening_resources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = _api()
    exit_code = api.main(
        [
            "--draft-id",
            "qzd_expected",
            "--expected-content-sha256",
            hashlib.sha256(b"approved content").hexdigest(),
            "--expected-uin-sha256",
            hashlib.sha256(b"123456789").hexdigest(),
            "--profile-id",
            "qzone-text-v1-live-dev",
            "--fixture",
            str(tmp_path / "qzone-text-v1-live-dev.json"),
            "--db",
            str(tmp_path / "must-not-open.db"),
            "--confirm",
            "wrong-token",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "error": "preflight_failed",
        "ok": False,
        "reason": "live confirmation token did not match",
    }
    assert not (tmp_path / "must-not-open.db").exists()


@pytest.mark.asyncio
async def test_cleanup_attempts_every_resource_and_returns_closed_warning_types() -> None:
    api = _api()
    calls: list[str] = []

    class Resource:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        async def aclose(self) -> None:
            calls.append(self.name)
            if self.fail:
                raise OSError("simulated cleanup failure")

    warnings = await api._close_resources_shielded(
        Resource("transport", fail=True),
        Resource("onebot"),
        Resource("store"),
    )

    assert set(calls) == {"transport", "onebot", "store"}
    assert warnings == ("OSError",)
