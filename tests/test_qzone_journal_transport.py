"""RED contracts for the QZone Journal credential and HTTP transport boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from importlib import import_module
from typing import Any
from urllib.parse import parse_qs

import pytest

_PUBLISH_ENDPOINT = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
    "cgi-bin/emotion_cgi_publish_v6"
)
_PUBLISH_PATH = "/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
_UIN = "384801062"
_P_SKEY = "fake-p-skey-value"
_COOKIE = f"uin=o0{_UIN}; p_skey={_P_SKEY}; skey=fake-skey-value"


def _transport_api() -> Any:
    """Load the future transport lazily so RED is an assertion, not import error."""
    try:
        module = import_module("plugins.qzone_journal.transport")
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "plugins.qzone_journal",
            "plugins.qzone_journal.transport",
        }:
            raise
        module = None

    assert module is not None, (
        "plugins.qzone_journal.transport must exist and implement the frozen "
        "QZone transport contract"
    )
    required = (
        "compute_g_tk",
        "CredentialBundle",
        "CredentialError",
        "NapCatCredentialSource",
        "WireProfile",
        "UnverifiedWireProfileError",
        "QZoneRequestBuilder",
        "QZoneTransport",
    )
    missing = [name for name in required if not hasattr(module, name)]
    assert not missing, f"QZone transport public API is missing: {', '.join(missing)}"
    return module


def _credentials(api: Any) -> Any:
    return api.CredentialBundle(
        uin=_UIN,
        cookie_header=_COOKIE,
        p_skey=_P_SKEY,
        g_tk=api.compute_g_tk(_P_SKEY),
    )


def _profile(api: Any, *, validated: bool) -> Any:
    return api.WireProfile(
        profile_id="qzone-text-v1",
        endpoint=_PUBLISH_ENDPOINT,
        validated=validated,
        content_field="con",
        uin_field="hostuin",
        static_form_fields={"format": "json"},
    )


class _FailIfTencentCalled:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send(self, request: Any, *, follow_redirects: bool) -> Any:
        self.calls.append({
            "request": request,
            "follow_redirects": follow_redirects,
        })
        raise AssertionError("Tencent HTTP must not be called in this contract path")


class _NapCatBot:
    def __init__(self, *, login_uin: str = _UIN, cookies: str = _COOKIE) -> None:
        self._login_uin = login_uin
        self._cookies = cookies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_api(self, action: str, **params: Any) -> dict[str, Any]:
        self.calls.append((action, params))
        if action == "get_login_info":
            return {"user_id": int(self._login_uin), "nickname": "fixture-bot"}
        if action == "get_cookies":
            return {"cookies": self._cookies}
        raise AssertionError(f"unexpected NapCat action: {action}")


def test_compute_g_tk_uses_deterministic_hash33_vector() -> None:
    api = _transport_api()

    assert api.compute_g_tk("abc") == 193485963
    assert api.compute_g_tk("p_skey_fixture_123") == 59738379
    assert api.compute_g_tk("abc") == api.compute_g_tk("abc")


def test_credential_bundle_repr_never_exposes_cookie_or_p_skey() -> None:
    api = _transport_api()

    rendered = repr(_credentials(api))

    assert _COOKIE not in rendered
    assert _P_SKEY not in rendered
    assert "fake-skey-value" not in rendered


async def test_unverified_wire_profile_fails_closed_without_tencent_http() -> None:
    api = _transport_api()
    client = _FailIfTencentCalled()
    transport = api.QZoneTransport(http_client=client)

    with pytest.raises(api.UnverifiedWireProfileError):
        await transport.publish(
            profile=_profile(api, validated=False),
            credentials=_credentials(api),
            text="这条说说不能离开本机",
        )

    assert client.calls == []


def test_validated_profile_builds_fixed_https_form_request_without_redirects() -> None:
    api = _transport_api()
    credentials = _credentials(api)
    text = "今天很开心 🌸 & 练习=完成+1%"

    built = api.QZoneRequestBuilder(_profile(api, validated=True)).build(
        credentials=credentials,
        text=text,
    )
    request = built.request
    form = parse_qs(request.content.decode("utf-8"), keep_blank_values=True)

    assert request.method == "POST"
    assert request.url.scheme == "https"
    assert request.url.host == "user.qzone.qq.com"
    assert request.url.path == _PUBLISH_PATH
    assert request.url.params["g_tk"] == str(credentials.g_tk)
    assert built.follow_redirects is False
    assert form["con"] == [text]
    assert form["hostuin"] == [_UIN]
    assert form["format"] == ["json"]


async def test_napcat_source_calls_only_login_info_and_qzone_cookies() -> None:
    api = _transport_api()
    bot = _NapCatBot()

    credentials = await api.NapCatCredentialSource(bot=bot).acquire()

    assert bot.calls == [
        ("get_login_info", {}),
        ("get_cookies", {"domain": "user.qzone.qq.com"}),
    ]
    assert credentials.uin == _UIN
    assert credentials.g_tk == api.compute_g_tk(_P_SKEY)


@pytest.mark.parametrize(
    ("bot", "error_pattern"),
    [
        pytest.param(
            _NapCatBot(cookies=f"uin=o0{_UIN}; skey=fake-skey-value"),
            "p_skey",
            id="missing-p-skey",
        ),
        pytest.param(
            _NapCatBot(login_uin="10001", cookies=_COOKIE),
            "uin",
            id="uin-mismatch",
        ),
    ],
)
async def test_napcat_source_rejects_incomplete_or_mismatched_credentials(
    bot: _NapCatBot,
    error_pattern: str,
) -> None:
    api = _transport_api()

    with pytest.raises(api.CredentialError, match=error_pattern):
        await api.NapCatCredentialSource(bot=bot).acquire()


def test_dry_run_returns_only_sanitized_descriptor_and_never_calls_tencent() -> None:
    api = _transport_api()
    client = _FailIfTencentCalled()
    transport = api.QZoneTransport(http_client=client)
    text = "只做预演 🌸 & token=不能泄露"

    descriptor = transport.dry_run(
        profile=_profile(api, validated=True),
        credentials=_credentials(api),
        text=text,
    )

    assert isinstance(descriptor, Mapping)
    assert descriptor["method"] == "POST"
    assert descriptor["host"] == "user.qzone.qq.com"
    assert descriptor["path"] == _PUBLISH_PATH
    assert descriptor["field_names"] == ["con", "format", "hostuin"]
    assert descriptor["content_chars"] == len(text)
    assert descriptor["content_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    serialized = json.dumps(descriptor, ensure_ascii=False, sort_keys=True)
    assert text not in serialized
    assert _COOKIE not in serialized
    assert _P_SKEY not in serialized
    assert str(_credentials(api).g_tk) not in serialized
    assert client.calls == []


# ---------------------------------------------------------------------------
# Strict JSON / JSONP response parsing (synthetic, unvalidated fixtures only)
# ---------------------------------------------------------------------------

_SECRET_SNIPPET = "p_skey=leaked-p-skey-value; g_tk=999111; Cookie: uin=o0384801062"


def _parser_api() -> Any:
    """Load the response parser (via transport re-export or dedicated module)."""
    transport = _transport_api()
    if hasattr(transport, "parse_qzone_publish_response"):
        return transport
    try:
        module = import_module("plugins.qzone_journal.response_parser")
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "plugins.qzone_journal",
            "plugins.qzone_journal.response_parser",
        }:
            raise
        module = None
    assert module is not None, (
        "plugins.qzone_journal must expose parse_qzone_publish_response "
        "(transport re-export or response_parser module)"
    )
    assert hasattr(module, "parse_qzone_publish_response"), (
        "parse_qzone_publish_response is required for strict CGI classification"
    )
    return module


def _http_response(
    *,
    status_code: int = 200,
    content: bytes | str = b"{}",
    headers: dict[str, str] | None = None,
) -> Any:
    import httpx

    body = content if isinstance(content, bytes) else content.encode("utf-8")
    return httpx.Response(
        status_code,
        content=body,
        headers=headers or {"content-type": "application/json"},
        request=httpx.Request("POST", _PUBLISH_ENDPOINT),
    )


def _assert_secret_free(result: Mapping[str, Any]) -> None:
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert set(result.keys()) <= {"status", "remote_id", "reason"}
    assert _COOKIE not in serialized
    assert _P_SKEY not in serialized
    assert "leaked-p-skey" not in serialized
    assert "fake-p-skey" not in serialized
    assert "Cookie:" not in serialized
    assert "p_skey=" not in serialized
    assert "g_tk" not in serialized
    # Free-form CGI message / HTML must never leak into the normalized result.
    assert "<html" not in serialized.lower()
    assert "<!doctype" not in serialized.lower()
    assert "please login" not in serialized.lower()


def test_parse_json_success_requires_zero_code_and_tid() -> None:
    api = _parser_api()
    # Synthetic envelope only — not a real sanitized QZone fixture.
    body = json.dumps({"code": 0, "tid": "synthetic-tid-1001", "msg": "ok"})
    result = api.parse_qzone_publish_response(_http_response(content=body))

    assert result == {"status": "published", "remote_id": "synthetic-tid-1001"}
    _assert_secret_free(result)


def test_parse_jsonp_success_with_bom_whitespace_and_validated_callback() -> None:
    api = _parser_api()
    payload = json.dumps({"code": 0, "data": {"tid": "jsonp-tid-42"}})
    raw = "\ufeff  _Callback(" + payload + ");  \n"
    result = api.parse_qzone_publish_response(_http_response(content=raw.encode("utf-8")))

    assert result["status"] == "published"
    assert result["remote_id"] == "jsonp-tid-42"
    _assert_secret_free(result)


def test_parse_explicit_cgi_failure_is_failed_not_published() -> None:
    api = _parser_api()
    body = json.dumps({
        "code": -3000,
        "message": f"login required {_SECRET_SNIPPET}",
        "msg": "please login",
    })
    result = api.parse_qzone_publish_response(_http_response(content=body))

    assert result["status"] == "failed"
    assert result.get("reason")
    assert "remote_id" not in result or not result.get("remote_id")
    _assert_secret_free(result)
    assert "-3000" not in json.dumps(result) or result["status"] == "failed"
    assert _SECRET_SNIPPET not in json.dumps(result)
    assert "please login" not in json.dumps(result)


def test_parse_zero_code_without_remote_id_is_ambiguous() -> None:
    api = _parser_api()
    body = json.dumps({"code": 0, "message": "ok but no tid"})
    result = api.parse_qzone_publish_response(_http_response(content=body))

    assert result["status"] == "ambiguous"
    assert result.get("reason") == "missing_remote_id"
    _assert_secret_free(result)


def test_parse_remote_id_max_len_aligns_with_store_contract() -> None:
    """Parser must not accept CGI ids longer than JournalStore.mark_published.

    Store/admin cap is 120 chars. A 121-char allowlisted id classified as
    ``published`` would claim the draft then fail durable mark and leave the
    row dispatching — never treat 121+ as published.
    """
    api = _parser_api()

    tid_120 = "a" * 120
    ok = api.parse_qzone_publish_response(
        _http_response(content=json.dumps({"code": 0, "tid": tid_120}))
    )
    assert ok == {"status": "published", "remote_id": tid_120}
    _assert_secret_free(ok)

    tid_121 = "a" * 121
    bad = api.parse_qzone_publish_response(
        _http_response(content=json.dumps({"code": 0, "tid": tid_121}))
    )
    # Specific misclassification regression: 121 must never be published.
    assert bad.get("status") != "published", (
        "121-char CGI id must not be classified published; "
        f"got {bad!r} (store mark_published max is 120)"
    )
    assert bad["status"] == "ambiguous"
    assert bad.get("reason") == "missing_remote_id"
    assert "remote_id" not in bad or not bad.get("remote_id")
    _assert_secret_free(bad)


@pytest.mark.parametrize(
    "body",
    [
        {"code": 0, "ret": -1, "tid": "must-not-publish"},
        {"code": 0, "err": {"code": -1}, "tid": "must-not-publish"},
        {"code": 0, "ret": "not-a-code", "tid": "must-not-publish"},
    ],
)
def test_parse_conflicting_or_invalid_code_fields_is_ambiguous(
    body: dict[str, Any],
) -> None:
    api = _parser_api()

    result = api.parse_qzone_publish_response(
        _http_response(content=json.dumps(body))
    )

    assert result["status"] == "ambiguous"
    assert result.get("status") != "published"
    _assert_secret_free(result)


@pytest.mark.parametrize(
    "remote_id",
    [
        {"p_skey": _SECRET_SNIPPET},
        [_SECRET_SNIPPET],
        True,
        "tid with spaces",
        "x" * 256,
        _SECRET_SNIPPET,
    ],
)
def test_parse_rejects_unsafe_remote_identifier_without_leaking_it(
    remote_id: Any,
) -> None:
    api = _parser_api()
    result = api.parse_qzone_publish_response(
        _http_response(content=json.dumps({"code": 0, "tid": remote_id}))
    )

    assert result["status"] == "ambiguous"
    assert result.get("status") != "published"
    _assert_secret_free(result)
    assert _SECRET_SNIPPET not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("content", "allowed_reasons"),
    [
        pytest.param(
            b"_Callback<script>({\"code\":0,\"tid\":\"x\"});",
            {"invalid_jsonp_callback", "html_body"},
            id="malformed-callback-html-injection",
        ),
        pytest.param(
            b"window['alert']({\"code\":0,\"tid\":\"x\"})",
            {"invalid_jsonp_callback"},
            id="malformed-callback-brackets",
        ),
        pytest.param(
            b"foo-bar({\"code\":0,\"tid\":\"x\"})",
            {"invalid_jsonp_callback"},
            id="malformed-callback-hyphen",
        ),
        pytest.param(
            b"123evil({\"code\":0,\"tid\":\"x\"})",
            {"invalid_jsonp_callback"},
            id="malformed-callback-leading-digit",
        ),
        pytest.param(
            b"{not-json",
            {"malformed_json"},
            id="malformed-json",
        ),
        pytest.param(
            b"_Callback({not-json})",
            {"malformed_jsonp"},
            id="malformed-jsonp-payload",
        ),
        pytest.param(
            b"<!DOCTYPE html><html><body>login " + _SECRET_SNIPPET.encode() + b"</body></html>",
            {"html_body"},
            id="html-login-page",
        ),
    ],
)
def test_parse_malformed_or_html_is_ambiguous(
    content: bytes,
    allowed_reasons: set[str],
) -> None:
    api = _parser_api()
    result = api.parse_qzone_publish_response(_http_response(content=content))

    assert result["status"] == "ambiguous"
    assert result.get("reason") in allowed_reasons
    _assert_secret_free(result)
    serialized = json.dumps(result)
    assert _SECRET_SNIPPET not in serialized
    assert b"<html" not in serialized.encode()



def test_parse_http_redirect_and_non_2xx_are_ambiguous_even_with_json_body() -> None:
    api = _parser_api()
    success_body = json.dumps({"code": 0, "tid": "should-not-count"})

    redirect = api.parse_qzone_publish_response(
        _http_response(status_code=302, content=success_body)
    )
    non_2xx = api.parse_qzone_publish_response(
        _http_response(status_code=500, content=success_body)
    )

    assert redirect["status"] == "ambiguous"
    assert redirect.get("reason") == "http_redirect"
    assert non_2xx["status"] == "ambiguous"
    assert non_2xx.get("reason") == "http_non_2xx"
    _assert_secret_free(redirect)
    _assert_secret_free(non_2xx)


def test_parse_oversized_body_is_ambiguous_without_reading_as_success() -> None:
    api = _parser_api()
    # 65 KiB synthetic pad — not a real fixture; must not become published.
    oversized = b'{"code":0,"tid":"x","pad":"' + (b"A" * (65 * 1024)) + b'"}'
    result = api.parse_qzone_publish_response(_http_response(content=oversized))

    assert result["status"] == "ambiguous"
    assert result.get("reason") == "oversized_body"
    _assert_secret_free(result)


def test_parse_unknown_schema_and_http_200_alone_never_succeed() -> None:
    api = _parser_api()
    cases = [
        b'{"success":true,"id":"not-allowlisted"}',
        b'{"retcode":0,"feedid":"also-not-tid"}',
        b"{}",
        b"[]",
        b'"ok"',
    ]
    for content in cases:
        result = api.parse_qzone_publish_response(_http_response(content=content))
        assert result["status"] in {"ambiguous", "failed"}
        assert result.get("status") != "published"
        _assert_secret_free(result)


def test_parse_never_returns_or_embeds_raw_body_or_secrets() -> None:
    api = _parser_api()
    toxic = {
        "code": 0,
        "tid": "ok-tid",
        "cookie": _COOKIE,
        "p_skey": _P_SKEY,
        "g_tk": 193485963,
        "message": _SECRET_SNIPPET,
        "content": "user journal text must not echo",
    }
    result = api.parse_qzone_publish_response(
        _http_response(content=json.dumps(toxic))
    )
    assert result == {"status": "published", "remote_id": "ok-tid"}
    serialized = json.dumps(result, ensure_ascii=False)
    assert _COOKIE not in serialized
    assert _P_SKEY not in serialized
    assert _SECRET_SNIPPET not in serialized
    assert "user journal text" not in serialized
    assert "193485963" not in serialized


class _RecordingHttpClient:
    """Synthetic HTTP client — never contacts Tencent."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def send(self, request: Any, *, follow_redirects: bool) -> Any:
        self.calls.append({"request": request, "follow_redirects": follow_redirects})
        return self.response


