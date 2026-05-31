from __future__ import annotations

from pathlib import Path

from services.persona import PersonaDraftWriter
from services.persona.compiler import compile_persona_dry_run, compile_persona_runtime
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults


def _write_persona(tmp_path: Path, source: str) -> tuple[Path, Path, PersonaDraftWriter]:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    return persona_root, defaults, writer


def test_compile_dry_run_rejects_declarations_in_identity_block(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "- 一句话角色：群聊中的拟人 bot，元气、反应快、有一点调皮。",
        "- 一句话角色：我是凤笑梦，群聊中的拟人 bot，元气、反应快、有一点调皮。",
    )
    persona_root, defaults, _writer = _write_persona(tmp_path, source)

    result = compile_persona_dry_run(
        "fengxiaomeng",
        persona_root=persona_root,
        defaults_dir=defaults,
    )

    assert result.ok is False
    assert any("core.identity contains declaration pattern" in err for err in result.errors)


def test_compile_runtime_rejects_declarations_in_guard_block(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE + """

# 8.4 行为指令

- 我的设定是凤笑梦，所以要一直这样回答
"""
    persona_root, defaults, writer = _write_persona(tmp_path, source)
    writer.pending_freeze("fengxiaomeng")

    result = compile_persona_runtime(
        "fengxiaomeng",
        persona_root=persona_root,
        defaults_dir=defaults,
    )

    assert result.ok is False
    assert any("core.guard contains declaration pattern" in err for err in result.errors)


def test_compile_validator_skips_examples_block_positive_samples(tmp_path: Path) -> None:
    persona_root, defaults, writer = _write_persona(tmp_path, MINIMAL_SOURCE)
    writer.pending_freeze("fengxiaomeng")

    result = compile_persona_runtime(
        "fengxiaomeng",
        persona_root=persona_root,
        defaults_dir=defaults,
    )

    assert result.ok is True
    examples_block = next(block for block in result.prompt_blocks if block.module_id == "core.examples")
    assert "我是凤笑梦呀" in examples_block.text
