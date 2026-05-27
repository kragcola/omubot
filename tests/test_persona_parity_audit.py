"""Persona v2 → v1 parity audit regression."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.identity import Identity
from services.llm.client import _GROUP_REPLY_STYLE_HINTS
from services.persona import PersonaDraftWriter
from services.persona.compiler import (
    _format_group_profile_fragment as _compiler_fragment,
)
from services.persona.compiler import compile_persona_dry_run
from services.persona.parity_audit import (
    GROUP_PROFILE_EXTENDED_FIELDS,
    REPLY_STYLE_HINTS_REFERENCE,
    GroupOverrideSnapshot,
    compare_v1_vs_v2_dry_run,
)
from services.persona.parity_audit import (
    _format_group_profile_fragment as _parity_fragment,
)
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults

INSTRUCTION_BLOCK = """

# 8.4 行为指令

- 默认只回一句话
- 不用 Markdown
"""

GROUP_PROFILE_FRONT_MATTER = (
    "language: zh-CN\n"
    "bot_self_id_hint: \"10000\"\n"
    "known_bot_self_ids: [\"10000\", \"20000\"]\n"
    "group_profiles:\n"
    "  \"12345\":\n"
    "    reply_style: playful\n"
    "    custom_prompt: 多接梗，少说教。\n"
    "---"
)


GROUP_PROFILE_FRONT_MATTER_FULL = (
    "language: zh-CN\n"
    "bot_self_id_hint: \"10000\"\n"
    "known_bot_self_ids: [\"10000\", \"20000\"]\n"
    "group_profiles:\n"
    "  \"12345\":\n"
    "    reply_style: playful\n"
    "    custom_prompt: 多接梗，少说教。\n"
    "    presence_mode: silent_learn\n"
    "    at_only: true\n"
    "    talk_value: 0.4\n"
    "    planner_smooth: 0.7\n"
    "    debounce_seconds: 6\n"
    "    batch_size: 4\n"
    "    history_load_count: 30\n"
    "    tools_enabled: true\n"
    "    allowed_tools: [chat, memo]\n"
    "    blocked_tools: [bilibili]\n"
    "    sticker_mode: rarely\n"
    "    slang_enabled: true\n"
    "    blocked_users: [9001, 9002]\n"
    "---"
)


def _import_and_compile(tmp_path: Path, source_text: str):
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source_text, encoding="utf-8")

    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    return compile_persona_dry_run(
        "fengxiaomeng",
        persona_root=persona_root,
        defaults_dir=defaults,
    )


@pytest.fixture
def identity() -> Identity:
    return Identity(
        id="fengxiaomeng",
        name="凤笑梦",
        personality="一句话角色：群聊中的拟人 bot，元气、反应快、有一点调皮。",
        proactive="## 插话方式\n- 看到群里有人提名字时主动接一句",
    )


def test_reply_style_hints_reference_matches_runtime() -> None:
    """v1 hint table is mirrored by parity audit; mismatch fails the audit."""

    assert REPLY_STYLE_HINTS_REFERENCE == _GROUP_REPLY_STYLE_HINTS


def test_parity_happy_path_aligned(tmp_path: Path, identity: Identity) -> None:
    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", GROUP_PROFILE_FRONT_MATTER
    ) + INSTRUCTION_BLOCK
    compile_result = _import_and_compile(tmp_path, source_text)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="10000",
        instruction_text="默认只回一句话\n不用 Markdown",
        admins=None,
        proactive=None,
        group_override=GroupOverrideSnapshot(
            reply_style="playful",
            custom_prompt="多接梗，少说教。",
        ),
        compile_result=compile_result,
    )

    statuses = {f.axis: f.status for f in report.findings}
    assert statuses["identity_personality"] == "aligned"
    assert statuses["bot_self_id"] == "aligned"
    assert statuses["behavior_instruction"] == "aligned"
    assert statuses["group_profile"] == "aligned"
    assert statuses["admins"] == "not_applicable"
    assert statuses["proactive_rules"] == "not_applicable"
    assert report.has_divergence is False


def test_parity_marks_admins_and_proactive_as_v1_only(
    tmp_path: Path, identity: Identity
) -> None:
    compile_result = _import_and_compile(tmp_path, MINIMAL_SOURCE)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins={"123456": "管理员小张"},
        proactive=identity.proactive,
        group_override=None,
        compile_result=compile_result,
    )

    statuses = {f.axis: f.status for f in report.findings}
    assert statuses["admins"] == "v1_only"
    assert statuses["proactive_rules"] == "v1_only"
    assert statuses["bot_self_id"] == "not_applicable"
    assert statuses["behavior_instruction"] == "not_applicable"
    assert statuses["group_profile"] == "not_applicable"
    assert "v1_only" in {f.status for f in report.findings}
    assert "admins" in report.v1_only_axes
    assert "proactive_rules" in report.v1_only_axes


def test_parity_detects_missing_behavior_instruction(
    tmp_path: Path, identity: Identity
) -> None:
    compile_result = _import_and_compile(tmp_path, MINIMAL_SOURCE)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="默认只回一句话",
        admins=None,
        proactive=None,
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(
        f for f in report.findings if f.axis == "behavior_instruction"
    )
    assert finding.status == "divergent"
    assert "缺『行为指令：』段" in finding.notes


def test_parity_handles_partial_group_override(
    tmp_path: Path, identity: Identity
) -> None:
    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", GROUP_PROFILE_FRONT_MATTER
    )
    compile_result = _import_and_compile(tmp_path, source_text)

    custom_only = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive=None,
        group_override=GroupOverrideSnapshot(custom_prompt="多接梗，少说教。"),
        compile_result=compile_result,
    )
    assert {
        f.axis: f.status for f in custom_only.findings
    }["group_profile"] == "aligned"

    empty_override = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive=None,
        group_override=GroupOverrideSnapshot(),
        compile_result=compile_result,
    )
    assert {
        f.axis: f.status for f in empty_override.findings
    }["group_profile"] == "not_applicable"


def test_parity_to_dict_serialization(tmp_path: Path, identity: Identity) -> None:
    compile_result = _import_and_compile(tmp_path, MINIMAL_SOURCE)
    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive=None,
        group_override=None,
        compile_result=compile_result,
    )

    payload = report.to_dict()
    assert payload["persona_id"] == "fengxiaomeng-v2"
    assert payload["has_divergence"] is False
    assert {item["axis"] for item in payload["findings"]} == {
        "identity_personality",
        "bot_self_id",
        "behavior_instruction",
        "admins",
        "proactive_rules",
        "group_profile",
    }
    for item in payload["findings"]:
        assert set(item.keys()) == {"axis", "status", "v1_signal", "v2_signal", "notes"}


def test_group_profile_fragment_matches_compiler() -> None:
    """parity audit must format group profile fragments identically to compiler."""

    cases = [
        ("at_only", True),
        ("at_only", False),
        ("talk_value", 0.4),
        ("debounce_seconds", 6),
        ("batch_size", 4),
        ("allowed_tools", ["chat", "memo"]),
        ("blocked_users", [9001, 9002]),
        ("sticker_mode", "chat_only"),
        ("presence_mode", "lurker"),
    ]
    for field, value in cases:
        assert _parity_fragment(field, value) == _compiler_fragment(field, value)


def test_parity_group_profile_fields_skips_when_only_prompt_fields(
    tmp_path: Path, identity: Identity
) -> None:
    """Snapshot with only reply_style/custom_prompt → no group_profile.fields finding."""

    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", GROUP_PROFILE_FRONT_MATTER
    )
    compile_result = _import_and_compile(tmp_path, source_text)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive=None,
        group_override=GroupOverrideSnapshot(
            reply_style="playful",
            custom_prompt="多接梗，少说教。",
        ),
        compile_result=compile_result,
    )

    axes = {f.axis for f in report.findings}
    assert "group_profile.fields" not in axes


def test_parity_group_profile_fields_v2_extended_full_coverage(
    tmp_path: Path, identity: Identity
) -> None:
    """All 13 extended fields landed in v2 dry-run → status=v2_extended."""

    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", GROUP_PROFILE_FRONT_MATTER_FULL
    )
    compile_result = _import_and_compile(tmp_path, source_text)

    snapshot = GroupOverrideSnapshot(
        reply_style="playful",
        custom_prompt="多接梗，少说教。",
        presence_mode="silent_learn",
        at_only=True,
        talk_value=0.4,
        planner_smooth=0.7,
        debounce_seconds=6.0,
        batch_size=4,
        history_load_count=30,
        tools_enabled=True,
        allowed_tools=["chat", "memo"],
        blocked_tools=["bilibili"],
        sticker_mode="rarely",
        slang_enabled=True,
        blocked_users=[9001, 9002],
    )
    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive=None,
        group_override=snapshot,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "group_profile.fields")
    assert finding.status == "v2_extended"
    assert "已 dry-run 输出" in finding.v2_signal
    assert report.has_divergence is False


def test_parity_group_profile_fields_divergent_when_compiler_drops_field(
    tmp_path: Path, identity: Identity, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop a field from compiler block → status=divergent listing the gap."""

    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", GROUP_PROFILE_FRONT_MATTER_FULL
    )
    compile_result = _import_and_compile(tmp_path, source_text)

    # Strip batch_size=4 from the runtime.group_profile block to simulate drift.
    blocks = list(compile_result.prompt_blocks)
    for index, block in enumerate(blocks):
        if block.module_id == "runtime.group_profile":
            mutated_text = block.text.replace("；batch_size=4", "")
            blocks[index] = type(block)(
                module_id=block.module_id,
                label=block.label,
                text=mutated_text,
                position=block.position,
            )
            break
    mutated_result = type(compile_result)(
        ok=compile_result.ok,
        mode=compile_result.mode,
        persona_id=compile_result.persona_id,
        prompt_blocks=tuple(blocks),
        module_order=compile_result.module_order,
        warnings=compile_result.warnings,
        errors=compile_result.errors,
    )

    snapshot = GroupOverrideSnapshot(
        reply_style="playful",
        custom_prompt="多接梗，少说教。",
        batch_size=4,
        at_only=True,
    )
    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive=None,
        group_override=snapshot,
        compile_result=mutated_result,
    )

    finding = next(f for f in report.findings if f.axis == "group_profile.fields")
    assert finding.status == "divergent"
    assert "batch_size=4" in finding.notes
    assert "at_only=true" not in finding.notes  # at_only锚点仍然存在
    assert report.has_divergence is True


