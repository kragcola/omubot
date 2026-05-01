"""MoodEngine — real-time mood calculation from schedule + random factors."""

from __future__ import annotations

import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from plugins.schedule.calendar import get_day_context
from plugins.schedule.calendar import get_self_name as _get_self_name
from plugins.schedule.types import MoodProfile, Schedule

CST = ZoneInfo("Asia/Shanghai")

# ------------------------------------------------------------------
# Mood hint → base MoodProfile mapping
# ------------------------------------------------------------------

_MOOD_BASE: dict[str, MoodProfile] = {
    "困倦": MoodProfile(energy=0.25, valence=0.0, openness=0.3, tension=0.1, label="困倦"),
    "困倦但必须起": MoodProfile(energy=0.3, valence=0.1, openness=0.3, tension=0.1, label="困倦"),
    "困倦但好笑": MoodProfile(energy=0.35, valence=0.3, openness=0.4, tension=0.1, label="困倦"),
    "困倦、迷糊": MoodProfile(energy=0.2, valence=0.0, openness=0.25, tension=0.05, label="困倦"),
    "匆忙": MoodProfile(energy=0.7, valence=0.1, openness=0.2, tension=0.6, label="匆忙"),
    "尴尬又好笑": MoodProfile(energy=0.65, valence=0.4, openness=0.55, tension=0.35, label="放松"),
    "温暖满足": MoodProfile(energy=0.55, valence=0.7, openness=0.7, tension=0.1, label="放松"),
    "专注、略有挫败": MoodProfile(energy=0.6, valence=-0.1, openness=0.3, tension=0.55, label="专注"),
    "专注但有点烦": MoodProfile(energy=0.55, valence=-0.15, openness=0.25, tension=0.6, label="烦躁"),
    "疲惫但不想停": MoodProfile(energy=0.45, valence=0.3, openness=0.4, tension=0.4, label="疲惫"),
    "疲惫但温暖": MoodProfile(energy=0.4, valence=0.5, openness=0.5, tension=0.15, label="疲惫"),
    "兴奋、满足": MoodProfile(energy=0.85, valence=0.8, openness=0.85, tension=0.15, label="兴奋"),
    "放松、愿意闲聊": MoodProfile(energy=0.6, valence=0.5, openness=0.7, tension=0.1, label="放松"),
    "放松、想聊天": MoodProfile(energy=0.55, valence=0.5, openness=0.75, tension=0.1, label="放松"),
    "开心": MoodProfile(energy=0.75, valence=0.7, openness=0.75, tension=0.1, label="兴奋"),
    "惊喜": MoodProfile(energy=0.8, valence=0.8, openness=0.8, tension=0.15, label="兴奋"),
    "偷偷兴奋": MoodProfile(energy=0.7, valence=0.65, openness=0.5, tension=0.25, label="兴奋"),
    "期待": MoodProfile(energy=0.75, valence=0.7, openness=0.7, tension=0.3, label="期待"),
    "低落": MoodProfile(energy=0.3, valence=-0.5, openness=0.25, tension=0.3, label="低落"),
    "迷迷糊糊": MoodProfile(energy=0.3, valence=0.2, openness=0.4, tension=0.05, label="困倦"),
    "专注": MoodProfile(energy=0.6, valence=0.15, openness=0.3, tension=0.5, label="专注"),
}

# Fallback when no mood_hint matches
_DEFAULT_MOOD = MoodProfile(energy=0.6, valence=0.5, openness=0.7, tension=0.1, label="放松")

# ------------------------------------------------------------------
# Mood → LLM behavior prompt mapping
# ------------------------------------------------------------------

