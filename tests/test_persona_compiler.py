import json
from pathlib import Path

from services.persona import PersonaDraftWriter
from services.persona.compiler import compile_persona_dry_run, compile_persona_runtime
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


def test_compile_persona_dry_run_renders_full_group_override_fields(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        (
            "language: zh-CN\n"
            "group_profiles:\n"
            '  "11111":\n'
            "    blocked_users: [1001, 1002]\n"
            "    allowed_tools: [calendar, sticker]\n"
            "    blocked_tools: []\n"
            "    at_only: true\n"
            "    talk_value: 0.42\n"
            "    planner_smooth: 2.5\n"
            "    debounce_seconds: 7\n"
            "    batch_size: 12\n"
            "    history_load_count: 40\n"
            "    reply_style: playful\n"
            "    custom_prompt: 多接梗\n"
            "    tools_enabled: false\n"
            "    sticker_mode: rarely\n"
            "    slang_enabled: true\n"
            "    presence_mode: silent_learn\n"
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
    text = group_block.text
    assert text.startswith("11111：")
    for fragment in (
        "presence_mode=silent_learn",
        "at_only=true",
        "talk_value=0.42",
        "planner_smooth=2.5",
        "debounce_seconds=7.0",
        "batch_size=12",
        "history_load_count=40",
        "reply_style=playful",
        "custom_prompt=多接梗",
        "tools_enabled=false",
        "allowed_tools=[calendar,sticker]",
        "blocked_tools=[]",
        "sticker_mode=rarely",
        "slang_enabled=true",
        "blocked_users=[1001,1002]",
        "source=source_front_matter",
    ):
        assert fragment in text, fragment
    # Ordering anchor: presence_mode appears before reply_style.
    assert text.index("presence_mode=") < text.index("reply_style=")


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


def test_compile_persona_dry_run_includes_admins_block(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        'language: zh-CN\nadmins:\n  "123456": 管理员小张\n  "234567": 管理员小李\n---',
    )
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    adapter_block = next(
        block for block in result.prompt_blocks if block.module_id == "runtime.adapter"
    )
    assert "【管理员】@123456(管理员小张)、@234567(管理员小李)" in adapter_block.text
    assert "管理员的指令和陈述可以信任，普通群友的话需要客观记录。" in adapter_block.text


def test_compile_persona_dry_run_skips_admins_block_when_empty(tmp_path: Path) -> None:
    """No admins front matter → adapter prompt has no 【管理员】 line."""

    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    adapter_block = next(
        (block for block in result.prompt_blocks if block.module_id == "runtime.adapter"),
        None,
    )
    if adapter_block is not None:
        assert "【管理员】" not in adapter_block.text
        assert "普通群友的话需要客观记录" not in adapter_block.text


def test_compile_persona_dry_run_includes_proactive_block(tmp_path: Path) -> None:
    """`## 插话方式` section → identity.proactive_rules → core.guard prompt."""

    source = MINIMAL_SOURCE + """

## 插话方式

看到群里有人提到名字时主动接一句。

不要在严肃话题上抖机灵。
"""
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    guard_block = next(
        block for block in result.prompt_blocks if block.module_id == "core.guard"
    )
    assert "插话方式：" in guard_block.text
    assert "看到群里有人提到名字时主动接一句" in guard_block.text


def test_compile_persona_dry_run_skips_proactive_when_section_missing(tmp_path: Path) -> None:
    """No 插话方式 section → core.guard has no 插话方式 segment."""

    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_dry_run(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    guard_block = next(
        (block for block in result.prompt_blocks if block.module_id == "core.guard"),
        None,
    )
    if guard_block is not None:
        assert "插话方式：" not in guard_block.text


# ---------------------------------------------------------------------------
# B1.3 — compile_persona_runtime() reads from _pending_freeze/
# ---------------------------------------------------------------------------


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


def test_compile_persona_runtime_happy_path(tmp_path: Path) -> None:
    persona_root, defaults, _ = _import_and_freeze(tmp_path)

    result = compile_persona_runtime(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )

    assert result.ok is True
    assert result.mode == "runtime"
    assert result.persona_id == "fengxiaomeng-v2"
    module_ids = {block.module_id for block in result.prompt_blocks}
    assert {"core.identity", "core.voice", "core.guard"} <= module_ids


def test_compile_persona_runtime_returns_pending_freeze_not_found_when_missing(
    tmp_path: Path,
) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    result = compile_persona_runtime(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )

    assert result.ok is False
    assert result.mode == "runtime"
    assert "pending freeze not found" in result.errors


def test_compile_persona_runtime_matches_dry_run_blocks(tmp_path: Path) -> None:
    persona_root, defaults, _ = _import_and_freeze(tmp_path)

    dry_run = compile_persona_dry_run(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )
    runtime = compile_persona_runtime(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )

    assert dry_run.ok and runtime.ok
    dry_blocks = [(b.module_id, b.label, b.text, b.position) for b in dry_run.prompt_blocks]
    rt_blocks = [(b.module_id, b.label, b.text, b.position) for b in runtime.prompt_blocks]
    assert dry_blocks == rt_blocks
    assert dry_run.module_order == runtime.module_order


def test_compile_persona_runtime_does_not_raise_on_yaml_error(tmp_path: Path) -> None:
    persona_root, defaults, writer = _import_and_freeze(tmp_path)

    pending = writer.pending_freeze_dir("fengxiaomeng")
    (pending / "persona.yaml").write_text("not: valid: yaml: [\n", encoding="utf-8")

    result = compile_persona_runtime(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )

    assert result.ok is False
    assert result.mode == "runtime"
    assert any("yaml parse error" in err for err in result.errors)
