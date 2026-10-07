"""Validated, versioned application settings with restart-required semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import JsonValue

from .config import Config
from .persona import PERSONA_VERSION_PREFIX
from .store import SettingsPersonaReceipt, Store
from .types import OperationError, QQDeliveryLimits

_IMMUTABLE_FIELDS = frozenset(
    {
        "instance_id",
        "instance_name",
        "bot_id",
        "db_path",
        "mode",
        "listen_port",
        "help_command_groups",
    }
)


def editable(config: Config) -> dict[str, JsonValue]:
    values = cast(dict[str, JsonValue], config.model_dump(mode="json"))
    return {name: value for name, value in values.items() if name not in _IMMUTABLE_FIELDS}


def validate_document(base: Config, document: Mapping[str, JsonValue]) -> Config:
    """Validate a complete editable document while preserving startup-only fields."""
    if not isinstance(document, dict) or any(type(name) is not str for name in document):
        raise OperationError("invalid_settings")
    # Older saved versions predate the optional persona fields. Normalize only
    # these additive defaults; all other missing/unknown fields remain errors.
    document = dict(document)
    document.setdefault("persona_name", "")
    document.setdefault("persona_instructions", "")
    document.setdefault("persona_mode", "simple")
    document.setdefault("persona_source_markdown", "")
    # Additive group settings default empty for saved documents from older builds.
    # Never infer or rewrite group rules from a missing field.
    document.setdefault("group_modes", {})
    document.setdefault("group_profiles", {})
    # Calendar events are entered explicitly in this project. Older revisions
    # start empty and never import records from the legacy project.
    document.setdefault("group_calendar_events", {})
    # N3 participation controls are additive to older saved settings versions.
    document.setdefault("bot_pair_guard_enabled", True)
    document.setdefault("bot_pair_loop_alt_threshold", 10)
    document.setdefault("bot_pair_known_alt_threshold", 6)
    document.setdefault("bot_pair_cooldown_seconds", 60)
    document.setdefault("known_bot_ids", [])
    # Direct @ force is enabled for older saved settings. Bot-reply force stays
    # independent of this switch.
    document.setdefault("mention_force_reply_enabled", True)
    # RWS is pure and optional. Older saved documents keep the gray-zone legacy
    # path until an operator explicitly chooses shadow or primary mode.
    document.setdefault("rws_mode", "off")
    document.setdefault("rws_hawkes_enabled", False)
    document.setdefault("rws_feedback_enabled", False)
    document.setdefault("rws_bandit_enabled", False)
    document.setdefault("rws_threshold", 0.5)
    # Climate is additive and disabled for older saved settings. No signal
    # collection or expression is enabled until an operator opts in.
    document.setdefault("climate_mode", "off")
    # Existing versions have no contact authorization or numeric scheduling rules.
    document.setdefault("proactive_contact", None)
    # Older saved total/segment limits remain authoritative. Add the bounded
    # phase clocks without extending the saved whole-turn cap or enabling sends.
    document.setdefault("reply_generation_timeout", 30.0)
    document.setdefault("reply_admission_timeout", 30.0)
    document.setdefault("reply_delivery_timeout", 45.0)
    # Governance is mandatory for real QQ transports. Older documents receive
    # the initial profile; saving it still requires the normal runtime restart.
    document.setdefault("qq_delivery_limits", QQDeliveryLimits().model_dump(mode="json"))
    # Streaming is additive and opt-in. Older persisted documents and browser
    # drafts keep using the ordinary reply path unless explicitly enabled.
    document.setdefault("stream_reply_enabled", False)
    document.setdefault("planned_reply_enabled", False)
    document.setdefault("planned_reply_groups", [])
    document.setdefault("followup_reply_enabled", False)
    document.setdefault("followup_reply_groups", [])
    document.setdefault("graph_extraction_enabled", False)
    document.setdefault("graph_extraction_groups", [])
    document.setdefault("video_metadata_enabled", False)
    document.setdefault("video_metadata_groups", [])
    document.setdefault("url_titles_enabled", False)
    document.setdefault("url_titles_groups", [])
    document.setdefault("episode_query_rerank_enabled", False)
    document.setdefault("element_rules_enabled", False)
    document.setdefault("element_rules_groups", [])
    document.setdefault("element_custom_rules", [])
    # No vendor rates are inferred for old saved settings.
    document.setdefault("model_prices", [])
    # Worldbook is additive and entirely closed for older saved settings. An
    # omitted allowlist means no group is eligible; it never means all groups.
    document.setdefault("worldbook_enabled", False)
    document.setdefault("worldbook_chat_projection_enabled", False)
    document.setdefault("worldbook_schedule_projection_enabled", False)
    document.setdefault("worldbook_storylet_enabled", False)
    document.setdefault("worldbook_dream_proposal_enabled", False)
    document.setdefault("worldbook_social_evidence_enabled", False)
    document.setdefault("worldbook_allowed_groups", [])
    document.setdefault("journal_enabled", False)
    document.setdefault("journal_allowed_groups", [])
    document.setdefault("journal_allow_live_publish", False)
    document.setdefault("journal_allowed_live_uins", [])
    # N6 memory capture is additive and default-off; older stored revisions
    # must never activate capture or imply a broad group allowlist.
    document.setdefault("self_nickname_enabled", False)
    document.setdefault("self_nickname_groups", [])
    document.setdefault("character_recognition_enabled", False)
    document.setdefault("character_recognition_groups", [])
    document.setdefault("ccip_endpoint", "")
    document.setdefault("character_reference_path", "")
    document.setdefault("animetrace_endpoint", "")
    document.setdefault("animetrace_model", "")
    document.setdefault("character_teaching_enabled", False)
    document.setdefault("character_teaching_groups", [])
    document.setdefault("willingness_enabled", False)
    document.setdefault("willingness_groups", [])
    document.setdefault("diagnostic_commands_enabled", False)
    document.setdefault("diagnostic_commands_groups", [])
    document.setdefault("research_enabled", False)
    document.setdefault("research_groups", [])
    document.setdefault("research_spool_dir", "")
    document.setdefault("research_key_file", "")
    # Older revisions keep observation, sharing and private conversations closed.
    document.setdefault("context_observation_enabled", False)
    document.setdefault("graph_observation_enabled", False)
    document.setdefault("cross_group_sharing_enabled", False)
    document.setdefault("private_conversation_enabled", False)
    document.setdefault("private_conversation_peers", [])
    document.setdefault("echo_enabled", False)
    document.setdefault("echo_groups", [])
    document.setdefault("food_enabled", False)
    document.setdefault("food_groups", [])
    document.setdefault("food_search_enabled", False)
    document.setdefault("food_search_group_overrides", {})
    document.setdefault("affection_enabled", False)
    document.setdefault("affection_groups", [])
    document.setdefault("slang_machine_review_enabled", False)
    document.setdefault("learning_auto_apply_enabled", False)
    document.setdefault("learning_auto_apply_groups", [])
    document.setdefault("learning_auto_apply_domains", ["fact", "slang", "style"])
    document.setdefault("retrieval_query_planner_enabled", False)
    document.setdefault("memory_capture_enabled", False)
    document.setdefault("memory_capture_groups", [])
    # Missing domain controls preserve the existing four-domain extraction contract.
    document.setdefault("memory_extraction_domains", ["fact", "slang", "style", "episode"])
    document.setdefault("memory_spool_dir", "")
    document.setdefault("memory_key_file", "")
    document.setdefault("search_endpoint", "")
    document.setdefault("web_fetch_hosts", [])
    document.setdefault("http_api_hosts", [])
    document.setdefault("visual_url_hosts", [])
    expected = set(editable(base))
    received = set(document)
    if received != expected:
        raise OperationError("invalid_settings")
    data = base.model_dump(mode="python")
    data.update(document)
    try:
        return Config.model_validate(data)
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_settings") from exc


def resolve_saved(config: Config) -> Config:
    saved = Store.read_saved_settings(config.db_path)
    if saved is None:
        return config
    return validate_document(config, saved)


class SettingsService:
    def __init__(self, store: Store, config: Config) -> None:
        self.store = store
        self.config = config
        self.effective_revision: int | None = None

    async def start(self, pinned_revision: int | None = None) -> None:
        revision, document = await self.store.settings_initialize(editable(self.config))
        if pinned_revision is not None:
            pinned = await self.store.settings_version(pinned_revision)
            if pinned is None:
                raise OperationError("settings_version_not_found")
            revision, document = pinned_revision, pinned
        saved = validate_document(self.config, document)
        if editable(saved) != editable(self.config):
            raise OperationError("startup_config_conflict")
        self.effective_revision = revision

    async def snapshot(self) -> dict[str, JsonValue]:
        if self.effective_revision is None:
            raise OperationError("settings_unstarted")
        current = await self.store.settings_current()
        if current is None:
            raise OperationError("settings_uninitialized")
        revision, document = current
        saved = editable(validate_document(self.config, document))
        running = editable(self.config)
        changed_fields = [name for name in running if saved[name] != running[name]]
        return cast(
            dict[str, JsonValue],
            {
                "revision": revision,
                "effective_revision": self.effective_revision,
                "restart_required": bool(changed_fields),
                "config": saved,
                "effective_config": running,
                "changed_fields": changed_fields,
                "versions": await self.store.settings_versions(),
            },
        )

    async def save(
        self, document: Mapping[str, JsonValue], expected_revision: int, *, actor: str | None = None,
    ) -> dict[str, JsonValue]:
        """Save without activation; only an authenticated caller supplies ``actor``.

        Local calls omit it. The principal must come from the upstream auth
        boundary, never from the editable document or another self-assertion.
        """
        validated = validate_document(self.config, document)
        archive_active = (self.config.memory_capture_enabled
                          or self.config.journal_enabled and bool(self.config.memory_spool_dir))
        if archive_active and (
            validated.memory_spool_dir != self.config.memory_spool_dir
            or validated.memory_key_file != self.config.memory_key_file
        ):
            raise OperationError("memory_storage_path_change_requires_restart")
        if self.config.research_enabled and (
            validated.research_storage_paths() != self.config.research_storage_paths()
        ):
            raise OperationError("research_storage_path_change_requires_restart")
        receipt = (
            self._persona_receipt(validated, actor, expected_revision + 1, "saved")
            if actor is not None else None
        )
        await self.store.settings_save(
            editable(validated), expected_revision, persona_receipt=receipt,
        )
        return await self.snapshot()

    async def rollback(
        self, target_revision: int, expected_revision: int, *, actor: str | None = None,
    ) -> dict[str, JsonValue]:
        document = await self.store.settings_version(target_revision)
        if document is None:
            raise OperationError("settings_version_not_found")
        return await self.save(document, expected_revision, actor=actor)

    def _persona_receipt(
        self, config: Config, actor: str, revision: int,
        decision: Literal["saved", "requested", "applied", "rejected"],
        request_id: str | None = None,
    ) -> SettingsPersonaReceipt:
        if self.effective_revision is None:
            raise OperationError("settings_unstarted")
        compiled = config.compiled_persona()
        return SettingsPersonaReceipt(
            actor=actor, persona_mode=config.persona_mode,
            source_hash=compiled.source_hash if compiled else None,
            compiler_version=PERSONA_VERSION_PREFIX.removesuffix(":sha256:") if compiled else None,
            persona_version=compiled.version if compiled else None,
            saved_revision=revision, effective_revision=self.effective_revision,
            decision=decision, request_id=request_id,
        )

    async def record_runtime_decision(
        self, decision: Literal["requested", "applied", "rejected"], *,
        actor: str, revision: int, request_id: str,
    ) -> SettingsPersonaReceipt:
        """Record an authenticated rebuild request or a caller-observed outcome.

        Root's lifecycle caller must wait for actual startup success before
        ``applied`` and for recovery of the old runtime before ``rejected``.
        Ordinary startup does not call this method. No effective state changes.
        ``actor`` and ``request_id`` are server-owned authenticated handoff
        metadata, not fields trusted from an HTTP configuration document.
        """
        document = await self.store.settings_version(revision)
        if document is None:
            raise OperationError("settings_version_not_found")
        target = validate_document(self.config, document)
        if decision == "applied" and (
            self.effective_revision != revision or editable(target) != editable(self.config)
        ):
            raise OperationError("settings_runtime_receipt_conflict")
        receipt = self._persona_receipt(target, actor, revision, decision, request_id)
        return await self.store.settings_record_runtime_decision(document, receipt)