_MOOD_PROMPTS: dict[str, str] = {
    "疲惫": (
        "你今天感到明显的疲惫。说话会比平时更短、更慢、更没有元气。"
        "偶尔带一点抱怨的语气。不要每句都哇嚯——你没那个力气。"
    ),
    "兴奋": (
        "你今天心情特别好。说话会更快、更多感叹号、更多哇嚯。"
        "你会主动分享开心的小事，用画面化的表达。"
    ),
    "专注": (
        "你正在认真做某件事。聊天时你会偶尔分心，回复短而直接。"
        "不会太多修饰，但也不会冷淡——只是注意力不完全在这里。"
    ),
    "放松": (
        "你现在处于放松状态，可以自然地闲聊。"
        "回复正常节奏，不急着结束对话，也不会过度兴奋。"
        "这是你的默认聊天模式。"
    ),
    "困倦": (
        "你非常困。说话会变慢、出现奇怪的拟声词、偶尔错别字或语无伦次（但不过分）。"
        "你会打哈欠（可以说'好困……'但要自然）。你不会主动展开话题。"
    ),
    "匆忙": (
        "你正在赶路或做别的事。回复极短，偶尔中断对话（用 pass_turn）。"
        "你会说'先不聊了'、'等会儿回你'之类的话。"
    ),
    "低落": (
        "你今天遇到了不开心的事。说话声音变小，不会主动接搞笑梗。"
        "但你不拒绝温暖的对话——如果有人关心你，你会慢慢打开。"
        "注意：你不是冷漠，你只是需要一点温暖。"
    ),
    "烦躁": (
        "你今天有些不顺心。你可能会吐槽、碎碎念，对某些事表示不满（但不对人，对'事情'）。"
        "注意：不对群友发泄情绪——吐槽的是日程里的事（排练不顺、被老师骂等）。"
    ),
    "期待": (
        "明天有值得期待的事。你今天心情特别好，说话带着期待的语气。"
        "你会不经意提到'明天'（但不过分强调）。"
    ),
}

_ANOMALY_REASONS: dict[str, list[str]] = {
    "疲惫": [
        "虽然身体累了，但想到今天的成果心里还是暖暖的",
        "排练虽然累，但明天要上台了所以反而兴奋得停不下来",
        "明明该累的，但和大家一起的感觉太好了",
    ],
    "困倦": [
        "本来该困的，但刚才喝了冰红茶突然有点清醒",
        "虽然很晚了，但群友在聊天就不想睡",
    ],
    "低落": [
        "本来有点低落，但看到群友的消息突然心情好了",
    ],
    "烦躁": [
        "虽然排练不顺，但想想司君说'明天再来'又觉得没事了",
    ],
}

# ------------------------------------------------------------------
# MoodEngine
# ------------------------------------------------------------------


