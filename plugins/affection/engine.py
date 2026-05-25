"""AffectionEngine — score computation, nickname resolution, prompt block."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from plugins.affection.models import AffectionProfile
from plugins.affection.store import AffectionStore
from services.humanization import AFFECTION_FAMILIARITY_SLOT, humanization_source
from services.system_module import Scope

if TYPE_CHECKING:
    from kernel.config import GroupMemoryConfig
    from services.system_module import RuntimeStateBus

CST = ZoneInfo("Asia/Shanghai")


class AffectionEngine:
    """Manages affection score, nickname resolution, and prompt injection."""

    def __init__(
        self,
        store: AffectionStore,
        score_increment: float = 0.8,
        daily_cap: float = 10.0,
    ) -> None:
        self._store = store
        self._score_increment = score_increment
        self._daily_cap = daily_cap
        self._group_memory_config: GroupMemoryConfig | None = None
        self._runtime_state: RuntimeStateBus | None = None

    def set_group_memory_config(self, config: GroupMemoryConfig) -> None:
        """Inject group memory config for pool-aware nickname resolution."""
        self._group_memory_config = config

    def set_runtime_state_bus(self, bus: RuntimeStateBus | None) -> None:
        """Inject runtime state bus for short-lived humanization signals."""
        self._runtime_state = bus

    # ------------------------------------------------------------------
    # Score management
    # ------------------------------------------------------------------

    async def record_interaction(self, user_id: str) -> AffectionProfile:
        """Record a user interaction, bumping score with daily cap."""
        profile = self._store.get(user_id)
        now = datetime.now(CST)
        today = now.strftime("%Y-%m-%d")

        if profile.daily_date != today:
            profile.daily_count = 0
            profile.daily_date = today

        max_daily_interactions = int(self._daily_cap / self._score_increment)
        if profile.daily_count >= max_daily_interactions:
            self._write_familiarity_state(user_id, profile)
            return profile

        if profile.first_interaction == "":
            profile.first_interaction = now.isoformat()

        profile.daily_count += 1
        profile.total_interactions += 1
        profile.score = min(100.0, profile.score + self._score_increment)
        profile.last_interaction = now.isoformat()

        self._store.save(profile)
        self._write_familiarity_state(user_id, profile)
        return profile

    def familiarity_score(self, user_id: str) -> float:
        """Return short-term familiarity as a 0..1 signal."""
        profile = self._store.get(user_id)
        long_term = max(0.0, min(1.0, profile.score / 100.0))
        daily = max(0.0, min(1.0, profile.daily_count / 10.0))
        return round(min(1.0, long_term * 0.7 + daily * 0.3), 4)

    def _write_familiarity_state(self, user_id: str, profile: AffectionProfile) -> None:
        if self._runtime_state is None or not user_id:
            return
        familiarity = self.familiarity_score(user_id)
        self._runtime_state.set(
            AFFECTION_FAMILIARITY_SLOT,
            {
                "user_id": user_id,
                "familiarity": familiarity,
                "score": profile.score,
                "tier": profile.tier,
                "daily_count": profile.daily_count,
                "total_interactions": profile.total_interactions,
                "mood_bonus_valence": profile.mood_bonus_valence,
            },
            scope=Scope(user_id=user_id),
            source=humanization_source("affection:familiarity"),
            confidence=1.0,
            decay_at=datetime.now() + timedelta(minutes=60),
        )

    def set_nickname(self, user_id: str, nickname: str) -> AffectionProfile:
        """Set a custom nickname for a user (private chat)."""
        profile = self._store.get(user_id)
        profile.custom_nickname = nickname
        self._store.save(profile)
        return profile

    def _resolve_pool_ids(self, group_id: str | None) -> list[str] | None:
        """Resolve pool IDs from group_id using the injected config."""
        if group_id and self._group_memory_config is not None:
            return self._group_memory_config.resolve_group_pools(group_id)
        return None

    def set_group_nickname(
        self,
        user_id: str,
        nickname: str,
        *,
        pool_ids: list[str] | None = None,
        group_id: str | None = None,
    ) -> AffectionProfile:
        """Set a custom nickname for a user (group chat, separate from private).

        pool_ids: explicit scope IDs (takes priority).
        group_id: used to resolve pool_ids via GroupMemoryConfig when pool_ids is None.
                  When neither is provided, stores under "__default__" (legacy fallback).
        """
        if pool_ids is None:
            pool_ids = self._resolve_pool_ids(group_id)
        profile = self._store.get(user_id)
        targets = pool_ids if pool_ids else ["__default__"]
        for sid in targets:
            profile.group_nicknames[sid] = nickname
        self._store.save(profile)
        return profile

    def set_suffix(self, user_id: str, suffix: str) -> AffectionProfile:
        """Set preferred address suffix (君, 酱, etc.)."""
        profile = self._store.get(user_id)
        profile.preferred_suffix = suffix
        self._store.save(profile)
        return profile

    # ------------------------------------------------------------------
    # Nickname resolution
    # ------------------------------------------------------------------

    def resolve_nickname(
        self,
        user_id: str,
        group_card: str = "",
        qq_nickname: str = "",
        *,
        in_group: bool = False,
        pool_ids: list[str] | None = None,
    ) -> str:
        """Resolve the best nickname for a user.

        Private: custom_nickname > QQ nickname > QQ号
        Group:   group_nickname (pool-aware) > group card > QQ nickname > QQ号
        """
        profile = self._store.get(user_id)
        if in_group:
            # Pool-aware: look up across all pool scope_ids
            gn = self._lookup_group_nickname(profile, pool_ids)
            if gn:
                return gn
        if not in_group and profile.custom_nickname:
            return profile.custom_nickname
        if group_card:
            return group_card
        if qq_nickname:
            return qq_nickname
        return user_id

    def _lookup_group_nickname(
        self, profile: AffectionProfile, pool_ids: list[str] | None
    ) -> str:
        """Look up group nickname across pool scope_ids."""
        if not profile.group_nicknames:
            return ""
        if pool_ids:
            for pid in pool_ids:
                if pid in profile.group_nicknames:
                    return profile.group_nicknames[pid]
        # Fallback: check __default__ or __legacy__
        return profile.group_nicknames.get("__default__") or profile.group_nicknames.get("__legacy__", "")

    def resolve_suffix(self, user_id: str) -> str:
        """Get the address suffix to use for a user."""
        profile = self._store.get(user_id)
        if profile.preferred_suffix:
            return profile.preferred_suffix
        return profile.default_suffix

    # ------------------------------------------------------------------
    # Prompt block
    # ------------------------------------------------------------------

    def build_affection_block(
        self,
        user_id: str,
        *,
        in_group: bool = False,
        pool_ids: list[str] | None = None,
    ) -> str:
        """Build the affection_block text for system prompt injection.

        When *in_group* is True, relationship detail is masked to simulate
        the difference between public and private interactions.
        """
        profile = self._store.get(user_id)

        if profile.total_interactions == 0:
            lines = [
                "【与当前用户的关系】",
                f"你和 QQ号{user_id} 的用户是初次对话。保持正常的友好态度。",
            ]
            if in_group:
                gn = self._lookup_group_nickname(profile, pool_ids)
                if gn:
                    lines.append(f"在群聊中他希望你称呼他为「{gn}」。")
            elif not in_group and profile.custom_nickname:
                lines.append(f"他希望你称呼他为「{profile.custom_nickname}」。")
            else:
                lines.append("使用对方显示的昵称来称呼即可。")
            return "\n".join(lines)

        # In group chat, use muted tier labels to maintain social distance
        if in_group:
            tier_public = {
                "陌生人": "不太熟",
                "熟人": "有点面熟",
                "朋友": "聊过不少",
                "好朋友": "关系不错",
                "重要的人": "很聊得来",
            }
            tier_label = tier_public.get(profile.tier, "不太熟")
            lines = [
                "【与当前用户的关系 — 群聊社交距离模式】",
                f"你认识 QQ号{user_id} 的用户。公开场合的评价：{tier_label}。",
                "注意：你现在在群聊中，应保持适度的社交距离。",
                "不要主动暴露你对他的深入了解（如私下聊过的话题、好感度、昵称偏好等）。",
                "用「好像记得」「似乎」「印象中」等模糊语气来提及对他的了解。",
                "但如果他主动提起私下聊过的话题或询问你，可以自然地接话——不装傻，只是不主动。",
            ]
            gn = self._lookup_group_nickname(profile, pool_ids)
            if gn:
                lines.append(f"在群聊中，他希望你称呼他为「{gn}」。这是他在群里的公开称呼，可以大方使用。")
        else:
            tier_descriptions = {
                "陌生人": "你们还不太熟悉，保持礼貌友好的距离。",
                "熟人": "你们已经聊过几次了，可以自然一些。",
                "朋友": "你把他当作朋友，聊天时更放松、更温暖。",
                "好朋友": "你们的关系很好，你对他有信任感和亲近感。",
                "重要的人": "他是你非常重视的人，你会格外真诚和用心地对待他。",
            }
            tier_text = tier_descriptions.get(profile.tier, tier_descriptions["陌生人"])

            lines = [
                "【与当前用户的关系】",
                f"好感度：{profile.score:.0f}/100（{profile.tier}）",
                f"你们已经聊过 {profile.total_interactions} 次。{tier_text}",
            ]

            # Nickname guidance — private only
            if profile.custom_nickname:
                lines.append(f"他希望你称呼他为「{profile.custom_nickname}」。")
            else:
                suffix = self.resolve_suffix(user_id)
                if suffix:
                    lines.append(
                        f"你可以自然地用「名字+{suffix}」来称呼他，"
                        f"但只在合适的时候用，不要每个回复都加。"
                    )

            # Mood boost for high-affection users — private only
            if profile.mood_bonus_valence >= 0.10:
                lines.append(
                    "因为你和他已经比较亲近了，即使你今天心情不太好，"
                    "对他的态度也会比平时温和一些——不会把负面情绪带给他。"
                )

        return "\n".join(lines)