async def test_transport_publish_returns_normalized_secret_free_dict() -> None:
    api = _transport_api()
    body = json.dumps({"code": 0, "tid": "transport-tid-7", "msg": _SECRET_SNIPPET})
    client = _RecordingHttpClient(_http_response(content=body))
    transport = api.QZoneTransport(http_client=client)

    result = await transport.publish(
        profile=_profile(api, validated=True),
        credentials=_credentials(api),
        text="synthetic body only",
    )

    assert result == {"status": "published", "remote_id": "transport-tid-7"}
    assert len(client.calls) == 1
    assert client.calls[0]["follow_redirects"] is False
    serialized = json.dumps(result)
    assert _SECRET_SNIPPET not in serialized
    assert _COOKIE not in serialized
    assert _P_SKEY not in serialized


async def test_transport_publish_ambiguous_html_never_looks_published() -> None:
    api = _transport_api()
    html = b"<html><title>login</title><body>" + _SECRET_SNIPPET.encode() + b"</body></html>"
    client = _RecordingHttpClient(_http_response(content=html))
    transport = api.QZoneTransport(http_client=client)

    result = await transport.publish(
        profile=_profile(api, validated=True),
        credentials=_credentials(api),
        text="synthetic",
    )

    assert result["status"] == "ambiguous"
    assert result.get("reason") == "html_body"
    assert "remote_id" not in result or not result.get("remote_id")
    _assert_secret_free(result)
