"""LLM client: assemble message lists, call Anthropic API, handle tool loops."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from loguru import logger as _base_logger

from kernel.types import ReplyContext, ThinkerContext
from services.identity import Identity
from services.llm.cache_diagnostic import (
    CacheDiagnostic,
    CacheDiagnosticDiff,
    compute_cache_diagnostic,
    diff_cache_diagnostics,
)
from services.llm.llm_request import LLMRequest
from services.llm.prompt_builder import PromptBuilder
from services.llm.provider import ToolUse, create_provider, is_deepseek_v4_model, provider_mode
from services.llm.usage import UsageTracker
from services.media.image_cache import ImageCache
from services.memory.card_store import CardStore, NewCard
from services.memory.message_log import MessageLog
from services.memory.short_term import ChatMessage, ShortTermMemory
from services.memory.timeline import GroupTimeline
from services.memory.types import Content
from services.tools.context import ToolContext
from services.tools.registry import ToolRegistry

MAX_TOOL_ROUNDS = 5
_MAX_COMPACT_TOOL_ROUNDS = 3
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 5.0  # seconds
_API_FIRST_BYTE_WARN_S = 20.0
_API_TOTAL_WARN_S = 60.0

# Channel-tagged loggers — each maps to a LogChannelConfig boolean
logger = _base_logger  # keep bare logger for ERROR / EXCEPTION
_log_msg_in = _base_logger.bind(channel="message_in")
_log_msg_out = _base_logger.bind(channel="message_out")
_log_thinking = _base_logger.bind(channel="thinking")
_log_compact = _base_logger.bind(channel="compact")
_log_debug = _base_logger.bind(channel="debug")

_SEGMENT_SEP = "---cut---"
_SEGMENT_DELAY = 0.8  # seconds between segment sends (human-like pacing)
_MAX_SEND_SEGMENTS = 4
_BLANK_LINE_RE = re.compile(r"\n{2,}")
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]+\]")
_CQ_KV_FIX_RE = re.compile(r",(\w+):")
_GROUP_REPLY_STYLE_HINTS: dict[str, str] = {
    "gentle": "回复风格偏柔和、耐心、安抚感更强，避免过硬或过冲的表达。",
    "playful": "回复风格可以更轻松俏皮，允许一点点玩梗和抖机灵，但不要失控。",
    "concise": "回复尽量短一些，优先直接结论，减少过长铺垫和重复解释。",
    "energetic": "回复可以更有活力和在场感，语气积极，但不要变得吵闹失真。",
    "steady": "回复保持平稳、克制、可靠，少用夸张语气和过度情绪化表达。",
}
_STICKER_TOOL_NAMES = {"send_sticker", "save_sticker", "manage_sticker"}
_DEEPSEEK_V4_COMPACT_RATIO = 0.88
_EMPTY_VISIBLE_REPLY_FALLBACK = "我先缓一下，马上接你。"
_CONTROL_TOKEN_RE = re.compile(
    r"(?is)\s*(?:\[\s*pass[_\s-]*turn\s*\]|pass[_\s-]*turn|passturn)\s*(?:[:：\-]\s*.*)?\s*"
)
_VISIBLE_TOOL_OUTPUT_NAMES = {"send_sticker", "send_group_msg"}

# Markdown stripping — QQ does not render Markdown.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_BOLD2_RE = re.compile(r"__(.+?)__")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LIST_RE = re.compile(r"^[\-\*]\s+", re.MULTILINE)
_MD_OLIST_RE = re.compile(r"^\d+\.\s+", re.MULTILINE)
_MD_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
_MD_FENCE_RE = re.compile(r"```[\s\S]*?```")


def _extract_fence_content(fence: str) -> str:
    """Remove triple-backtick fences, keep content."""
    inner = fence[3:-3].strip()
    # Drop optional language tag on opening fence
    if "\n" in inner:
        inner = inner.split("\n", 1)[1] if inner.split("\n", 1)[0].strip().isidentifier() else inner
    return inner


def _strip_markdown(text: str) -> str:
    """Strip common Markdown formatting that QQ cannot render."""
    text = _MD_FENCE_RE.sub(lambda m: _extract_fence_content(m.group()), text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_BOLD2_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_LIST_RE.sub("", text)
    text = _MD_OLIST_RE.sub("", text)
    text = _MD_CODE_INLINE_RE.sub(r"\1", text)
    text = _MD_STRIKE_RE.sub(r"\1", text)
    return text


# Patterns for stripping parenthetical stage directions from LLM output.
# Stage directions use full-width Chinese parentheses （）with action text,
# or half-width () with Chinese action words (not kaomoji).
#
# Action/state characters: body parts, physical actions, emotional states that
# DeepSeek commonly puts in parens as stage direction.
_STAGE_ACTION_CHARS = (
    "困累饿躺趴揉打眨伸爬走跑跳坐站睡抱推拉哭笑叹捂挥"
    "滚晃闹踢踹蹲跪抓捏掐按摸拍击砍劈砍撕扯倒掉跌落飘"
    "昏倦乏疲酸疼痛痒颤抖嗦转翻扭摆摇发送补"
)
_STAGE_DIR_FULL_RE = re.compile(r"（[^）]*?[" + _STAGE_ACTION_CHARS + r"][^）]*?）")
# Standalone parenthetical that occupies the whole line (short, likely action)
_STAGE_DIR_SOLO_RE = re.compile(r"^[（(][^)）]{1,20}[）)]\s*$", re.MULTILINE)
# Half-width parenthetical: short with Chinese text, NOT kaomoji
_STAGE_DIR_HALF_RE = re.compile(r"\([^)≡≧≦▽◕‿✧☆♪・ω･]{1,20}\)")


# Patterns for stripping sticker-narration phrases the LLM may emit after
# calling send_sticker: "（已发送表情包）"、"表情包补上啦"、"表情包来啦" etc.
_STICKER_NARRATION_RE = re.compile(
    r"(?:（[^）]*?(?:已发送|表情包[已补发来去]|补上)[^）]*?）"
    r"|表情包(?:已发送|补上啦|来啦|送到|到啦)"
    r"|[已补]发送了?表情包[啦喔呢~！]*"
    r")"
)

def _strip_stage_direction(text: str) -> str:
    """Strip parenthetical stage directions that DeepSeek sometimes outputs.

    Removes （揉眼睛）（好困）（已发送表情包）etc., but preserves kaomoji like (≧▽≦).
    """
    text = _STAGE_DIR_FULL_RE.sub("", text)
    text = _STAGE_DIR_HALF_RE.sub("", text)
    # Remove lingering standalone lines that are just parentheses
    text = _STAGE_DIR_SOLO_RE.sub("", text)
    # Strip sticker-narration phrases
    text = _STICKER_NARRATION_RE.sub("", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_reply(text: str) -> str:
    """Full reply cleaning pipeline: markdown + narration + stage direction strip."""
    text = _strip_stage_direction(_strip_markdown(text or "..."))
    # Final safety: if the reply is now just narration/metacommentary, drop it
    if text.strip() in ("", "...", "☆", "~"):
        return text
    # Drop lines that are purely sticker narration
    lines = text.split("\n")
    kept = [
        line for line in lines
        if not _STICKER_NARRATION_RE.search(line)
        and line.strip() not in ("...",)
    ]
    return "\n".join(kept).strip() or "..."


def _strip_control_tokens(text: str) -> tuple[str, bool]:
    """Strip leaked internal control tokens from visible assistant text."""
    stripped = text or ""
    changed = False
    lines: list[str] = []
    for line in stripped.split("\n"):
        cleaned = _CONTROL_TOKEN_RE.sub("", line).strip()
        if cleaned != line.strip():
            changed = True
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip(), changed


def _contains_control_token(text: str) -> bool:
    return bool(_CONTROL_TOKEN_RE.search(text or ""))

# Kaomoji / action-description detection for sticker enforcement.
#
# Pattern A — special-character kaomoji: (≧▽≦)/  (｡･ω･｡)  (◕‿◕)✧  etc.
#   Parentheses containing at least one character from the "kaomoji" set
#   (symbols, Japanese kana, circled/pictographic glyphs).
#
# Pattern B — Chinese action description: （心虚地挠挠脸） （开心地晃了晃脑袋）
#   Full-width parentheses with short content containing an action verb,
#   body-part word, or emotion expression.
_KAOMOJI_SPECIAL_SET = (
    "≧▽≦^ω･｡☆★╥╯∀°∇⌒Д｀´～ˇ˙˘˚＾；;＞＜⊕⊙〃￣ーﾉヾ◕‿✧Θ□"
    "゜ﾟ┐┘┌└╮╭╰╯♪♭♯♂♀"
)
_KAOMOJI_SPECIAL_RE = re.compile(
    r"[\(（][^\)）]{0,15}[" + re.escape(_KAOMOJI_SPECIAL_SET) + r"][^\)）]{0,15}[\)）]"
)
# Full-width / half-width parentheses containing 1-15 chars of Chinese-friendly content
_ACTION_PAREN_RE = re.compile(r"[\(（]([一-鿿\w，,。！？、…　 ]{1,15})[\)）]")
_ACTION_INDICATOR_CHARS: set[str] = {
    "笑", "叹", "哭", "气", "挠", "晃", "摸", "拍", "戳", "推", "拉",
    "蹲", "跳", "蹦", "窜", "缩", "躲", "靠", "躺", "趴", "站", "坐",
    "歪", "转", "挥", "招", "喊", "叫", "嚷", "吼", "骂", "念", "想",
    "望", "盯", "瞅", "瞥", "瞪", "瞟", "看", "听", "问", "答", "说",
    "唱", "哼", "嘻", "嘿", "哈", "呵", "呐", "嘛", "捂", "捏", "掐",
    "按", "抓", "扯", "撕", "捶", "打", "踢", "踹", "摔", "跌", "撞",
    "冲", "逃", "溜", "飘", "荡", "摇", "曳", "颤", "抖", "羞", "愧",
    "慌", "窘", "臊", "尬", "懊", "悔", "恨", "恼", "怒", "怨",
    "闷", "烦", "愁", "急", "怕", "吓", "惊", "呆", "愣", "怔", "懵",
    "泪", "汗", "涕", "唾", "涎", "飞", "溅", "流", "淌", "掉", "甩",
    "抛", "扔", "头", "脸", "眉", "眼", "嘴", "鼻", "耳", "舌", "牙",
    "唇", "额", "腮", "颊", "膊", "臂", "掌", "拳", "指", "肚", "腿",
    "脚", "膝", "腰", "背", "肩", "叉腰", "托腮", "扶额", "捂脸", "嘟嘴",
    "吐舌", "摊手", "耸肩", "握拳", "鼓掌", "转圈", "歪头", "缩脖子",
    "开心", "得意", "心虚", "悄悄", "偷偷", "默默", "轻轻", "慢慢",
    "缓缓", "猛地", "忽然", "一下", "有点", "有些", "好", "很",
}
# Precompute single chars from the multi-char indicators for fast scanning
_ACTION_INDICATOR_SINGLE: set[str] = set()
for _s in _ACTION_INDICATOR_CHARS:
    _ACTION_INDICATOR_SINGLE.update(_s)


def _text_has_kaomoji(text: str) -> bool:
    """Return True if *text* contains a kaomoji or a Chinese action description.

    Two detection paths:
    1. Special-character kaomoji — (≧▽≦)/  (｡･ω･｡)  (◕‿◕)✧  etc.
    2. Chinese action descriptions in full-width parens — （笑）（心虚地挠挠脸）
    """
    if _KAOMOJI_SPECIAL_RE.search(text):
        return True
    for m in _ACTION_PAREN_RE.finditer(text):
        inner = m.group(1)
        if any(c in _ACTION_INDICATOR_SINGLE for c in inner):
            return True
    return False


class RateLimitError(RuntimeError):
    """Raised when the Anthropic API returns a rate-limit error."""


RATE_LIMIT_BASE_DELAY = 5.0  # seconds, doubles each retry
RATE_LIMIT_MAX_RETRIES = 2
PROFILE_RATE_LIMIT_MAX_COOLDOWN = 60.0


@dataclass
class ProfileRateLimitState:
    """Runtime rate-limit state for a single resolved LLM profile."""

    profile: str
    total_calls: int = 0
    successes: int = 0
    failures: int = 0
    rate_limited: int = 0
    blocked_calls: int = 0
    consecutive_rate_limits: int = 0
    last_task: str = ""
    last_error: str = ""
    last_success_at: float = 0.0
    last_limited_at: float = 0.0
    cooldown_until: float = 0.0
    cooldown_until_monotonic: float = 0.0
    provider_kind: str = ""
    provider_mode: str = ""
    last_model: str = ""
    last_api_format: str = ""
    last_cache_hit_pct: float | None = None
    last_cache_hit_pct_by_task: dict[str, float] = field(default_factory=dict)
    last_prompt_cache_hit_tokens: int = 0
    last_prompt_cache_miss_tokens: int = 0
    last_reasoning_replay_tokens: int = 0
    last_payload_sanitized: bool = False
    last_usage: dict[str, Any] | None = None

    def cooldown_remaining(self) -> float:
        return max(0.0, self.cooldown_until_monotonic - time.monotonic())

    def as_payload(self) -> dict[str, Any]:
        remaining = self.cooldown_remaining()
        return {
            "profile": self.profile,
            "status": "cooldown" if remaining > 0 else "ready",
            "cooldown_remaining_seconds": round(remaining, 2),
            "cooldown_until": self.cooldown_until if remaining > 0 else 0.0,
            "total_calls": self.total_calls,
            "successes": self.successes,
            "failures": self.failures,
            "rate_limited": self.rate_limited,
            "blocked_calls": self.blocked_calls,
            "consecutive_rate_limits": self.consecutive_rate_limits,
            "last_task": self.last_task,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at,
            "last_limited_at": self.last_limited_at,
            "provider_kind": self.provider_kind,
            "provider_mode": self.provider_mode,
            "last_model": self.last_model,
            "last_api_format": self.last_api_format,
            "last_cache_hit_pct": self.last_cache_hit_pct,
            "last_cache_hit_pct_by_task": dict(self.last_cache_hit_pct_by_task),
            "last_prompt_cache_hit_tokens": self.last_prompt_cache_hit_tokens,
            "last_prompt_cache_miss_tokens": self.last_prompt_cache_miss_tokens,
            "last_reasoning_replay_tokens": self.last_reasoning_replay_tokens,
            "last_payload_sanitized": self.last_payload_sanitized,
            "last_usage": self.last_usage or {},
        }


def _clean_text(text: str) -> str:
    """Collapse consecutive blank lines into a single newline."""
    return _BLANK_LINE_RE.sub("\n", text).strip()


def fix_cq_codes(text: str) -> str:
    """Normalize CQ code params: [CQ:reply,id:123] → [CQ:reply,id=123]."""
    return _CQ_CODE_RE.sub(lambda m: _CQ_KV_FIX_RE.sub(r",\1=", m.group(0)), text)


def _split_segments(text: str) -> list[str]:
    """Split reply into multiple messages by --- separator, cleaning blank lines."""
    text = fix_cq_codes(text)
    segments: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == _SEGMENT_SEP:
            seg = "\n".join(current).strip()
            if seg:
                segments.append(_clean_text(seg))
            current = []
        else:
            current.append(line)
    last = "\n".join(current).strip()
    if last:
        segments.append(_clean_text(last))
    return segments or [_clean_text(text)]


_SENTENCE_ENDING = set("。！？～…」』）\"!?~)")  # chars that terminate a thought
_SENTENCE_BREAK = set("。！？～…!?~")  # priority 1: sentence-ending breaks
_CLAUSE_BREAK = set("，；：、,;:")     # priority 2: clause-level breaks
_MIN_CHUNK = 6
_MAX_CHUNK = 20


def _smart_chunk(text: str, max_len: int = _MAX_CHUNK) -> list[str]:
    """Split text into segments ≤ max_len, preferring natural punctuation breaks.

    Scans backward from max_len to find the best split point:
    1. After sentence-ending punctuation (。！？～…) — delimiter stays with preceding text
    2. After clause punctuation (，；：、)
    3. At a character boundary that doesn't break an English word
    4. Hard split at max_len (last resort, should rarely fire)

    A trailing segment shorter than _MIN_CHUNK is merged into the previous one.
    """
    segments: list[str] = []
    t = text
    while t:
        if len(t) <= max_len:
            segments.append(t)
            break

        best = 0
        half = max_len // 2

        # Priority 1: sentence-ending break
        for i in range(max_len - 1, half - 1, -1):
            if t[i] in _SENTENCE_BREAK:
                best = i + 1
                break

        # Priority 2: clause break
        if best == 0:
            for i in range(max_len - 1, half - 1, -1):
                if t[i] in _CLAUSE_BREAK:
                    best = i + 1
                    break

        # Priority 3: character boundary (don't break English words)
        if best == 0:
            for i in range(max_len, half - 1, -1):
                if i >= len(t):
                    continue
                if i > 0 and t[i - 1].isalpha() and (i < len(t) and t[i].isalpha()):
                    continue
                best = i
                break

        # Priority 4: hard split
        if best == 0:
            best = max_len

        segments.append(t[:best])
        t = t[best:].lstrip()

    # Post-process: merge trailing short segment
    if len(segments) >= 2 and len(segments[-1]) < _MIN_CHUNK:
        segments[-2] += segments[-1]
        segments.pop()

    return segments or [text]


def _split_naturally(text: str) -> list[str]:
    """Split text for human-like sequential sending.

    Each newline is a split point (as told to the LLM).
    Short consecutive lines are merged to avoid fragmentation;
    long single lines are split on 。！？ then comma.
    Honors explicit ---cut--- markers.
    """
    if any(line.strip() == _SEGMENT_SEP for line in text.split("\n")):
        return _split_segments(text)

    # Step 1: paragraphs (double newline = topic shift, always split)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text]

    chunks: list[str] = []
    for para in paragraphs:
        # Step 2: split on \n but only as a real boundary when the previous
        # line ends with sentence-ending punctuation.  Otherwise the \n is
        # likely a mid-sentence line-wrap from the LLM — merge the two lines.
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue

        merged: list[str] = []
        for line in lines:
            if not merged:
                merged.append(line)
            elif merged[-1] and merged[-1][-1] in _SENTENCE_ENDING:
                # Previous line ended a thought → \n is intentional
                merged.append(line)
            elif len(line) < _MIN_CHUNK:
                # Short line → fragment, always merge
                merged[-1] += line
            else:
                # Previous line didn't end with sentence-ending punctuation
                # and this line isn't trivially short — \n is mid-sentence.
                # Merge without adding \n to preserve natural reading.
                merged[-1] += line

        # Step 3: split any merged line that's still too long
        for line in merged:
            if len(line) <= _MAX_CHUNK:
                chunks.append(line)
            else:
                chunks.extend(_smart_chunk(line))

    # Strip trailing clause punctuation — meaningless at end of a standalone message
    _TRAILING_CLAUSE = "，；：、,;:"
    chunks = [c.rstrip(_TRAILING_CLAUSE) for c in chunks]

    return chunks or [text]


def _coalesce_segments(segments: list[str], max_segments: int = _MAX_SEND_SEGMENTS) -> list[str]:
    """Cap visible send fragments by merging overflow into the last segment."""
    if len(segments) <= max_segments:
        return segments
    if max_segments <= 1:
        return ["\n".join(segments)]
    head = segments[: max_segments - 1]
    tail = "\n".join(segments[max_segments - 1:])
    return [*head, tail]


def _reply_segments(reply: str) -> tuple[list[str], int]:
    raw_segments = _split_naturally(reply)
    return _coalesce_segments(raw_segments), len(raw_segments)


def _cached_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def to_anthropic_message(msg: ChatMessage) -> dict[str, Any]:
    return {"role": msg["role"], "content": msg["content"]}


def content_text(content: Content) -> str:
    """Extract plain text from Content, ignoring image blocks."""
    if isinstance(content, str):
        return content
    return " ".join(b["text"] for b in content if b["type"] == "text")


def _pending_conversation_text(timeline: GroupTimeline | None, group_id: str | None) -> str:
    """Return pending human text only, so direct @ retrieval does not drift."""
    if timeline is None or group_id is None:
        return ""
    parts: list[str] = []
    with contextlib.suppress(Exception):
        for msg in timeline.get_pending(group_id):
            if msg.get("trigger_reason"):
                continue
            text = content_text(msg.get("content", ""))
            if text.strip():
                parts.append(text.strip())
    return " ".join(parts[-3:])


def _hash_scope_id(scope: str, raw_id: str, salt: str) -> str:
    digest = hashlib.sha256(f"{scope}:{salt}:{raw_id}".encode("utf-8")).hexdigest()[:16]
    return f"{scope}_{digest}"


def _render_tail_metadata(blocks: list[dict[str, Any]]) -> str:
    parts = [
        str(block.get("text", "") or "").strip()
        for block in blocks
        if isinstance(block, dict) and str(block.get("text", "") or "").strip()
    ]
    if not parts:
        return ""
    return "<turn_meta>\n" + "\n\n".join(parts) + "\n</turn_meta>"


def _append_tail_metadata(
    messages: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tail = _render_tail_metadata(blocks)
    if not tail:
        return messages

    for msg in reversed(messages):
        if not isinstance(msg, dict) or str(msg.get("role", "")) != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            msg["content"] = (content + "\n\n" + tail).strip()
            return messages
        if isinstance(content, list):
            msg["content"] = [*content, {"type": "text", "text": tail}]
            return messages

    messages.append({"role": "user", "content": tail})
    return messages


def _usage_observability_fields(result: dict[str, Any]) -> tuple[int, int, int]:
    cache_hit = int(result.get("prompt_cache_hit_tokens", result.get("cache_read", 0)) or 0)
    cache_miss = int(
        result.get(
            "prompt_cache_miss_tokens",
            max(
                0,
                int(result.get("input_tokens", 0) or 0)
                - int(result.get("cache_read", 0) or 0)
                - int(result.get("cache_create", 0) or 0),
            ),
        ) or 0
    )
    reasoning_replay = int(result.get("reasoning_replay_tokens", 0) or 0)
    return cache_hit, cache_miss, reasoning_replay


async def resolve_image_refs(
    messages: list[dict[str, Any]],
    image_cache: ImageCache | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Convert image_ref blocks to Anthropic image blocks (base64).

    Returns (messages, image_tag_map) where image_tag_map maps "img:N" to disk paths.
    """
    image_tag_map: dict[str, str] = {}
    if image_cache is None:
        return messages, image_tag_map

    t0 = time.perf_counter()
    resolved_count = 0
    tag_counter = 0

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        # Collect async resolve tasks with their indices
        tasks: list[tuple[int, dict[str, Any], asyncio.Task[dict[str, Any] | None]]] = []
        for i, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image_ref":
                task = asyncio.ensure_future(image_cache.load_as_base64(block))
                tasks.append((i, block, task))

        if not tasks:
            continue

        await asyncio.gather(*(t for _, _, t in tasks))

        new_content: list[dict[str, Any]] = []
        task_map = {i: (block, task) for i, block, task in tasks}
        for i, block in enumerate(content):
            if i not in task_map:
                new_content.append(block)
                continue
            orig_block, task = task_map[i]
            cache_ctrl = orig_block.get("cache_control")
            resolved = task.result()
            if resolved is not None:
                tag_counter += 1
                tag = f"img:{tag_counter}"
                image_tag_map[tag] = orig_block["path"]
                new_content.append({"type": "text", "text": f"«{tag}»"})
                if cache_ctrl:
                    resolved = {**resolved, "cache_control": cache_ctrl}
                new_content.append(resolved)
                resolved_count += 1
            else:
                fallback: dict[str, Any] = {"type": "text", "text": "«图片已过期»"}
                if cache_ctrl:
                    fallback["cache_control"] = cache_ctrl
                new_content.append(fallback)
        msg["content"] = new_content

    if resolved_count:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _log_debug.debug(
            "resolve_image_refs | images={} tags={} elapsed={:.0f}ms",
            resolved_count, tag_counter, elapsed_ms,
        )

    return messages, image_tag_map


