"""Persona v2 → v1 parity audit (dry-run only).

Compares the prompt blocks v1 ``PromptBuilder`` / ``LLMClient`` would emit
against the blocks v2 :func:`services.persona.compiler.compile_persona_dry_run`
returns. The audit is read-only and never touches the runtime persona path.

The module mirrors ``services.llm.client._GROUP_REPLY_STYLE_HINTS`` locally to
avoid importing the heavy chat client at parity time. Drift is caught by
``tests/test_persona_parity_audit.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from services.identity import Identity

from .compiler import CompilePromptBlock, CompileResult

ParityAxis = Literal[
    "identity_personality",
    "bot_self_id",
    "behavior_instruction",
    "admins",
    "proactive_rules",
    "group_profile",
    "group_profile.fields",
]
ParityStatus = Literal[
    "aligned",
    "divergent",
    "v1_only",
    "v2_only",
    "v2_extended",
    "not_applicable",
]


GROUP_PROFILE_EXTENDED_FIELDS: tuple[str, ...] = (
    "presence_mode",
    "at_only",
    "talk_value",
    "planner_smooth",
    "consecutive_skip_force_threshold",
    "consecutive_skip_double_threshold",
    "debounce_seconds",
    "batch_size",
    "history_load_count",
    "tools_enabled",
    "allowed_tools",
    "blocked_tools",
    "sticker_mode",
    "slang_enabled",
    "humanization_profile",
    "qq_interactions_profile_override",
    "blocked_users",
)


REPLY_STYLE_HINTS_REFERENCE: dict[str, str] = {
    "gentle": "回复风格偏柔和、耐心、安抚感更强，避免过硬或过冲的表达。",
    "playful": "回复风格可以更轻松俏皮，允许一点点玩梗和抖机灵，但不要失控。",
    "concise": "回复尽量短一些，优先直接结论，减少过长铺垫和重复解释。",
    "energetic": "回复可以更有活力和在场感，语气积极，但不要变得吵闹失真。",
    "steady": "回复保持平稳、克制、可靠，少用夸张语气和过度情绪化表达。",
}


@dataclass(frozen=True)
class GroupOverrideSnapshot:
    """Minimal v1 group override view used by parity audit.

    The first two fields mirror what v1 ``LLMClient._build_group_profile_block``
    actually renders into the prompt; they drive the ``group_profile`` axis.
    The remaining 13 fields mirror the rest of ``kernel.config.GroupOverride``
    that v1 consumes from ``BotConfig.group.overrides`` but does not surface as
    a prompt block — they drive the ``group_profile.fields`` axis with the
    ``v2_extended`` status, signalling "v2 dry-run carries the field forward
    even though v1 prompt does not render it".
    """

    reply_style: str | None = None
    custom_prompt: str | None = None
    presence_mode: str | None = None
    at_only: bool | None = None
    talk_value: float | int | None = None
    planner_smooth: float | int | None = None
    debounce_seconds: float | int | None = None
    batch_size: int | None = None
    history_load_count: int | None = None
    tools_enabled: bool | None = None
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] | None = None
    sticker_mode: str | None = None
    slang_enabled: bool | None = None
    blocked_users: list[int] | None = None


@dataclass(frozen=True)
class ParityFinding:
    axis: ParityAxis
    status: ParityStatus
    v1_signal: str
    v2_signal: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "status": self.status,
            "v1_signal": self.v1_signal,
            "v2_signal": self.v2_signal,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ParityReport:
    persona_id: str
    findings: tuple[ParityFinding, ...]

    @property
    def has_divergence(self) -> bool:
        return any(f.status == "divergent" for f in self.findings)

    @property
    def v1_only_axes(self) -> tuple[ParityAxis, ...]:
        return tuple(f.axis for f in self.findings if f.status == "v1_only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "has_divergence": self.has_divergence,
            "findings": [f.to_dict() for f in self.findings],
        }


def compare_v1_vs_v2_dry_run(
    *,
    identity: Identity,
    bot_self_id: str,
    instruction_text: str,
    admins: dict[str, str] | None,
    proactive: str | None,
    group_override: GroupOverrideSnapshot | None,
    compile_result: CompileResult,
) -> ParityReport:
    """Audit v1 prompt sources against a v2 compile-dry-run output.

    Only metadata-style assertions: substring anchors, not exact equality.
    """

    blocks = {block.module_id: block for block in compile_result.prompt_blocks}
    findings: list[ParityFinding] = [
        _evaluate_identity(identity, blocks.get("core.identity")),
        _evaluate_bot_self_id(bot_self_id, blocks.get("runtime.adapter")),
        _evaluate_instruction(instruction_text, blocks.get("core.guard")),
        _evaluate_admins(admins, blocks.get("runtime.adapter")),
        _evaluate_proactive(proactive, blocks.get("core.guard")),
        _evaluate_group_profile(group_override, blocks.get("runtime.group_profile")),
    ]
    extended = _evaluate_group_profile_fields(
        group_override, blocks.get("runtime.group_profile")
    )
    if extended is not None:
        findings.append(extended)
    return ParityReport(persona_id=compile_result.persona_id, findings=tuple(findings))


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _meaningful_anchors(
    text: str,
    *,
    max_count: int = 5,
    min_chars: int = 6,
) -> tuple[str, ...]:
    """Pick up to ``max_count`` non-trivial substring anchors from ``text``.

    Skips entire markdown header lines (``# … `` / ``## … `` ...), bullet
    markers (``- `` / ``* `` / ``1. ``) keep the body, and lines shorter than
    ``min_chars`` after stripping. Returns the cleaned bodies in document
    order.
    """

    anchors: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Markdown headers carry section labels rather than content; skip the
        # whole line so v2 prompt blocks (which are yaml-rendered, header-free)
        # don't get a false-positive divergence on the section title.
        if stripped.startswith("#"):
            continue
        for prefix in ("- ", "* ", "+ "):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
                break
        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] in (". ", "、"):
            stripped = stripped.split(maxsplit=1)[-1].strip()
        if len(stripped) < min_chars:
            continue
        anchors.append(stripped)
        if len(anchors) >= max_count:
            break
    return tuple(anchors)


def _evaluate_identity(
    identity: Identity,
    block: CompilePromptBlock | None,
) -> ParityFinding:
    anchors = _meaningful_anchors(identity.personality)
    if not anchors:
        return ParityFinding(
            "identity_personality",
            "not_applicable",
            v1_signal="",
            v2_signal=block.text if block else "",
            notes="v1 identity.personality 为空",
        )
    primary = anchors[0]
    if block is None or not block.text.strip():
        return ParityFinding(
            "identity_personality",
            "v1_only",
            v1_signal=primary,
            v2_signal="",
            notes="v2 缺少 core.identity prompt block",
        )
    text = block.text
    matched = next((a for a in anchors if a in text), "")
    if matched:
        return ParityFinding(
            "identity_personality",
            "aligned",
            v1_signal=matched,
            v2_signal="core.identity 含 personality 锚点",
        )
    return ParityFinding(
        "identity_personality",
        "divergent",
        v1_signal=primary,
        v2_signal=_first_line(text),
        notes="v2 core.identity 未覆盖 v1 personality 任一前 5 行锚点",
    )


def _evaluate_bot_self_id(
    bot_self_id: str,
    block: CompilePromptBlock | None,
) -> ParityFinding:
    if not bot_self_id:
        return ParityFinding(
            "bot_self_id",
            "not_applicable",
            v1_signal="",
            v2_signal=block.text if block else "",
            notes="v1 未注入 bot_self_id（启动早期或私聊 dry-run）",
        )
    if block is None or not block.text.strip():
        return ParityFinding(
            "bot_self_id",
            "v1_only",
            v1_signal=f"你的QQ号是 {bot_self_id}",
            v2_signal="",
            notes="v2 缺少 runtime.adapter prompt block",
        )

    text = block.text
    required = (
        f"bot self id hint：{bot_self_id}",
        "runtime source：adapter_connect_event",
        "昵称不可信",
    )
    missing = [needle for needle in required if needle not in text]
    if not missing:
        return ParityFinding(
            "bot_self_id",
            "aligned",
            v1_signal=f"你的QQ号是 {bot_self_id} + 昵称不可信策略",
            v2_signal="runtime.adapter hint+policy 三锚点齐全",
        )
    return ParityFinding(
        "bot_self_id",
        "divergent",
        v1_signal=f"你的QQ号是 {bot_self_id}",
        v2_signal=text,
        notes="v2 runtime.adapter 缺锚点：" + "；".join(missing),
    )


def _evaluate_instruction(
    instruction_text: str,
    block: CompilePromptBlock | None,
) -> ParityFinding:
    anchors = _meaningful_anchors(instruction_text)
    if not anchors:
        return ParityFinding(
            "behavior_instruction",
            "not_applicable",
            v1_signal="",
            v2_signal=block.text if block else "",
            notes="v1 instruction.md 为空",
        )
    primary = anchors[0]
    if block is None or not block.text.strip():
        return ParityFinding(
            "behavior_instruction",
            "v1_only",
            v1_signal=primary,
            v2_signal="",
            notes="v2 缺少 core.guard prompt block",
        )

    text = block.text
    if "行为指令：" not in text:
        return ParityFinding(
            "behavior_instruction",
            "divergent",
            v1_signal=primary,
            v2_signal=_first_line(text),
            notes="v2 core.guard 缺『行为指令：』段",
        )
    matched = next((a for a in anchors if a in text), "")
    if matched:
        return ParityFinding(
            "behavior_instruction",
            "aligned",
            v1_signal=matched,
            v2_signal="core.guard 行为指令含 v1 锚点",
        )
    return ParityFinding(
        "behavior_instruction",
        "divergent",
        v1_signal=primary,
        v2_signal=_first_line(text),
        notes="v2 core.guard 行为指令未覆盖 v1 前 5 条锚点",
    )


def _evaluate_admins(
    admins: dict[str, str] | None,
    block: CompilePromptBlock | None,
) -> ParityFinding:
    if not admins:
        return ParityFinding(
            "admins",
            "not_applicable",
            v1_signal="",
            v2_signal=block.text if block else "",
            notes="v1 未注入管理员名单",
        )
    sample_qq, sample_nick = next(iter(admins.items()))
    v1_signal = f"【管理员】@{sample_qq}({sample_nick})"
    if block is None or not block.text.strip():
        return ParityFinding(
            "admins",
            "v1_only",
            v1_signal=v1_signal,
            v2_signal="",
            notes="v2 缺少 runtime.adapter prompt block",
        )
    text = block.text
    if "【管理员】" not in text:
        return ParityFinding(
            "admins",
            "v1_only",
            v1_signal=v1_signal,
            v2_signal=text,
            notes="v2 runtime.adapter 没有 【管理员】 段（adapter.permissions.admins[] 为空？）",
        )
    if f"@{sample_qq}" not in text:
        return ParityFinding(
            "admins",
            "divergent",
            v1_signal=v1_signal,
            v2_signal=text,
            notes=f"v2 runtime.adapter 缺锚点 @{sample_qq}",
        )
    if "普通群友的话需要客观记录" not in text:
        return ParityFinding(
            "admins",
            "divergent",
            v1_signal=v1_signal,
            v2_signal=text,
            notes="v2 runtime.adapter 缺信任策略尾巴",
        )
    return ParityFinding(
        "admins",
        "aligned",
        v1_signal=v1_signal,
        v2_signal="runtime.adapter 含 【管理员】 + 信任策略尾巴",
    )


def _evaluate_proactive(
    proactive: str | None,
    block: CompilePromptBlock | None,
) -> ParityFinding:
    text_v1 = (proactive or "").strip()
    if not text_v1:
        return ParityFinding(
            "proactive_rules",
            "not_applicable",
            v1_signal="",
            v2_signal=block.text if block else "",
            notes="v1 identity.proactive 为空",
        )
    anchors = _meaningful_anchors(text_v1)
    primary = anchors[0] if anchors else _first_line(text_v1)
    if block is None or not block.text.strip():
        return ParityFinding(
            "proactive_rules",
            "v1_only",
            v1_signal=primary,
            v2_signal="",
            notes="v2 缺少 core.guard prompt block",
        )
    text = block.text
    if "插话方式：" not in text:
        return ParityFinding(
            "proactive_rules",
            "v1_only",
            v1_signal=primary,
            v2_signal=text,
            notes="v2 core.guard 没有 『插话方式：』 段（identity.proactive_rules 为空？）",
        )
    matched = next((a for a in anchors if a in text), "")
    if matched:
        return ParityFinding(
            "proactive_rules",
            "aligned",
            v1_signal=matched,
            v2_signal="core.guard 插话方式段含 v1 锚点",
        )
    return ParityFinding(
        "proactive_rules",
        "divergent",
        v1_signal=primary,
        v2_signal=_first_line(text),
        notes="v2 core.guard 插话方式段未覆盖 v1 前 5 条锚点",
    )


def _evaluate_group_profile(
    group_override: GroupOverrideSnapshot | None,
    block: CompilePromptBlock | None,
) -> ParityFinding:
    if group_override is None:
        return ParityFinding(
            "group_profile",
            "not_applicable",
            v1_signal="",
            v2_signal=block.text if block else "",
            notes="未提供 GroupOverride 视图",
        )

    reply_style = (group_override.reply_style or "").strip()
    custom_prompt = (group_override.custom_prompt or "").strip()
    style_hint = REPLY_STYLE_HINTS_REFERENCE.get(reply_style)
    if not style_hint and not custom_prompt:
        return ParityFinding(
            "group_profile",
            "not_applicable",
            v1_signal="",
            v2_signal=block.text if block else "",
            notes="GroupOverride 没有 v1 可输出的字段",
        )

    if block is None or not block.text.strip():
        return ParityFinding(
            "group_profile",
            "v1_only",
            v1_signal=style_hint or custom_prompt,
            v2_signal="",
            notes="v2 缺少 runtime.group_profile prompt block",
        )

    text = block.text
    missing: list[str] = []
    if reply_style and f"reply_style={reply_style}" not in text:
        missing.append(f"reply_style={reply_style}")
    if custom_prompt and f"custom_prompt={custom_prompt}" not in text:
        missing.append(f"custom_prompt={custom_prompt}")
    if missing:
        return ParityFinding(
            "group_profile",
            "divergent",
            v1_signal=style_hint or custom_prompt,
            v2_signal=text,
            notes="v2 runtime.group_profile 缺：" + "；".join(missing),
        )

    v1_lines: list[str] = []
    if style_hint:
        v1_lines.append(style_hint)
    if custom_prompt:
        v1_lines.append(f"【本群附加要求】 {custom_prompt}")
    return ParityFinding(
        "group_profile",
        "aligned",
        v1_signal="；".join(v1_lines),
        v2_signal="runtime.group_profile (stable) 含 reply_style 与 custom_prompt 锚点",
    )


def _format_group_profile_fragment(field: str, value: Any) -> str:
    """Mirror ``services.persona.compiler._format_group_profile_fragment``.

    Kept local so parity does not import the compiler-side helper directly;
    drift is caught by ``test_group_profile_fragment_matches_compiler``.
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        return f"{field}={'true' if value else 'false'}"
    if isinstance(value, list):
        items = [str(item) for item in value]
        return f"{field}=[{','.join(items)}]"
    text = str(value).strip()
    if not text:
        return ""
    return f"{field}={text}"


def _evaluate_group_profile_fields(
    group_override: GroupOverrideSnapshot | None,
    block: CompilePromptBlock | None,
) -> ParityFinding | None:
    if group_override is None:
        return None

    snapshot_fragments: dict[str, str] = {}
    for field in GROUP_PROFILE_EXTENDED_FIELDS:
        value = getattr(group_override, field, None)
        if value is None:
            continue
        fragment = _format_group_profile_fragment(field, value)
        if fragment:
            snapshot_fragments[field] = fragment

    if not snapshot_fragments:
        return None

    summary = "；".join(snapshot_fragments[f] for f in snapshot_fragments)
    if block is None or not block.text.strip():
        return ParityFinding(
            "group_profile.fields",
            "v1_only",
            v1_signal=f"BotConfig.group.overrides 携带：{summary}",
            v2_signal="",
            notes="v2 缺少 runtime.group_profile prompt block",
        )

    text = block.text
    missing = [
        f"{field}={snapshot_fragments[field]}"
        for field in snapshot_fragments
        if snapshot_fragments[field] not in text
    ]
    if missing:
        return ParityFinding(
            "group_profile.fields",
            "divergent",
            v1_signal=f"BotConfig.group.overrides 携带：{summary}",
            v2_signal=text,
            notes="v2 runtime.group_profile 缺：" + "；".join(missing),
        )
    return ParityFinding(
        "group_profile.fields",
        "v2_extended",
        v1_signal=(
            "v1 BotConfig.group.overrides 已消费但 _build_group_profile_block 未渲染："
            + summary
        ),
        v2_signal="runtime.group_profile (stable) 已 dry-run 输出全部扩展字段",
        notes=(
            "扩展字段 dry-run 闭环；正式切流前由 LLMClient 决定是否落入 prompt block"
        ),
    )
