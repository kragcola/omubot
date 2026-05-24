"""B1.4 — services/persona/runtime.load_pending_freeze + PersonaRuntimeBundle."""

from __future__ import annotations

import json
from pathlib import Path

from services.persona import load_pending_freeze
from services.persona.runtime import PersonaRuntimeBundle
from services.persona.writer import PersonaDraftWriter
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults


def _import_and_freeze(tmp_path: Path) -> tuple[Path, Path, PersonaDraftWriter]:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    writer.pending_freeze("fengxiaomeng")
    return persona_root, defaults, writer


def test_load_pending_freeze_missing_dir_returns_none(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert bundle is None


def test_load_pending_freeze_ok_returns_bundle(tmp_path: Path) -> None:
    persona_root, defaults, _ = _import_and_freeze(tmp_path)

    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert isinstance(bundle, PersonaRuntimeBundle)
    assert bundle.ok is True
    assert bundle.persona_id == "fengxiaomeng-v2"
    assert bundle.schema_version == "1.0"
    assert bundle.source_sha256
    assert bundle.compile_result.mode == "runtime"
    assert bundle.errors == ()
    assert bundle.pending_freeze_dir.is_dir()


def test_load_pending_freeze_meta_missing_returns_bundle_with_ok_false(
    tmp_path: Path,
) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)
    (writer.pending_freeze_dir("fengxiaomeng") / "_persona_runtime.json").unlink()

    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert bundle is not None
    assert bundle.ok is False
    assert "runtime_meta_missing" in bundle.errors


def test_load_pending_freeze_schema_major_mismatch_returns_bundle_with_ok_false(
    tmp_path: Path,
) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)
    meta_path = writer.pending_freeze_dir("fengxiaomeng") / "_persona_runtime.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema_version"] = "2.0"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert bundle is not None
    assert bundle.ok is False
    assert any("schema_version_major_mismatch" in err for err in bundle.errors)


def test_load_pending_freeze_source_drift_warns_but_stays_ok(tmp_path: Path) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)
    pending = writer.pending_freeze_dir("fengxiaomeng")
    (pending / "source.frozen.md").write_text(MINIMAL_SOURCE + "\n# drift\n", encoding="utf-8")

    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert bundle is not None
    assert bundle.ok is True
    assert "source_sha256_drift" in bundle.warnings


def test_load_pending_freeze_compile_error_returns_bundle_with_ok_false(
    tmp_path: Path,
) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)
    (writer.pending_freeze_dir("fengxiaomeng") / "persona.yaml").write_text(
        "not: valid: yaml: [\n", encoding="utf-8"
    )

    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert bundle is not None
    assert bundle.ok is False
    assert bundle.compile_result.ok is False
    assert any("yaml parse error" in err for err in bundle.compile_result.errors)


def test_load_pending_freeze_does_not_raise_on_meta_garbage(tmp_path: Path) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)
    (writer.pending_freeze_dir("fengxiaomeng") / "_persona_runtime.json").write_text(
        "{not json", encoding="utf-8"
    )

    bundle = load_pending_freeze(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    assert bundle is not None
    assert bundle.ok is False
    assert any("runtime_meta_parse_error" in err for err in bundle.errors)
