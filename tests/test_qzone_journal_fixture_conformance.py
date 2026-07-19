"""Offline QZone sanitized-fixture conformance contracts (TDD).

These tests exercise the pure fixture contract, secret scan, request
fingerprint match against an *unverified* WireProfile, and parser outcome
match.  They never hit the network, never load credentials, and never
mutate or create a validated wire profile.

Checked-in fixtures under tests/fixtures/qzone_journal/ are synthetic
templates only — origin must remain ``synthetic`` and must never claim
``real_sanitized``.
"""

from __future__ import annotations

import copy
import json
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "qzone_journal"
_SYNTHETIC_JSON = _FIXTURE_DIR / "synthetic_publish_json_v1.json"

_PUBLISH_ENDPOINT = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
    "cgi-bin/emotion_cgi_publish_v6"
)
_PUBLISH_PATH = (
    "/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
)


def _api() -> Any:
    """Load fixture conformance public API; RED is assertion failure, not import noise."""
    try:
        module = import_module("plugins.qzone_journal.fixture_conformance")
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "plugins.qzone_journal",
            "plugins.qzone_journal.fixture_conformance",
        }:
            raise
        module = None

    assert module is not None, (
        "plugins.qzone_journal.fixture_conformance must implement the offline "
        "sanitized fixture contract"
    )
    required = (
        "FixtureValidationError",
        "ConformanceReport",
        "load_fixture",
        "validate_fixture_dict",
        "fingerprint_from_wire_profile",
        "run_conformance",
        "FIXTURE_SCHEMA_VERSION",
        "MAX_FIXTURE_BODY_BYTES",
    )
    missing = [name for name in required if not hasattr(module, name)]
    assert not missing, f"fixture conformance public API missing: {', '.join(missing)}"
    return module


def _transport() -> Any:
    return import_module("plugins.qzone_journal.transport")


def _delivery() -> Any:
    return import_module("plugins.qzone_journal.delivery")


def _cli() -> Any:
    return import_module("tools.verify_qzone_fixture")


def _candidate_profile(*, validated: bool = False) -> Any:
    transport = _transport()
    return transport.WireProfile(
        profile_id="qzone-text-v1-candidate-unverified",
        endpoint=_PUBLISH_ENDPOINT,
        validated=validated,
        content_field="con",
        uin_field="hostuin",
        static_form_fields={
            "format": "json",
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
        },
    )


def _base_fixture_dict() -> dict[str, Any]:
    return json.loads(_SYNTHETIC_JSON.read_text(encoding="utf-8"))


def test_checked_in_fixture_is_synthetic_template_only() -> None:
    raw = _base_fixture_dict()
    assert raw["origin"] == "synthetic"
    note = str(raw.get("note", ""))
    assert "SYNTHETIC" in note.upper()
    assert "not a real" in note.lower()
    # Never commit a real_sanitized claim without a genuine capture.
    assert raw["origin"] != "real_sanitized"


def test_valid_synthetic_json_mechanics_pass_but_not_profile_eligible() -> None:
    api = _api()
    fixture = api.load_fixture(_SYNTHETIC_JSON)
    report = api.run_conformance(fixture, profile=_candidate_profile())

    assert report.ok is True
    assert report.fixture_origin == "synthetic"
    assert report.request_match is True
    assert report.response_match is True
    assert report.profile_creation_eligible is False
    as_dict = report.to_dict()
    assert as_dict["profile_creation_eligible"] is False
    assert as_dict["fixture_origin"] == "synthetic"
    assert "body" not in as_dict
    assert "Cookie" not in json.dumps(as_dict)
    # Report must not echo response body content.
    assert "syntheticTid001" not in json.dumps(as_dict)


