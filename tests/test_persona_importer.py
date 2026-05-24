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
