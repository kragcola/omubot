"""Validated startup settings; secrets never enter effective configuration or audit."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import secrets
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lunar_python import Solar  # pyright: ignore[reportMissingTypeStubs]
from pydantic import Field, JsonValue, PrivateAttr, field_validator, model_validator

from omubot_new.element_rules import ElementRuleConfig
from omubot_new.persona import CompiledPersona, PersonaCompileError, PersonaCompiler, PersonaSourceImporter
from omubot_new.pricing import ModelPrice
from omubot_new.types import QQDeliveryLimits, StrictModel

TaskName = Literal["reply", "thinker", "vision", "schedule", "dream", "memory", "journal"]
GroupMode = Literal["active", "silent", "off"]
GroupReplyStyle = Literal["default", "gentle", "playful", "concise", "energetic", "steady"]
CalendarCategory = Literal["birthday", "anniversary", "special_day"]
CalendarSubjectKind = Literal["bot", "member", "group"]
RwsMode = Literal["off", "shadow", "primary"]
ClimateMode = Literal["off", "observe", "active"]
PersonaMode = Literal["simple", "source"]
MAX_MODEL_SYSTEM_CHARS = 6200  # Keep aligned with ModelRequest.system.
MAX_PERSONA_SOURCE_CHARS = 24000
PERSONA_SOURCE_REF = "config.persona_source_markdown"
_TASK_NAMES: tuple[TaskName, ...] = ("reply", "thinker", "vision", "schedule", "dream", "memory", "journal")


def validate_journal_uins(value: list[str]) -> list[str]:
    if len(set(value)) != len(value) or any(not re.fullmatch(r"[1-9][0-9]{0,19}", uin) for uin in value):
        raise ValueError("journal live targets require distinct exact account identifiers")
    return value


def compile_persona_source(markdown: str) -> CompiledPersona:
    """Compile the server-owned source field without activating runtime state."""

    imported = PersonaSourceImporter().import_markdown(
        markdown, source_ref=PERSONA_SOURCE_REF, required=True
    )
    if not imported.ok:
        issue = imported.issues[0]
        raise ValueError(
            f"persona source invalid: {issue.code} at line {issue.line}: {issue.message}"
        )
    try:
        return PersonaCompiler(max_system_chars=MAX_MODEL_SYSTEM_CHARS).compile(
            imported.require_source()
        )
    except PersonaCompileError as exc:
        raise ValueError(f"persona source invalid: {exc}") from exc


def validate_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        any(ch.isspace() or ord(ch) < 32 for ch in endpoint)
        or endpoint != endpoint.strip()
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("invalid endpoint")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint cannot contain query credentials or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("non-local endpoints require HTTPS")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid endpoint port") from exc
    return endpoint


def normalize_model_endpoint(api_format: str, endpoint: str) -> str:
    validate_endpoint(endpoint)
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    suffix = {
        "anthropic": "/messages",
        "openai_chat": "/chat/completions",
        "openai_responses": "/responses",
        "deepseek": "/chat/completions",
    }.get(api_format)
    if suffix is None:
        return endpoint
    if not path:
        path = ("" if api_format == "deepseek" else "/v1") + suffix
    elif path.endswith("/v1"):
        path += suffix
    else:
        return endpoint
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def validate_group_mode_ids(value: dict[str, GroupMode]) -> dict[str, GroupMode]:
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id) for group_id in value):
        raise ValueError("group_modes keys must be exact group identifiers")
    return value


def validate_group_profile_ids(value: dict[str, GroupProfileOverride]) -> dict[str, GroupProfileOverride]:
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id) for group_id in value):
        raise ValueError("group_profiles keys must be exact group identifiers")
    return value


def validate_group_calendar_events(
    value: dict[str, list[GroupCalendarEvent]],
) -> dict[str, list[GroupCalendarEvent]]:
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id) for group_id in value):
        raise ValueError("group_calendar_events keys must be exact group identifiers")
    if any(len(events) > 64 for events in value.values()):
        raise ValueError("group_calendar_events allows at most 64 entries per group")
    return value


class _LunarDate(Protocol):
    def getMonth(self) -> int: ...

    def getDay(self) -> int: ...


def _lunar_date_for(day: date) -> _LunarDate:
    return cast(
        _LunarDate,
        Solar.fromYmd(day.year, day.month, day.day).getLunar(),  # pyright: ignore[reportUnknownMemberType]
    )


def validate_worldbook_group_ids(value: list[str]) -> list[str]:
    if len(set(value)) != len(value) or any(
        not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id) for group_id in value
    ):
        raise ValueError("worldbook_allowed_groups must contain unique exact group identifiers")
    return value


def validate_memory_group_ids(value: list[str]) -> list[str]:
    if len(set(value)) != len(value) or any(
        not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id) for group_id in value
    ):
        raise ValueError("memory_capture_groups must contain unique exact group identifiers")
    return value


def validate_help_command_groups(value: list[str]) -> list[str]:
    if len(set(value)) != len(value) or any(
        not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group_id) for group_id in value
    ):
        raise ValueError("help_command_groups must contain unique exact group identifiers")
    return value


def _absolute_config_path(value: str, *, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(path))


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _inside_source_checkout(path: Path) -> bool:
    for ancestor in (path, *path.parents):
        project_file = ancestor / "pyproject.toml"
        if not project_file.is_file() or not (ancestor / "src" / "omubot_new").is_dir():
            continue
        try:
            project = tomllib.loads(project_file.read_text(encoding="utf-8")).get("project", {})
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            continue
        if isinstance(project, dict) and cast(dict[str, object], project).get("name") == "omubot-new":
            return True
    return False


class GroupProfileOverride(StrictModel):
    """Narrow, non-authorizing persona additions for one exact group."""

    reply_style: GroupReplyStyle | None = None
    custom_prompt: str | None = Field(default=None, max_length=6000)


class CalendarWeekdayRule(StrictModel):
    """One Gregorian weekday occurrence within a fixed month."""

    month: int = Field(ge=1, le=12)
    ordinal: int = Field(ge=-5, le=5)
    weekday: int = Field(ge=0, le=6)

    @field_validator("ordinal")
    @classmethod
    def nonzero_ordinal(cls, value: int) -> int:
        if value == 0:
            raise ValueError("ordinal must be 1..5 or -1..-5")
        return value


class CalendarFixedLunarRule(StrictModel):
    """A fixed day in the Chinese lunar calendar."""

    kind: Literal["fixed"]
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=30)
    leap_month: bool


class CalendarYearEveRule(StrictModel):
    """The day whose following Gregorian day is lunar New Year's Day."""

    kind: Literal["year_eve"]


