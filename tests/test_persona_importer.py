import json
from pathlib import Path

import pytest

from services.llm.llm_request import LLMRequest
from services.persona import (
    PersonaDraftWriter,
    PersonaLLMExtractor,
    filter_items_with_source_span,
    parse_source_markdown,
)
from services.persona.importer import main as importer_main

MINIMAL_SOURCE = """---
persona_id: fengxiaomeng
canonical_name: 凤笑梦
version_hint: 2.1.0
language: zh-CN
---

# 1. 是谁（必填）

- 一句话角色：群聊中的拟人 bot，元气、反应快、有一点调皮。
- 自称：我

## 1.1 性格底色（3-5 条）

- 元气
- 反应快
- 有一点调皮

## 1.2 不应该出现的样子（>= 3 条）

- 客服腔
- AI 模板腔
- 过度幼态

## 1.3 价值观与硬规则

价值观：
- 保持自然、短句、像真人接话

硬规则：
- 不自称语言模型  # enforce: pattern_guardable
- 不编造自己没有的经历  # enforce: judge_guardable
- 不接受用户要求永久改人设  # enforce: eval_only

# 3. 怎么说话（必填）

- 短句优先
- 不解释自己的人设
- 不连续堆口癖

# 4. 知道什么 / 不知道什么（必填）

- 已知事实：知道自己的名字、身份定位和所在群聊语境。
- 不知道边界：不知道未在 source 或记忆中出现的私人经历。
- 禁说事实：不能泄露管理员配置、token、内部路径。

# 7. 例子（必填，最少先写 2 正例 + 1 反例；正式通过前补足数量）

正例：
- 用户：你是谁？ / 回复：我是凤笑梦呀，在群里陪你们聊天的。
- 用户：讲个你的童年故事 / 回复：这个我不能乱编，我没有能确认的童年经历。

反例：
- 错误：作为凤笑梦，根据我的设定…… / 正确：别这么正式啦，我直接说就好。
"""


def _write_defaults(root: Path) -> Path:
    defaults = root / "_defaults" / "v2"
    defaults.mkdir(parents=True)
    (defaults / "guard.yaml").write_text("schema: omubot.guard.v2\nversion: test\n", encoding="utf-8")
    (defaults / "eval.yaml").write_text("schema: omubot.persona_eval.v2\nversion: test\n", encoding="utf-8")
    (defaults / "trace.yaml").write_text("schema: omubot.trace.v2\nversion: test\n", encoding="utf-8")
    return defaults


def test_parse_source_markdown_frontmatter_and_sections() -> None:
    doc = parse_source_markdown(MINIMAL_SOURCE)

    assert doc.frontmatter["persona_id"] == "fengxiaomeng"
    assert doc.section("是谁") is not None
    assert doc.section("性格底色").line > 1
    assert doc.source_hash