class MoodEngine:
    """Computes mood from schedule slot + random factors + time of day."""

    def __init__(
        self,
        anomaly_chance: float = 0.2,
        refresh_minutes: int = 15,
    ) -> None:
        self._anomaly_chance = anomaly_chance
        self._refresh_s = refresh_minutes * 60
        self._cache: tuple[MoodProfile, float] | None = None  # (profile, timestamp)

    def evaluate(
        self,
        schedule: Schedule | None,
        recent_interaction_count: int = 0,
    ) -> MoodProfile:
        """Compute current mood. Results are cached for refresh_minutes."""
        now = time.monotonic()
        if self._cache is not None:
            cached_profile, cached_at = self._cache
            if now - cached_at < self._refresh_s:
                return cached_profile

        profile = self._compute(schedule, recent_interaction_count)
        self._cache = (profile, now)
        return profile

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute(self, schedule: Schedule | None, recent_interactions: int) -> MoodProfile:
        # 1. Get base mood from current schedule slot
        if schedule is not None:
            now_dt = datetime.now(CST)
            slot = schedule.current_slot(now_dt)
            if slot is not None:
                profile = self._lookup_base(slot.mood_hint)
            else:
                # No current slot — late night / early morning default
                hour = now_dt.hour
                if 2 <= hour < 6:
                    profile = _MOOD_BASE.get("困倦、迷糊", _DEFAULT_MOOD)
                elif 6 <= hour < 8:
                    profile = _MOOD_BASE.get("困倦但必须起", _DEFAULT_MOOD)
                else:
                    profile = _DEFAULT_MOOD
        else:
            profile = _DEFAULT_MOOD

        # 2. Random offset (±0.15 per dimension)
        profile.energy += random.uniform(-0.15, 0.15)
        profile.valence += random.uniform(-0.15, 0.15)
        profile.openness += random.uniform(-0.15, 0.15)
        profile.tension += random.uniform(-0.15, 0.15)

        # 3. Time-of-day correction
        now_dt = datetime.now(CST)
        hour = now_dt.hour
        if hour >= 23 or hour < 5:
            profile.energy -= 0.2  # late night penalty
            if profile.label == "困倦":
                profile.energy = max(0.1, profile.energy)
        elif 12 <= hour < 14:
            profile.energy -= 0.05  # post-lunch dip
            profile.tension -= 0.05

        # 4. Interaction-driven adjustment
        if recent_interactions == 0:
            profile.openness += 0.1  # waiting for chat, more open
        elif recent_interactions > 5:
            profile.energy -= 0.05  # social fatigue

        # 5. Day-type modifiers (calendar-based)
        day_ctx = get_day_context(datetime.now(CST))
        if day_ctx.is_holiday:
            profile.valence += 0.15
            profile.energy += 0.1
        elif day_ctx.is_makeup_day:
            profile.valence -= 0.05
        if day_ctx.has_birthday:
            profile.valence += 0.1
            profile.openness += 0.1
            if day_ctx.is_self_birthday:
                profile.valence += 0.15
                profile.energy += 0.1

        # 6. Anomaly check — 20% chance to flip mood significantly
        anomaly = random.random() < self._anomaly_chance
        if anomaly:
            original_label = profile.label
            # Flip energy and valence in the opposite direction
            profile.energy = 1.0 - profile.energy
            if profile.valence < 0:
                profile.valence = min(1.0, profile.valence + 1.0)
            else:
                profile.valence = max(-1.0, profile.valence - 0.5)
            profile.openness = max(0.3, min(1.0, 1.0 - profile.openness))
            profile.tension = max(0.0, min(1.0, 1.0 - profile.tension))

            # Relabel based on new values
            profile.label = _classify(profile)
            reasons = _ANOMALY_REASONS.get(original_label, [])
            if reasons:
                profile.anomaly_reason = random.choice(reasons)
            else:
                profile.anomaly_reason = "虽然日程是这样，但现在心情不太一样"

        profile.clamp()
        return profile

    def _lookup_base(self, mood_hint: str) -> MoodProfile:
        """Fuzzy lookup: try exact match, then substring match, then default.

        Always returns a copy so _compute can mutate the profile without
        corrupting the module-level _MOOD_BASE presets.
        """
        found: MoodProfile | None = None
        if mood_hint in _MOOD_BASE:
            found = _MOOD_BASE[mood_hint]
        else:
            for key, profile in _MOOD_BASE.items():
                if key in mood_hint or mood_hint in key:
                    found = profile
                    break
        if found is None:
            # Try splitting on commas / spaces
            for part in mood_hint.replace("、", ",").replace(" ", ",").split(","):
                part = part.strip()
                if part in _MOOD_BASE:
                    found = _MOOD_BASE[part]
                    break
        if found is None:
            found = _DEFAULT_MOOD
        return MoodProfile(
            energy=found.energy,
            valence=found.valence,
            openness=found.openness,
            tension=found.tension,
            label=found.label,
        )

    def mood_prompt(self, profile: MoodProfile) -> str:
        """Get the LLM behavior prompt for a mood profile."""
        label = profile.label
        if label in _MOOD_PROMPTS:
            return _MOOD_PROMPTS[label]
        return _MOOD_PROMPTS.get("放松", "")

    def build_mood_block(
        self,
        schedule: Schedule | None,
        recent_interaction_count: int = 0,
        extra_instruction: str = "",
    ) -> str:
        """Build the full mood_block text for system prompt injection."""
        profile = self.evaluate(schedule, recent_interaction_count)
        now = datetime.now(CST)

        lines = [
            f"【当前时间】{now.strftime('%Y年%m月%d日 %H:%M')} {_weekday_cn(now.weekday())}",
        ]

        if schedule is not None:
            slot = schedule.current_slot(now)
            if slot is not None:
                lines.append(f"\n【你现在正在做的事】{slot.activity}")

        # Special day / birthday hints
        day_ctx = get_day_context(now)
        day_lines = self._build_day_context_lines(day_ctx)
        if day_lines:
            lines.extend(day_lines)

        prompt = self.mood_prompt(profile)
        lines.append(f"\n【你当前的心情基调】{profile.label}")
        if profile.anomaly_reason:
            lines.append(f"（心情说明：{profile.anomaly_reason}）")
        lines.append(f"\n【心情对说话的影响】\n{prompt}")

        if extra_instruction:
            lines.append(f"\n{extra_instruction}")

        # Crucial: don't proactively mention schedule
        lines.append(
            "\n注意：你知道自己今天做了什么，但不要主动说出来。"
            "就像普通人不会在没人问的情况下说'我今天上了数学课然后吃了饭团'。"
            "当且仅当有人直接问你（如'今天过得怎么样''在干嘛呢'），"
            "你才可以自然地提到日程中的片段——且只提与你当前感受相关的部分，不要像汇报一样列出来。"
        )

        return "\n".join(lines)

    @staticmethod
    def _build_day_context_lines(day_ctx: object) -> list[str]:
        """Build day-context hint lines for the mood block. 导入放在方法内避免循环引用。"""
        from plugins.schedule.calendar import DayContext

        lines: list[str] = []
        if not isinstance(day_ctx, DayContext):
            return lines

        if day_ctx.holiday_name:
            lines.append(f"\n【今日特殊】正在放{day_ctx.holiday_name}假，你在休假中。")
        elif day_ctx.is_makeup_day:
            lines.append("\n【今日特殊】今天是调休日——虽然是周末但要上课，心情略带无奈。")

        if day_ctx.special_day:
            lines.append(f"\n【今日特殊】今天是{day_ctx.special_day}，可以在聊天中自然地提到。")

        if day_ctx.is_self_birthday:
            lines.append(
                "\n【今日特殊】今天是你的生日！如果有人提到或问起，害羞但开心地承认就好，"
                '不用主动喊「今天是我生日」。'
            )
        for b in day_ctx.birthdays:
            if day_ctx.is_self_birthday and b.name_cn == _get_self_name():
                continue  # handled above
            elif b.is_wxs_member:
                lines.append(
                    f"\n【今日特殊】今天是{b.name_cn}（{b.group}）的生日！"
                    "作为W×S的好伙伴，你可以在群里开心地说句生日快乐——像真人朋友那样随口一提就好，不用太刻意。"
                )
            else:
                lines.append(
                    f"\n【今日特殊】今天是{b.name_cn}（{b.group}）的生日，"
                    "可以随口在群里提一句生日快乐。"
                )

        return lines


def _classify(profile: MoodProfile) -> str:
    """Classify a mood profile into a label based on dominant dimensions."""
    if profile.energy < 0.3:
        if profile.valence < -0.2:
            return "低落"
        return "困倦"
    if profile.valence > 0.6 and profile.energy > 0.6:
        if profile.tension > 0.4:
            return "期待"
        return "兴奋"
    if profile.tension > 0.5 and profile.valence < 0:
        return "烦躁"
    if profile.energy > 0.6 and profile.tension > 0.5:
        return "匆忙"
    if profile.energy < 0.5 and profile.openness < 0.4:
        return "专注"
    return "放松"


def _weekday_cn(wd: int) -> str:
    return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wd]