CalendarLunarRule = Annotated[
    CalendarFixedLunarRule | CalendarYearEveRule,
    Field(discriminator="kind"),
]


class GroupCalendarEvent(StrictModel):
    """One explicitly entered recurring calendar event for one group."""

    date: str | None = Field(default=None, pattern=r"^\d{2}-\d{2}$")
    weekday: CalendarWeekdayRule | None = None
    lunar: CalendarLunarRule | None = None
    name: str = Field(min_length=1, max_length=80)
    category: CalendarCategory
    subject_kind: CalendarSubjectKind
    subject_id: str | None = Field(default=None, max_length=64)

    @field_validator("date")
    @classmethod
    def valid_month_day(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            date.fromisoformat("2000-" + value)
        except ValueError as exc:
            raise ValueError("date must be a valid Gregorian MM-DD date") from exc
        return value

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("subject_id")
    @classmethod
    def exact_subject_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError("subject_id must be an exact identifier")
        return value

    @model_validator(mode="after")
    def subject_identity_matches_kind(self) -> GroupCalendarEvent:
        if sum((self.date is not None, self.weekday is not None, self.lunar is not None)) != 1:
            raise ValueError("exactly one of date, weekday, or lunar is required")
        if self.subject_kind == "member" and self.subject_id is None:
            raise ValueError("member events require an exact subject_id")
        if self.subject_kind != "member" and self.subject_id is not None:
            raise ValueError("subject_id is only valid for member events")
        return self


def calendar_event_matches_day(event: GroupCalendarEvent, day: date) -> bool:
    """Match one event against a date already resolved in the config timezone."""
    if event.date is not None:
        return event.date == day.strftime("%m-%d")

    if event.weekday is not None:
        rule = event.weekday
        if day.month != rule.month or day.weekday() != rule.weekday:
            return False
        if rule.ordinal > 0:
            return (day.day - 1) // 7 + 1 == rule.ordinal
        month_end = calendar.monthrange(day.year, day.month)[1]
        return (month_end - day.day) // 7 + 1 == -rule.ordinal

    rule = event.lunar
    if rule is None:
        raise AssertionError("validated calendar event must have one date rule")
    if isinstance(rule, CalendarYearEveRule):
        next_day = day + timedelta(days=1)
        next_lunar = _lunar_date_for(next_day)
        return next_lunar.getMonth() == 1 and next_lunar.getDay() == 1
    lunar = _lunar_date_for(day)
    return (
        lunar.getMonth() == (-rule.month if rule.leap_month else rule.month)
        and lunar.getDay() == rule.day
    )


@dataclass(frozen=True)
class EffectiveGroupPersona:
    """The instance persona core plus the selected group's optional additions."""

    persona_name: str
    persona_instructions: str
    reply_style: GroupReplyStyle | None
    custom_prompt: str | None
    compiled_system: str | None = None
    persona_version: str | None = None
    persona_source_hash: str | None = None

    @property
    def system(self) -> str:
        base = self.compiled_system
        if base is None:
            base = "\n".join(
                part
                for part in (
                    "你的名称：" + self.persona_name.strip()
                    if self.persona_name.strip()
                    else "",
                    self.persona_instructions.strip(),
                )
                if part
            )
        parts = [
            base,
            "群回复风格：" + self.reply_style if self.reply_style is not None else "",
            "群补充提示：" + self.custom_prompt.strip()
            if self.custom_prompt and self.custom_prompt.strip()
            else "",
        ]
        return "\n".join(part for part in parts if part)


class ModelProfile(StrictModel):
    api_format: Literal["openai_chat", "openai_responses", "anthropic", "deepseek"] = "anthropic"
    endpoint: str = "https://api.anthropic.com/v1/messages"
    model: str = Field(default="offline-model", min_length=1, max_length=200)
    api_key_env: str = "OMUBOT_MODEL_KEY"
    max_output_tokens: int = Field(default=1024, ge=1, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    thinking: bool = False
    send_history: bool = True
    # Provider image input remains opt-in per named model profile.
    vision_enabled: bool = False
    token_parameter: Literal["max_tokens", "max_completion_tokens"] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_address(cls, value: object) -> object:
        if isinstance(value, dict):
            data = dict(cast(dict[str, object], value))
            endpoint = data.get("endpoint")
            api_format = data.get("api_format", "anthropic")
            if isinstance(endpoint, str) and isinstance(api_format, str):
                data["endpoint"] = normalize_model_endpoint(api_format, endpoint)
            return data
        return value

    @model_validator(mode="after")
    def validate_profile(self) -> ModelProfile:
        validate_endpoint(self.endpoint)
        if not self.model.strip() or self.model != self.model.strip():
            raise ValueError("model must be a nonblank exact identifier")
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", self.api_key_env):
            raise ValueError("api_key_env must name an environment variable, not contain a key")
        if self.token_parameter is not None and self.api_format != "openai_chat":
            raise ValueError("token_parameter is supported only for openai_chat")
        if self.thinking and (self.send_history or self.temperature is not None):
            raise ValueError("deepseek thinking requires send_history=false and no temperature")
        if self.thinking and self.api_format != "deepseek":
            raise ValueError("thinking is supported only for deepseek")
        if self.reasoning_effort is not None and self.api_format == "anthropic":
            raise ValueError("reasoning_effort is not supported for anthropic")
        if self.api_format == "deepseek" and self.reasoning_effort is not None and not self.thinking:
            raise ValueError("deepseek reasoning_effort requires thinking")
        if self.api_format == "anthropic" and self.temperature is not None and self.temperature > 1:
            raise ValueError("anthropic temperature must be between 0 and 1")
        return self


class ContactLocalWindow(StrictModel):
    """An explicitly reviewed local daily window; midnight windows are split."""

    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def ordered_window(self) -> ContactLocalWindow:
        if self.start_minute >= self.end_minute:
            raise ValueError("contact window must end after its start")
        return self


class ContactEndpointRules(StrictModel):
    """Product contact bounds, independently applied to the user and the group."""

    windows: list[ContactLocalWindow] = Field(min_length=1, max_length=16)
    minimum_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    day_limit: int = Field(ge=1, le=180)
    decision_minimum_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    decision_day_limit: int = Field(ge=1, le=180)

    @model_validator(mode="after")
    def distinct_windows(self) -> ContactEndpointRules:
        ordered = sorted(self.windows, key=lambda window: window.start_minute)
        if any(left.end_minute > right.start_minute
               for left, right in zip(ordered, ordered[1:], strict=False)):
            raise ValueError("contact windows must not overlap")
        return self


class ContactSettings(StrictModel):
    """No frequency defaults or permission grants; absent endpoint rules are closed."""

    enabled: bool = False
    users: dict[str, ContactEndpointRules] = Field(max_length=128)
    groups: dict[str, ContactEndpointRules] = Field(max_length=128)

    @field_validator("users", "groups")
    @classmethod
    def exact_targets(cls, value: dict[str, ContactEndpointRules]) -> dict[str, ContactEndpointRules]:
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity) for identity in value):
            raise ValueError("contact rules require exact target identifiers")
        return value


