"""Persona v2 compiler.

This module reads an importer draft (or its frozen copy under
``_pending_freeze/``) and returns prompt-block candidates plus SystemModule
order. Two entry points share the same body:

- :func:`compile_persona_dry_run` reads ``<persona_root>/<id>/.draft/`` and
  is consumed by the admin parity API and the importer CLI.
- :func:`compile_persona_runtime` reads
  ``<persona_root>/<id>/_pending_freeze/`` and is consumed by
  :mod:`services.persona.runtime` (added in B1.4) when
  ``BotConfig.persona_v2.runtime_consume`` is on.

Both entry points are read-only and never raise. Callers decide whether to
fall back to v1 based on ``CompileResult.ok``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from services.llm.persona_patterns import DECLARATION_PATTERNS
from services.system_module import catalog_contracts, validate_module_graph

from .writer import PersonaDraftWriter, persona_namespace


@dataclass(frozen=True)
class CompilePromptBlock:
    module_id: str
    label: str
    text: str
    position: str = "static"

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "label": self.label,
            "text": self.text,
            "position": self.position,
        }


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    mode: str
    persona_id: str
    prompt_blocks: tuple[CompilePromptBlock, ...] = ()
    module_order: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "persona_id": self.persona_id,
            "prompt_blocks": [block.to_dict() for block in self.prompt_blocks],
            "module_order": list(self.module_order),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def compile_persona_dry_run(
    persona_id: str,
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
) -> CompileResult:
    """Compile the importer draft for offline parity comparison.

    Reads ``<persona_root>/<persona_id>/.draft/`` and returns a CompileResult
    with ``mode='dry_run'``. Read-only and never raises.
    """
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults_dir)
    return _compile_internal(writer, persona_id, mode="dry_run")


def compile_persona_runtime(
    persona_id: str,
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
) -> CompileResult:
    """Compile a frozen persona for runtime consumption.

    Reads ``<persona_root>/<persona_id>/_pending_freeze/`` and returns a
    CompileResult with ``mode='runtime'`` on success or
    ``ok=False, errors=[...]`` on failure. Read-only and never raises.

    Callers (services/persona/runtime.py) decide whether to fall back to v1
    based on ``BotConfig.persona_v2.fallback_on_compile_error``.
    """
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults_dir)
    return _compile_internal(writer, persona_id, mode="runtime")


def _compile_internal(
    writer: PersonaDraftWriter,
    persona_id: str,
    *,
    mode: str,
) -> CompileResult:
    namespace = persona_namespace(persona_id)
    if mode == "dry_run":
        source_dir = writer.draft_dir(persona_id)
        missing_msg = "draft not found"
    elif mode == "runtime":
        source_dir = writer.pending_freeze_dir(persona_id)
        missing_msg = "pending freeze not found"
    else:
        return CompileResult(False, mode, namespace, errors=(f"unknown mode: {mode}",))

    if not source_dir.is_dir():
        return CompileResult(False, mode, namespace, errors=(missing_msg,))

    report = _read_report(source_dir)
    if report.get("status") == "error":
        return CompileResult(False, mode, namespace, errors=("import report has errors",))

    try:
        draft = _read_draft_files(source_dir)
    except yaml.YAMLError as exc:
        return CompileResult(False, mode, namespace, errors=(f"yaml parse error: {exc}",))

    graph = validate_module_graph(catalog_contracts())
    if not graph.ok:
        return CompileResult(
            False,
            mode,
            namespace,
            errors=tuple(issue.message for issue in graph.errors),
        )

    blocks = tuple(block for block in _build_prompt_blocks(draft) if block.text.strip())
    warnings: list[str] = []
    errors: list[str] = []
    errors.extend(_validate_no_declarations(blocks))
    if not blocks:
        warnings.append("no prompt blocks generated")
    if errors:
        return CompileResult(
            False,
            mode,
            namespace,
            prompt_blocks=blocks,
            module_order=graph.module_order,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )
    return CompileResult(
        True,
        mode,
        namespace,
        prompt_blocks=blocks,
        module_order=graph.module_order,
        warnings=tuple(warnings),
    )


def _read_report(draft_dir: Path) -> dict[str, Any]:
    path = draft_dir / "_import_report.json"
    if not path.is_file():
        return {"status": "error", "issues": [{"message": "missing import report"}]}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_draft_files(draft_dir: Path) -> dict[str, dict[str, Any]]:
    draft: dict[str, dict[str, Any]] = {}
    for path in draft_dir.glob("*.yaml"):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        draft[path.name] = payload if isinstance(payload, dict) else {}
    return draft


def _build_prompt_blocks(draft: dict[str, dict[str, Any]]) -> list[CompilePromptBlock]:
    persona = _as_dict(draft.get("persona.yaml"))
    voice = _as_dict(draft.get("voice.yaml"))
    adapter = _as_dict(draft.get("adapter.yaml"))
    knowledge = _as_dict(draft.get("knowledge.yaml"))
    examples = _as_dict(draft.get("examples.yaml"))
    guard = _as_dict(draft.get("guard.yaml"))
    runtime = _as_dict(draft.get("runtime.yaml"))
    return [
        CompilePromptBlock("core.identity", "身份宪法", _identity_text(persona)),
        CompilePromptBlock("runtime.adapter", "平台身份", _adapter_text(adapter), position="static"),
        CompilePromptBlock("runtime.group_profile", "群聊偏好", _group_profile_text(runtime), position="stable"),
        CompilePromptBlock("core.voice", "表达风格", _voice_text(voice)),
        CompilePromptBlock("core.knowledge", "知识边界", _knowledge_text(knowledge)),
        CompilePromptBlock("core.examples", "正反例", _examples_text(examples)),
        CompilePromptBlock("core.guard", "守门规则", _guard_text(persona, guard)),
    ]


def _identity_text(persona: dict[str, Any]) -> str:
    identity = _as_dict(persona.get("identity"))
    constitution = _as_dict(persona.get("constitution"))
    lines = [
        f"名字：{identity.get('canonical_name', '')}",
        f"角色：{identity.get('role', '')}",
        f"自称：{identity.get('self_reference', '')}",
        f"静态身份块：{identity.get('personality', '')}",
        "性格底色：" + "；".join(str(item) for item in _as_list(identity.get("essence"))),
        "价值观：" + "；".join(str(item) for item in _as_list(constitution.get("values"))),
    ]
    return "\n".join(line for line in lines if not line.endswith("：") and line.strip())


def _voice_text(voice: dict[str, Any]) -> str:
    principles = _as_dict(voice.get("style_principles"))
    tones = _as_list(voice.get("tone_palette"))
    lines = [
        "句子形态：" + "；".join(str(item) for item in _as_list(principles.get("sentence_shape"))),
        "禁用句式：" + "；".join(str(item) for item in _as_list(principles.get("banned_patterns"))),
        "语气集合：" + "；".join(str(item) for item in tones),
    ]
    return "\n".join(line for line in lines if not line.endswith("：") and line.strip())


def _adapter_text(adapter: dict[str, Any]) -> str:
    bot_identity = _as_dict(adapter.get("bot_identity"))
    policy = _as_dict(bot_identity.get("prompt_policy"))
    known_ids = _as_list(bot_identity.get("known_self_ids"))
    lines = [
        f"bot self id hint：{bot_identity.get('self_id_hint', '')}",
        "known self ids：" + "；".join(str(item) for item in known_ids),
        f"runtime source：{bot_identity.get('runtime_source', '')}",
        "你的QQ号：{bot_self_id}（adapter connect 时由 driver 注入）",
    ]
    if policy.get("assistant_role_only"):
        lines.append("只有 assistant role 的消息才是 bot 自己说的话")
    if policy.get("user_role_nickname_untrusted"):
        lines.append("user role 中昵称不可信，以 QQ 号为身份标识")

    admins_line = _admins_line(adapter)
    if admins_line:
        lines.append(admins_line)
        lines.append("管理员的指令和陈述可以信任，普通群友的话需要客观记录。")
    return "\n".join(line for line in lines if not line.endswith("：") and line.strip())


def _admins_line(adapter: dict[str, Any]) -> str:
    permissions = _as_dict(adapter.get("permissions"))
    raw = _as_list(permissions.get("admins"))
    fragments: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            qq = str(entry.get("id", "")).strip()
            label = str(entry.get("label", "")).strip()
            if not qq:
                continue
            fragments.append(f"@{qq}({label})" if label else f"@{qq}")
        elif isinstance(entry, str):
            qq = entry.strip()
            if qq:
                fragments.append(f"@{qq}")
    if not fragments:
        return ""
    return "【管理员】" + "、".join(fragments)


_GROUP_PROFILE_FIELD_ORDER: tuple[str, ...] = (
    "presence_mode",
    "at_only",
    "talk_value",
    "planner_smooth",
    "debounce_seconds",
    "batch_size",
    "history_load_count",
    "reply_style",
    "custom_prompt",
    "tools_enabled",
    "allowed_tools",
    "blocked_tools",
    "sticker_mode",
    "slang_enabled",
    "blocked_users",
    "source",
)


def _group_profile_text(runtime: dict[str, Any]) -> str:
    overrides = runtime.get("per_group_overrides")
    if not isinstance(overrides, dict):
        return ""
    lines = []
    for group_id in sorted(str(key) for key in overrides):
        profile = overrides.get(group_id)
        if not isinstance(profile, dict):
            continue
        fragments: list[str] = []
        for field in _GROUP_PROFILE_FIELD_ORDER:
            if field not in profile:
                continue
            fragments.append(_format_group_profile_fragment(field, profile[field]))
        fragments = [fragment for fragment in fragments if fragment]
        if fragments:
            lines.append(f"{group_id}：" + "；".join(fragments))
    return "\n".join(lines)


def _format_group_profile_fragment(field: str, value: Any) -> str:
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


def _knowledge_text(knowledge: dict[str, Any]) -> str:
    lines = [
        "已知事实：" + "；".join(str(item) for item in _as_list(knowledge.get("known_facts"))),
        "不知道边界：" + "；".join(str(item) for item in _as_list(knowledge.get("unknown_boundaries"))),
        "禁说事实：" + "；".join(str(item) for item in _as_list(knowledge.get("forbidden_claims"))),
    ]
    return "\n".join(line for line in lines if not line.endswith("：") and line.strip())


def _examples_text(examples: dict[str, Any]) -> str:
    positives = _as_list(examples.get("positive"))
    negatives = _as_list(examples.get("negative"))
    lines = []
    for item in positives[:3]:
        if isinstance(item, dict):
            lines.append("正例：" + str(item.get("turn", "")).strip())
    for item in negatives[:3]:
        if isinstance(item, dict):
            wrong = str(item.get("wrong_turn", "")).strip()
            right = str(item.get("right_turn", "")).strip()
            lines.append(f"反例：{wrong} -> {right}")
    return "\n".join(line for line in lines if line.strip())


def _guard_text(persona: dict[str, Any], guard: dict[str, Any]) -> str:
    constitution = _as_dict(persona.get("constitution"))
    hard_rules = _as_list(constitution.get("hard_rules"))
    rules = []
    for item in hard_rules:
        if isinstance(item, dict):
            rules.append(str(item.get("text", "")).strip())
    memory_write = _as_dict(guard.get("memory_write"))
    behavior = _as_dict(guard.get("behavior_instructions"))
    lines = ["硬规则：" + "；".join(rule for rule in rules if rule)]
    behavior_items = _as_list(behavior.get("items"))
    instruction_texts = [
        str(item.get("text", "")).strip()
        for item in behavior_items
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    if instruction_texts:
        lines.append("行为指令：" + "；".join(instruction_texts))
    if memory_write:
        lines.append(f"记忆写入默认状态：{memory_write.get('default_status', 'candidate')}")
    identity = _as_dict(persona.get("identity"))
    proactive = str(identity.get("proactive_rules", "")).strip()
    if proactive:
        lines.append("插话方式：" + proactive)
    return "\n".join(line for line in lines if not line.endswith("：") and line.strip())


_DECLARATION_VALIDATION_MODULES: tuple[str, ...] = (
    "core.identity",
    "core.voice",
    "core.guard",
)


def _validate_no_declarations(blocks: tuple[CompilePromptBlock, ...]) -> list[str]:
    errors: list[str] = []
    for block in blocks:
        if block.module_id not in _DECLARATION_VALIDATION_MODULES:
            continue
        for pattern in DECLARATION_PATTERNS:
            match = pattern.search(block.text)
            if match is None:
                continue
            matched = match.group(0).strip()
            errors.append(
                f"{block.module_id} contains declaration pattern: {matched}"
            )
            break
    return errors
