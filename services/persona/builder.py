"""Build Persona v2 draft dictionaries from parsed source."""

from __future__ import annotations

from typing import Any

from services.system_module import REQUIRED_MODULE_IDS, RESERVED_MODULE_IDS, catalog_system_modules

from .models import (
    ImportIssue,
    ImportReport,
    ImportResult,
    ReportField,
    SourceDocument,
    SourceField,
    SourceSection,
    SourceSpan,
)
from .parser import bullet_items, first_prefixed_value, list_after_label
from .system_validation import validate_system_modules

DRAFT_YAML_FILES = (
    "persona.yaml",
    "voice.yaml",
    "runtime.yaml",
    "knowledge.yaml",
    "relationships.yaml",
    "memory.yaml",
    "capabilities.yaml",
    "adapter.yaml",
    "guard.yaml",
    "examples.yaml",
    "eval.yaml",
    "trace.yaml",
    "state.yaml",
    "thinker.yaml",
    "system.yaml",
)

DEFAULT_TEMPLATE_FILES = (
    "guard.yaml",
    "eval.yaml",
    "trace.yaml",
    "runtime.yaml",
    "state.yaml",
    "thinker.yaml",
    "adapter.yaml",
    "capabilities.yaml",
    "system.yaml",
)


def build_persona_draft(
    source: SourceDocument,
    *,
    defaults: dict[str, dict[str, Any]] | None = None,
) -> ImportResult:
    persona_id = _frontmatter_str(source, "persona_id") or "unknown-v2"
    report = ImportReport(
        persona_id=persona_id,
        source_file=source.path,
        source_hash=source.source_hash,
    )

    draft = _empty_draft(persona_id, source)
    defaults = defaults or {}
    for filename, payload in defaults.items():
        if filename in DEFAULT_TEMPLATE_FILES and isinstance(payload, dict):
            draft[filename] = payload
            report.fields.append(
                ReportField(
                    file=filename,
                    key_path=".",
                    source_span=None,
                    confidence=1.0,
                    extractor="default_template",
                    default_used=True,
                    issue_level="info",
                )
            )

    _extract_frontmatter(source, draft, report)
    _extract_identity(source, draft, report)
    _extract_voice(source, draft, report)
    _extract_knowledge(source, draft, report)
    _extract_memory(source, draft, report)
    _extract_examples(source, draft, report)
    _extract_behavior_instructions(source, draft, report)
    _extract_part_b_overrides(source, draft, report)
    _extract_admins(source, draft, report)
    _extract_bot_identity(source, draft, report)
    _extract_group_profiles(source, draft, report)
    validate_system_modules(draft, report)
    _validate_required(draft, report)

    report.generated_files = [
        *DRAFT_YAML_FILES,
        "modules/_README.md",
        "_import_report.json",
    ]
    return ImportResult(
        persona_id=persona_id,
        draft=draft,
        modules_readme=_modules_readme(persona_id),
        report=report,
    )


