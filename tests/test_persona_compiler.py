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


def test_compile_persona_dry_run_includes_behavior_instructions(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE + """

# 8.4 行为指令

- 默认只回一句话
- 不用 Markdown
"""
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run("fengxiaomeng", persona_root=persona_root, defaults_dir=defaults)

    guard_block = next(block for block in result.prompt_blocks if block.module_id == "core.guard")
    assert "行为指令：默认只回一句话；不用 Markdown" in guard_block.text


def test_compile_persona_dry_run_includes_adapter_identity_block(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        'language: zh-CN\nbot_self_id_hint: "10000"\nknown_bot_self_ids: ["10000", "20000"]\n---',
    )
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run("fengxiaomeng", persona_root=persona_root, defaults_dir=defaults)

    adapter_block = next(block for block in result.prompt_blocks if block.module_id == "runtime.adapter")
    assert "bot self id hint：10000" in adapter_block.text
    assert "known self ids：10000；20000" in adapter_block.text
    assert "runtime source：adapter_connect_event" in adapter_block.text
    assert "昵称不可信" in adapter_block.text


def test_compile_persona_dry_run_includes_group_profile_block(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        (
            "language: zh-CN\n"
            "group_profiles:\n"
            '  "12345":\n'
            "    reply_style: playful\n"
            "    custom_prompt: 多接梗，少说教。\n"
            "---"
        ),
    )
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run("fengxiaomeng", persona_root=persona_root, defaults_dir=defaults)

    group_block = next(block for block in result.prompt_blocks if block.module_id == "runtime.group_profile")
    assert group_block.position == "stable"
    assert "12345：reply_style=playful；custom_prompt=多接梗，少说教。" in group_block.text


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