def test_parity_group_profile_fields_v1_only_when_block_missing(
    identity: Identity,
) -> None:
    """No runtime.group_profile block at all → status=v1_only."""

    from services.persona.compiler import CompileResult

    empty = CompileResult(
        ok=True,
        mode="dry_run",
        persona_id="fengxiaomeng-v2",
        prompt_blocks=(),
    )
    snapshot = GroupOverrideSnapshot(at_only=True, batch_size=4)
    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive=None,
        group_override=snapshot,
        compile_result=empty,
    )

    finding = next(f for f in report.findings if f.axis == "group_profile.fields")
    assert finding.status == "v1_only"
    assert "BotConfig.group.overrides" in finding.v1_signal


def test_extended_fields_constant_covers_all_kernel_override_fields() -> None:
    """Drift guard: keep parity audit's extended-field tuple aligned with kernel.config.GroupOverride."""

    from kernel.config import GroupOverride

    # Two fields are surfaced by v1 prompt and have their own axis; the rest
    # are tracked via the v2_extended status.
    handled_by_prompt = {"reply_style", "custom_prompt"}
    kernel_fields = set(GroupOverride.model_fields.keys())
    expected = kernel_fields - handled_by_prompt
    assert set(GROUP_PROFILE_EXTENDED_FIELDS) == expected