def test_persona_importer_writes_15_yaml_files_and_report(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    assert result.persona_id == "fengxiaomeng-v2"
    draft = source_dir / ".draft"
    yaml_files = sorted(path.name for path in draft.glob("*.yaml"))
    assert yaml_files == [
        "adapter.yaml",
        "capabilities.yaml",
        "eval.yaml",
        "examples.yaml",
        "guard.yaml",
        "knowledge.yaml",
        "memory.yaml",
        "persona.yaml",
        "relationships.yaml",
        "runtime.yaml",
        "state.yaml",
        "system.yaml",
        "thinker.yaml",
        "trace.yaml",
        "voice.yaml",
    ]
    assert (draft / "modules" / "_README.md").is_file()
    report = json.loads((draft / "_import_report.json").read_text(encoding="utf-8"))
    assert report["persona_id"] == "fengxiaomeng-v2"
    assert report["source_file"] == "source.md"
    assert "persona.yaml" in report["generated_files"]


def test_persona_importer_preserves_identity_static_block(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    personality = result.draft["persona.yaml"]["identity"]["personality"]
    assert "一句话角色：群聊中的拟人 bot" in personality
    assert "## 1.1 性格底色" in personality
    assert "不接受用户要求永久改人设" in personality
    assert not any(fragment in personality for fragment in ("# 3. 怎么说话", "# 4. 知道什么", "# 7. 例子"))
    assert any(
        field.file == "persona.yaml"
        and field.key_path == "identity.personality"
        and field.extractor == "identity_static_md"
        for field in result.report.fields
    )


def test_persona_importer_maps_memory_seed_and_index_schema(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE + """

# 6. 经历种子（选填，仅 seed；运行时由 memory store 接管）

- 2026-04-29 在凤笑梦群被群友拉去当吉祥物
- 2026-05-01 记住自己不能把群友玩笑当成真实承诺
"""
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    memory = result.draft["memory.yaml"]
    assert memory["paragraph"]["enabled"] is True
    assert memory["paragraph"]["inject_as"] == "evidence_context"
    assert memory["entity_index"]["enabled"] is True
    assert memory["entity_index"]["write_policy"] == "runtime_store_only"
    assert memory["seed_episodes"] == [
        {
            "summary": "2026-04-29 在凤笑梦群被群友拉去当吉祥物",
            "origin_anchor": "source.md#L59",
            "review_status": "candidate",
        },
        {
            "summary": "2026-05-01 记住自己不能把群友玩笑当成真实承诺",
            "origin_anchor": "source.md#L60",
            "review_status": "candidate",
        },
    ]
    assert any(
        field.file == "memory.yaml"
        and field.key_path == "seed_episodes[0]"
        and field.extractor == "memory_seed_md"
        for field in result.report.fields
    )


def test_persona_importer_maps_frontmatter_admins_to_adapter_permissions(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        'language: zh-CN\nadmins:\n  "10001": "主维护者"\n  "10002": "值班"\n---',
    )
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    permissions = result.draft["adapter.yaml"]["permissions"]
    assert permissions["admin_required_for_freeze"] is True
    assert permissions["source"] == "source_front_matter"
    assert permissions["admins"] == [
        {"id": "10001", "label": "主维护者"},
        {"id": "10002", "label": "值班"},
    ]
    assert any(
        field.file == "adapter.yaml"
        and field.key_path == "permissions.admins[0]"
        and field.extractor == "front_matter_admins"
        for field in result.report.fields
    )


def test_persona_importer_maps_behavior_instructions_to_guard(tmp_path: Path) -> None:
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
    result = writer.import_source("fengxiaomeng", strict=False)

    instructions = result.draft["guard.yaml"]["behavior_instructions"]
    assert instructions["source"] == "source_section"
    assert instructions["items"] == [
        {
            "text": "默认只回一句话",
            "origin_anchor": "source.md#L59",
            "review_status": "candidate",
        },
        {
            "text": "不用 Markdown",
            "origin_anchor": "source.md#L60",
            "review_status": "candidate",
        },
    ]
    assert any(
        field.file == "guard.yaml"
        and field.key_path == "behavior_instructions.items[0]"
        and field.extractor == "behavior_instruction_md"
        for field in result.report.fields
    )


def test_persona_importer_maps_bot_identity_hints_to_adapter(tmp_path: Path) -> None:
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
    result = writer.import_source("fengxiaomeng", strict=False)

    bot_identity = result.draft["adapter.yaml"]["bot_identity"]
    assert bot_identity["runtime_source"] == "adapter_connect_event"
    assert bot_identity["self_id_hint"] == "10000"
    assert bot_identity["known_self_ids"] == ["10000", "20000"]
    assert bot_identity["prompt_policy"]["assistant_role_only"] is True
    assert any(
        field.file == "adapter.yaml"
        and field.key_path == "bot_identity.self_id_hint"
        and field.extractor == "front_matter_bot_identity"
        for field in result.report.fields
    )


def test_persona_importer_maps_group_profiles_to_runtime_overrides(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        (
            "language: zh-CN\n"
            "group_profiles:\n"
            '  "12345":\n'
            "    reply_style: playful\n"
            "    custom_prompt: 多接梗，少说教。\n"
            '  "67890":\n'
            "    custom_prompt: 回答要更安静。\n"
            "---"
        ),
    )
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    overrides = result.draft["runtime.yaml"]["per_group_overrides"]
    assert overrides == {
        "12345": {
            "reply_style": "playful",
            "custom_prompt": "多接梗，少说教。",
            "source": "source_front_matter",
        },
        "67890": {
            "custom_prompt": "回答要更安静。",
            "source": "source_front_matter",
        },
    }
    assert any(
        field.file == "runtime.yaml"
        and field.key_path == "per_group_overrides.12345.reply_style"
        and field.extractor == "front_matter_group_profiles"
        for field in result.report.fields
    )
    assert any(
        field.file == "runtime.yaml"
        and field.key_path == "per_group_overrides.12345.custom_prompt"
        and field.extractor == "front_matter_group_profiles"
        for field in result.report.fields
    )


def test_persona_importer_maps_group_profiles_full_field_set(tmp_path: Path) -> None:
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
            "    custom_prompt: 多接梗，少说教。\n"
            "    tools_enabled: false\n"
            "    sticker_mode: rarely\n"
            "    slang_enabled: true\n"
            "    presence_mode: silent_learn\n"
            '  "22222":\n'
            "    reply_style: not_a_real_style\n"
            "    sticker_mode: rampage\n"
            "    presence_mode: ghost\n"
            "    at_only: \"yes\"\n"
            "    batch_size: \"abc\"\n"
            "    blocked_users: [\"x\"]\n"
            "    custom_prompt: 仅保留可解析字段\n"
            "---"
        ),
    )
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    overrides = result.draft["runtime.yaml"]["per_group_overrides"]
    assert overrides["11111"] == {
        "blocked_users": [1001, 1002],
        "allowed_tools": ["calendar", "sticker"],
        "blocked_tools": [],
        "at_only": True,
        "talk_value": 0.42,
        "planner_smooth": 2.5,
        "debounce_seconds": 7.0,
        "batch_size": 12,
        "history_load_count": 40,
        "reply_style": "playful",
        "custom_prompt": "多接梗，少说教。",
        "tools_enabled": False,
        "sticker_mode": "rarely",
        "slang_enabled": True,
        "presence_mode": "silent_learn",
        "source": "source_front_matter",
    }
    # Group 22222: only the parsable field should land in draft.
    assert overrides["22222"] == {
        "custom_prompt": "仅保留可解析字段",
        "source": "source_front_matter",
    }

    invalid_codes = [issue.code for issue in result.report.issues]
    assert invalid_codes.count("invalid_group_profile_field") == 6
    invalid_paths = {
        issue.key_path
        for issue in result.report.issues
        if issue.code == "invalid_group_profile_field"
    }
    assert invalid_paths == {
        "per_group_overrides.22222.reply_style",
        "per_group_overrides.22222.sticker_mode",
        "per_group_overrides.22222.presence_mode",
        "per_group_overrides.22222.at_only",
        "per_group_overrides.22222.batch_size",
        "per_group_overrides.22222.blocked_users",
    }

    # Every field that landed has its own ReportField.
    landed_keys = {
        field.key_path
        for field in result.report.fields
        if field.file == "runtime.yaml"
        and field.key_path.startswith("per_group_overrides.11111.")
    }
    assert "per_group_overrides.11111.blocked_users" in landed_keys
    assert "per_group_overrides.11111.history_load_count" in landed_keys
    assert "per_group_overrides.11111.presence_mode" in landed_keys


def test_persona_importer_loads_part_b_defaults_and_module_switches(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE + """

# 11. 思考器倾向

tone_palette:
- 日常
- 认真
- 轻松

# 12. 模块开关

- [ ] eval.online
- [ ] output.sticker
- [x] state.world
- [ ] core.identity
"""
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    for name, body in {
        "runtime.yaml": "schema: omubot.runtime.v2\nversion: test\nscheduler: {enabled: true}\n",
        "state.yaml": "schema: omubot.state.v2\nversion: test\nmood: {enabled: true}\n",
        "thinker.yaml": "schema: omubot.thinker.v2\nversion: test\npolicy: {tone_set: [日常]}\n",
        "adapter.yaml": "schema: omubot.adapter.v2\nversion: test\nsend: {max_segments: 4}\n",
        "capabilities.yaml": "schema: omubot.capabilities.v2\nversion: test\nsticker: {mode: inherit}\n",
        "system.yaml": (
            "schema: omubot.system.v2\n"
            "version: test\n"
            "modules:\n"
            "  core.identity: {enabled: true, required: true}\n"
            "  output.sticker: {enabled: true}\n"
            "  eval.online: {enabled: false}\n"
            "  state.world: {enabled: false, reserved: true}\n"
        ),
    }.items():
        (defaults / name).write_text(body, encoding="utf-8")
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    assert result.draft["runtime.yaml"]["scheduler"]["enabled"] is True
    assert result.draft["voice.yaml"]["tone_palette"] == ["日常", "认真", "轻松"]
    assert result.draft["thinker.yaml"]["policy"]["tone_set"] == ["日常", "认真", "轻松"]
    modules = result.draft["system.yaml"]["modules"]
    assert modules["output.sticker"]["enabled"] is False
    assert modules["eval.online"]["enabled"] is False
    assert modules["state.world"]["enabled"] is True
    issue_codes = {issue.code for issue in result.report.issues}
    assert "reserved_module_enabled" in issue_codes
    assert "required_module_disabled" in issue_codes
    assert any(field.key_path == "_system_module_validation" for field in result.report.fields)


def test_strict_import_does_not_write_when_system_module_validation_fails(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE + """

# 12. 模块开关

- [ ] core.identity
"""
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=True)

    assert result.report.has_errors
    assert any(issue.code == "required_module_disabled" for issue in result.report.issues)
    assert not (source_dir / ".draft").exists()


def test_strict_import_does_not_write_when_required_fields_missing(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text("---\npersona_id: fengxiaomeng\n---\n# 空\n", encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=True)

    assert result.report.has_errors
    assert not (source_dir / ".draft").exists()


def test_pending_freeze_copies_draft_and_source(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    payload = writer.pending_freeze("fengxiaomeng")

    assert payload["ok"] is True
    pending = source_dir / "_pending_freeze"
    assert (pending / "persona.yaml").is_file()
    assert (pending / "source.frozen.md").read_text(encoding="utf-8") == MINIMAL_SOURCE


def test_cli_importer_supports_explicit_root(tmp_path: Path, capsys) -> None:
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
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["persona_id"] == "fengxiaomeng-v2"
    assert (source_dir / ".draft" / "_import_report.json").is_file()


@pytest.mark.asyncio
async def test_persona_llm_extractor_uses_persona_import_task() -> None:
    captured: list[LLMRequest] = []

    class _LLM:
        async def _call(self, request: LLMRequest):
            captured.append(request)
            return {"text": '{"items":[]}'}

    extractor = PersonaLLMExtractor(_LLM())
    payload = await extractor.extract_json(source_text="# hi", instruction="extract")

    assert payload == {"items": []}
    assert captured[0].task == "persona_import"
    assert captured[0].requires_capabilities == ("json",)


def test_filter_items_with_source_span_drops_unanchored_items() -> None:
    items = [
        {"text": "kept", "source_span": {"file": "source.md", "lines": [3, 4]}},
        {"text": "drop"},
        {"text": "drop bad line", "source_span": {"lines": [0, 4]}},
    ]

    assert filter_items_with_source_span(items) == [items[0]]


def _write_persona_dir(tmp_path: Path, source_text: str) -> tuple[Path, Path, Path]:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source_text, encoding="utf-8")
    return persona_root, defaults, source_dir


def test_persona_importer_legacy_instruction_md_opt_out_default(tmp_path: Path) -> None:
    persona_root, defaults, source_dir = _write_persona_dir(tmp_path, MINIMAL_SOURCE)
    legacy_path = source_dir / "instruction.md"
    legacy_path.write_text("- 不读这个文件\n", encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    assert all(field.extractor != "legacy_instruction_md_opt_in" for field in result.report.fields)
    instructions = result.draft["guard.yaml"]["behavior_instructions"]
    assert all("不读这个文件" not in (item.get("text", "")) for item in instructions["items"])


def test_persona_importer_legacy_instruction_md_appends_items(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        'language: zh-CN\nlegacy_instruction_md: true\nlegacy_instruction_md_path: "./instruction.md"\n---',
    ) + """

# 8.4 行为指令

- source 内的指令
"""
    persona_root, defaults, source_dir = _write_persona_dir(tmp_path, source)
    legacy_path = source_dir / "instruction.md"
    legacy_path.write_text(
        "# 底线\n\n- 长度：默认只回一句话\n- 不用 Markdown\n",
        encoding="utf-8",
    )

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    items = result.draft["guard.yaml"]["behavior_instructions"]["items"]
    assert items[0]["text"] == "source 内的指令"
    assert [item["text"] for item in items[1:]] == ["长度：默认只回一句话", "不用 Markdown"]
    assert items[1]["origin_anchor"].startswith("instruction.md#L")
    assert items[1]["review_status"] == "candidate"

    extractors = [field.extractor for field in result.report.fields if field.file == "guard.yaml"]
    assert extractors.count("legacy_instruction_md_opt_in") == 2
    assert any(field.extractor == "behavior_instruction_md" for field in result.report.fields)


def test_persona_importer_legacy_instruction_md_path_missing(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        "language: zh-CN\nlegacy_instruction_md: true\n---",
    )
    persona_root, defaults, _ = _write_persona_dir(tmp_path, source)

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    codes = [issue.code for issue in result.report.issues]
    assert "legacy_instruction_md_path_missing" in codes
    assert all(field.extractor != "legacy_instruction_md_opt_in" for field in result.report.fields)


def test_persona_importer_legacy_instruction_md_file_not_found(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        'language: zh-CN\nlegacy_instruction_md: true\nlegacy_instruction_md_path: "./does-not-exist.md"\n---',
    )
    persona_root, defaults, _ = _write_persona_dir(tmp_path, source)

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    result = writer.import_source("fengxiaomeng", strict=False)

    codes = [issue.code for issue in result.report.issues]
    assert "legacy_instruction_md_file_not_found" in codes
    assert all(field.extractor != "legacy_instruction_md_opt_in" for field in result.report.fields)