class Config(StrictModel):
    _compiled_persona_cache: CompiledPersona | None = PrivateAttr(default=None)
    _compiled_persona_source: str | None = PrivateAttr(default=None)

    @field_validator("qq_delivery_limits")
    @classmethod
    def tightened_qq_profile(cls, value: QQDeliveryLimits) -> QQDeliveryLimits:
        if (value.account_min_interval < 5 or value.target_min_interval < 8
                or value.account_hour_limit > 60 or value.account_day_limit > 180
                or value.target_hour_limit > 30 or value.target_day_limit > 90):
            raise ValueError("QQ delivery limits may only tighten the initial profile")
        return value

    persona_name: str = Field(default="", max_length=80)
    persona_instructions: str = Field(default="", max_length=6000)
    persona_mode: PersonaMode = "simple"
    persona_source_markdown: str = Field(default="", max_length=MAX_PERSONA_SOURCE_CHARS)
    group_modes: dict[str, GroupMode] = Field(default_factory=dict, max_length=128)
    # Startup-only and closed by default; never infer a chat command allowlist.
    help_command_groups: list[str] = Field(default_factory=list, max_length=128)
    group_profiles: dict[str, GroupProfileOverride] = Field(default_factory=dict, max_length=128)
    group_calendar_events: dict[str, list[GroupCalendarEvent]] = Field(
        default_factory=dict, max_length=128
    )
    proactive_contact: ContactSettings | None = None
    instance_id: str = Field(default="standalone", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    instance_name: str = Field(default="default", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    listen_port: int = Field(default=18081, ge=1024, le=65535)
    onebot_token_env: str = Field(default="OMUBOT_ONEBOT_TOKEN", pattern=r"^[A-Z_][A-Z0-9_]{0,127}$")
    reply_segment_chars: int = Field(default=1000, ge=1, le=2000)
    max_reply_segments: int = Field(default=5, ge=1, le=5)
    bot_id: str = Field(default="10001", min_length=1, max_length=64)
    db_path: str = "storage/d2.sqlite3"
    mode: Literal["offline", "live"] = "offline"
    active_model: str = "default"
    model_prices: list[ModelPrice] = Field(default_factory=lambda: list[ModelPrice](), max_length=64)
    models: dict[str, ModelProfile] = Field(default_factory=lambda: {"default": ModelProfile()})
    task_models: dict[TaskName, str] = Field(default_factory=lambda: dict[TaskName, str]())
    onebot_endpoint: str = "http://127.0.0.1:3000"
    qq_delivery_limits: QQDeliveryLimits = Field(default_factory=QQDeliveryLimits)
    timezone: str = "Asia/Shanghai"
    queue_capacity: int = Field(default=16, ge=1, le=256)
    total_timeout: float = Field(default=105.0, gt=0, le=105)
    reply_generation_timeout: float = Field(default=30.0, gt=0, le=30)
    reply_admission_timeout: float = Field(default=30.0, gt=0, le=30)
    reply_delivery_timeout: float = Field(default=45.0, gt=0, le=45)
    model_timeout: float = Field(default=20.0, gt=0, le=60)
    send_timeout: float = Field(default=5.0, gt=0, le=30)
    history_ttl: float = Field(default=900.0, gt=0, le=3600)
    max_sessions: int = Field(default=128, ge=1, le=1024)
    tool_capabilities: list[str] = Field(default_factory=lambda: ["clock.read"])
    search_endpoint: str = Field(default="", max_length=2048)
    web_fetch_hosts: list[str] = Field(default_factory=list, max_length=32)
    http_api_hosts: list[str] = Field(default_factory=list, max_length=32)
    visual_url_hosts: list[str] = Field(default_factory=list, max_length=32)
    model_concurrency: int = Field(default=4, ge=2, le=32)
    thinker_reserve: int = Field(default=1, ge=1)
    max_active_sessions: int = Field(default=8, ge=1, le=64)
    thinker_enabled: bool = True
    # Direct, trusted @ mentions force the reply path by default. Bot-reply
    # references remain an independent force source.
    mention_force_reply_enabled: bool = True
    # Streaming is opt-in and currently applies only to tool-free group text replies.
    stream_reply_enabled: bool = False
    followup_reply_enabled: bool = False
    followup_reply_groups: list[str] = Field(default_factory=list, max_length=128)
    graph_extraction_enabled: bool = False
    graph_extraction_groups: list[str] = Field(default_factory=list, max_length=128)
    video_metadata_enabled: bool = False
    video_metadata_groups: list[str] = Field(default_factory=list, max_length=128)
    url_titles_enabled: bool = False
    url_titles_groups: list[str] = Field(default_factory=list, max_length=128)
    episode_query_rerank_enabled: bool = False
    element_rules_enabled: bool = False
    element_rules_groups: list[str] = Field(default_factory=list, max_length=128)
    element_custom_rules: list[ElementRuleConfig] = Field(
        default_factory=lambda: list[ElementRuleConfig](), max_length=16,
    )
    planned_reply_enabled: bool = False
    planned_reply_groups: list[str] = Field(default_factory=list, max_length=128)
    max_interruptions: int = Field(default=3, ge=0, le=16)
    bot_pair_guard_enabled: bool = True
    bot_pair_loop_alt_threshold: int = Field(default=10, ge=1, le=127)
    bot_pair_known_alt_threshold: int = Field(default=6, ge=1, le=127)
    bot_pair_cooldown_seconds: int = Field(default=60, ge=1, le=3600)
    known_bot_ids: list[str] = Field(default_factory=list, max_length=128)
    rws_mode: RwsMode = "off"
    rws_hawkes_enabled: bool = False
    rws_feedback_enabled: bool = False
    rws_bandit_enabled: bool = False
    rws_threshold: float = Field(default=0.5, ge=0, le=1)
    climate_mode: ClimateMode = "off"
    willingness_enabled: bool = False
    willingness_groups: list[str] = Field(default_factory=list, max_length=128)
    diagnostic_commands_enabled: bool = False
    diagnostic_commands_groups: list[str] = Field(default_factory=list, max_length=128)
    research_enabled: bool = False
    research_groups: list[str] = Field(default_factory=list, max_length=128)
    research_spool_dir: str = Field(default="", max_length=4096)
    research_key_file: str = Field(default="", max_length=4096)
    context_observation_enabled: bool = False
    graph_observation_enabled: bool = False
    cross_group_sharing_enabled: bool = False
    private_conversation_enabled: bool = False
    private_conversation_peers: list[str] = Field(default_factory=list, max_length=128)
    echo_enabled: bool = False
    echo_groups: list[str] = Field(default_factory=list, max_length=128)
    food_enabled: bool = False
    food_search_enabled: bool = False
    food_search_group_overrides: dict[str, bool] = Field(
        default_factory=lambda: dict[str, bool](), max_length=128,
    )
    food_groups: list[str] = Field(default_factory=list, max_length=128)
    affection_enabled: bool = False
    affection_groups: list[str] = Field(default_factory=list, max_length=128)
    # Worldbook remains opt-in. The schedule child gate never stands alone;
    # runtime assembly must also require the total gate and exact group allowlist.
    worldbook_enabled: bool = False
    worldbook_chat_projection_enabled: bool = False
    worldbook_schedule_projection_enabled: bool = False
    worldbook_storylet_enabled: bool = False
    worldbook_dream_proposal_enabled: bool = False
    worldbook_social_evidence_enabled: bool = False
    worldbook_allowed_groups: list[str] = Field(default_factory=list, max_length=128)
    journal_enabled: bool = False
    journal_allowed_groups: list[str] = Field(default_factory=list, max_length=128)
    journal_allow_live_publish: bool = False
    journal_allowed_live_uins: list[str] = Field(default_factory=list, max_length=8)
    # N6 raw-text capture is opt-in and uses an independently encrypted spool.
    # These paths are admin-editable, but are never included in public status.
    character_recognition_enabled: bool = False
    character_recognition_groups: list[str] = Field(default_factory=list, max_length=128)
    ccip_endpoint: str = Field(default="", max_length=2048)
    character_reference_path: str = Field(default="", max_length=4096)
    animetrace_endpoint: str = Field(default="", max_length=2048)
    animetrace_model: str = Field(default="", max_length=128)
    character_teaching_enabled: bool = False
    character_teaching_groups: list[str] = Field(default_factory=list, max_length=128)
    self_nickname_enabled: bool = False
    self_nickname_groups: list[str] = Field(default_factory=list, max_length=128)
    slang_machine_review_enabled: bool = False
    learning_auto_apply_enabled: bool = False
    learning_auto_apply_groups: list[str] = Field(default_factory=list, max_length=128)
    learning_auto_apply_domains: list[Literal["fact", "slang", "style"]] = Field(
        default_factory=lambda: ["fact", "slang", "style"], max_length=3
    )
    retrieval_query_planner_enabled: bool = False
    memory_capture_enabled: bool = False
    memory_capture_groups: list[str] = Field(default_factory=list, max_length=128)
    memory_extraction_domains: list[Literal["fact", "slang", "style", "episode"]] = Field(
        default_factory=lambda: ["fact", "slang", "style", "episode"], min_length=1, max_length=4
    )
    memory_spool_dir: str = Field(default="", max_length=4096)
    memory_key_file: str = Field(default="", max_length=4096)

    @field_validator("group_modes")
    @classmethod
    def exact_group_mode_ids(cls, value: dict[str, GroupMode]) -> dict[str, GroupMode]:
        return validate_group_mode_ids(value)

    @field_validator("help_command_groups")
    @classmethod
    def exact_help_command_group_ids(cls, value: list[str]) -> list[str]:
        return validate_help_command_groups(value)

    @field_validator("group_profiles")
    @classmethod
    def exact_group_profile_ids(
        cls, value: dict[str, GroupProfileOverride]
    ) -> dict[str, GroupProfileOverride]:
        return validate_group_profile_ids(value)

    @field_validator("group_calendar_events")
    @classmethod
    def exact_group_calendar_ids(
        cls, value: dict[str, list[GroupCalendarEvent]]
    ) -> dict[str, list[GroupCalendarEvent]]:
        return validate_group_calendar_events(value)

    @field_validator("known_bot_ids")
    @classmethod
    def exact_known_bot_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", peer) for peer in value
        ):
            raise ValueError("known_bot_ids must contain unique exact user identifiers")
        return value

    @field_validator("journal_allowed_live_uins")
    @classmethod
    def exact_journal_accounts(cls, value: list[str]) -> list[str]:
        return validate_journal_uins(value)

    @field_validator("worldbook_allowed_groups", "journal_allowed_groups")
    @classmethod
    def exact_worldbook_group_ids(cls, value: list[str]) -> list[str]:
        return validate_worldbook_group_ids(value)

    @field_validator(
        "memory_capture_groups", "learning_auto_apply_groups", "self_nickname_groups",
        "character_teaching_groups", "character_recognition_groups", "planned_reply_groups",
        "followup_reply_groups", "graph_extraction_groups", "video_metadata_groups",
        "url_titles_groups", "element_rules_groups",
        "affection_groups", "willingness_groups", "food_groups", "echo_groups", "diagnostic_commands_groups",
        "research_groups",
        "private_conversation_peers",
    )
    @classmethod
    def exact_memory_group_ids(cls, value: list[str]) -> list[str]:
        return validate_memory_group_ids(value)

    @field_validator("web_fetch_hosts", "http_api_hosts")
    @classmethod
    def exact_web_fetch_hosts(cls, value: list[str]) -> list[str]:
        from .web_fetch import normalize_web_fetch_hosts
        return list(normalize_web_fetch_hosts(value))

    @model_validator(mode="after")
    def validate_boundaries(self) -> Config:
        if self.persona_mode == "source":
            self.compiled_persona()
        validate_endpoint(self.onebot_endpoint)
        if not 1 <= len(self.models) <= 16 or self.active_model not in self.models:
            raise ValueError("active_model must select one of 1..16 named model profiles")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in self.models):
            raise ValueError("profile names must use letters, digits, underscore or hyphen")
        if any(profile not in self.models for profile in self.task_models.values()):
            raise ValueError("task_models must reference known model profiles")
        if len({(price.provider, price.model) for price in self.model_prices}) != len(self.model_prices):
            raise ValueError("model_prices requires one version per exact provider/model")
        if len({rule.id for rule in self.element_custom_rules}) != len(self.element_custom_rules):
            raise ValueError("element_custom_rules requires unique stable ids")
        if self.thinker_reserve >= self.model_concurrency:
            raise ValueError("thinker_reserve must be less than model_concurrency")
        if set(self.tool_capabilities) - {
            "clock.read", "network.search", "network.fetch", "network.http.read", "network.http.write",
            "onebot.manage_group", "onebot.poke", "onebot.reaction", "onebot.history",
        }:
            raise ValueError("unknown tool capability")
        if self.search_endpoint:
            validate_endpoint(self.search_endpoint)
            search_path = urlsplit(self.search_endpoint).path
            if search_path not in {"", "/"} and not search_path.endswith("/search"):
                raise ValueError("search endpoint path must end in /search")
        from .visual_transport import VisualTransport
        VisualTransport(self.visual_url_hosts)
        if len(self.visual_url_hosts) != len(set(self.visual_url_hosts)):
            raise ValueError("visual_url_hosts must be unique")
        if any(
            len(self.effective_group_persona(group_id).system) > MAX_MODEL_SYSTEM_CHARS
            for group_id in self.group_profiles
        ):
            raise ValueError("effective group persona exceeds model system character budget")
        if not self.db_path.strip() or self.db_path == ":memory:":
            raise ValueError("a persistent independent database path is required")
        checkout = Path(__file__).resolve().parents[2]
        legacy = checkout.parent / "omubot"
        if checkout.name == "omubot-new" and Path(self.db_path).resolve().is_relative_to(legacy):
            raise ValueError("legacy runtime path is not a rewrite database")
        if len(set(self.learning_auto_apply_domains)) != len(self.learning_auto_apply_domains):
            raise ValueError("learning_auto_apply_domains must be unique")
        if len(set(self.memory_extraction_domains)) != len(self.memory_extraction_domains):
            raise ValueError("memory_extraction_domains must be unique")
        if self.character_recognition_enabled and not self.character_recognition_groups:
            raise ValueError("character recognition requires an exact group allowlist")
        if bool(self.ccip_endpoint) != bool(self.character_reference_path):
            raise ValueError("CCIP requires an endpoint and application reference path together")
        if bool(self.animetrace_endpoint) != bool(self.animetrace_model):
            raise ValueError("AnimeTrace requires an endpoint and model together")
        for endpoint in (self.ccip_endpoint, self.animetrace_endpoint):
            if endpoint:
                validate_endpoint(endpoint)
        if self.character_teaching_enabled and not self.character_teaching_groups:
            raise ValueError("character teaching requires an exact group allowlist")
        if self.willingness_enabled and (not self.willingness_groups or not self.thinker_enabled):
            raise ValueError("willingness requires Thinker and an exact group allowlist")
        if (self.proactive_contact is not None and self.proactive_contact.enabled
                and not self.thinker_enabled):
            raise ValueError("proactive contact requires the existing Thinker")
        if self.diagnostic_commands_enabled and not self.diagnostic_commands_groups:
            raise ValueError("diagnostic commands require an exact group allowlist")
        if self.research_enabled and not self.research_groups:
            raise ValueError("research requires an exact group allowlist")
        if self.research_enabled or self.research_spool_dir or self.research_key_file:
            self.research_storage_paths()
        if self.private_conversation_enabled and not self.private_conversation_peers:
            raise ValueError("private conversation requires an exact peer allowlist")
        if self.echo_enabled and not self.echo_groups:
            raise ValueError("echo_enabled requires exact echo_groups")
        for enabled, groups, label in (
            (self.followup_reply_enabled, self.followup_reply_groups, "followup reply"),
            (self.graph_extraction_enabled, self.graph_extraction_groups, "graph extraction"),
            (self.video_metadata_enabled, self.video_metadata_groups, "video metadata"),
            (self.url_titles_enabled, self.url_titles_groups, "URL titles"),
            (self.element_rules_enabled, self.element_rules_groups, "element rules"),
        ):
            if enabled and not groups:
                raise ValueError(f"{label} requires an exact group allowlist")
        if self.planned_reply_enabled and not self.planned_reply_groups:
            raise ValueError("planned reply requires an exact group allowlist")
        if self.food_enabled and not (self.food_groups or (self.private_conversation_enabled
                                                          and self.private_conversation_peers)):
            raise ValueError("food requires an exact group or enabled private peer allowlist")
        if (any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", group) for group in self.food_search_group_overrides)
                or not set(self.food_search_group_overrides) <= set(self.food_groups)):
            raise ValueError("food search overrides require exact configured food groups")
        if self.affection_enabled and not self.affection_groups:
            raise ValueError("affection requires an exact group allowlist")
        if self.journal_enabled and not self.journal_allowed_groups:
            raise ValueError("journal requires an exact group allowlist")
        if (self.journal_allow_live_publish
                and (not self.journal_enabled or not self.journal_allowed_live_uins)):
            raise ValueError("journal live publishing requires Journal and exact account targets")
        if self.rws_feedback_enabled and self.rws_mode == "off":
            raise ValueError("RWS feedback requires shadow or primary mode")
        if self.rws_bandit_enabled and (
            not self.rws_feedback_enabled or not 0.35 <= self.rws_threshold <= 0.65
        ):
            raise ValueError("RWS bandit requires feedback and a threshold in [0.35, 0.65]")
        if self.slang_machine_review_enabled and (
            not self.memory_capture_enabled or not self.memory_capture_groups
        ):
            raise ValueError("slang machine review requires explicit capture and captured groups")
        if self.self_nickname_enabled and (
            not self.memory_capture_enabled
            or not self.self_nickname_groups
            or not set(self.self_nickname_groups) <= set(self.memory_capture_groups)
        ):
            raise ValueError("self nickname requires capture and an exact captured group allowlist")
        if self.learning_auto_apply_enabled and (
            not self.memory_capture_enabled
            or not self.learning_auto_apply_groups
            or not self.learning_auto_apply_domains
            or not set(self.learning_auto_apply_groups) <= set(self.memory_capture_groups)
        ):
            raise ValueError("automatic learning requires capture and an exact captured group allowlist")
        if self.memory_capture_enabled:
            memory_profile_name = self._profile_for_task("memory")
            if len(memory_profile_name) > 63 or len(self.models[memory_profile_name].model) > 64:
                raise ValueError("memory task model destination exceeds the memory runtime limits")
        if self.memory_capture_enabled or self.memory_spool_dir or self.memory_key_file:
            self._validate_memory_capture_paths(
                legacy, require_enabled=self.memory_capture_enabled
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        return self

    def _validate_memory_capture_paths(self, legacy: Path, *, require_enabled: bool) -> None:
        if require_enabled and not self.memory_capture_groups:
            raise ValueError("memory_capture_groups must not be empty when memory capture is enabled")
        if not self.memory_spool_dir or not self.memory_key_file:
            raise ValueError("memory_spool_dir and memory_key_file must be configured together")
        for name, value in (
            ("memory_spool_dir", self.memory_spool_dir),
            ("memory_key_file", self.memory_key_file),
        ):
            if not value or value != value.strip() or any(ord(char) < 32 for char in value):
                raise ValueError(f"{name} is required when memory capture is enabled")

        database = Path(os.path.abspath(self.db_path))
        base = database.parent
        spool = _absolute_config_path(self.memory_spool_dir, base=base)
        key = _absolute_config_path(self.memory_key_file, base=base)
        # Resolve existing symlink targets for overlap checks. The original
        # configured paths are still passed to EncryptedTextSpool, which rejects
        # symlink components instead of silently following them.
        database_real = database.resolve(strict=False)
        database_directory = database_real.parent
        spool_real = spool.resolve(strict=False)
        key_real = key.resolve(strict=False)
        legacy_real = legacy.resolve(strict=False)
        if _inside_source_checkout(spool_real) or _inside_source_checkout(key_real):
            raise ValueError("memory spool and key paths must be outside the source checkout")
        if _paths_overlap(database_directory, spool_real):
            raise ValueError("memory_spool_dir must be isolated from the database directory")
        if (
            key_real == database_real
            or key_real.is_relative_to(database_directory)
            or _paths_overlap(spool_real, key_real)
        ):
            raise ValueError("memory spool and key paths must be distinct and isolated")
        if spool_real.is_relative_to(legacy_real) or legacy_real.is_relative_to(spool_real):
            raise ValueError("legacy runtime paths cannot be used for memory capture")
        if key_real.is_relative_to(legacy_real):
            raise ValueError("legacy runtime paths cannot be used for memory capture")

    def memory_capture_storage_paths(self) -> tuple[Path, Path]:
        """Resolve configured staging paths relative to the database directory."""
        if not self.memory_spool_dir or not self.memory_key_file:
            raise ValueError("memory capture storage paths are not configured")
        database = Path(os.path.abspath(self.db_path))
        return (
            _absolute_config_path(self.memory_spool_dir, base=database.parent),
            _absolute_config_path(self.memory_key_file, base=database.parent),
        )

    def research_storage_paths(self) -> tuple[Path, Path]:
        """Resolve a separate research spool/key without creating or enabling it."""
        database = Path(os.path.abspath(self.db_path))
        prefix = database.stem + "-research"
        for value in (self.research_spool_dir, self.research_key_file):
            if value != value.strip() or any(ord(char) < 32 for char in value):
                raise ValueError("research storage paths cannot contain whitespace or controls")
        spool = _absolute_config_path(self.research_spool_dir, base=database.parent) \
            if self.research_spool_dir else database.parent / (prefix + "-spool")
        key = _absolute_config_path(self.research_key_file, base=database.parent) \
            if self.research_key_file else database.parent / (prefix + ".key")
        spool_real, key_real = spool.resolve(strict=False), key.resolve(strict=False)
        database_real = database.resolve(strict=False)
        legacy = (Path(__file__).resolve().parents[2].parent / "omubot").resolve(strict=False)
        if (_paths_overlap(spool_real, key_real) or spool_real == database_real or key_real == database_real
                or _paths_overlap(legacy, spool_real) or _paths_overlap(legacy, key_real)):
            raise ValueError("research spool and key must be distinct from database and legacy runtime")
        for configured in (self.memory_spool_dir, self.memory_key_file):
            if configured:
                memory = _absolute_config_path(configured, base=database.parent).resolve(strict=False)
                if _paths_overlap(memory, spool_real) or _paths_overlap(memory, key_real):
                    raise ValueError("research storage cannot share memory spool or key")
        return spool, key

    @property
    def selected_model(self) -> ModelProfile:
        return self.models[self.active_model]

    @property
    def model(self) -> str:
        return self.selected_model.model

    @property
    def policy_provider(self) -> str:
        # Bind grants to destination identity, not merely a wire format or display name.
        return self._policy_provider_for(self.active_model)

    def group_mode_for(self, group_id: str) -> GroupMode:
        """Return the exact configured mode, defaulting unlisted groups to active."""
        return self.group_modes.get(group_id, "active")

    def worldbook_schedule_enabled_for(self, group_id: str) -> bool:
        """Return the complete runtime gate for one group's internal schedule."""
        return (
            self.worldbook_enabled
            and self.worldbook_schedule_projection_enabled
            and group_id in self.worldbook_allowed_groups
        )

    def worldbook_chat_enabled_for(self, group_id: str) -> bool:
        """Return the complete gate for one group's fiction chat projection."""
        return (
            self.worldbook_enabled
            and self.worldbook_chat_projection_enabled
            and group_id in self.worldbook_allowed_groups
        )

    def effective_group_persona(self, group_id: str) -> EffectiveGroupPersona:
        """Keep the instance persona core and resolve only narrow group additions."""
        profile = self.group_profiles.get(group_id)
        compiled = self.compiled_persona()
        return EffectiveGroupPersona(
            persona_name=self.persona_name,
            persona_instructions=self.persona_instructions,
            reply_style=profile.reply_style if profile else None,
            custom_prompt=profile.custom_prompt if profile else None,
            compiled_system=compiled.system if compiled else None,
            persona_version=compiled.version if compiled else None,
            persona_source_hash=compiled.source_hash if compiled else None,
        )

    def calendar_events_for(self, group_id: str) -> tuple[GroupCalendarEvent, ...]:
        """Return only events explicitly stored for the exact requested group."""
        return tuple(self.group_calendar_events.get(group_id, ()))

    def compiled_persona(self) -> CompiledPersona | None:
        """Return the effective source Persona, leaving activation to runtime."""

        if self.persona_mode == "simple":
            return None
        if (
            self._compiled_persona_cache is not None
            and self._compiled_persona_source == self.persona_source_markdown
        ):
            return self._compiled_persona_cache
        compiled = compile_persona_source(self.persona_source_markdown)
        object.__setattr__(self, "_compiled_persona_cache", compiled)
        object.__setattr__(self, "_compiled_persona_source", self.persona_source_markdown)
        return compiled

    def _policy_provider_for(self, profile_name: str) -> str:
        profile = self.models[profile_name]
        identity = [profile_name, profile.api_format, profile.endpoint, profile.api_key_env]
        digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        return f"{profile_name}:{digest}"

    def _profile_for_task(self, task: TaskName) -> str:
        return self.task_models.get(task, self.active_model)

    def for_task(self, task: TaskName | str) -> Config:
        """Return a deep, revalidated configuration snapshot for one task."""
        if task not in _TASK_NAMES:
            raise ValueError("unknown task")
        task_name = task
        data = self.model_dump(mode="python")
        data["active_model"] = self._profile_for_task(task_name)
        return Config.model_validate(data)

    def model_status(self) -> dict[str, JsonValue]:
        task_bindings: dict[str, JsonValue] = {}
        for task in _TASK_NAMES:
            profile_name = self._profile_for_task(task)
            profile = self.models[profile_name]
            task_bindings[task] = {
                "profile": profile_name,
                "api_format": profile.api_format,
                "model": profile.model,
                "policy_provider": self._policy_provider_for(profile_name),
            }
        return {
            "profile": self.active_model,
            "api_format": self.selected_model.api_format,
            "model": self.model,
            "policy_provider": self.policy_provider,
            "task_bindings": task_bindings,
        }


@dataclass(frozen=True)
class Credentials:
    admin: str = field(repr=False)
    status: str = field(repr=False)
    ingress: str = field(repr=False)
    model_key: str = field(default="", repr=False)
    onebot: str = field(default="", repr=False)
    model_keys: dict[str, str] = field(default_factory=lambda: dict[str, str](), repr=False)

    def validate(self, *, live: bool) -> None:
        values = (self.admin, self.status, self.ingress)
        if any(len(v) < 24 for v in values) or len(set(values)) != 3:
            raise ValueError("admin/status/ingress credentials must be distinct random secrets (24+ chars)")
        if live:
            if not self.model_key.strip() or not self.onebot.strip():
                raise ValueError("live mode requires explicit model and OneBot credentials")
            if any(not value.strip() for value in self.model_keys.values()):
                raise ValueError("live mode requires nonblank credentials for used model profiles")


def load_config(path: Path | None, *, live: bool = False) -> Config:
    data: dict[str, object] = {}
    if path is not None:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    # Mode is a process launch decision, never restored from persisted grants.
    if data.get("mode", "offline") != "offline":
        raise ValueError("set live mode with --live, not a copied configuration file")
    data["mode"] = "live" if live else "offline"
    return Config.model_validate(data)


def load_credentials(
    directory: Path,
    *,
    live: bool,
    model_key_env: str = "OMUBOT_MODEL_KEY",
    onebot_token_env: str = "OMUBOT_ONEBOT_TOKEN",
) -> Credentials:
    """Local bootstrap credentials; preserve on restart without printing values."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "credentials.toml"
    if not path.exists():
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            for name in ("admin", "status", "ingress"):
                handle.write(f'{name} = "{secrets.token_urlsafe(32)}"\n')
    with path.open("rb") as handle:
        content = tomllib.load(handle)
    values = [content.get(name) for name in ("admin", "status", "ingress")]
    if not all(isinstance(v, str) for v in values):
        raise ValueError("invalid bootstrap credentials")
    credentials = Credentials(
        admin=str(values[0]),
        status=str(values[1]),
        ingress=str(values[2]),
        model_key=os.environ.get(model_key_env, "") if live else "",
        onebot=os.environ.get(onebot_token_env, ""),
    )
    credentials.validate(live=live)
    return credentials


def load_task_credentials(directory: Path, config: Config, *, reverse_ws: bool = False) -> Credentials:
    """Load bootstrap credentials and only the live keys used by configured tasks."""
    base = load_credentials(directory, live=False, onebot_token_env=config.onebot_token_env)
    if config.mode == "offline":
        return base

    reply_profile = config.task_models.get("reply", config.active_model)
    profiles = [reply_profile]
    if config.thinker_enabled:
        profiles.append(config.task_models.get("thinker", config.active_model))
    if config.worldbook_enabled and config.worldbook_allowed_groups:
        if config.worldbook_schedule_projection_enabled:
            profiles.append(config.task_models.get("schedule", config.active_model))
        if config.worldbook_dream_proposal_enabled:
            profiles.append(config.task_models.get("dream", config.active_model))
    if config.memory_capture_enabled:
        profiles.append(config.task_models.get("memory", config.active_model))
    if config.journal_enabled and config.worldbook_enabled and config.worldbook_allowed_groups:
        profiles.append(config.task_models.get("journal", config.active_model))

    model_keys: dict[str, str] = {}
    for profile_name in dict.fromkeys(profiles):
        environment = config.models[profile_name].api_key_env
        from .model_secrets import read_model_secret

        value = os.environ.get(environment, "") or read_model_secret(
            directory, profile_name, config.models[profile_name]
        )
        if not value.strip():
            raise ValueError("live mode requires nonblank credentials for used model profiles")
        model_keys[profile_name] = value

    credentials = Credentials(
        admin=base.admin,
        status=base.status,
        ingress=base.ingress,
        model_key=model_keys[reply_profile],
        onebot=base.ingress if reverse_ws else base.onebot,
        model_keys=model_keys,
    )
    credentials.validate(live=True)
    return credentials
