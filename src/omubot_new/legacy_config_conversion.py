"""Pure, single-group proposals from explicitly supplied legacy configuration data.

This is a synthetic/offline conversion report, never a production migration or
an effective-configuration resolver. No paths, environment, Store or Policy are
read. Unsupported values are never reproduced in reports or runtime placeholders.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue, ValidationError

from .config import GroupMode, GroupProfileOverride, validate_group_profile_ids
from .types import ConversationScope, OperationError, Scope

LegacySourceFormat = Literal["persona_front_matter", "bot_config"]
FieldStatus = Literal["converted", "inherited", "unsupported", "excluded", "invalid", "outside_scope"]
ConversionStatus = Literal["proposed", "partial", "unsupported", "invalid", "no_proposal"]

# Historical importer/compiler fields, not a second runtime schema.
_PROFILE_FIELDS = frozenset({"reply_style", "custom_prompt"})
_UNSUPPORTED_GROUP_FIELDS = frozenset({
    "blocked_users", "allowed_tools", "blocked_tools", "at_only", "talk_value", "planner_smooth",
    "debounce_seconds", "batch_size", "history_load_count", "tools_enabled", "sticker_mode", "slang_enabled",
})
_PRESENCE_PROPOSALS: dict[str, GroupMode] = {"active": "active", "silent_learn": "silent", "off": "off"}
_EXCLUDED_ROOTS: dict[str, str] = {
    "admins": "permissions_not_migrated", "allowed_private_users": "permissions_not_migrated",
    "admin_token": "credentials_not_migrated", "api_key": "credentials_not_migrated",
    "password": "credentials_not_migrated", "token": "credentials_not_migrated",
    "llm": "model_credentials_and_destination_not_migrated",
    "vision": "model_credentials_and_destination_not_migrated",
    "napcat": "connection_and_login_state_not_migrated",
    "kernel": "instance_identity_and_runtime_not_migrated",
    "bot_self_id_hint": "instance_identity_not_migrated",
    "known_bot_self_ids": "instance_identity_not_migrated",
    "agent_runtime": "runtime_activation_not_migrated",
    "memory": "business_state_not_migrated", "backup": "storage_and_backup_not_migrated",
    "legacy_instruction_md": "file_import_not_performed",
    "legacy_instruction_md_path": "file_import_not_performed",
}


@dataclass(frozen=True, slots=True)
class LegacyFieldReport:
    source_path: tuple[str, ...]
    status: FieldStatus
    code: str
    target_path: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class LegacyConfigConversionReport:
    scope: Scope
    source_format: LegacySourceFormat
    profile: GroupProfileOverride | None
    group_mode: GroupMode | None
    fields: tuple[LegacyFieldReport, ...]
    # Fixed provenance limits, not copied legacy configuration values.
    limitations: tuple[str, ...] = (
        "proposal_only_requires_manual_review_and_separate_adoption",
        "raw_overrides_are_not_effective_behavior_equivalence",
        "legacy_presence_depended_on_access_and_allowed_groups_not_migrated",
        "current_learning_switches_and_permissions_are_not_inferred",
        "no_configuration_policy_or_business_state_is_applied",
    )

    @property
    def status(self) -> ConversionStatus:
        has_proposal = self.profile is not None or self.group_mode is not None
        unconverted = any(field.status not in {"converted", "inherited"} for field in self.fields)
        if has_proposal:
            return "partial" if unconverted else "proposed"
        if any(field.status == "invalid" for field in self.fields):
            return "invalid"
        return "unsupported" if unconverted else "no_proposal"


def convert_legacy_config(document: Mapping[str, JsonValue], *, scope: ConversationScope,
                          source_format: LegacySourceFormat) -> LegacyConfigConversionReport:
    """Propose only explicit values for exactly one target Group.

    Supported shapes are parsed persona front matter with ``group_profiles``
    and legacy BotConfig with ``group.overrides``. Global values are reported,
    never merged into a selected group. Missing/None never means current active.
    The caller supplies data and target identity; this function cannot load it.
    """
    if not isinstance(scope, Scope):
        raise OperationError("unsupported_scope")
    if source_format not in {"persona_front_matter", "bot_config"}:
        raise OperationError("unsupported_legacy_format")
    # Use the current configuration owner's exact Group key boundary.
    validate_group_profile_ids({scope.group_id: GroupProfileOverride()})
    fields: list[LegacyFieldReport] = []
    profile: GroupProfileOverride | None = None
    mode: GroupMode | None = None

    def report(path: tuple[str, ...], status: FieldStatus, code: str,
               target: tuple[str, ...] | None = None) -> None:
        fields.append(LegacyFieldReport(path, status, code, target))

    if source_format == "persona_front_matter":
        container_key, profile_path = "group_profiles", ("group_profiles",)
        groups = document.get(container_key)
    else:
        container_key, profile_path = "group", ("group", "overrides")
        group = document.get(container_key)
        groups = None
        if isinstance(group, dict):
            for key in group:
                if key == "overrides":
                    groups = group[key]
                elif key in {"access", "allowed_groups"}:
                    report(("group", key), "excluded", "permissions_not_migrated")
                else:
                    report(("group", key), "unsupported", "global_group_defaults_not_resolved")
        elif group is not None:
            report(("group",), "invalid", "expected_group_mapping")
    for key in document:
        if key != container_key:
            code = _EXCLUDED_ROOTS.get(key)
            report((key,), "excluded" if code is not None else "unsupported",
                   code if code is not None else "unsupported_root_field")

    if not isinstance(groups, dict):
        report(profile_path, "invalid", "missing_or_invalid_group_mapping")
        return LegacyConfigConversionReport(scope, source_format, None, None, tuple(fields))
    selected: JsonValue = None
    found = False
    for group_id, values in groups.items():
        if group_id != scope.group_id:
            report((*profile_path, group_id), "outside_scope", "group_not_selected_no_aggregation")
        else:
            found, selected = True, values
    if not found:
        report((*profile_path, scope.group_id), "invalid", "selected_group_missing")
    elif not isinstance(selected, dict):
        report((*profile_path, scope.group_id), "invalid", "expected_override_mapping")
    else:
        profile_values: dict[str, JsonValue] = {}
        profile_paths: dict[str, tuple[str, ...]] = {}
        for key, value in selected.items():
            path = (*profile_path, scope.group_id, key)
            normalized = (
                value.strip() if source_format == "persona_front_matter" and isinstance(value, str) else value
            )
            empty_override = normalized is None or (
                source_format == "persona_front_matter" and normalized == ""
            )
            if key in _PROFILE_FIELDS:
                if empty_override:
                    report(path, "inherited", "legacy_empty_override_not_materialized")
                else:
                    profile_values[key] = normalized
                    profile_paths[key] = path
            elif key == "presence_mode":
                if empty_override:
                    report(path, "inherited", "legacy_empty_override_not_materialized")
                elif isinstance(normalized, str) and normalized in _PRESENCE_PROPOSALS:
                    mode = _PRESENCE_PROPOSALS[normalized]
                    report(path, "converted", "raw_presence_proposal_not_effective_equivalence",
                           ("group_modes", scope.group_id))
                else:
                    report(path, "invalid", "invalid_legacy_presence_mode")
            elif key in _UNSUPPORTED_GROUP_FIELDS:
                report(path, "unsupported", "legacy_field_has_no_current_group_contract")
            else:
                report(path, "unsupported", "unknown_or_later_legacy_group_field")
        if profile_values:
            try:
                profile = GroupProfileOverride.model_validate(profile_values)
            except ValidationError:
                # Atomic Profile proposal: never claim a field converted when no draft was produced.
                for path in profile_paths.values():
                    report(path, "invalid", "invalid_current_group_profile")
            else:
                for key, path in profile_paths.items():
                    report(path, "converted", "current_narrow_profile_proposal",
                           ("group_profiles", scope.group_id, key))
    return LegacyConfigConversionReport(scope, source_format,
                                        profile, mode, tuple(fields))