ADMINS_FRONT_MATTER = (
    "language: zh-CN\n"
    "admins:\n"
    "  \"123456\": 管理员小张\n"
    "  \"234567\": 管理员小李\n"
    "---"
)


PROACTIVE_BLOCK = """

## 插话方式

看到群里有人提到名字时主动接一句。

不要在严肃话题上抖机灵。
"""


def test_parity_admins_aligned_when_v2_emits_admin_block(
    tmp_path: Path, identity: Identity
) -> None:
    """admins front matter → adapter.permissions.admins[] → runtime.adapter prompt → aligned."""

    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", ADMINS_FRONT_MATTER
    )
    compile_result = _import_and_compile(tmp_path, source_text)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins={"123456": "管理员小张", "234567": "管理员小李"},
        proactive=None,
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "admins")
    assert finding.status == "aligned"
    assert "【管理员】" in finding.v2_signal
    assert report.has_divergence is False


def test_parity_admins_v1_only_when_source_has_no_admins(
    tmp_path: Path, identity: Identity
) -> None:
    """source.md without admins → adapter has no admins → status v1_only."""

    compile_result = _import_and_compile(tmp_path, MINIMAL_SOURCE)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins={"123456": "管理员小张"},
        proactive=None,
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "admins")
    assert finding.status == "v1_only"
    assert "@123456" in finding.v1_signal