def _empty_draft(persona_id: str, source: SourceDocument) -> dict[str, dict[str, Any]]:
    return {
        "persona.yaml": {
            "schema": "omubot.persona.v2",
            "version": "2.1.0-proposal",
            "id": persona_id,
            "identity": {
                "canonical_name": "",
                "aliases": [],
                "role": "",
                "self_reference": "",
                "personality": "",
                "essence": [],
                "not_traits": [],
            },
            "constitution": {
                "values": [],
                "hard_rules": [],
            },
            "runtime_state_seed": {},
        },
        "voice.yaml": {
            "schema": "omubot.voice.v2",
            "version": "2.1.0-proposal",
            "language": "",
            "style_principles": {
                "sentence_shape": [],
                "rhythm": [],
                "banned_patterns": [],
            },
            "expression_library": {"items": []},
            "slang_policy": {},
        },
        "runtime.yaml": _skeleton("omubot.runtime.v2", source),
        "knowledge.yaml": {
            "schema": "omubot.knowledge.v2",
            "version": "2.1.0-proposal",
            "known_facts": [],
            "unknown_boundaries": [],
            "forbidden_claims": [],
        },
        "relationships.yaml": {
            "schema": "omubot.relationships.v2",
            "version": "2.1.0-proposal",
            "default_disposition": "",
            "profiles": [],
        },
        "memory.yaml": {
            "schema": "omubot.memory.v2",
            "version": "2.1.0-proposal",
            "workspace": {
                "scope_fields": ["workspace_id", "platform", "channel_id", "persona_id"],
                "hard_filter_required": True,
            },
            "paragraph": {
                "enabled": True,
                "fields": [
                    "id",
                    "workspace_id",
                    "content",
                    "summary",
                    "event_time",
                    "confidence",
                    "origin_kind",
                    "origin_ref",
                    "anchor_msg_id_start",
                    "anchor_msg_id_end",
                ],
                "inject_as": "evidence_context",
                "never_as": "system_instruction",
            },
            "entity_index": {
                "enabled": True,
                "fields": ["entity_id", "name", "entity_type", "aliases", "paragraph_refs", "confidence"],
                "source": "memory_cards_index",
                "write_policy": "runtime_store_only",
            },
            "retrieval_policy": {
                "required_filters": ["workspace_id"],
                "combine": ["vector", "entity_index", "recency_weight"],
            },
            "seed_episodes": [],
        },
        "capabilities.yaml": _skeleton("omubot.capabilities.v2", source),
        "adapter.yaml": {
            **_skeleton("omubot.adapter.v2", source),
            "bot_identity": {
                "runtime_source": "adapter_connect_event",
                "self_id_hint": "",
                "known_self_ids": [],
                "prompt_policy": {
                    "assistant_role_only": True,
                    "user_role_nickname_untrusted": True,
                    "qq_id_is_identity_key": True,
                },
            },
            "permissions": {
                "admin_required_for_freeze": True,
                "source": "source_front_matter",
                "admins": [],
            },
        },
        "guard.yaml": _skeleton("omubot.guard.v2", source),
        "examples.yaml": {
            "schema": "omubot.examples.v2",
            "version": "2.1.0-proposal",
            "positive": [],
            "negative": [],
        },
        "eval.yaml": _skeleton("omubot.persona_eval.v2", source),
        "trace.yaml": _skeleton("omubot.trace.v2", source),
        "state.yaml": _skeleton("omubot.state.v2", source),
        "thinker.yaml": _skeleton("omubot.thinker.v2", source),
        "system.yaml": {
            "schema": "omubot.system.v2",
            "version": "2.1.0-proposal",
            "modules": catalog_system_modules(),
            "dag_check": {
                "on_cycle": "refuse_boot",
                "on_missing_dep": "refuse_boot",
            },
            "trace": {
                "retention_days": 30,
                "records_required": [
                    "state_snapshots",
                    "module_decisions",
                    "prompt_blocks_per_module",
                    "guard_verdict",
                    "send_receipt",
                ],
            },
        },
    }


def _skeleton(schema: str, source: SourceDocument) -> dict[str, Any]:
    return {
        "schema": schema,
        "version": "2.1.0-proposal",
        "status": "skeleton",
        "todo": "Generated by Persona Source Importer Part A; fill in later stages.",
        "source_hash": source.source_hash,
    }


