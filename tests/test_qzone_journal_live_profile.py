from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_FIXTURE = (
    ROOT / "tests" / "fixtures" / "qzone_journal" / "synthetic_publish_json_v1.json"
)
TEST_ATTESTATION_SECRET = "qzone-test-attestation-secret-v1"


def _api() -> Any:
    try:
        module = import_module("plugins.qzone_journal.wire_profiles")
    except ModuleNotFoundError as exc:
        if exc.name != "plugins.qzone_journal.wire_profiles":
            raise
        module = None
    assert module is not None, (
        "plugins.qzone_journal.wire_profiles must provide the validated "
        "real_sanitized profile loader"
    )
    return module


def _write_fixture(
    profile_dir: Path,
    *,
    profile_id: str,
    origin: str,
    attest: bool = True,
) -> Path:
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    payload["origin"] = origin
    payload["note"] = "genuine sanitized capture fixture" if origin == "real_sanitized" else payload["note"]
    path = profile_dir / f"{profile_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if attest:
        _write_attestation(path, profile_id=profile_id)
    return path


def _write_attestation(path: Path, *, profile_id: str) -> Path:
    api = _api()
    payload = api.build_capture_attestation(
        profile_id=profile_id,
        fixture_bytes=path.read_bytes(),
        secret=TEST_ATTESTATION_SECRET,
    )
    attestation_path = api.attestation_path_for(
        profile_id,
        profile_dir=path.parent,
    )
    attestation_path.write_text(json.dumps(payload), encoding="utf-8")
    return attestation_path


def _write_runtime_attestation_secret(root: Path) -> Path:
    api = _api()
    path = root / api.DEFAULT_ATTESTATION_SECRET_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TEST_ATTESTATION_SECRET.encode("utf-8"))
    return path


def test_real_sanitized_fixture_creates_independent_validated_profile(
    tmp_path: Path,
) -> None:
    api = _api()
    profile_id = "qzone-text-v1-live-dev"
    _write_fixture(tmp_path, profile_id=profile_id, origin="real_sanitized")

    profile = api.load_wire_profile(
        profile_id,
        profile_dir=tmp_path,
        attestation_secret=TEST_ATTESTATION_SECRET,
    )

    assert profile.profile_id == profile_id
    assert profile.validated is True
    assert profile is not api.BUILTIN_WIRE_PROFILE
    assert api.BUILTIN_WIRE_PROFILE.validated is False


def test_synthetic_fixture_cannot_create_validated_profile(tmp_path: Path) -> None:
    api = _api()
    profile_id = "qzone-text-v1-synthetic"
    _write_fixture(tmp_path, profile_id=profile_id, origin="synthetic")

    with pytest.raises(api.WireProfileLoadError, match="not eligible"):
        api.load_wire_profile(
            profile_id,
            profile_dir=tmp_path,
            attestation_secret=TEST_ATTESTATION_SECRET,
        )

    assert api.BUILTIN_WIRE_PROFILE.validated is False


def test_origin_relabel_without_capture_attestation_cannot_validate(
    tmp_path: Path,
) -> None:
    api = _api()
    profile_id = "qzone-text-v1-relabelled"
    _write_fixture(
        tmp_path,
        profile_id=profile_id,
        origin="real_sanitized",
        attest=False,
    )

    with pytest.raises(api.WireProfileLoadError, match="attestation"):
        api.load_wire_profile(profile_id, profile_dir=tmp_path)

    assert api.BUILTIN_WIRE_PROFILE.validated is False


