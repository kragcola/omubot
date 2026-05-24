import json
from pathlib import Path

from services.persona import PersonaDraftWriter
from services.persona.compiler import compile_persona_dry_run
from services.persona.importer import main as importer_main
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults


def test_compile_persona_dry_run_returns_prompt_blocks(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run("fengxiaomeng", persona_root=persona_root, defaults_dir=defaults)

    assert result.ok is True
    assert result.mode == "dry_run"
    assert result.persona_id == "fengxiaomeng-v2"
    module_ids = {block.module_id for block in result.prompt_blocks}
    assert {"core.identity", "core.voice", "core.knowledge", "core.examples", "core.guard"} <= module_ids
    assert "runtime.adapter" in result.module_order


def test_compile_persona_dry_run_rejects_error_report(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text("---\npersona_id: fengxiaomeng\n---\n# 空\n", encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run("fengxiaomeng", persona_root=persona_root, defaults_dir=defaults)

    assert result.ok is False
    assert result.errors == ("import report has errors",)


def test_cli_can_run_compile_dry_run(tmp_path: Path, capsys) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")

    exit_code = importer_main([
        "fengxiaomeng",
        "--root",
        str(persona_root),
        "--defaults",
        str(defaults),
        "--compile-dry-run",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compile"]["ok"] is True
    assert payload["compile"]["mode"] == "dry_run"