def test_cli_synthetic_report_is_secret_free_and_not_eligible(capsys) -> None:
    exit_code = _cli().main([str(_SYNTHETIC_JSON), "--indent", "0"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["fixture_origin"] == "synthetic"
    assert payload["profile_creation_eligible"] is False
    assert "syntheticTid001" not in output
    assert "body" not in payload


def test_valid_synthetic_jsonp_mechanics_pass() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["response"]["body"] = (
        '_Callback({"code":0,"data":{"tid":"jsonpTid42"}});'
    )
    raw["response"]["headers"] = {"content-type": "text/javascript; charset=utf-8"}
    raw["expected"] = {"status": "published", "remote_id": "jsonpTid42"}
    fixture = api.validate_fixture_dict(raw)
    report = api.run_conformance(fixture, profile=_candidate_profile())
    assert report.ok is True
    assert report.response_match is True
    assert report.profile_creation_eligible is False


def test_synthetic_cannot_qualify_for_profile_creation() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["origin"] = "synthetic"
    fixture = api.validate_fixture_dict(raw)
    report = api.run_conformance(fixture, profile=_candidate_profile())
    assert report.ok is True
    assert report.profile_creation_eligible is False


def test_real_sanitized_eligible_only_when_all_checks_pass_advisory() -> None:
    """Eligibility is advisory: true only for real_sanitized + full pass.

    This packet never commits a real capture; the test only proves the
    advisory flag logic using an in-memory dict that claims real_sanitized
    with synthetic body text (still offline, no network).
    """
    api = _api()
    raw = _base_fixture_dict()
    raw["origin"] = "real_sanitized"
    fixture = api.validate_fixture_dict(raw)
    report = api.run_conformance(fixture, profile=_candidate_profile())
    assert report.ok is True
    assert report.profile_creation_eligible is True
    # Must not return or mutate a WireProfile.
    assert not hasattr(report, "profile") or getattr(report, "profile", None) is None
    as_dict = report.to_dict()
    assert "validated" not in as_dict
    assert as_dict["profile_creation_eligible"] is True


def test_secret_bearing_fixture_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    # Cookie header *value* must never appear; secret scan rejects.
    raw["response"]["headers"] = {
        "content-type": "application/json",
        "set-cookie": "p_skey=leaked-value",
    }
    with pytest.raises(api.FixtureValidationError) as excinfo:
        api.validate_fixture_dict(raw)
    message = str(excinfo.value).lower()
    assert "secret" in message or "header" in message or "set-cookie" in message


def test_secret_value_in_body_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["response"]["body"] = (
        '{"code":0,"tid":"x","cookie":"p_skey=abc123tokenvalue"}'
    )
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)


@pytest.mark.parametrize(
    "secret_body",
    [
        '{"code":0,"p_skey":"redacted-but-still-secret-shaped"}',
        '{"code":0,"g_tk":123456}',
        '{"code":0,"hostuin":"384801062"}',
        '{"code":0,"token":"credential-value"}',
    ],
)
def test_json_style_secret_keys_in_body_rejected(secret_body: str) -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["response"]["body"] = secret_body
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)


def test_cli_invalid_secret_fixture_never_echoes_secret(
    tmp_path: Path,
    capsys,
) -> None:
    raw = _base_fixture_dict()
    secret = "p_skey=must-not-escape"
    raw["response"]["body"] = '{"code":0,"p_skey":"' + secret + '"}'
    fixture_path = tmp_path / "secret-fixture.json"
    fixture_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = _cli().main([str(fixture_path), "--indent", "0"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 1
    assert payload["error"] == "fixture_invalid"
    assert payload["profile_creation_eligible"] is False
    assert secret not in output
    assert "must-not-escape" not in output


def test_case_insensitive_duplicate_response_headers_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["response"]["headers"] = {
        "content-type": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)


def test_form_field_values_in_fingerprint_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    # Values must never be stored; only names. Unknown keys rejected.
    raw["request_fingerprint"]["form_field_values"] = {"con": "secret text"}
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)


def test_unsorted_or_duplicate_field_names_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["request_fingerprint"]["form_field_names"] = [
        "hostuin",
        "con",
        "format",
        "con",
    ]
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)

    raw2 = _base_fixture_dict()
    raw2["request_fingerprint"]["query_field_names"] = ["z_field", "g_tk"]
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw2)


def test_unknown_top_level_key_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["raw_request"] = "POST / leaked"
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)


def test_request_drift_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["request_fingerprint"]["path"] = "/cgi-bin/wrong_path"
    # Path change is still a valid fixture structurally if names stay sorted.
    fixture = api.validate_fixture_dict(raw)
    report = api.run_conformance(fixture, profile=_candidate_profile())
    assert report.ok is False
    assert report.request_match is False
    assert report.profile_creation_eligible is False
    assert any("request" in r or "path" in r or "fingerprint" in r for r in report.reasons)