def _hash_json(obj: Any) -> str:
    """Return first 8 hex chars of SHA-256 of JSON-serialized obj."""
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _log_cache_debug(
    session_id: str,
    system_blocks: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]],
    msg_count: int,
) -> None:
    tools_hash = _hash_json(tool_defs)
    block_hashes = [_hash_json(b) for b in system_blocks]
    _log_debug.debug(
        "cache_debug | session={} tools={} system=[{}] msgs={}",
        session_id, tools_hash, ", ".join(block_hashes), msg_count,
    )


def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "input_schema": t["function"]["parameters"],
        }
        for t in tools
    ]


_PASS_TURN_TOOL: dict[str, Any] = {
    "name": "pass_turn",
    "description": "当你认为不需要回复时调用此工具，跳过本轮发言。",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "不回复的简短原因",
            }
        },
        "required": ["reason"],
    },
}

_ADD_CARD_TOOL: dict[str, Any] = {
    "name": "add_card",
    "description": (
        "向记忆库添加一张新卡片。每张卡片有类别（preference/boundary/relationship/event/promise/fact/status）、"
        "作用域（user/group/global）和内容（一句话结论）。不要重复已有内容，只记新信息。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["user", "group", "global"],
                "description": "卡片作用域",
            },
            "scope_id": {
                "type": "string",
                "description": "作用域 ID：QQ号（user时）或群号（group时）",
            },
            "category": {
                "type": "string",
                "enum": ["preference", "boundary", "relationship", "event", "promise", "fact", "status"],
                "description": "卡片类别",
            },
            "content": {
                "type": "string",
                "description": "一句话结论，简洁准确",
            },
        },
        "required": ["scope", "scope_id", "category", "content"],
    },
}