def _extract_frontmatter(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    for key, target in (
        ("persona_id", ("persona.yaml", "id")),
        ("canonical_name", ("persona.yaml", "identity.canonical_name")),
        ("language", ("voice.yaml", "language")),
    ):
        value = _frontmatter_str(source, key)
        if not value:
            report.issues.append(
                ImportIssue("error", "missing_frontmatter", f"front matter `{key}` 缺失", target[0], target[1])
            )
            continue
        _set_path(draft[target[0]], target[1], value)
        report.fields.append(
            ReportField(
                file=target[0],
                key_path=target[1],
                source_span=SourceSpan(source.path, (1, max(1, _frontmatter_end_line(source.text)))),
                confidence=1.0,
                extractor="front_matter",
            )
        )


def _extract_identity(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    root = source.section("是谁")
    essence = source.section("性格底色")
    not_traits = source.section("不应该", "不应出现")
    rules = source.section("价值观", "硬规则")

    role = first_prefixed_value(root, "一句话角色", source_file=source.path)
    self_ref = first_prefixed_value(root, "自称", source_file=source.path)
    _set_field(draft, report, "persona.yaml", "identity.role", role, "sentence_md")
    _set_field(draft, report, "persona.yaml", "identity.self_reference", self_ref, "sentence_md")
    _set_list(
        draft,
        report,
        "persona.yaml",
        "identity.essence",
        bullet_items(essence, source_file=source.path),
        "list_md",
    )
    _set_list(
        draft,
        report,
        "persona.yaml",
        "identity.not_traits",
        bullet_items(not_traits, source_file=source.path),
        "list_md",
    )
    _set_list(
        draft,
        report,
        "persona.yaml",
        "constitution.values",
        list_after_label(rules, "价值观", source_file=source.path),
        "list_md",
    )

    hard_rules = []
    for item in list_after_label(rules, "硬规则", source_file=source.path):
        text, enforce = _split_enforce(str(item.value))
        hard_rules.append(
            SourceField(
                key=item.key,
                value={"text": text, "enforce": enforce},
                span=item.span,
            )
        )
    _set_list(draft, report, "persona.yaml", "constitution.hard_rules", hard_rules, "list_md")
    _set_identity_personality_block(draft, report, source, (root, essence, not_traits, rules))


def _extract_voice(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    voice = source.section("怎么说话")
    items = bullet_items(voice, source_file=source.path)
    _set_list(draft, report, "voice.yaml", "style_principles.sentence_shape", items, "list_md")
    _set_list(draft, report, "voice.yaml", "style_principles.rhythm", [], "list_md")
    banned = [item for item in items if "不" in str(item.value) or "禁" in str(item.value)]
    _set_list(draft, report, "voice.yaml", "style_principles.banned_patterns", banned, "list_md")
    expression_items = [
        SourceField(
            key=item.key,
            value={
                "text": item.value,
                "use_when": "",
                "avoid_when": "",
                "review_status": "candidate",
            },
            span=item.span,
        )
        for item in items
    ]
    _set_list(draft, report, "voice.yaml", "expression_library.items", expression_items, "list_md")


def _extract_knowledge(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    knowledge = source.section("知道什么", "不知道")
    mappings = (
        ("已知事实", "knowledge.yaml", "known_facts"),
        ("不知道边界", "knowledge.yaml", "unknown_boundaries"),
        ("禁说事实", "knowledge.yaml", "forbidden_claims"),
    )
    for label, filename, key_path in mappings:
        field = first_prefixed_value(knowledge, label, source_file=source.path)
        values: list[SourceField] = []
        if field is not None:
            values.append(field)
        values.extend(list_after_label(knowledge, label, source_file=source.path))
        _set_list(draft, report, filename, key_path, values, "list_md")


def _extract_memory(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    memory = source.section("经历种子", "记忆种子")
    seeds = [
        SourceField(
            key=item.key,
            value={
                "summary": item.value,
                "origin_anchor": f"{item.span.file}#L{item.span.lines[0]}",
                "review_status": "candidate",
            },
            span=item.span,
        )
        for item in bullet_items(memory, source_file=source.path)
    ]
    _set_list(draft, report, "memory.yaml", "seed_episodes", seeds, "memory_seed_md")


def _extract_examples(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    examples = source.section("例子")
    positives = [
        SourceField("positive", {"turn": item.value, "scene": "", "comment": ""}, item.span)
        for item in list_after_label(examples, "正例", source_file=source.path)
    ]
    negatives = [
        SourceField("negative", {"wrong_turn": item.value, "right_turn": ""}, item.span)
        for item in list_after_label(examples, "反例", source_file=source.path)
    ]
    _set_list(draft, report, "examples.yaml", "positive", positives, "llm_extract_stub")
    _set_list(draft, report, "examples.yaml", "negative", negatives, "llm_extract_stub")


def _extract_behavior_instructions(
    source: SourceDocument,
    draft: dict[str, dict[str, Any]],
    report: ImportReport,
) -> None:
    section = source.section("行为指令", "回复规则", "instruction", "instructions")
    items = [
        SourceField(
            key=item.key,
            value={
                "text": item.value,
                "origin_anchor": f"{item.span.file}#L{item.span.lines[0]}",
                "review_status": "candidate",
            },
            span=item.span,
        )
        for item in bullet_items(section, source_file=source.path)
    ]
    if not items:
        instructions = draft.setdefault("guard.yaml", {}).setdefault("behavior_instructions", {})
        if isinstance(instructions, dict):
            instructions.setdefault("source", "source_section")
            instructions.setdefault("items", [])
        return

    guard = draft.setdefault("guard.yaml", {})
    instructions = guard.setdefault("behavior_instructions", {})
    if not isinstance(instructions, dict):
        instructions = {}
        guard["behavior_instructions"] = instructions
    instructions["source"] = "source_section"
    _set_list(draft, report, "guard.yaml", "behavior_instructions.items", items, "behavior_instruction_md")


def _extract_part_b_overrides(
    source: SourceDocument,
    draft: dict[str, dict[str, Any]],
    report: ImportReport,
) -> None:
    _extract_tone_palette(source, draft, report)
    _extract_module_switches(source, draft, report)


def _extract_tone_palette(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    thinker = source.section("思考器", "tone_palette", "tone palette")
    tones = list_after_label(thinker, "tone_palette", source_file=source.path)
    if not tones:
        tones = list_after_label(thinker, "语气集合", source_file=source.path)
    if not tones:
        return
    _set_list(draft, report, "voice.yaml", "tone_palette", tones, "list_md")
    _set_list(draft, report, "thinker.yaml", "policy.tone_set", tones, "list_md")


def _extract_module_switches(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    section = source.section("模块开关")
    if section is None:
        return
    modules = draft.get("system.yaml", {}).get("modules")
    if not isinstance(modules, dict):
        return
    for item in _checkbox_items(section, source_file=source.path):
        module_id = item.value["module_id"]
        enabled = item.value["enabled"]
        row = modules.get(module_id)
        if not isinstance(row, dict):
            report.issues.append(
                ImportIssue(
                    "warn",
                    "unknown_system_module",
                    f"source.md §12 引用了未知模块 `{module_id}`",
                    "system.yaml",
                    f"modules.{module_id}",
                    item.span,
                )
            )
            continue
        row["enabled"] = enabled
        report.fields.append(
            ReportField(
                "system.yaml",
                f"modules.{module_id}.enabled",
                item.span,
                1.0,
                "checkbox_md",
            )
        )
        if module_id in REQUIRED_MODULE_IDS and not enabled:
            report.issues.append(
                ImportIssue(
                    "error",
                    "required_module_disabled",
                    f"required module `{module_id}` cannot be disabled",
                    "system.yaml",
                    f"modules.{module_id}.enabled",
                    item.span,
                )
            )
        if module_id in RESERVED_MODULE_IDS and enabled:
            report.issues.append(
                ImportIssue(
                    "error",
                    "reserved_module_enabled",
                    f"reserved module `{module_id}` has no implementation",
                    "system.yaml",
                    f"modules.{module_id}.enabled",
                    item.span,
                )
            )


def _extract_admins(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    admins = _frontmatter_admins(source)
    if not admins:
        permissions = draft.setdefault("adapter.yaml", {}).setdefault("permissions", {})
        if isinstance(permissions, dict):
            permissions.setdefault("admins", [])
            permissions.setdefault("source", "source_front_matter")
        return

    permissions = draft.setdefault("adapter.yaml", {}).setdefault("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
        draft["adapter.yaml"]["permissions"] = permissions
    permissions.setdefault("admin_required_for_freeze", True)
    permissions["source"] = "source_front_matter"
    permissions["admins"] = admins

    span = SourceSpan(source.path, (1, max(1, _frontmatter_end_line(source.text))))
    for index, _admin in enumerate(admins):
        report.fields.append(
            ReportField(
                "adapter.yaml",
                f"permissions.admins[{index}]",
                span,
                1.0,
                "front_matter_admins",
            )
        )


def _extract_bot_identity(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    self_id_hint = _frontmatter_str(source, "bot_self_id_hint")
    known_self_ids = _frontmatter_str_list(source, "known_bot_self_ids")
    adapter = draft.setdefault("adapter.yaml", {})
    bot_identity = adapter.setdefault("bot_identity", {})
    if not isinstance(bot_identity, dict):
        bot_identity = {}
        adapter["bot_identity"] = bot_identity
    bot_identity.setdefault("runtime_source", "adapter_connect_event")
    bot_identity.setdefault("self_id_hint", "")
    bot_identity.setdefault("known_self_ids", [])
    bot_identity.setdefault(
        "prompt_policy",
        {
            "assistant_role_only": True,
            "user_role_nickname_untrusted": True,
            "qq_id_is_identity_key": True,
        },
    )

    span = SourceSpan(source.path, (1, max(1, _frontmatter_end_line(source.text))))
    if self_id_hint:
        bot_identity["self_id_hint"] = self_id_hint
        report.fields.append(
            ReportField(
                "adapter.yaml",
                "bot_identity.self_id_hint",
                span,
                1.0,
                "front_matter_bot_identity",
            )
        )
    if known_self_ids:
        bot_identity["known_self_ids"] = known_self_ids
        for index, _self_id in enumerate(known_self_ids):
            report.fields.append(
                ReportField(
                    "adapter.yaml",
                    f"bot_identity.known_self_ids[{index}]",
                    span,
                    1.0,
                    "front_matter_bot_identity",
                )
            )


def _extract_group_profiles(source: SourceDocument, draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    profiles = _frontmatter_group_profiles(source, report)
    runtime = draft.setdefault("runtime.yaml", {})
    overrides = runtime.setdefault("per_group_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
        runtime["per_group_overrides"] = overrides
    if not profiles:
        return

    span = SourceSpan(source.path, (1, max(1, _frontmatter_end_line(source.text))))
    for group_id, payload in profiles.items():
        row = dict(payload)
        row["source"] = "source_front_matter"
        overrides[group_id] = row
        for key in ("reply_style", "custom_prompt"):
            if key in payload:
                report.fields.append(
                    ReportField(
                        "runtime.yaml",
                        f"per_group_overrides.{group_id}.{key}",
                        span,
                        1.0,
                        "front_matter_group_profiles",
                    )
                )


def _validate_required(draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    required = (
        ("persona.yaml", "id", "persona_id"),
        ("persona.yaml", "identity.canonical_name", "canonical_name"),
        ("persona.yaml", "identity.role", "一句话角色"),
        ("persona.yaml", "identity.self_reference", "自称"),
        ("persona.yaml", "identity.essence", "性格底色"),
        ("persona.yaml", "identity.not_traits", "不应该出现的样子"),
        ("persona.yaml", "constitution.values", "价值观"),
        ("persona.yaml", "constitution.hard_rules", "硬规则"),
        ("voice.yaml", "language", "language"),
        ("voice.yaml", "style_principles.sentence_shape", "怎么说话"),
        ("voice.yaml", "style_principles.banned_patterns", "禁用句式"),
        ("voice.yaml", "expression_library.items", "表达素材"),
        ("knowledge.yaml", "known_facts", "已知事实"),
        ("knowledge.yaml", "unknown_boundaries", "不知道边界"),
        ("knowledge.yaml", "forbidden_claims", "禁说事实"),
        ("examples.yaml", "positive", "正例"),
        ("examples.yaml", "negative", "反例"),
    )
    for filename, key_path, label in required:
        if _get_path(draft[filename], key_path):
            continue
        report.issues.append(
            ImportIssue(
                "error",
                "missing_required_field",
                f"`{label}` 缺失或无法抽取",
                filename,
                key_path,
            )
        )

    for index, rule in enumerate(_get_path(draft["persona.yaml"], "constitution.hard_rules") or []):
        if not isinstance(rule, dict) or rule.get("enforce") not in {
            "pattern_guardable",
            "judge_guardable",
            "eval_only",
        }:
            report.issues.append(
                ImportIssue(
                    "error",
                    "hard_rule_missing_enforce",
                    "每条 hard_rule 必须标注 enforce: pattern_guardable / judge_guardable / eval_only",
                    "persona.yaml",
                    f"constitution.hard_rules[{index}]",
                )
            )


def _set_identity_personality_block(
    draft: dict[str, dict[str, Any]],
    report: ImportReport,
    source: SourceDocument,
    sections: tuple[SourceSection | None, ...],
) -> None:
    existing = [section for section in sections if section is not None and section.body.strip()]
    if not existing:
        return

    lines: list[str] = []
    for section in existing:
        lines.append(f"## {section.title.strip()}")
        lines.append(section.body.strip())
    value = "\n\n".join(lines).strip()
    if not value:
        return

    _set_path(draft["persona.yaml"], "identity.personality", value)
    report.fields.append(
        ReportField(
            "persona.yaml",
            "identity.personality",
            SourceSpan(source.path, (existing[0].line, existing[-1].end_line)),
            1.0,
            "identity_static_md",
        )
    )


def _set_field(
    draft: dict[str, dict[str, Any]],
    report: ImportReport,
    filename: str,
    key_path: str,
    field: SourceField | None,
    extractor: str,
) -> None:
    if field is None:
        return
    _set_path(draft[filename], key_path, field.value)
    report.fields.append(
        ReportField(filename, key_path, field.span, 1.0, extractor)
    )


def _set_list(
    draft: dict[str, dict[str, Any]],
    report: ImportReport,
    filename: str,
    key_path: str,
    fields: list[SourceField],
    extractor: str,
) -> None:
    values = [field.value for field in fields]
    _set_path(draft[filename], key_path, values)
    for index, field in enumerate(fields):
        report.fields.append(
            ReportField(filename, f"{key_path}[{index}]", field.span, 1.0, extractor)
        )


def _set_path(payload: dict[str, Any], key_path: str, value: Any) -> None:
    parts = key_path.split(".")
    cursor = payload
    for part in parts[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[part] = next_value
        cursor = next_value
    cursor[parts[-1]] = value


def _get_path(payload: dict[str, Any], key_path: str) -> Any:
    cursor: Any = payload
    for part in key_path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _frontmatter_str(source: SourceDocument, key: str) -> str:
    value = source.frontmatter.get(key)
    return str(value).strip() if value is not None else ""


def _frontmatter_admins(source: SourceDocument) -> list[dict[str, str]]:
    value = source.frontmatter.get("admins")
    admins: list[dict[str, str]] = []
    if isinstance(value, dict):
        for raw_id, raw_label in value.items():
            admin_id = str(raw_id).strip()
            if not admin_id:
                continue
            label = str(raw_label).strip() if raw_label is not None else ""
            admins.append({"id": admin_id, "label": label})
        return admins
    if isinstance(value, list):
        for raw_id in value:
            admin_id = str(raw_id).strip()
            if admin_id:
                admins.append({"id": admin_id, "label": ""})
    return admins


def _frontmatter_str_list(source: SourceDocument, key: str) -> list[str]:
    value = source.frontmatter.get(key)
    if isinstance(value, str | int):
        text = str(value).strip()
        return [text] if text else []
    if not isinstance(value, list | tuple | set):
        return []
    return [text for item in value if (text := str(item).strip())]


def _frontmatter_group_profiles(
    source: SourceDocument,
    report: ImportReport,
) -> dict[str, dict[str, str]]:
    value = source.frontmatter.get("group_profiles")
    if value is None:
        return {}
    span = SourceSpan(source.path, (1, max(1, _frontmatter_end_line(source.text))))
    if not isinstance(value, dict):
        report.issues.append(
            ImportIssue(
                "warn",
                "invalid_group_profiles",
                "front matter `group_profiles` must be a mapping keyed by group id",
                "runtime.yaml",
                "per_group_overrides",
                span,
            )
        )
        return {}

    profiles: dict[str, dict[str, str]] = {}
    for raw_group_id, raw_payload in value.items():
        group_id = str(raw_group_id).strip()
        if not group_id:
            report.issues.append(
                ImportIssue(
                    "warn",
                    "invalid_group_profile_id",
                    "front matter `group_profiles` contains an empty group id",
                    "runtime.yaml",
                    "per_group_overrides",
                    span,
                )
            )
            continue
        if not isinstance(raw_payload, dict):
            report.issues.append(
                ImportIssue(
                    "warn",
                    "invalid_group_profile",
                    f"front matter `group_profiles.{group_id}` must be a mapping",
                    "runtime.yaml",
                    f"per_group_overrides.{group_id}",
                    span,
                )
            )
            continue
        payload: dict[str, str] = {}
        for key in ("reply_style", "custom_prompt"):
            raw_value = raw_payload.get(key)
            if raw_value is None:
                continue
            text = str(raw_value).strip()
            if text:
                payload[key] = text
        if payload:
            profiles[group_id] = payload
    return profiles


def _frontmatter_end_line(text: str) -> int:
    if not text.startswith("---"):
        return 1
    lines = text.split("\n")
    for index, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            return index
    return 1


def _split_enforce(text: str) -> tuple[str, str]:
    marker = "# enforce:"
    if marker not in text:
        return text.strip(), ""
    body, enforce = text.split(marker, 1)
    return body.strip(), enforce.strip().split()[0] if enforce.strip() else ""


def _checkbox_items(section: SourceSection, *, source_file: str) -> list[SourceField]:
    items: list[SourceField] = []
    for offset, line in enumerate(section.body.split("\n")):
        stripped = line.strip()
        if not stripped.startswith("- ["):
            continue
        if len(stripped) < 5:
            continue
        mark = stripped[3].lower()
        if mark not in {" ", "x"}:
            continue
        module_id = stripped[5:].strip().split(" ", 1)[0].strip()
        if not module_id:
            continue
        line_no = section.body_start_line + offset
        items.append(
            SourceField(
                "module_switch",
                {"module_id": module_id, "enabled": mark == "x"},
                SourceSpan(source_file, (line_no, line_no)),
            )
        )
    return items


def _modules_readme(persona_id: str) -> str:
    return (
        f"# modules placeholder for {persona_id}\n\n"
        "Persona Part B S1' provides a canonical SystemModule catalog and "
        "RuntimeStateBus dry-run validator.\n"
        "Concrete `modules/<id>/module.yaml` files still belong to later "
        "module implementation slices.\n"
    )