def test_parity_proactive_aligned_when_section_present(
    tmp_path: Path, identity: Identity
) -> None:
    """`## 插话方式` section → identity.proactive_rules → core.guard prompt → aligned."""

    source_text = MINIMAL_SOURCE + PROACTIVE_BLOCK
    compile_result = _import_and_compile(tmp_path, source_text)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive="看到群里有人提到名字时主动接一句。\n不要在严肃话题上抖机灵。",
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "proactive_rules")
    assert finding.status == "aligned"
    assert "插话方式" in finding.v2_signal
    assert report.has_divergence is False


def test_parity_proactive_v1_only_when_section_missing(
    tmp_path: Path, identity: Identity
) -> None:
    """source.md without `## 插话方式` → core.guard has no proactive segment → status v1_only."""

    compile_result = _import_and_compile(tmp_path, MINIMAL_SOURCE)

    report = compare_v1_vs_v2_dry_run(
        identity=identity,
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive="看到群里有人提到名字时主动接一句。",
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "proactive_rules")
    assert finding.status == "v1_only"
    assert "看到群里有人提到名字时主动接一句" in finding.v1_signal


def test_persona_draft_landed_proactive_rules_field(
    tmp_path: Path,
) -> None:
    """A2 builder regression: proactive_rules section must land in identity.proactive_rules."""

    import yaml

    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE + PROACTIVE_BLOCK, encoding="utf-8")
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)

    persona_yaml = yaml.safe_load(
        (persona_root / "fengxiaomeng-v2" / ".draft" / "persona.yaml").read_text(encoding="utf-8")
    )
    assert "看到群里有人提到名字时主动接一句" in persona_yaml["identity"]["proactive_rules"]


# ---------------------------------------------------------------------------
# Anchor robustness regressions (D1: shadow log of fengxiaomeng-v2 was full of
# false-positive divergences because the v1 first line was either a markdown
# header or a narrative prefix that v2's yaml-styled compile output never
# carried verbatim. Anchor matching now scans the first 5 meaningful lines.)
# ---------------------------------------------------------------------------