def test_fixture_change_after_attestation_is_rejected(tmp_path: Path) -> None:
    api = _api()
    profile_id = "qzone-text-v1-attested"
    path = _write_fixture(
        tmp_path,
        profile_id=profile_id,
        origin="real_sanitized",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["note"] = "mutated after capture attestation"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(api.WireProfileLoadError, match="attestation"):
        api.load_wire_profile(
            profile_id,
            profile_dir=tmp_path,
            attestation_secret=TEST_ATTESTATION_SECRET,
        )

    assert api.BUILTIN_WIRE_PROFILE.validated is False


def test_loader_validates_the_same_attested_bytes_without_second_path_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    profile_id = "qzone-text-v1-same-bytes"
    _write_fixture(
        tmp_path,
        profile_id=profile_id,
        origin="real_sanitized",
    )

    fixture_path = tmp_path / f"{profile_id}.json"
    original_read_bytes = Path.read_bytes
    fixture_reads = 0

    def audited_read_bytes(path: Path) -> bytes:
        nonlocal fixture_reads
        if path == fixture_path:
            fixture_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    profile = api.load_wire_profile(
        profile_id,
        profile_dir=tmp_path,
        attestation_secret=TEST_ATTESTATION_SECRET,
    )

    assert profile.validated is True
    assert fixture_reads == 1
    assert api.BUILTIN_WIRE_PROFILE.validated is False


def test_research_pseudonymization_secret_cannot_sign_live_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    profile_id = "qzone-text-v1-wrong-authority"
    _write_fixture(
        tmp_path,
        profile_id=profile_id,
        origin="real_sanitized",
    )
    monkeypatch.setenv(
        "OMUBOT_RESEARCH_EVENT_PSEUDONYMIZATION_SECRET",
        TEST_ATTESTATION_SECRET,
    )

    with pytest.raises(api.WireProfileLoadError, match="secret is unavailable"):
        api.load_wire_profile(profile_id, profile_dir=tmp_path)

    assert api.BUILTIN_WIRE_PROFILE.validated is False


def test_invalid_fixture_error_never_echoes_fixture_controlled_secret_name(
    tmp_path: Path,
) -> None:
    api = _api()
    profile_id = "qzone-text-v1-invalid"
    payload = json.loads(SYNTHETIC_FIXTURE.read_text(encoding="utf-8"))
    secret_marker = "p_skey_DO_NOT_LOG_THIS_VALUE"
    payload[secret_marker] = True
    path = tmp_path / f"{profile_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    _write_attestation(path, profile_id=profile_id)

    with pytest.raises(api.WireProfileLoadError) as excinfo:
        api.load_wire_profile(
            profile_id,
            profile_dir=tmp_path,
            attestation_secret=TEST_ATTESTATION_SECRET,
        )

    assert secret_marker not in str(excinfo.value)
    assert api.BUILTIN_WIRE_PROFILE.validated is False


@pytest.mark.parametrize(
    "profile_id",
    ("../escape", "nested/profile", "qzone profile", ""),
)
def test_profile_id_cannot_escape_profile_directory(
    tmp_path: Path,
    profile_id: str,
) -> None:
    api = _api()

    with pytest.raises(api.WireProfileLoadError, match="profile id"):
        api.load_wire_profile(profile_id, profile_dir=tmp_path)


@pytest.mark.asyncio
async def test_plugin_custom_profile_makes_live_gate_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    plugin_api = import_module("plugins.qzone_journal.plugin")
    profile_id = "qzone-text-v1-live-dev"
    _write_fixture(
        tmp_path / "config" / "qzone_wire_profiles",
        profile_id=profile_id,
        origin="real_sanitized",
    )
    _write_runtime_attestation_secret(tmp_path)
    monkeypatch.chdir(tmp_path)
    plugin = plugin_api.QZoneJournalPlugin(
        plugin_api.PluginConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=["123456789"],
            wire_profile_id=profile_id,
        )
    )
    ctx = SimpleNamespace(
        storage_dir=tmp_path / "storage",
        llm_client=SimpleNamespace(_call=lambda _request: None),
        bot=object(),
    )

    try:
        await plugin.on_startup(ctx)
        assert plugin._live_publish_gate() == {"ready": True, "reasons": []}
        assert api.BUILTIN_WIRE_PROFILE.validated is False
    finally:
        await plugin.on_shutdown(ctx)


@pytest.mark.asyncio
async def test_plugin_rejects_ineligible_profile_before_runtime_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    plugin_api = import_module("plugins.qzone_journal.plugin")
    profile_id = "qzone-text-v1-synthetic"
    _write_fixture(
        tmp_path / "config" / "qzone_wire_profiles",
        profile_id=profile_id,
        origin="synthetic",
    )
    _write_runtime_attestation_secret(tmp_path)
    monkeypatch.chdir(tmp_path)
    plugin = plugin_api.QZoneJournalPlugin(
        plugin_api.PluginConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=["123456789"],
            wire_profile_id=profile_id,
        )
    )
    ctx = SimpleNamespace(
        storage_dir=tmp_path / "storage",
        llm_client=SimpleNamespace(_call=lambda _request: None),
        bot=object(),
    )

    with pytest.raises(api.WireProfileLoadError, match="not eligible"):
        await plugin.on_startup(ctx)

    assert plugin._store is None
    assert plugin._transport is None
    assert not (tmp_path / "storage" / "qzone_journal.db").exists()