def test_parser_outcome_mismatch_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    # Body is success JSON but expected claims failed.
    raw["expected"] = {"status": "failed", "reason": "cgi_error"}
    fixture = api.validate_fixture_dict(raw)
    report = api.run_conformance(fixture, profile=_candidate_profile())
    assert report.ok is False
    assert report.response_match is False
    assert report.profile_creation_eligible is False
    assert any(
        "outcome" in r or "expected" in r or "parser" in r or "status" in r
        for r in report.reasons
    )


def test_builtin_profile_remains_unvalidated_and_conformance_never_mutates() -> None:
    api = _api()
    delivery = _delivery()
    profile = delivery.BUILTIN_WIRE_PROFILE
    assert profile.validated is False

    fixture = api.load_fixture(_SYNTHETIC_JSON)
    report = api.run_conformance(fixture, profile=profile)
    assert report.ok is True
    assert profile.validated is False
    assert delivery.BUILTIN_WIRE_PROFILE.validated is False
    # Advisory only for synthetic.
    assert report.profile_creation_eligible is False


def test_fingerprint_from_wire_profile_names_only() -> None:
    api = _api()
    fp = api.fingerprint_from_wire_profile(_candidate_profile())
    assert fp.method == "POST"
    assert fp.scheme == "https"
    assert fp.host == "user.qzone.qq.com"
    assert fp.path == _PUBLISH_PATH
    assert list(fp.query_field_names) == ["g_tk"]
    assert list(fp.form_field_names) == sorted(
        ["con", "feedversion", "format", "hostuin", "ugc_right", "ver"]
    )
    assert list(fp.header_names) == sorted(["Cookie", "Origin", "Referer"])
    assert fp.follow_redirects is False


def test_conformance_report_to_dict_is_deterministic() -> None:
    api = _api()
    fixture = api.load_fixture(_SYNTHETIC_JSON)
    report = api.run_conformance(fixture, profile=_candidate_profile())
    d1 = report.to_dict()
    d2 = report.to_dict()
    assert d1 == d2
    assert set(d1.keys()) >= {
        "ok",
        "fixture_origin",
        "request_match",
        "response_match",
        "profile_creation_eligible",
        "reasons",
    }
    # reasons must be a list for JSON and sorted/stable for determinism.
    assert isinstance(d1["reasons"], list)


def test_oversized_body_rejected_at_validation() -> None:
    api = _api()
    raw = _base_fixture_dict()
    # Cap must align with parser (64 KiB).
    pad = "x" * (api.MAX_FIXTURE_BODY_BYTES + 1)
    raw["response"]["body"] = f'{{"code":0,"tid":"t","pad":"{pad}"}}'
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)


def test_load_fixture_rejects_non_dict_json() -> None:
    api = _api()
    path = _FIXTURE_DIR / "_tmp_list_fixture.json"
    path.write_text("[]", encoding="utf-8")
    try:
        with pytest.raises(api.FixtureValidationError):
            api.load_fixture(path)
    finally:
        path.unlink(missing_ok=True)


def test_load_fixture_rejects_duplicate_json_object_keys() -> None:
    api = _api()
    path = _FIXTURE_DIR / "_tmp_duplicate_key_fixture.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )
    try:
        with pytest.raises(api.FixtureValidationError, match="duplicate object key"):
            api.load_fixture(path)
    finally:
        path.unlink(missing_ok=True)


def test_g_tk_name_allowed_in_fingerprint_without_value() -> None:
    api = _api()
    raw = _base_fixture_dict()
    # Literal field name g_tk / Cookie are allowed (names only).
    assert "g_tk" in raw["request_fingerprint"]["query_field_names"]
    assert "Cookie" in raw["request_fingerprint"]["header_names"]
    fixture = api.validate_fixture_dict(raw)
    assert "g_tk" in fixture.request_fingerprint.query_field_names


def test_schema_version_mismatch_rejected() -> None:
    api = _api()
    raw = _base_fixture_dict()
    raw["schema_version"] = 99
    with pytest.raises(api.FixtureValidationError):
        api.validate_fixture_dict(raw)


def test_copy_mutation_does_not_affect_loaded_fixture() -> None:
    """Sanity: validate returns an immutable/frozen structure."""
    api = _api()
    raw = _base_fixture_dict()
    fixture = api.validate_fixture_dict(raw)
    mutated = copy.deepcopy(raw)
    mutated["origin"] = "real_sanitized"
    # Original fixture object must not reflect later dict edits.
    assert fixture.origin == "synthetic"