async def call_api(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    model: str,
    system_blocks: list[dict[str, Any]],
    messages: list[Any],
    max_tokens: int = 1024,
    tools: list[dict[str, Any]] | None = None,
    thinking: dict[str, Any] | None = None,
    api_format: str = "anthropic",
    request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the configured provider API and parse its SSE stream."""
    provider = create_provider(api_format, base_url, api_key)
    body, headers, request_meta = provider.build_request(
        system_blocks=system_blocks,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        model=model,
        thinking=thinking,
        request_options=request_options,
    )
    t_ser = time.perf_counter()
    payload_bytes = json.dumps(body).encode()
    payload = io.BytesIO(payload_bytes)
    _log_debug.trace(
        "api payload serialized | size={}KB elapsed={:.1f}ms",
        len(payload_bytes) // 1024,
        (time.perf_counter() - t_ser) * 1000,
    )

    raw_lines: list[str] = []
    t_api = time.perf_counter()
    first_byte_ms: float | None = None
    response_headers_ms = 0.0
    async with session.post(provider.request_url(), data=payload, headers=headers) as resp:
        response_headers_ms = (time.perf_counter() - t_api) * 1000
        if resp.status == 429:
            body_text = await resp.text()
            raise RateLimitError(f"HTTP 429: {body_text}")
        if resp.status >= 400:
            body_text = await resp.text()
            if api_format == "deepseek" and resp.status >= 500:
                logger.warning(
                    "deepseek api unavailable | status={} headers_ms={:.0f} body={}",
                    resp.status, response_headers_ms, body_text[:240],
                )
            logger.error("API {} | body={}", resp.status, body_text[:500])
            resp.raise_for_status()
        async for raw_line in resp.content:
            if first_byte_ms is None:
                first_byte_ms = (time.perf_counter() - t_api) * 1000
            line = raw_line.decode().strip()
            if line:
                raw_lines.append(line)
    stream_ms = (time.perf_counter() - t_api) * 1000

    t_parse = time.perf_counter()
    try:
        result = provider.parse_sse_stream(raw_lines)
    except Exception as exc:
        if "rate limit" in str(exc).lower():
            raise RateLimitError(str(exc)) from exc
        raise
    parse_ms = (time.perf_counter() - t_parse) * 1000
    if isinstance(request_meta, dict):
        for key, value in request_meta.items():
            result.setdefault(key, value)
    timing = {
        "api_payload_kb": len(payload_bytes) / 1024,
        "api_response_headers_ms": response_headers_ms,
        "api_first_byte_ms": first_byte_ms if first_byte_ms is not None else response_headers_ms,
        "api_stream_ms": stream_ms,
        "api_parse_ms": parse_ms,
        "api_total_ms": stream_ms + parse_ms,
        "api_sse_lines": len(raw_lines),
    }
    result["timing"] = timing
    first_s = timing["api_first_byte_ms"] / 1000
    total_s = timing["api_total_ms"] / 1000
    if first_s >= _API_FIRST_BYTE_WARN_S or total_s >= _API_TOTAL_WARN_S:
        logger.warning(
            "llm api slow | provider={} model={} first_byte={:.1f}s stream={:.1f}s parse={:.0f}ms lines={}",
            api_format, model, first_s, stream_ms / 1000, parse_ms, len(raw_lines),
        )
    else:
        _log_debug.debug(
            "llm api timing | provider={} model={} first_byte={:.1f}s stream={:.1f}s parse={:.0f}ms lines={}",
            api_format, model, first_s, stream_ms / 1000, parse_ms, len(raw_lines),
        )
    return result


async def _build_debug_block(
    user_id: str,
    session_id: str,
    mood_engine: Any = None,
    affection_engine: Any = None,
    schedule_store: Any = None,
    memo_store: Any = None,
    card_store: Any = None,
    short_term: Any = None,
    message_log: Any = None,
) -> str:
    """Build a system block with live debug state for the admin /debug command."""
    from datetime import datetime, timedelta, timezone

    CST = timezone(timedelta(hours=8))
    today_str = datetime.now(CST).strftime("%Y-%m-%d")

    lines: list[str] = [
        "【调试模式 — 最高优先级指令】",
        "",
        "重要：你现在处于调试/维护模式。用户是管理员，请暂时搁置角色设定。",
        "用简洁、直接、事实性的方式回答。不要用颜文字，不要扮演高中生，不要闲聊。",
        "格式约束：QQ 不支持 Markdown。禁止使用 ``` 代码块、** 加粗、` 行内代码、- 列表等格式。使用纯文本。",
        "基于下面的实时运行状态数据如实回答用户的问题。",
        "",
        "--- 实时状态 ---",
        "",
    ]

    # Mood — read from engine cache
    if mood_engine is not None:
        try:
            cache = getattr(mood_engine, "_cache", None)
            if cache is not None:
                profile, _ts = cache
                lines.append(
                    f"心情: label={profile.label} energy={profile.energy:.2f} "
                    f"valence={profile.valence:+.2f} tension={profile.tension:.2f} "
                    f"openness={profile.openness:.2f}"
                )
                if profile.anomaly_reason:
                    lines.append(f"心情异常: {profile.anomaly_reason}")
            else:
                lines.append("心情: (未初始化)")
        except Exception as e:
            lines.append(f"心情: (读取失败: {e})")

    # Affection — read from store via engine
    if affection_engine is not None:
        try:
            store = getattr(affection_engine, "store", None)
            if store is not None:
                profile = store.get(user_id)
                if profile:
                    lines.append(
                        f"好感度: user={user_id} score={profile.score:.1f} "
                        f"level={profile.level} nickname={profile.nickname or '无'}"
                    )
                else:
                    lines.append(f"好感度: user={user_id} (无记录)")
            else:
                lines.append("好感度: (store 未初始化)")
        except Exception as e:
            lines.append(f"好感度: (读取失败: {e})")

    # Today's schedule
    if schedule_store is not None:
        try:
            sched = schedule_store.load(today_str)
            if sched:
                lines.append(f"今日日程: date={today_str} theme={sched.theme} slots={len(sched.slots)}")
                for slot in sched.slots[:8]:
                    loc = f" @{slot.location}" if slot.location else ""
                    lines.append(f"  {slot.time} {slot.activity} [{slot.mood_hint}]{loc}")
                if len(sched.slots) > 8:
                    lines.append(f"  ... 还有 {len(sched.slots) - 8} 个时间段")
            else:
                lines.append(f"今日日程: date={today_str} (无)")
        except Exception as e:
            lines.append(f"今日日程: (读取失败: {e})")

    # Memory — typed cards about this user
    if card_store is not None:
        try:
            cards = await card_store.get_entity_cards("user", user_id)
            if cards:
                lines.append(f"用户记忆卡片: {len(cards)} 张")
                for c in cards[:5]:
                    lines.append(f"  [{c.category}] {c.content[:120]}")
                if len(cards) > 5:
                    lines.append(f"  ... 还有 {len(cards) - 5} 张")
            else:
                lines.append("用户记忆卡片: (无)")
        except Exception as e:
            lines.append(f"用户记忆卡片: (读取失败: {e})")

    # Legacy memo_store (for backward compat during migration)
    if memo_store is not None:
        try:
            memos = memo_store.about(user_id)
            if memos:
                lines.append(f"旧版用户记忆: {len(memos)} 条")
        except Exception:
            pass

    # Session — enumerate messages from memory + persisted log (survives restart)
    msgs: list[Any] = []
    if short_term is not None:
        with contextlib.suppress(Exception):
            msgs = list(short_term.get(session_id))

    # If memory is empty (after restart), try the persisted message log
    if not msgs and message_log is not None:
        try:
            rows = await message_log.query_recent(f"session:{session_id}", limit=50)
            msgs = [
                type('_Msg', (), {'role': r['role'], 'content': r['content_text']})()
                for r in rows
            ]
        except Exception:
            pass

    lines.append(f"会话消息数: {len(msgs)} (内存+持久化)")
    if msgs:
        lines.append("--- 会话消息列表 ---")
        for i, m in enumerate(msgs[-20:]):  # last 20 messages
            # Handle ChatMessage objects, namedtuples, and dicts
            if hasattr(m, 'role'):
                role = m.role
                content = m.content
            elif isinstance(m, dict):
                role = m.get('role', '?')
                content = m.get('content', m.get('content_text', ''))
            else:
                continue
            if isinstance(content, list):
                parts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(str(b.get("text", "")))
                content = "".join(parts)
            if isinstance(content, str):
                preview = content[:300] + ("…" if len(content) > 300 else "")
            else:
                preview = str(content)[:300]
            lines.append(f"[{i+1}] {role}: {preview}")
    else:
        lines.append("(会话为空 — 可能是重启后尚未有消息)")

    lines.append("")
    lines.append(
        "--- 指令重申 ---\n"
        "以上是当前的运行状态数据。你是调试助手，不是聊天机器人。\n"
        "请基于数据直接、准确地回答管理员的问题。\n"
        "不要编造数据。如果某项数据显示为'无'或'读取失败'，直接说明。"
    )
    return "\n".join(lines)


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        prompt_builder: PromptBuilder,
        short_term: ShortTermMemory,
        tools: ToolRegistry,
        api_format: str = "anthropic",
        max_context_tokens: int = 200_000,
        compact_ratio: float = 0.7,
        compress_ratio: float = 0.5,
        max_compact_failures: int = 3,
        group_timeline: GroupTimeline | None = None,
        message_log: MessageLog | None = None,
        card_store: CardStore | None = None,
        bot_self_id: str = "",
        on_compact: Callable[[], None] | None = None,
        image_cache: ImageCache | None = None,
        affection_engine: object | None = None,
        thinker_enabled: bool = True,
        thinker_max_tokens: int = 256,
        mood_getter: Callable[[], Any] | None = None,
        bus: object | None = None,
        task_profiles: dict[str, Any] | None = None,
        group_config: Any | None = None,
    ) -> None:
        connector = aiohttp.TCPConnector(
            enable_cleanup_closed=True,
            keepalive_timeout=15,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=120, sock_read=30),
        )
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._api_format = api_format
        self._prompt = prompt_builder
        self._short_term = short_term
        self._tools = tools
        self._max_context_tokens = max_context_tokens
        self._compact_ratio = compact_ratio
        self._compress_ratio = compress_ratio
        self._max_compact_failures = max_compact_failures
        self._private_compact_failures: int = 0
        self._group_compact_failures: int = 0
        self._timeline = group_timeline
        self._message_log = message_log
        self._card_store = card_store
        self._bot_self_id = bot_self_id
        self._on_compact = on_compact
        self._usage_tracker: UsageTracker | None = None
        self._image_cache = image_cache
        self._affection_engine = affection_engine
        self._thinker_enabled = thinker_enabled
        self._thinker_max_tokens = thinker_max_tokens
        self._mood_getter = mood_getter
        self._bus = bus
        self._group_config = group_config
        self._task_profiles = task_profiles or {}
        self._task_profile_names = {
            task: str(getattr(profile, "name", "") or task)
            for task, profile in self._task_profiles.items()
        }
        self._profile_rate_limits: dict[str, ProfileRateLimitState] = {}
        # Cache-diagnostic ring buffer: per-task list of recent (snapshot, diff) pairs.
        # Ring depth tuned at 200 per task to bound memory while still letting admin
        # show last few breaks. Diffs are computed lazily against the previous entry
        # of the same task on every successful LLMRequest dispatch.
        self._cache_diag_ring_size: int = 200
        self._cache_diag_history: dict[str, list[tuple[CacheDiagnostic, CacheDiagnosticDiff | None]]] = {}

    async def close(self) -> None:
        await self._session.close()

    def set_group_config(self, group_config: Any | None) -> None:
        self._group_config = group_config

    async def _call(
        self,
        system_blocks_or_request: list[dict[str, Any]] | LLMRequest,
        messages: list[Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        thinking: dict[str, Any] | None = None,
        task: str = "main",
        user_id: str = "",
        group_id: str | None = None,
    ) -> dict[str, Any]:
        # Spine path: caller passed LLMRequest. Unwrap into the same kwargs the
        # legacy 6-positional signature uses, then dispatch through the same
        # body so the two paths share rate-limit + provider plumbing.
        if isinstance(system_blocks_or_request, LLMRequest):
            req = system_blocks_or_request
            system_blocks_arg, messages_arg, tools_arg = req.to_provider_payload()
            return await self._dispatch_call(
                system_blocks=system_blocks_arg,
                messages=messages_arg,
                tools=tools_arg,
                max_tokens=req.max_tokens,
                thinking=req.thinking,
                task=str(req.task),
                user_id=req.user_id,
                group_id=req.group_id,
                request=req,
            )
        if messages is None:
            raise TypeError("_call() missing required argument: 'messages'")
        return await self._dispatch_call(
            system_blocks=system_blocks_or_request,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            thinking=thinking,
            task=task,
            user_id=user_id,
            group_id=group_id,
            request=None,
        )

    def _enforce_capabilities(
        self,
        profile_name: str,
        requires: tuple[str, ...] | None,
    ) -> None:
        """Fail-fast if the resolved profile is missing any required capability.

        Profiles that omit ``capabilities`` fall back to ``["chat"]`` so that
        legacy single-profile setups keep working; only when ``requires``
        explicitly demands more (e.g. ``("vision",)``) do we enforce.
        """
        if not requires:
            return
        profile = self._task_profiles.get(profile_name) or self._task_profiles.get("main")
        if profile is None:
            available = ("chat",)
        else:
            raw = getattr(profile, "capabilities", None) or ["chat"]
            available = tuple(str(cap) for cap in raw)
        missing = [cap for cap in requires if str(cap) not in available]
        if missing:
            raise ValueError(
                f"profile {profile_name!r} missing capability {missing!r} "
                f"(available={list(available)})"
            )

    def _record_cache_diagnostic(
        self,
        *,
        task: str,
        profile: str,
        system_blocks: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        messages: list[Any],
    ) -> CacheDiagnosticDiff | None:
        """Compute per-axis snapshot + diff against the previous same-task call.

        Returns the diff (or ``None`` if this is the first snapshot for the
        task). The snapshot is appended to a per-task ring buffer bounded by
        ``self._cache_diag_ring_size``.
        """
        snapshot = compute_cache_diagnostic(
            task=task,
            profile=profile,
            system_blocks=system_blocks,
            tools=tools,
            messages=messages,
        )
        history = self._cache_diag_history.setdefault(task, [])
        diff: CacheDiagnosticDiff | None = None
        if history:
            prev_snapshot = history[-1][0]
            try:
                diff = diff_cache_diagnostics(prev_snapshot, snapshot)
            except ValueError:  # pragma: no cover — same-task guarantee broken
                diff = None
        history.append((snapshot, diff))
        if len(history) > self._cache_diag_ring_size:
            del history[: len(history) - self._cache_diag_ring_size]
        return diff

    def cache_diagnostic_history(
        self,
        task: str,
        *,
        limit: int = 20,
    ) -> list[tuple[CacheDiagnostic, CacheDiagnosticDiff | None]]:
        """Return up to ``limit`` most recent diagnostic entries for ``task``."""
        history = self._cache_diag_history.get(task, [])
        if limit <= 0:
            return list(history)
        return list(history[-limit:])

    async def _dispatch_call(
        self,
        *,
        system_blocks: list[dict[str, Any]],
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        thinking: dict[str, Any] | None,
        task: str,
        user_id: str,
        group_id: str | None,
        request: LLMRequest | None,
    ) -> dict[str, Any]:
        profile_name, base_url, api_key, model, api_format = self._profile_for_task(task)
        if request is not None:
            self._enforce_capabilities(profile_name, request.requires_capabilities)
        state = self._profile_rate_state(profile_name)
        self._raise_if_profile_cooling_down(state, task=task)
        state.total_calls += 1
        state.last_task = task
        provider_request_options = self._provider_request_options(
            task=task,
            api_format=api_format,
            model=model,
            user_id=user_id,
            group_id=group_id,
        )
        if api_format == "deepseek" and task in {"thinker", "compact", "slang", "reply_gate"} and thinking is None:
            thinking = {"type": "disabled"}
        try:
            t_call = time.monotonic()
            result = await call_api(
                self._session, base_url, api_key, model,
                system_blocks, messages, max_tokens=max_tokens, tools=tools,
                thinking=thinking, api_format=api_format,
                request_options=provider_request_options,
            )
            elapsed = time.monotonic() - t_call
            result["call_elapsed_s"] = elapsed
        except RateLimitError as exc:
            self._record_profile_rate_limited(state, task=task, error=str(exc))
            raise
        except Exception as exc:
            state.failures += 1
            state.last_error = str(exc)[:240]
            raise
        result.setdefault("provider_kind", "deepseek" if api_format == "deepseek" else api_format)
        result.setdefault("provider_mode", provider_mode(api_format, base_url))
        result["provider_model"] = model
        result["provider_api_format"] = api_format
        self._record_profile_success(
            state,
            task=task,
            result=result,
            model=model,
            api_format=api_format,
            base_url=base_url,
        )
        if request is not None:
            # Spine path: the unified contract carries a task label, so we own
            # cache diagnostic recording here regardless of who writes the usage
            # row. ``auto_record_usage=False`` lets aggregating callers (e.g.
            # ``_compact_with_tools`` and the main ``chat()`` tool loop) keep
            # the legacy "1 session = 1 usage row" contract — they accumulate
            # token counts across rounds and call ``_record_usage`` themselves.
            try:
                self._record_cache_diagnostic(
                    task=task,
                    profile=profile_name,
                    system_blocks=system_blocks,
                    tools=tools,
                    messages=messages,
                )
            except Exception:  # pragma: no cover — defensive, never block the call
                logger.bind(channel="usage").warning("cache diagnostic record failed")
            if request.auto_record_usage:
                self._record_usage(
                    call_type=task,
                    user_id=user_id,
                    group_id=group_id,
                    model=model,
                    provider_kind=str(result.get("provider_kind", "") or api_format),
                    input_tokens=int(result.get("input_tokens", 0) or 0),
                    cache_read_tokens=int(result.get("cache_read", 0) or 0),
                    cache_create_tokens=int(result.get("cache_create", 0) or 0),
                    output_tokens=int(result.get("output_tokens", 0) or 0),
                    prompt_cache_hit_tokens=int(result.get("prompt_cache_hit_tokens", 0) or 0),
                    prompt_cache_miss_tokens=int(result.get("prompt_cache_miss_tokens", 0) or 0),
                    reasoning_replay_tokens=int(result.get("reasoning_replay_tokens", 0) or 0),
                    tool_rounds=0,
                    elapsed_s=float(result.get("call_elapsed_s", 0.0) or 0.0),
                    error=None,
                )
        return result

    def _profile_for_task(self, task: str) -> tuple[str, str, str, str, str]:
        profile = self._task_profiles.get(task) or self._task_profiles.get("main")
        if profile is None:
            return "main", self._base_url, self._api_key, self._model, self._api_format
        profile_name = self._task_profile_names.get(task) or ("main" if task == "main" else task)
        return (
            profile_name,
            str(getattr(profile, "base_url", "") or self._base_url),
            str(getattr(profile, "api_key", "") or self._api_key),
            str(getattr(profile, "model", "") or self._model),
            str(getattr(profile, "api_format", "") or self._api_format),
        )

    def set_task_profile_names(self, task_profile_names: dict[str, str]) -> None:
        """Attach resolved task → profile names for diagnostics and rate limiting."""
        self._task_profile_names = {
            str(task or "main"): str(profile_name or "main")
            for task, profile_name in task_profile_names.items()
        }

    def set_task_profiles(
        self,
        task_profiles: dict[str, Any],
        task_profile_names: dict[str, str] | None = None,
    ) -> None:
        """Hot-swap resolved task profiles without rebuilding the client session."""
        self._task_profiles = dict(task_profiles or {})
        if task_profile_names is None:
            self._task_profile_names = {
                str(task or "main"): str(getattr(profile, "name", "") or task or "main")
                for task, profile in self._task_profiles.items()
            }
        else:
            self.set_task_profile_names(task_profile_names)

        main_profile = self._task_profiles.get("main")
        if main_profile is not None:
            self._base_url = str(getattr(main_profile, "base_url", "") or self._base_url)
            self._api_key = str(getattr(main_profile, "api_key", "") or self._api_key)
            self._model = str(getattr(main_profile, "model", "") or self._model)
            self._api_format = str(getattr(main_profile, "api_format", "") or self._api_format)

    def _resolve_group_profile(self, group_id: str | None) -> Any | None:
        if not group_id or self._group_config is None:
            return None
        with contextlib.suppress(Exception):
            return self._group_config.resolve(int(group_id))
        return None

    def _build_group_profile_block(self, group_profile: Any | None) -> dict[str, Any] | None:
        if group_profile is None:
            return None

        lines: list[str] = []
        reply_style = str(getattr(group_profile, "reply_style", "default") or "default")
        style_hint = _GROUP_REPLY_STYLE_HINTS.get(reply_style)
        if style_hint:
            lines.append(style_hint)

        custom_prompt = str(getattr(group_profile, "custom_prompt", "") or "").strip()
        if custom_prompt:
            lines.append(f"【本群附加要求】\n{custom_prompt}")

        if not lines:
            return None
        return {
            "type": "text",
            "text": "【群聊回复偏好】\n" + "\n".join(lines),
            "cache_control": {"type": "ephemeral"},
        }

    def _deepseek_hash_salt(self) -> str:
        return (
            self._api_key
            or self._bot_self_id
            or "omubot-deepseek"
        )

    def _provider_request_options(
        self,
        *,
        task: str,
        api_format: str,
        model: str,
        user_id: str = "",
        group_id: str | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if api_format != "deepseek":
            return options

        salt = self._deepseek_hash_salt()
        if group_id:
            options["user_id"] = _hash_scope_id("grp", group_id, salt)
        elif user_id:
            options["user_id"] = _hash_scope_id("dm", user_id, salt)
        else:
            options["user_id"] = f"sys_{task or 'main'}"

        if is_deepseek_v4_model(model) and task == "main":
            profile = self._task_profiles.get(task) or self._task_profiles.get("main")
            configured_effort = str(getattr(profile, "reasoning_effort", "") or "").strip().lower()
            if configured_effort in {"default", "auto", "none", "disabled"}:
                configured_effort = ""
            elif configured_effort and configured_effort not in {"low", "medium", "high"}:
                _log_debug.warning(
                    "invalid deepseek reasoning_effort={!r}; falling back to medium",
                    configured_effort,
                )
                configured_effort = ""
            options["reasoning_effort"] = configured_effort or "medium"
        return options

    def _compact_ratio_for_main(self) -> float:
        _, _, _, model, api_format = self._profile_for_task("main")
        if api_format == "deepseek" and is_deepseek_v4_model(model):
            return _DEEPSEEK_V4_COMPACT_RATIO
        return self._compact_ratio

    def _build_tool_defs(self, group_profile: Any | None) -> list[dict[str, Any]]:
        openai_tools = self._tools.to_openai_tools() if not self._tools.empty else []
        if group_profile is not None:
            if not bool(getattr(group_profile, "tools_enabled", True)):
                openai_tools = []
            else:
                allowed = {
                    str(name).strip()
                    for name in (getattr(group_profile, "allowed_tools", []) or [])
                    if str(name).strip()
                }
                if allowed:
                    openai_tools = [
                        tool for tool in openai_tools
                        if str(tool.get("function", {}).get("name", "")) in allowed
                    ]
                blocked: set[str] = set()
                blocked.update({
                    str(name).strip()
                    for name in (getattr(group_profile, "blocked_tools", []) or [])
                    if str(name).strip()
                })
                if str(getattr(group_profile, "sticker_mode", "inherit") or "inherit") == "off":
                    blocked.update(_STICKER_TOOL_NAMES)
                if not bool(getattr(group_profile, "slang_enabled", True)):
                    blocked.add("slang_lookup")
                if blocked:
                    openai_tools = [
                        tool for tool in openai_tools
                        if str(tool.get("function", {}).get("name", "")) not in blocked
                    ]

        tool_defs = _to_anthropic_tools(openai_tools) if openai_tools else []
        return [*tool_defs, _PASS_TURN_TOOL]

    def provider_rate_limit_payload(self) -> dict[str, Any]:
        names = set(self._profile_rate_limits.keys()) | set(self._task_profile_names.values()) | {"main"}
        profiles = {
            name: self._profile_rate_state(name).as_payload()
            for name in sorted(names)
        }
        return {
            "profiles": profiles,
            "tasks": {
                task: self._profile_rate_state(profile_name).as_payload()
                for task, profile_name in sorted(self._task_profile_names.items())
            },
        }

    def _profile_rate_state(self, profile_name: str) -> ProfileRateLimitState:
        key = str(profile_name or "main")
        if key not in self._profile_rate_limits:
            self._profile_rate_limits[key] = ProfileRateLimitState(profile=key)
        return self._profile_rate_limits[key]

    def _raise_if_profile_cooling_down(self, state: ProfileRateLimitState, *, task: str) -> None:
        remaining = state.cooldown_remaining()
        if remaining <= 0:
            return
        state.blocked_calls += 1
        state.last_task = task
        raise RateLimitError(
            f"profile {state.profile} cooling down for {remaining:.1f}s after rate limit"
        )

    def _record_profile_rate_limited(
        self,
        state: ProfileRateLimitState,
        *,
        task: str,
        error: str,
    ) -> None:
        now = time.time()
        state.rate_limited += 1
        state.failures += 1
        state.consecutive_rate_limits += 1
        state.last_task = task
        state.last_error = str(error or "")[:240]
        state.last_limited_at = now
        delay = min(
            PROFILE_RATE_LIMIT_MAX_COOLDOWN,
            RATE_LIMIT_BASE_DELAY * (2 ** max(0, state.consecutive_rate_limits - 1)),
        )
        state.cooldown_until = now + delay
        state.cooldown_until_monotonic = time.monotonic() + delay
        _log_debug.warning(
            "provider rate limited | profile={} task={} cooldown={:.0f}s count={}",
            state.profile, task, delay, state.consecutive_rate_limits,
        )

    def _record_profile_success(
        self,
        state: ProfileRateLimitState,
        *,
        task: str,
        result: dict[str, Any],
        model: str,
        api_format: str,
        base_url: str,
    ) -> None:
        state.successes += 1
        state.consecutive_rate_limits = 0
        state.last_task = task
        state.last_success_at = time.time()
        state.provider_kind = str(result.get("provider_kind", "") or api_format)
        state.provider_mode = str(result.get("provider_mode", "") or provider_mode(api_format, base_url))
        state.last_model = model
        state.last_api_format = api_format
        hit_tokens = int(result.get("prompt_cache_hit_tokens", result.get("cache_read", 0)) or 0)
        miss_tokens = int(result.get("prompt_cache_miss_tokens", 0) or 0)
        if miss_tokens == 0:
            total_input = int(result.get("input_tokens", 0) or 0)
            cache_create = int(result.get("cache_create", 0) or 0)
            miss_tokens = max(0, total_input - hit_tokens - cache_create)
        total_prompt = hit_tokens + miss_tokens
        pct = (hit_tokens / total_prompt * 100) if total_prompt > 0 else None
        state.last_cache_hit_pct = pct
        if pct is not None and task:
            state.last_cache_hit_pct_by_task[task] = pct
        state.last_prompt_cache_hit_tokens = hit_tokens
        state.last_prompt_cache_miss_tokens = miss_tokens
        state.last_reasoning_replay_tokens = int(result.get("reasoning_replay_tokens", 0) or 0)
        state.last_payload_sanitized = bool(result.get("payload_sanitized", False))
        usage = result.get("usage")
        state.last_usage = usage if isinstance(usage, dict) else {}
        if state.cooldown_remaining() <= 0:
            state.cooldown_until = 0.0
            state.cooldown_until_monotonic = 0.0

    def _record_usage(
        self,
        *,
        call_type: str,
        user_id: str,
        group_id: str | None,
        model: str | None = None,
        provider_kind: str = "",
        input_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        output_tokens: int,
        prompt_cache_hit_tokens: int = 0,
        prompt_cache_miss_tokens: int = 0,
        reasoning_replay_tokens: int = 0,
        tool_rounds: int,
        elapsed_s: float,
        error: str | None = None,
    ) -> None:
        """Fire-and-forget usage recording."""
        if not self._usage_tracker:
            return
        asyncio.create_task(self._usage_tracker.record(  # noqa: RUF006
            call_type=call_type,
            user_id=user_id or None,
            group_id=group_id,
            model=model or self._model,
            provider_kind=provider_kind,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            output_tokens=output_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
            reasoning_replay_tokens=reasoning_replay_tokens,
            tool_rounds=tool_rounds,
            elapsed_s=elapsed_s,
            error=error,
        ))

    async def _fire_thinker_decision(
        self,
        *,
        session_id: str,
        group_id: str | None,
        user_id: str,
        action: str,
        thought: str,
        elapsed_ms: float,
    ) -> None:
        if self._bus is None:
            return
        await self._bus.fire_on_thinker_decision(
            ThinkerContext(
                session_id=session_id,
                group_id=group_id,
                user_id=user_id,
                action=action,
                thought=thought,
                elapsed_ms=elapsed_ms,
            )
        )

    async def _fire_post_reply(
        self,
        *,
        session_id: str,
        group_id: str | None,
        user_id: str,
        user_content: Content,
        reply_content: str,
        elapsed_ms: float,
        thinker_action: str,
        thinker_thought: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        if self._bus is None or not reply_content.strip():
            return
        await self._bus.fire_on_post_reply(
            ReplyContext(
                session_id=session_id,
                group_id=group_id,
                user_id=user_id,
                user_msg=content_text(user_content),
                reply_content=reply_content,
                tool_calls=[dict(item) for item in tool_calls],
                elapsed_ms=elapsed_ms,
                thinker_action=thinker_action,
                thinker_thought=thinker_thought,
            )
        )

    def _finalize_visible_reply(
        self,
        *,
        reply: str,
        session_id: str,
        force_reply: bool,
        has_visible_tool_output: bool,
        is_group: bool,
    ) -> tuple[str, str]:
        raw_reply = reply or ""
        control_only_reply = _contains_control_token(raw_reply)
        cleaned = _clean_reply(reply or "...")
        cleaned, stripped = _strip_control_tokens(cleaned)
        if stripped:
            _log_msg_out.info("reply_control_token_stripped | session={}", session_id)
            if control_only_reply:
                cleaned = ""

        normalized = cleaned.strip()
        if normalized in ("", "...", "☆", "~"):
            if has_visible_tool_output:
                _log_msg_out.info("reply_suppressed_empty | session={} reason=tool_visible", session_id)
                return "", "suppressed"
            if force_reply or not is_group:
                _log_msg_out.info("reply_fallback_emitted | session={}", session_id)
                return _EMPTY_VISIBLE_REPLY_FALLBACK, "fallback"
            _log_msg_out.info("reply_suppressed_empty | session={} reason=autonomous", session_id)
            return "", "suppressed"
        return normalized, "reply"

    def _has_visible_tool_output(self, tool_calls: list[dict[str, Any]]) -> bool:
        for call in tool_calls:
            if call.get("is_error"):
                continue
            if str(call.get("name", "")) in _VISIBLE_TOOL_OUTPUT_NAMES:
                return True
        return False

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    def _build_group_messages(self, group_id: str) -> list[dict[str, Any]]:
        """Build message list for group chat: optional summary + turns + pending + cache breakpoint."""
        assert self._timeline is not None
        messages: list[dict[str, Any]] = []

        # Summary as stable prefix for cache hits
        summary = self._timeline.get_summary(group_id)
        if summary:
            messages.append({
                "role": "user",
                "content": [_cached_text(f"«对话摘要»\n{summary}")],
            })
            messages.append({"role": "assistant", "content": "好的，我已了解之前的对话内容。"})

        # Turns — finalized, byte-identical to previous API calls
        messages.extend(self._timeline.get_turns(group_id))

        # Pending — temporary merge as tail user message
        pending = self._timeline.get_pending(group_id)
        if pending:
            from services.memory.timeline import merge_user_contents
            messages.append({"role": "user", "content": merge_user_contents(pending)})

        # Place cache breakpoint at the position recorded by the previous API call
        cached_idx = self._timeline.get_cached_msg_index(group_id)
        if 0 < cached_idx < len(messages):
            target = messages[cached_idx]
            content = target.get("content")
            if isinstance(content, str):
                messages[cached_idx] = {"role": target["role"], "content": [_cached_text(content)]}
            elif isinstance(content, list):
                content = [*content]
                content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
                messages[cached_idx] = {"role": target["role"], "content": content}

        # Store second-to-last for next call (last may grow with new pending)
        if len(messages) >= 2:
            self._timeline.set_cached_msg_index(group_id, len(messages) - 2)

        return messages

    def _build_private_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Build message list for private chat: optional summary + history + cache breakpoint."""
        messages: list[dict[str, Any]] = []

        # Summary as stable prefix
        summary = self._short_term.get_summary(session_id)
        if summary:
            messages.append({
                "role": "user",
                "content": [_cached_text(f"«对话摘要»\n{summary}")],
            })
            messages.append({"role": "assistant", "content": "好的，我已了解之前的对话内容。"})

        # Chat history
        history = self._short_term.get(session_id)
        for i, msg in enumerate(history):
            m = to_anthropic_message(msg)
            if i == len(history) - 2:
                content = m["content"]
                if isinstance(content, str):
                    m = {"role": m["role"], "content": [_cached_text(content)]}
                elif isinstance(content, list):
                    content = [*content]
                    content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
                    m = {"role": m["role"], "content": content}
            messages.append(m)

        return messages

    # ------------------------------------------------------------------
    # Thinker helpers
    # ------------------------------------------------------------------

    def _build_thinker_mood_text(self) -> str:
        """Build a one-line mood summary for the pre-reply thinker."""
        if self._mood_getter is None:
            return ""
        try:
            profile = self._mood_getter()
            if profile is None:
                return ""
            mood_line = (
                f"【当前心情】{profile.label} "
                f"(energy={profile.energy:.2f} valence={profile.valence:+.2f} "
                f"openness={profile.openness:.2f} tension={profile.tension:.2f})"
            )
            if getattr(profile, "anomaly_reason", ""):
                mood_line += f"\n（心情说明：{profile.anomaly_reason}）"
            return mood_line
        except Exception:
            return ""

    def _build_thinker_affection_text(self, user_id: str) -> str:
        """Build a one-line relationship summary for the pre-reply thinker."""
        if self._affection_engine is None:
            return ""
        try:
            engine = self._affection_engine
            store = getattr(engine, "_store", None)
            if store is None:
                return ""
            profile = store.get(user_id)
            if profile is None or profile.total_interactions == 0:
                return ""
            nickname = profile.custom_nickname or profile.group_nickname or ""
            tag = f"（称呼：{nickname}）" if nickname else ""
            return (
                f"【与当前用户的关系】tier={profile.tier} "
                f"score={profile.score:.0f} interactions={profile.total_interactions} {tag}".strip()
            )
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Main chat entry point
    # ------------------------------------------------------------------

    async def chat(
        self,
        session_id: str,
        user_id: str,
        user_content: Content,
        identity: Identity,
        group_id: str | None = None,
        ctx: ToolContext | None = None,
        on_segment: Callable[[str], Awaitable[None]] | None = None,
        force_reply: bool = False,
        *,
        privacy_mask: bool = True,
    ) -> str | None:
        content_preview = user_content[:80] if isinstance(user_content, str) else str(user_content)[:80]
        _log_msg_in.info(
            "chat | session={} user={} identity={} text={!r}",
            session_id, user_id, identity.id, content_preview,
        )
        t0 = time.monotonic()
        prompt_start = time.perf_counter()

        is_group = group_id is not None and self._timeline is not None
        _, _, _, main_model, main_api_format = self._profile_for_task("main")
        deepseek_native_main = main_api_format == "deepseek" and is_deepseek_v4_model(main_model)
        compact_ratio = self._compact_ratio_for_main()

        if is_group:
            assert group_id is not None
            assert self._timeline is not None
            # Group: messages already added to timeline by group_listener
            if self._timeline.needs_compact(group_id, self._max_context_tokens, compact_ratio):
                _log_compact.info(
                    "compact triggering | group={} input_tokens={} threshold={}",
                    group_id, self._timeline.get_input_tokens(group_id),
                    int(self._max_context_tokens * compact_ratio),
                )
                await self._compact_group(group_id, identity)
            messages = self._build_group_messages(group_id)
            # Append user_content as a transient user message so directives
            # like "respond to this video" reach the LLM in group context.
            if user_content:
                messages.append({"role": "user", "content": user_content})
        else:
            # Private: use ShortTermMemory
            self._short_term.add(session_id, "user", user_content)
            # Persist to SQLite so /debug works after restart
            if self._message_log is not None:
                text_preview = (
                    user_content[:2000]
                    if isinstance(user_content, str)
                    else str(user_content)[:2000]
                )
                await self._message_log.record_session_msg(session_id, "user", text_preview)
            if self._short_term.needs_compact(session_id, self._max_context_tokens, compact_ratio):
                _log_compact.info(
                    "compact triggering | session={} input_tokens={} threshold={}",
                    session_id, self._short_term.get_input_tokens(session_id),
                    int(self._max_context_tokens * compact_ratio),
                )
                await self._compact(session_id)
            messages = self._build_private_messages(session_id)

        # Extract conversation text for retrieval gating
        if is_group and self._timeline is not None:
            pending_text = _pending_conversation_text(self._timeline, group_id)
            recent_text = self._timeline.get_recent_text(group_id, last_n=3)
            if force_reply and pending_text:
                conversation_text = pending_text
            else:
                conversation_text = " ".join(part for part in (recent_text, pending_text) if part)
        else:
            conversation_text = content_text(user_content) if user_content else ""

        # ------------------------------------------------------------------
        # Pre-reply thinker: decide whether to speak before building full prompt
        # ------------------------------------------------------------------
        thinker_decision: object | None = None
        thinker_action = ""
        if self._thinker_enabled and not force_reply:
            from services.llm.thinker import think

            # Filter to text-only: image_ref blocks are not valid for the API
            # and the thinker doesn't need images for its text-based decision.
            def _text_only(msg: dict[str, Any]) -> dict[str, Any]:
                content = msg.get("content", [])
                if isinstance(content, list):
                    filtered = [b for b in content if b.get("type") == "text"]
                    if filtered:
                        return {**msg, "content": filtered}
                return msg

            recent_raw = messages[-8:] if len(messages) > 8 else messages
            recent_for_thinker = [_text_only(m) for m in recent_raw]
            recent_for_thinker = [m for m in recent_for_thinker if m.get("content")]
            mood_text = self._build_thinker_mood_text()
            affection_text = self._build_thinker_affection_text(user_id)
            thinker_decision = await think(
                api_call=lambda req: self._call(req),
                recent_messages=recent_for_thinker,
                max_tokens=self._thinker_max_tokens,
                mood_text=mood_text,
                affection_text=affection_text,
                identity_name=identity.name,
                user_id=user_id,
                group_id=group_id,
            )
            # Persist decision in prompt context so plugins can see it
            thinker_action = thinker_decision.action
            thinker_thought = thinker_decision.thought

            if thinker_action == "search":
                _log_thinking.info("thinker_search_coerced | session={} thought={!r}", session_id, thinker_thought)
                thinker_action = "reply"
                thinker_decision.action = "reply"

            await self._fire_thinker_decision(
                session_id=session_id,
                group_id=group_id,
                user_id=user_id,
                action=thinker_action,
                thought=thinker_thought,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

            if thinker_action == "wait":
                elapsed = time.monotonic() - t0
                _log_msg_out.info(
                    "thinker_wait | session={} thought={!r} elapsed={:.1f}s",
                    session_id, thinker_thought, elapsed,
                )
                self._record_usage(
                    call_type="proactive",
                    user_id=user_id, group_id=group_id,
                    model=self._profile_for_task("thinker")[3],
                    provider_kind=self._profile_for_task("thinker")[4],
                    input_tokens=thinker_decision.usage.get("input_tokens", 0),
                    cache_read_tokens=thinker_decision.usage.get("cache_read", 0),
                    cache_create_tokens=thinker_decision.usage.get("cache_create", 0),
                    output_tokens=thinker_decision.usage.get("output_tokens", 0),
                    tool_rounds=0, elapsed_s=elapsed,
                )
                return None
        else:
            thinker_thought = ""

        group_profile = self._resolve_group_profile(group_id)
        prompt_build_start = time.perf_counter()
        if self._card_store:
            try:
                # Collect plugin blocks via bus.on_pre_prompt
                plugin_static: list[dict[str, Any]] = []
                plugin_stable: list[dict[str, Any]] = []
                plugin_dynamic: list[dict[str, Any]] = []
                tail_blocks: list[dict[str, Any]] = []
                group_profile_block = self._build_group_profile_block(group_profile)
                if group_profile_block is not None:
                    plugin_stable.append(group_profile_block)
                if self._bus is not None:
                    from kernel.types import PromptContext
                    prompt_ctx = PromptContext(
                        session_id=session_id,
                        group_id=group_id,
                        user_id=user_id,
                        identity=identity,
                        conversation_text=conversation_text,
                        force_reply=force_reply,
                        privacy_mask=privacy_mask,
                    )
                    await self._bus.fire_on_pre_prompt(prompt_ctx)
                    for pb in prompt_ctx.blocks:
                        block_dict: dict[str, Any] = {
                            "type": "text",
                            "text": f"【{pb.label}】\n{pb.text}" if pb.label else pb.text,
                        }
                        # static/stable blocks get cache_control for prompt caching
                        if pb.position in ("static", "stable"):
                            block_dict["cache_control"] = {"type": "ephemeral"}
                        if pb.position == "static":
                            plugin_static.append(block_dict)
                        elif pb.position == "stable":
                            plugin_stable.append(block_dict)
                        else:
                            plugin_dynamic.append(block_dict)

                if deepseek_native_main:
                    state_board_block = await self._prompt.build_state_board_block(group_id)
                    if state_board_block.get("text"):
                        tail_blocks.append(state_board_block)
                    tail_blocks.extend(plugin_dynamic)

                system_blocks = await self._prompt.build_blocks(
                    user_id=user_id,
                    group_id=group_id,
                    card_store=self._card_store,
                    privacy_mask=privacy_mask,
                    session_id=session_id,
                    conversation_text=conversation_text,
                    plugin_static=plugin_static or None,
                    plugin_stable=plugin_stable or None,
                    plugin_dynamic=None if deepseek_native_main else (plugin_dynamic or None),
                    include_state_board=not deepseek_native_main,
                )
                if deepseek_native_main and tail_blocks:
                    messages = _append_tail_metadata(messages, tail_blocks)
            except Exception:
                logger.exception("build_blocks failed, falling back to static block")
                system_blocks = [self._prompt.static_block]
        else:
            system_blocks = [self._prompt.static_block]

        # Inject thinker decision as final system block so the main LLM
        # knows what direction to take — placed last for highest attention.
        if thinker_decision is not None and thinker_thought:
            hints = [f"你决定说话：{thinker_thought}"]
            d = thinker_decision
            if d.sticker:
                hints.append(
                    "sticker: yes — 请在本轮同时调用 send_sticker 发送匹配的表情包，"
                    "发送后直接接文字内容，不要提及已发送表情包"
                )
            else:
                hints.append("sticker: no")
            hints.append(f"tone: {d.tone}")
            thinker_block: dict[str, Any] = {
                "type": "text",
                "text": "【" + "】【".join(hints) + "】",
            }
            system_blocks = [*system_blocks, thinker_block]

        messages, image_tag_map = await resolve_image_refs(messages, self._image_cache)

        tool_defs = self._build_tool_defs(group_profile)

        # Debug: hash system blocks and tools to diagnose cache misses
        _log_cache_debug(session_id, system_blocks, tool_defs, len(messages))
        _log_debug.debug(
            "chat prompt timing | session={} prepare={:.1f}ms build={:.1f}ms system_blocks={} messages={} tools={}",
            session_id,
            (time.perf_counter() - prompt_start) * 1000,
            (time.perf_counter() - prompt_build_start) * 1000,
            len(system_blocks),
            len(messages),
            len(tool_defs),
        )

        # Token accumulators across tool rounds
        acc_input = 0
        acc_output = 0
        acc_cache_read = 0
        acc_cache_create = 0
        acc_prompt_cache_hit = 0
        acc_prompt_cache_miss = 0
        acc_reasoning_replay = 0
        acc_llm_elapsed = 0.0

        _sticker_sent = False
        tool_call_records: list[dict[str, Any]] = []

        for round_i in range(MAX_TOOL_ROUNDS):
            main_request = LLMRequest(
                task="main",
                user_id=user_id,
                group_id=group_id,
                static_blocks=list(system_blocks),
                user_messages=list(messages),
                tools=tool_defs,
                auto_record_usage=False,
                requires_capabilities=("chat",),
            )
            result = await self._call(main_request)
            acc_llm_elapsed += float(result.get("call_elapsed_s", 0.0) or 0.0)
            acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
            acc_output += result.get("output_tokens", 0)
            acc_cache_read += result.get("cache_read", 0)
            acc_cache_create += result.get("cache_create", 0)
            round_cache_hit, round_cache_miss, round_reasoning_replay = _usage_observability_fields(result)
            acc_prompt_cache_hit += round_cache_hit
            acc_prompt_cache_miss += round_cache_miss
            acc_reasoning_replay += round_reasoning_replay
            text: str = result["text"]
            tool_uses: list[ToolUse] = result["tool_uses"]

            # Check for pass_turn
            pass_turn = next((tu for tu in tool_uses if tu.name == "pass_turn"), None)
            if pass_turn:
                reason = pass_turn.input.get("reason", "")
                total_elapsed = time.monotonic() - t0
                _log_msg_out.info(
                    "pass_turn | session={} reason={!r} llm={:.1f}s total={:.1f}s",
                    session_id, reason, acc_llm_elapsed, total_elapsed,
                )
                if is_group and group_id is not None and self._timeline is not None:
                    self._timeline.set_input_tokens(group_id, result["input_tokens"])
                self._record_usage(
                    call_type="proactive",
                    user_id=user_id, group_id=group_id,
                    model=main_model,
                    provider_kind=str(result.get("provider_kind", main_api_format)),
                    input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                    cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                    prompt_cache_hit_tokens=acc_prompt_cache_hit,
                    prompt_cache_miss_tokens=acc_prompt_cache_miss,
                    reasoning_replay_tokens=acc_reasoning_replay,
                    tool_rounds=round_i, elapsed_s=acc_llm_elapsed,
                )
                return None

            if not tool_uses:
                reply, reply_state = self._finalize_visible_reply(
                    reply=text or "...",
                    session_id=session_id,
                    force_reply=force_reply,
                    has_visible_tool_output=self._has_visible_tool_output(tool_call_records),
                    is_group=is_group,
                )

                if not reply:
                    if is_group and group_id is not None and self._timeline is not None:
                        self._timeline.set_input_tokens(group_id, result["input_tokens"])
                    else:
                        self._short_term.set_input_tokens(session_id, result["input_tokens"])
                    self._record_usage(
                        call_type="proactive" if is_group else "chat",
                        user_id=user_id, group_id=group_id,
                        model=main_model,
                        provider_kind=str(result.get("provider_kind", main_api_format)),
                        input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                        cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                        prompt_cache_hit_tokens=acc_prompt_cache_hit,
                        prompt_cache_miss_tokens=acc_prompt_cache_miss,
                        reasoning_replay_tokens=acc_reasoning_replay,
                        tool_rounds=round_i, elapsed_s=acc_llm_elapsed,
                    )
                    return None

                # Kaomoji enforcement: if the reply contains a kaomoji / action
                # description but the LLM forgot to call send_sticker, inject a
                # forced sticker-selection round (once only, and only if we have
                # at least one round left).
                if (
                    _text_has_kaomoji(reply)
                    and not _sticker_sent
                    and round_i < MAX_TOOL_ROUNDS - 1
                ):
                    _sticker_sent = True  # prevent re-entry
                    assistant_content = []
                    for tb in result.get("thinking_blocks", []):
                        assistant_content.append(tb)
                    if text:
                        assistant_content.append({"type": "text", "text": text})
                    messages.append({"role": "assistant", "content": assistant_content})
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "text",
                            "text": "请现在发送一个表情包来配合你刚才的颜文字"
                                    "（只调用 send_sticker，不要重复文字内容）",
                        }],
                    })
                    _log_thinking.info("kaomoji_enforce | forcing sticker round after kaomoji detected")
                    continue

                segments, raw_segment_count = _reply_segments(reply)
                if raw_segment_count > len(segments):
                    _log_msg_out.debug(
                        "segments coalesced | session={} raw={} capped={}",
                        session_id, raw_segment_count, len(segments),
                    )
                send_start = time.monotonic()
                if on_segment and len(segments) > 1:
                    for seg in segments[:-1]:
                        await on_segment(seg)
                        await asyncio.sleep(_SEGMENT_DELAY)
                    last_seg = segments[-1] if segments else reply
                elif not on_segment and len(segments) > 1:
                    # No callback to send segments — rejoin so caller gets full text
                    last_seg = "\n".join(segments)
                else:
                    last_seg = segments[-1] if segments else reply
                send_elapsed = time.monotonic() - send_start
                full_reply = "\n".join(segments)
                total_elapsed = time.monotonic() - t0
                preview = full_reply[:120] + "…" if len(full_reply) > 120 else full_reply
                _log_msg_out.info(
                    "{!r} | sticker={} len={} segments={} raw_segments={} llm={:.1f}s send_partial={:.1f}s total={:.1f}s",
                    preview, "sent" if _sticker_sent else "none",
                    len(full_reply), len(segments), raw_segment_count,
                    acc_llm_elapsed, send_elapsed, total_elapsed,
                )
                if is_group and group_id is not None and self._timeline is not None:
                    self._timeline.add(group_id, role="assistant", content=full_reply)
                    self._timeline.set_input_tokens(group_id, result["input_tokens"])
                else:
                    self._short_term.add(session_id, "assistant", full_reply)
                    self._short_term.set_input_tokens(session_id, result["input_tokens"])
                    if self._message_log is not None:
                        await self._message_log.record_session_msg(
                            session_id, "assistant", full_reply[:2000]
                        )
                self._record_usage(
                    call_type="proactive" if is_group else "chat",
                    user_id=user_id, group_id=group_id,
                    model=main_model,
                    provider_kind=str(result.get("provider_kind", main_api_format)),
                    input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                    cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                    prompt_cache_hit_tokens=acc_prompt_cache_hit,
                    prompt_cache_miss_tokens=acc_prompt_cache_miss,
                    reasoning_replay_tokens=acc_reasoning_replay,
                    tool_rounds=round_i, elapsed_s=acc_llm_elapsed,
                )
                await self._fire_post_reply(
                    session_id=session_id,
                    group_id=group_id,
                    user_id=user_id,
                    user_content=user_content,
                    reply_content=full_reply,
                    elapsed_ms=total_elapsed * 1000,
                    thinker_action=thinker_action,
                    thinker_thought=thinker_thought,
                    tool_calls=tool_call_records,
                )

                return last_seg

            for tu in tool_uses:
                args_str = json.dumps(tu.input, ensure_ascii=False)[:200]
                args_str = args_str.replace("{", "{{").replace("}", "}}")
                _log_thinking.info(
                    "tool_call | round={} name={} args={}",
                    round_i, tu.name, args_str,
                )
                if tu.name == "send_sticker":
                    _sticker_sent = True

            # Assistant message content — preserve thinking blocks for DeepSeek
            assistant_content: list[dict[str, Any]] = []
            for tb in result.get("thinking_blocks", []):
                assistant_content.append(tb)
            if text:
                assistant_content.append({"type": "text", "text": text})
            for tu in tool_uses:
                assistant_content.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input})
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools in parallel
            tool_ctx = ctx or ToolContext(user_id=user_id, group_id=group_id)
            tool_ctx.extra["image_tags"] = image_tag_map
            if self._timeline is not None:
                tool_ctx.extra["timeline"] = self._timeline
            call_results = await asyncio.gather(
                *[self._tools.call(tu.name, json.dumps(tu.input), ctx=tool_ctx) for tu in tool_uses],
                return_exceptions=True,
            )
            # Convert any exceptions to error strings
            call_results = [
                r if isinstance(r, str) else f"Tool error: {r}" for r in call_results
            ]
            tool_results: list[dict[str, Any]] = []
            for tu, result_text in zip(tool_uses, call_results, strict=True):
                is_error = result_text.startswith("Tool error:")
                _log_thinking.debug(
                    "tool_result | name={} result={}",
                    tu.name, result_text[:200].replace("{", "{{").replace("}", "}}"),
                )
                tool_call_records.append(
                    {
                        "name": tu.name,
                        "input": dict(tu.input),
                        "result": result_text,
                        "is_error": is_error,
                    }
                )
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result_text})
            messages.append({"role": "user", "content": tool_results})

        _log_thinking.warning("tool loop exhausted | session={} rounds={}", session_id, MAX_TOOL_ROUNDS)
        final_main_request = LLMRequest(
            task="main",
            user_id=user_id,
            group_id=group_id,
            static_blocks=list(system_blocks),
            user_messages=list(messages),
            auto_record_usage=False,
            requires_capabilities=("chat",),
        )
        result = await self._call(final_main_request)
        acc_llm_elapsed += float(result.get("call_elapsed_s", 0.0) or 0.0)
        acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
        acc_output += result.get("output_tokens", 0)
        acc_cache_read += result.get("cache_read", 0)
        acc_cache_create += result.get("cache_create", 0)
        round_cache_hit, round_cache_miss, round_reasoning_replay = _usage_observability_fields(result)
        acc_prompt_cache_hit += round_cache_hit
        acc_prompt_cache_miss += round_cache_miss
        acc_reasoning_replay += round_reasoning_replay
        reply, reply_state = self._finalize_visible_reply(
            reply=result["text"] or "...",
            session_id=session_id,
            force_reply=force_reply,
            has_visible_tool_output=self._has_visible_tool_output(tool_call_records),
            is_group=is_group,
        )
        if not reply:
            if is_group and group_id is not None and self._timeline is not None:
                self._timeline.set_input_tokens(group_id, result["input_tokens"])
            else:
                self._short_term.set_input_tokens(session_id, result["input_tokens"])
            self._record_usage(
                call_type="proactive" if is_group else "chat",
                user_id=user_id, group_id=group_id,
                model=main_model,
                provider_kind=str(result.get("provider_kind", main_api_format)),
                input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                prompt_cache_hit_tokens=acc_prompt_cache_hit,
                prompt_cache_miss_tokens=acc_prompt_cache_miss,
                reasoning_replay_tokens=acc_reasoning_replay,
                tool_rounds=MAX_TOOL_ROUNDS, elapsed_s=acc_llm_elapsed,
            )
            return None
        segments, raw_segment_count = _reply_segments(reply)
        if raw_segment_count > len(segments):
            _log_msg_out.debug(
                "segments coalesced | session={} raw={} capped={}",
                session_id, raw_segment_count, len(segments),
            )
        send_start = time.monotonic()
        if on_segment and len(segments) > 1:
            for seg in segments[:-1]:
                await on_segment(seg)
                await asyncio.sleep(_SEGMENT_DELAY)
            last_seg = segments[-1] if segments else reply
        elif not on_segment and len(segments) > 1:
            last_seg = "\n".join(segments)
        else:
            last_seg = segments[-1] if segments else reply
        send_elapsed = time.monotonic() - send_start
        full_reply = "\n".join(segments)
        if is_group and group_id is not None and self._timeline is not None:
            self._timeline.add(group_id, role="assistant", content=full_reply)
            self._timeline.set_input_tokens(group_id, result["input_tokens"])
        else:
            self._short_term.add(session_id, "assistant", full_reply)
            self._short_term.set_input_tokens(session_id, result["input_tokens"])
            if self._message_log is not None:
                await self._message_log.record_session_msg(
                    session_id, "assistant", full_reply[:2000]
                )
        elapsed = time.monotonic() - t0
        _log_msg_out.info(
            "tool_exhausted_reply | session={} segments={} raw_segments={} llm={:.1f}s send_partial={:.1f}s total={:.1f}s",
            session_id, len(segments), raw_segment_count, acc_llm_elapsed, send_elapsed, elapsed,
        )
        self._record_usage(
            call_type="proactive" if is_group else "chat",
            user_id=user_id, group_id=group_id,
            model=main_model,
            provider_kind=str(result.get("provider_kind", main_api_format)),
            input_tokens=acc_input, cache_read_tokens=acc_cache_read,
            cache_create_tokens=acc_cache_create, output_tokens=acc_output,
            prompt_cache_hit_tokens=acc_prompt_cache_hit,
            prompt_cache_miss_tokens=acc_prompt_cache_miss,
            reasoning_replay_tokens=acc_reasoning_replay,
            tool_rounds=MAX_TOOL_ROUNDS, elapsed_s=acc_llm_elapsed,
        )
        await self._fire_post_reply(
            session_id=session_id,
            group_id=group_id,
            user_id=user_id,
            user_content=user_content,
            reply_content=full_reply,
            elapsed_ms=elapsed * 1000,
            thinker_action=thinker_action,
            thinker_thought=thinker_thought,
            tool_calls=tool_call_records,
        )
        return last_seg

    # ------------------------------------------------------------------
    # Compact — private chat
    # ------------------------------------------------------------------

    async def _compact_with_tools(
        self,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        source: str,
        group_id: str | None,
        user_id: str = "",
    ) -> tuple[str, int]:
        """Run a compact LLM call with an update_memo tool loop.

        Returns (summary_text, memo_writes).
        """
        tools: list[dict[str, Any]] | None = None
        if self._card_store:
            tools = [_ADD_CARD_TOOL]

        acc_input = 0
        acc_output = 0
        acc_cache_read = 0
        acc_cache_create = 0
        acc_prompt_cache_hit = 0
        acc_prompt_cache_miss = 0
        acc_reasoning_replay = 0
        memo_writes = 0

        for round_i in range(_MAX_COMPACT_TOOL_ROUNDS):
            compact_request = LLMRequest(
                task="compact",
                user_id=user_id,
                group_id=group_id,
                static_blocks=list(system),
                user_messages=list(messages),
                tools=tools,
                max_tokens=1024,
                auto_record_usage=False,
                requires_capabilities=("chat",),
            )
            result = await self._call(compact_request)
            acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
            acc_output += result.get("output_tokens", 0)
            acc_cache_read += result.get("cache_read", 0)
            acc_cache_create += result.get("cache_create", 0)
            round_cache_hit, round_cache_miss, round_reasoning_replay = _usage_observability_fields(result)
            acc_prompt_cache_hit += round_cache_hit
            acc_prompt_cache_miss += round_cache_miss
            acc_reasoning_replay += round_reasoning_replay

            text: str = result["text"].strip()
            tool_uses: list[ToolUse] = result["tool_uses"]

            if not tool_uses:
                self._record_usage(
                    call_type="compact", user_id="", group_id=group_id,
                    model=self._profile_for_task("compact")[3],
                    provider_kind=str(result.get("provider_kind", self._profile_for_task("compact")[4])),
                    input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                    cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                    prompt_cache_hit_tokens=acc_prompt_cache_hit,
                    prompt_cache_miss_tokens=acc_prompt_cache_miss,
                    reasoning_replay_tokens=acc_reasoning_replay,
                    tool_rounds=round_i, elapsed_s=0.0,
                )
                return text, memo_writes

            # Build assistant message — preserve thinking blocks for DeepSeek
            assistant_content: list[dict[str, Any]] = []
            for tb in result.get("thinking_blocks", []):
                assistant_content.append(tb)
            if text:
                assistant_content.append({"type": "text", "text": text})
            for tu in tool_uses:
                assistant_content.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input})
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute add_card tool calls
            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                if tu.name == "add_card" and self._card_store:
                    scope = tu.input.get("scope", "")
                    scope_id = tu.input.get("scope_id", "")
                    category = tu.input.get("category", "")
                    content = tu.input.get("content", "")
                    if scope and scope_id and category and content:
                        try:
                            await self._card_store.add_card(NewCard(
                                category=category,
                                scope=scope,
                                scope_id=str(scope_id),
                                content=content,
                                confidence=0.6,
                                source=source,
                            ))
                            memo_writes += 1
                            _log_compact.debug("compact card add | scope={}/{} category={}", scope, scope_id, category)
                            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "已添加"})
                        except (ValueError, Exception) as e:
                            _log_compact.warning("compact card add failed | scope={}/{} error={}", scope, scope_id, e)
                            err_msg = f"写入失败: {e}"
                            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": err_msg})
                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": "缺少 scope、scope_id、category 或 content 参数",
                        })
                else:
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "未知工具"})
            messages.append({"role": "user", "content": tool_results})

        # Exhausted rounds — do a final call without tools to get summary
        final_request = LLMRequest(
            task="compact",
            user_id=user_id,
            group_id=group_id,
            static_blocks=list(system),
            user_messages=list(messages),
            max_tokens=1024,
            auto_record_usage=False,
            requires_capabilities=("chat",),
        )
        result = await self._call(final_request)
        acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
        acc_output += result.get("output_tokens", 0)
        acc_cache_read += result.get("cache_read", 0)
        acc_cache_create += result.get("cache_create", 0)
        round_cache_hit, round_cache_miss, round_reasoning_replay = _usage_observability_fields(result)
        acc_prompt_cache_hit += round_cache_hit
        acc_prompt_cache_miss += round_cache_miss
        acc_reasoning_replay += round_reasoning_replay
        self._record_usage(
            call_type="compact", user_id="", group_id=group_id,
            model=self._profile_for_task("compact")[3],
            provider_kind=str(result.get("provider_kind", self._profile_for_task("compact")[4])),
            input_tokens=acc_input, cache_read_tokens=acc_cache_read,
            cache_create_tokens=acc_cache_create, output_tokens=acc_output,
            prompt_cache_hit_tokens=acc_prompt_cache_hit,
            prompt_cache_miss_tokens=acc_prompt_cache_miss,
            reasoning_replay_tokens=acc_reasoning_replay,
            tool_rounds=_MAX_COMPACT_TOOL_ROUNDS, elapsed_s=0.0,
        )
        return result["text"].strip(), memo_writes

    async def _compact(self, session_id: str) -> None:
        """Compress first half of history into summary and extract user memo."""
        if self._private_compact_failures >= self._max_compact_failures:
            history = self._short_term.get(session_id)
            drop = max(2, int(len(history) * self._compress_ratio))
            self._short_term.drop_oldest(session_id, drop)
            _log_compact.warning("compact circuit breaker active, dropped {} msgs | session={}", drop, session_id)
            return

        try:
            history = self._short_term.get(session_id)
            if len(history) < 4:
                return

            old_summary = self._short_term.get_summary(session_id)
            split = max(2, int(len(history) * self._compress_ratio))

            # Assemble content for compression
            lines: list[str] = []
            if old_summary:
                lines.append(f"«之前的对话摘要»\n{old_summary}\n")
            for msg in history[:split]:
                role_label = "用户" if msg["role"] == "user" else "助手"
                lines.append(f"{role_label}: {content_text(msg['content'])}")
            conversation_text = "\n".join(lines)

            # Extract user_id from session_id (format: "private_{user_id}")
            user_id = ""
            if self._card_store and session_id.startswith("private_"):
                user_id = session_id[len("private_"):]

            if self._card_store and user_id:
                system = [{"type": "text", "text": (
                    "你是一个对话压缩助手。请完成两个任务：\n"
                    "1. 将以下对话历史压缩成简洁的中文摘要。"
                    "保留关键信息：用户的问题、重要决策、关键结论、用户偏好。"
                    "去掉寒暄、重复内容和过程性细节。\n"
                    "2. 如果对话中出现了关于用户的新信息（性格、偏好、背景等），"
                    "用 add_card 工具添加记忆卡片。scope=user, scope_id=用户QQ号，"
                    "category 从 preference/boundary/relationship/event/promise/fact/status 中选择。"
                    "每条 content 写一句话结论。没有新信息则不需要调用。\n"
                    "只记新的印象和结论，不记流水账。\n"
                    "最终请输出纯摘要文本（不要加标题或格式）。"
                )}]
            else:
                system = [{"type": "text", "text": (
                    "你是一个对话压缩助手。请将以下对话历史压缩成简洁的中文摘要。"
                    "保留关键信息：用户的问题、重要决策、关键结论、用户偏好。"
                    "去掉寒暄、重复内容和过程性细节。输出纯摘要文本，不要加标题或格式。"
                )}]
            compress_messages: list[dict[str, Any]] = [{"role": "user", "content": conversation_text}]

            _log_compact.info("compact | session={} split={}/{}", session_id, split, len(history))
            source = f"compact:private:{session_id}"
            t_compact = time.monotonic()
            new_summary, memo_writes = await self._compact_with_tools(
                system, compress_messages, source, group_id=None, user_id=user_id,
            )
            compact_elapsed = time.monotonic() - t_compact

            if new_summary:
                self._short_term.compact(session_id, split, new_summary)
                _log_compact.info(
                    "compact done | session={} messages={}->{} summary_len={} memo_writes={} elapsed={:.1f}s",
                    session_id, len(history), len(history) - split,
                    len(new_summary), memo_writes, compact_elapsed,
                )
            else:
                _log_compact.warning("compact produced empty summary | session={}", session_id)

            # Rebuild system blocks so updated memos are reflected
            if memo_writes > 0 and user_id:
                self._prompt.invalidate(user_id=user_id)

            self._private_compact_failures = 0
            if self._on_compact:
                self._on_compact()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._private_compact_failures += 1
            logger.exception("compact failed ({}/{})", self._private_compact_failures, self._max_compact_failures)

    # ------------------------------------------------------------------
    # Compact — group chat (with memo extraction)
    # ------------------------------------------------------------------

    async def _compact_group(self, group_id: str, identity: Identity) -> None:
        """Compress first half of group timeline into summary and extract memos."""
        if self._group_compact_failures >= self._max_compact_failures:
            assert self._timeline is not None
            turns = self._timeline.get_turns(group_id)
            drop = max(2, int(len(turns) * self._compress_ratio))
            self._timeline.drop_oldest(group_id, drop)
            _log_compact.warning("compact circuit breaker active, dropped {} turns | group={}", drop, group_id)
            return

        try:
            assert self._timeline is not None
            turns = self._timeline.get_turns(group_id)
            if len(turns) < 4:
                return

            old_summary = self._timeline.get_summary(group_id)
            split = max(2, int(len(turns) * self._compress_ratio))

            # Assemble content for compression with speaker info
            lines: list[str] = []
            seen_user_ids: list[str] = []
            seen: set[str] = set()

            if old_summary:
                lines.append(f"«之前的对话摘要»\n{old_summary}\n")

            if self._message_log:
                # Query raw messages up to the time of the last turn being compacted
                cutoff = self._timeline.get_turn_time(group_id, split - 1)
                rows = await self._message_log.query_for_compact(group_id, before=cutoff)
                for row in rows:
                    speaker = row.get("speaker") or ""
                    text = row.get("content_text") or ""
                    if row["role"] == "assistant":
                        lines.append(f"{identity.name}: {text}")
                    elif speaker:
                        lines.append(f"{speaker}: {text}")
                    else:
                        lines.append(f"用户: {text}")
                    # Extract QQ IDs for memo targeting
                    if speaker and self._card_store:
                        m = re.search(r"\((\d+)\)$", speaker)
                        if m and m.group(1) not in seen:
                            seen.add(m.group(1))
                            seen_user_ids.append(m.group(1))
            else:
                # Fallback: reconstruct from turns content (no speaker info)
                for turn in turns[:split]:
                    text = content_text(turn.get("content", ""))
                    if turn["role"] == "assistant":
                        lines.append(f"{identity.name}: {text}")
                    else:
                        lines.append(text)

            conversation_text = "\n".join(lines)

            system = [{"type": "text", "text": (
                "你是一个对话分析助手。请完成两个任务：\n"
                "1. 将以下群聊记录压缩成简洁的中文摘要。保留关键信息。\n"
                "2. 如果对话中出现了关于用户或群组的新信息，"
                "用 add_card 工具添加记忆卡片。每条 content 写一句话结论，"
                "category 从 preference/boundary/relationship/event/promise/fact/status 中选择。"
                "没有新信息则不需要调用。\n\n"
                "**关键规则——个人情报与群情报分离：**\n"
                "- 关于某个人的信息（性格、偏好、背景、身份、观点、与他人的关系）"
                "→ scope=user, scope_id=该用户的QQ号\n"
                "- 只有群级别信息（群氛围、群事件、群规矩、成员变动）"
                "→ scope=group, scope_id=群号\n"
                "- 判断标准：如果信息跟着这个人走（换个群也成立），写 user；"
                "如果只在本群语境下有意义，写 group\n\n"
                f"本群 group_id: {group_id}\n"
                f"出现的用户 QQ: {', '.join(seen_user_ids)}\n\n"
                "QQ号是唯一身份标识，昵称不可信。\n"
                "只记新的印象和结论，不记流水账。\n"
                "最终请输出纯摘要文本（不要加标题或格式）。"
            )}]
            compress_messages: list[dict[str, Any]] = [{"role": "user", "content": conversation_text}]

            _log_compact.info("compact_group | group={} split={}/{}", group_id, split, len(turns))
            source = f"compact:group:{group_id}"
            t_compact = time.monotonic()
            new_summary, memo_writes = await self._compact_with_tools(
                system, compress_messages, source, group_id=group_id,
            )
            compact_elapsed = time.monotonic() - t_compact

            if new_summary:
                self._timeline.compact(group_id, split, new_summary)
                _log_compact.info(
                    "compact_group done | group={} turns={}->{} summary_len={} memo_writes={} elapsed={:.1f}s",
                    group_id, len(turns), len(turns) - split,
                    len(new_summary), memo_writes, compact_elapsed,
                )
            else:
                _log_compact.warning("compact_group produced empty summary | group={}", group_id)

            # Rebuild system blocks so updated memos are reflected
            if memo_writes > 0:
                self._prompt.invalidate(group_id=group_id)

            self._group_compact_failures = 0
            if self._on_compact:
                self._on_compact()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._group_compact_failures += 1
            logger.exception("compact_group failed ({}/{})", self._group_compact_failures, self._max_compact_failures)
