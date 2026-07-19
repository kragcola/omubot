"""Evidence-backed factual shared-experience prompt adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel, Field

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext, PromptContext, ReplyContext

_L = logger.bind(channel="social_narrative")
_CST = ZoneInfo("Asia/Shanghai")


class SocialNarrativeConfig(BaseModel):
    """Fail-closed adapter configuration."""

    enabled: bool = False
    allowed_group_ids: list[str] = Field(default_factory=list)


class SocialNarrativePlugin(AmadeusPlugin):
    """Record and inject factual shared experiences for explicitly allowed groups."""

    name = "social_narrative"
    description = "基于群聊消息证据记录并注入 factual 共同经历"
    version = "0.1.0"
    priority = 44
    silent_safe = False

    def __init__(self, config: SocialNarrativeConfig | None = None) -> None:
        super().__init__()
        self._config_override = config
        self._enabled = False
        self._allowed_group_ids: set[str] = set()
        self._store: Any | None = None
        self._affection_engine: Any | None = None
        self._climate_engine: Any | None = None
        # Persistent root PluginContext for late-bound worldbook_runtime lookup.
        # Do not cache the runtime object: worldbook may start after priority-44
        # social and hot-replace the attribute later.
        self._root_ctx: PluginContext | None = None

    async def on_startup(self, ctx: PluginContext) -> None:
        cfg = self._config_override or load_plugin_config(
            "plugins/social_narrative/config.default.json",
            SocialNarrativeConfig,
        )
        self._enabled = bool(cfg.enabled)
        self._allowed_group_ids = {
            str(group_id).strip()
            for group_id in cfg.allowed_group_ids
            if str(group_id).strip()
        }
        self._store = getattr(ctx, "social_narrative_store", None)
        self._affection_engine = getattr(ctx, "affection_engine", None)
        self._climate_engine = getattr(ctx, "climate_engine", None)
        self._root_ctx = ctx
        ctx.social_narrative_reflection_provider = self
        if not self._enabled:
            _L.info("social narrative plugin disabled")
            return
        if not self._allowed_group_ids:
            _L.warning("social narrative enabled without allowed groups; remaining fail-closed")
            return
        if self._store is None:
            _L.warning("social narrative store unavailable; adapter inactive")
            return
        _L.info(
            "social narrative plugin enabled | allowed_groups={}",
            len(self._allowed_group_ids),
        )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if getattr(ctx, "social_narrative_reflection_provider", None) is self:
            ctx.social_narrative_reflection_provider = None
        self._root_ctx = None
        self._store = None
        self._affection_engine = None
        self._climate_engine = None

    def is_group_reflection_eligible(self, group_id: str | None) -> bool:
        """Public fail-closed gate for Dream Arc reflection_group_id selection.

        Returns True only when the plugin is enabled, the store is mounted,
        and ``group_id`` is a positive decimal id present in the configured
        allowlist. Disabled plugin, empty allowlist, missing store, non-numeric
        ids, and post-shutdown state all return False without reading history.
        """
        if self._store is None:
            return False
        allowed_group_id = self._allowed_group(group_id)
        if allowed_group_id is None:
            return False
        return allowed_group_id.isdecimal() and int(allowed_group_id) > 0

    async def build_group_reflection_context(
        self,
        *,
        group_id: str,
        limit: int = 12,
    ) -> str:
        if not self.is_group_reflection_eligible(group_id):
            return ""
        allowed_group_id = self._allowed_group(group_id)
        if allowed_group_id is None or self._store is None:
            return ""
        build = getattr(self._store, "build_group_reflection_context", None)
        if not callable(build):
            return ""
        try:
            return str(
                await cast(Any, build)(group_id=allowed_group_id, limit=limit)
                or ""
            )
        except Exception as exc:
            _L.warning(
                "social narrative reflection context failed | group={} err={}",
                allowed_group_id,
                exc,
            )
            return ""

    async def on_post_reply(self, ctx: ReplyContext) -> None:
        group_id = self._allowed_group(ctx.group_id)
        if group_id is None or self._store is None:
            return
        source_message_id = getattr(ctx, "source_message_id", None)
        if source_message_id is None:
            return
        user_id = str(ctx.user_id or "").strip()
        if not user_id:
            return
        user_text = str(ctx.user_msg or "").strip()
        bot_reply = str(ctx.reply_content or "").strip()
        if not user_text or not bot_reply:
            return
        record = await self._store.record_shared_experience(
            group_id=group_id,
            user_id=user_id,
            evidence_message_id=source_message_id,
            evidence_time=datetime.now(_CST).isoformat(),
            evidence_source="reply_context",
            user_text=user_text,
            bot_reply=bot_reply,
            entity_kind="factual",
            relationship=self._relationship_snapshot(group_id, user_id),
        )
        # Story bridge only after factual persist success. Late-bound runtime
        # so priority-44 social never caches a stale None from priority-45 startup.
        if record is None:
            return
        await self._maybe_commit_social_story(ctx, record, group_id=group_id, user_id=user_id)

    def _resolve_worldbook_runtime(self) -> Any | None:
        """Resolve worldbook_runtime from the retained root PluginContext.

        Production ``ReplyContext`` has no worldbook_runtime / plugin_context /
        ctx fields. The bridge must late-bind via the startup PluginContext
        reference and re-read the attribute each call (never cache runtime).
        """
        root = self._root_ctx
        if root is None:
            return None
        return getattr(root, "worldbook_runtime", None)

    async def _maybe_commit_social_story(
        self,
        ctx: ReplyContext,
        record: Any,
        *,
        group_id: str,
        user_id: str,
    ) -> None:
        """Best-effort social→story bridge; never rolls back factual success."""
        _ = ctx  # production ReplyContext is intentionally runtime-free
        runtime = self._resolve_worldbook_runtime()
        if runtime is None:
            return
        commit = getattr(runtime, "commit_social_experience", None)
        if not callable(commit):
            return
        try:
            result = commit(record, group_id=group_id, user_id=user_id)
        except Exception as exc:
            _L.warning(
                "social story commit raised | group={} experience={} err={}",
                group_id,
                getattr(record, "experience_id", ""),
                exc,
            )
            return
        status = str(getattr(result, "status", "") or "")
        reason = str(getattr(result, "reason", "") or "")
        if status == "rejected":
            _L.debug(
                "social story commit rejected | group={} reason={}",
                group_id,
                reason,
            )
            return
        _L.info(
            "social story commit | status={} reason={} event_id={} arc_id={}",
            status,
            reason,
            getattr(result, "event_id", ""),
            getattr(result, "arc_id", ""),
        )

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        group_id = self._allowed_group(ctx.group_id)
        if group_id is None or self._store is None:
            return
        user_id = str(ctx.user_id or "").strip()
        if not user_id:
            return
        text = await self._store.build_prompt_context(
            group_id=group_id,
            user_id=user_id,
            limit=10,
        )
        if text:
            ctx.add_block(
                text=str(text),
                label="共同经历（factual）",
                position="dynamic",
                priority=46,
                source="social_narrative",
            )

    def _allowed_group(self, group_id: str | None) -> str | None:
        if not self._enabled or not group_id:
            return None
        normalized = str(group_id).strip()
        if not normalized or normalized not in self._allowed_group_ids:
            return None
        return normalized

    def _relationship_snapshot(self, group_id: str, user_id: str) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        try:
            affection_store = getattr(self._affection_engine, "_store", None)
            get_profile = getattr(affection_store, "get", None)
            if callable(get_profile):
                profile = cast(Any, get_profile)(user_id)
                snapshot["affection_score"] = float(profile.score)
                snapshot["affection_tier"] = str(profile.tier)
        except Exception as exc:
            _L.debug("social narrative affection snapshot skipped | err={}", exc)
        try:
            resolve = getattr(self._climate_engine, "resolve", None)
            if callable(resolve):
                state = cast(Any, resolve)(group_id=group_id, user_id=user_id)
                snapshot["climate_valence"] = float(state.valence)
                snapshot["climate_familiarity"] = float(state.familiarity)
        except Exception as exc:
            _L.debug("social narrative climate snapshot skipped | err={}", exc)
        return snapshot


def config_schema() -> dict[str, Any]:
    return SocialNarrativeConfig.model_json_schema()
