"""Worldbook feature gates and path configuration (all default false)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class WorldbookConfig(BaseModel):
    """Explicit feature gates for the Living Story Runtime.

    Every gate defaults to False. With ``enabled=false`` the runtime must
    perform no registry I/O, prompt changes, storylet selection, state
    mutation, or background work.
    """

    enabled: bool = False
    chat_projection_enabled: bool = False
    schedule_projection_enabled: bool = False
    storylet_enabled: bool = False
    dream_proposal_enabled: bool = False
    social_evidence_enabled: bool = False
    # Fail-closed social→story bridge: empty list rejects all groups even when
    # enabled + social_evidence_enabled are true.
    social_group_allowlist: list[str] = Field(default_factory=list)

    # Versioned configuration roots (read-only canon / storylets).
    canon_dir: str = Field(default="config/worldbook/canon", min_length=1)
    storylet_dir: str = Field(default="config/worldbook/storylets", min_length=1)
    # Versioned production StoryArc seed pack (read-only sources; importer only).
    arc_seed_dir: str = Field(default="config/worldbook/arcs", min_length=1)
    # Mutable runtime state (life state, proposals) — isolated namespace.
    state_dir: str = Field(default="storage/worldbook", min_length=1)
    # StoryArc ledger (reuses existing store; not a second truth source).
    story_arc_dir: str = Field(
        default="storage/living_persona/story_arcs",
        min_length=1,
    )

    @field_validator("social_group_allowlist", mode="before")
    @classmethod
    def _unique_nonempty_group_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("social_group_allowlist must be a list of strings")
        seen: set[str] = set()
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                raise ValueError(
                    "social_group_allowlist entries must be non-empty strings"
                )
            if text in seen:
                raise ValueError(
                    f"social_group_allowlist has duplicate group id: {text!r}"
                )
            seen.add(text)
            out.append(text)
        return out

    # Projection budgets (characters). Atomic blocks never truncated mid-fact.
    total_budget_chars: int = Field(default=2400, ge=0)
    canon_budget_chars: int = Field(default=900, ge=0)
    life_budget_chars: int = Field(default=400, ge=0)
    arc_budget_chars: int = Field(default=700, ge=0)
    social_budget_chars: int = Field(default=400, ge=0)
    storylet_budget_chars: int = Field(default=300, ge=0)

    # Drama manager defaults.
    max_setbacks_per_arc: int = Field(default=1, ge=0)
    max_events_per_tick: int = Field(default=1, ge=0)
    recovery_window_steps: int = Field(default=3, ge=0)

    def any_projection_enabled(self) -> bool:
        return bool(
            self.enabled
            and (
                self.chat_projection_enabled
                or self.schedule_projection_enabled
            )
        )

    def needs_story_arc_store(self) -> bool:
        """True when any causal gate requires a StoryArc ledger adapter."""
        return bool(
            self.enabled
            and (
                self.schedule_projection_enabled
                or self.storylet_enabled
                or self.dream_proposal_enabled
                or self.social_evidence_enabled
            )
        )

    def resolve_paths(self, root: str | Path | None = None) -> dict[str, Path]:
        base = Path(root) if root is not None else Path(".")
        return {
            "canon_dir": base / self.canon_dir,
            "storylet_dir": base / self.storylet_dir,
            "arc_seed_dir": base / self.arc_seed_dir,
            "state_dir": base / self.state_dir,
            "story_arc_dir": base / self.story_arc_dir,
        }


def worldbook_config_from_mapping(data: Any) -> WorldbookConfig:
    """Build config from a plain mapping (plugin defaults or test fixtures)."""
    if isinstance(data, WorldbookConfig):
        return data
    if not isinstance(data, dict):
        return WorldbookConfig()
    # Support both flat plugin values and nested ``values`` envelopes.
    payload = data.get("values") if isinstance(data.get("values"), dict) else data
    if not isinstance(payload, dict):
        return WorldbookConfig()
    return WorldbookConfig.model_validate(payload)


def worldbook_gates_all_false(cfg: WorldbookConfig) -> bool:
    return not any(
        (
            cfg.enabled,
            cfg.chat_projection_enabled,
            cfg.schedule_projection_enabled,
            cfg.storylet_enabled,
            cfg.dream_proposal_enabled,
            cfg.social_evidence_enabled,
        )
    )