def test_identity_anchor_matches_when_v1_first_line_is_narrative(
    tmp_path: Path,
) -> None:
    """v1 personality first line is a sentence; v2 core.identity rephrases it
    as ``名字：…`` but still carries the canonical name later. Should align."""

    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", GROUP_PROFILE_FRONT_MATTER
    ) + INSTRUCTION_BLOCK
    compile_result = _import_and_compile(tmp_path, source_text)

    narrative_identity = Identity(
        id="fengxiaomeng",
        name="凤笑梦",
        personality=(
            "你是一个 QQ 群聊机器人。\n"  # noisy first line, no canonical name
            "一句话角色：群聊中的拟人 bot，元气、反应快、有一点调皮。\n"  # actual anchor on line 2
            "- 自称：我"
        ),
    )

    report = compare_v1_vs_v2_dry_run(
        identity=narrative_identity,
        bot_self_id="10000",
        instruction_text="默认只回一句话",
        admins=None,
        proactive=None,
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "identity_personality")
    assert finding.status == "aligned"


def test_instruction_anchor_skips_markdown_header(tmp_path: Path) -> None:
    """v1 instruction.md may start with ``## 底线规则…`` style header; v2
    core.guard renders ``行为指令：默认只回一句话`` without the markdown
    prefix. Anchor matcher must skip the header and use the next bullet."""

    compile_result = _import_and_compile(tmp_path, MINIMAL_SOURCE)

    report = compare_v1_vs_v2_dry_run(
        identity=Identity(id="x", name="x", personality="一句话角色：测试 bot"),
        bot_self_id="",
        instruction_text=(
            "## 底线规则（每次回复前必查）\n"
            "- 默认只回一句话\n"
            "- 不用 Markdown\n"
        ),
        admins=None,
        proactive=None,
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "behavior_instruction")
    assert finding.status == "divergent"
    assert "缺『行为指令：』段" in finding.notes
    # The anchor we surface must NOT be the markdown header — that's the bug we
    # are fixing. The first meaningful anchor is the bullet body.
    assert finding.v1_signal != "## 底线规则（每次回复前必查）"
    assert "默认只回一句话" in finding.v1_signal


def test_instruction_anchor_aligned_when_bullet_is_present(tmp_path: Path) -> None:
    """Same shape as above but with INSTRUCTION_BLOCK in source — the
    bullet body is present in v2 core.guard. Must align even though v1 line 1
    is a markdown header."""

    source_text = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---", GROUP_PROFILE_FRONT_MATTER
    ) + INSTRUCTION_BLOCK
    compile_result = _import_and_compile(tmp_path, source_text)

    report = compare_v1_vs_v2_dry_run(
        identity=Identity(id="x", name="x", personality="一句话角色：测试 bot"),
        bot_self_id="10000",
        instruction_text=(
            "## 底线规则（每次回复前必查）\n"
            "- 默认只回一句话\n"
            "- 不用 Markdown\n"
        ),
        admins=None,
        proactive=None,
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "behavior_instruction")
    assert finding.status == "aligned"


def test_proactive_anchor_skips_markdown_header(tmp_path: Path) -> None:
    """``identity.proactive`` typically starts with ``## 插话方式`` header in
    v1. v2 core.guard exposes the section as ``插话方式：…``. Anchor matcher
    must skip the header and align on the bullet body."""

    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(
        MINIMAL_SOURCE
        + "\n"
        + "## 插话方式\n\n- 看到群里有人提到名字时主动接一句\n",
        encoding="utf-8",
    )
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults)
    writer.import_source("fengxiaomeng", strict=False)
    compile_result = compile_persona_dry_run(
        "fengxiaomeng", persona_root=persona_root, defaults_dir=defaults
    )

    report = compare_v1_vs_v2_dry_run(
        identity=Identity(
            id="x",
            name="x",
            personality="一句话角色：测试 bot",
            proactive="## 插话方式\n- 看到群里有人提到名字时主动接一句",
        ),
        bot_self_id="",
        instruction_text="",
        admins=None,
        proactive="## 插话方式\n- 看到群里有人提到名字时主动接一句",
        group_override=None,
        compile_result=compile_result,
    )

    finding = next(f for f in report.findings if f.axis == "proactive_rules")
    assert finding.status == "aligned"
    assert finding.v1_signal != "## 插话方式"
