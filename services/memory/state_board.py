"""Group state board: lightweight rule-based conversation state summarizer.

Derives active users, recent topics, message frequency, and @mentions
from the SQLite MessageLog. No external dependencies, no extra LLM calls.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.memory.message_log import MessageLog

_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]+\]")
_AT_RE = re.compile(r"@(\d+)")
_NON_CJK_OR_DIGIT = re.compile(r"[^一-鿿㐀-䶿0-9]")
QQ_FROM_SPEAKER = re.compile(r"\((\d+)\)$")

STOP_CHARS: frozenset[str] = frozenset(
    "的了吗呢吧啊呀哦嗯呗呗哈嘻嘿哼呵哟嘛呐啦呀哇"
    "在是和不这我有他她它你我"
    "一个就都也还"
    "说看去想来去做"
    "过到着"
    "很太比较"
    "可以"
    "什么怎么哪"
    "因为所以但是如果虽然"
    "对给让把被"
    "上中下前"
    "今天昨天明"
    "这个那个"
    "没有已经"
    "会能"
    "要不要"
    "时候"
    "自己"
    "知道觉得"
    "应该可能"
    "现在"
    "真的"
    "为什么"
    "有点"
    "然后"
    "哈哈哈"
    "好的"
    "没事"
    "其实"
    "感觉"
    "好像"
    "只是"
    "还有"
    "确实"
    "不过"
    "已经"
)


@dataclass(frozen=True)
class GroupStateSnapshot:
    """Immutable snapshot of current group conversation state."""

    active_users: str = "暂无"
    recent_topics: str = "暂无显著话题"
    message_frequency: str = "暂无消息"
    recent_mentions: str = "无"

    def to_prompt_text(self) -> str:
        lines = [
            "【当前群聊状态】",
            f"最近活跃：{self.active_users}",
            f"近期话题：{self.recent_topics}",
            f"消息频率：{self.message_frequency}",
            f"最近@你：{self.recent_mentions}",
        ]
        return "\n".join(lines)


def extract_qq(speaker: str) -> str:
    """Extract QQ number from '昵称(QQ号)' format."""
    m = QQ_FROM_SPEAKER.search(speaker)
    return m.group(1) if m else speaker


def extract_nick(speaker: str) -> str:
    """Extract display name from '昵称(QQ号)' format."""
    m = QQ_FROM_SPEAKER.search(speaker)
    return speaker[:m.start()] if m else speaker


def clean_text(text: str | None) -> str:
    """Remove CQ codes and non-CJK characters, return cleaned text."""
    if not text:
        return ""
    cleaned = _CQ_CODE_RE.sub("", text)
    cleaned = _NON_CJK_OR_DIGIT.sub("", cleaned)
    return cleaned


def extract_bigrams(text: str) -> list[str]:
    """Extract character bigrams from cleaned text."""
    bigrams = []
    for i in range(len(text) - 1):
        a, b = text[i], text[i + 1]
        if a not in STOP_CHARS and b not in STOP_CHARS:
            bigrams.append(text[i:i + 2])
    return bigrams


class GroupStateBoard:
    """Rule-based group conversation state summarizer.

    Reads recent messages from MessageLog (SQLite) and derives:
    active users, recent topics, message frequency, and @mentions.
    """

    _LOOKBACK_COUNT = 30
    _TOPIC_MSG_COUNT = 20
    _ACTIVE_WINDOW_SECONDS = 300  # 5 minutes
    _TOP_TOPIC_COUNT = 3

    def __init__(
        self, message_log: MessageLog, bot_self_id: str = ""
    ) -> None:
        self._message_log = message_log
        self.bot_self_id = bot_self_id

    async def query_state(self, group_id: str) -> GroupStateSnapshot:
        """Return a snapshot of current group conversation state."""
        rows = await self._message_log.query_recent(
            group_id, limit=self._LOOKBACK_COUNT
        )
        if not rows:
            return GroupStateSnapshot()

        return GroupStateSnapshot(
            active_users=self._derive_active_users(rows),
            recent_topics=self._derive_topics(rows),
            message_frequency=self._derive_frequency(rows),
            recent_mentions=self._derive_mentions(rows),
        )

    # ------------------------------------------------------------------
    # Internal derivation methods
    # ------------------------------------------------------------------

    def _derive_active_users(self, rows: list[dict]) -> str:
        """Extract distinct recent speakers, most recent first."""
        seen: set[str] = set()
        users: list[str] = []
        for row in rows:
            if row.get("role") != "user":
                continue
            speaker = row.get("speaker") or ""
            if not speaker:
                continue
            qq = extract_qq(speaker)
            if qq in seen or not qq.isdigit():
                continue
            seen.add(qq)
            nick = extract_nick(speaker)
            users.append(f"{nick}({qq})")
            if len(users) >= 5:
                break
        return "、".join(users) if users else "暂无"

    def _derive_frequency(self, rows: list[dict]) -> str:
        """Count messages within the active window."""
        now = time.time()
        cutoff = now - self._ACTIVE_WINDOW_SECONDS
        count = sum(
            1
            for row in rows
            if row.get("role") == "user"
            and isinstance(row.get("created_at"), (int, float))
            and row["created_at"] >= cutoff
        )
        if count >= 8:
            label = "活跃"
        elif count >= 3:
            label = "正常"
        elif count >= 1:
            label = "冷清"
        else:
            return "暂无消息"
        return f"{label}（过去5分钟 {count} 条消息）"

    def _derive_topics(self, rows: list[dict]) -> str:
        """Extract top bigrams from recent message content_text."""
        user_rows = [r for r in rows if r.get("role") == "user"]
        topic_rows = user_rows[-self._TOPIC_MSG_COUNT:]
        if len(topic_rows) < 3:
            return "暂无显著话题"

        freq: dict[str, int] = {}
        for row in topic_rows:
            text = clean_text(row.get("content_text"))
            if len(text) < 4:
                continue
            bigrams = extract_bigrams(text)
            for bg in bigrams:
                freq[bg] = freq.get(bg, 0) + 1

        # Filter: must appear at least twice
        qualified = [
            (bg, count) for bg, count in freq.items() if count >= 2
        ]
        qualified.sort(key=lambda x: x[1], reverse=True)

        topics = [bg for bg, _ in qualified[:self._TOP_TOPIC_COUNT]]
        return "、".join(topics) if topics else "暂无显著话题"

    def _derive_mentions(self, rows: list[dict]) -> str:
        """Find recent @mentions of the bot, grouped by speaker with timing."""
        if not self.bot_self_id:
            return "无"

        now = time.time()
        mentions: dict[str, list[float]] = {}
        for row in rows:
            text = row.get("content_text") or ""
            at_targets = _AT_RE.findall(text)
            if self.bot_self_id not in at_targets:
                continue
            speaker = row.get("speaker") or ""
            if not speaker:
                continue
            nick = extract_nick(speaker)
            ts = row.get("created_at", 0)
            if nick not in mentions:
                mentions[nick] = []
            mentions[nick].append(ts)

        if not mentions:
            return "无"

        parts: list[str] = []
        for nick, timestamps in mentions.items():
            count = len(timestamps)
            latest = max(timestamps)
            minutes_ago = int((now - latest) / 60)
            if minutes_ago < 1:
                time_str = "刚刚"
            elif minutes_ago == 1:
                time_str = "1 分钟前"
            else:
                time_str = f"{minutes_ago} 分钟前"
            if count > 1:
                parts.append(f"{nick} {time_str} @了你 {count}次")
            else:
                parts.append(f"{nick} {time_str} @了你")
        return "、".join(parts)
