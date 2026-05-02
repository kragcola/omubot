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
from typing import Any

import aiohttp
from loguru import logger as _base_logger

from services.identity import Identity
from services.llm.prompt_builder import PromptBuilder
from services.llm.thinker import think
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

# Channel-tagged loggers — each maps to a LogChannelConfig boolean
logger = _base_logger  # keep bare logger for ERROR / EXCEPTION
_log_msg_in = _base_logger.bind(channel="message_in")
_log_msg_out = _base_logger.bind(channel="message_out")
_log_thinking = _base_logger.bind(channel="thinking")
_log_compact = _base_logger.bind(channel="compact")
_log_debug = _base_logger.bind(channel="debug")

_SEGMENT_SEP = "---cut---"
_SEGMENT_DELAY = 1.2  # seconds between segment sends (human-like pacing)
_BLANK_LINE_RE = re.compile(r"\n{2,}")
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]+\]")
_CQ_KV_FIX_RE = re.compile(r",(\w+):")

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


_SENTENCE_END = re.compile(r"([。！？\n])")
_CLAUSE_SEP = re.compile(r"([,，;；:：、])")
_MIN_CHUNK = 4
_MAX_CHUNK = 45


def _split_on_sentence_end(text: str) -> list[str]:
    """Split a long line on 。！？ boundaries, with hard cap at _MAX_CHUNK."""
    parts = _SENTENCE_END.split(text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        combined = buf + part
        if len(combined) <= _MAX_CHUNK:
            buf = combined
        else:
            if buf.strip():
                chunks.append(buf.strip())
            buf = part
    if buf.strip():
        chunks.append(buf.strip())
    # Don't leave a lone delimiter as the last chunk
    if len(chunks) >= 2 and len(chunks[-1]) == 1 and chunks[-1] in "。！？\n":
        chunks[-2] += chunks[-1]
        chunks.pop()
    # Hard split: force-break any remaining chunk > _MAX_CHUNK
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= _MAX_CHUNK:
            result.append(chunk)
        else:
            for i in range(0, len(chunk), _MAX_CHUNK):
                result.append(chunk[i:i + _MAX_CHUNK])
    return result or [text]


def _split_long_on_comma(text: str) -> list[str]:
    """Last resort: split an overly long sentence at comma/clause boundaries."""
    parts = _CLAUSE_SEP.split(text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if len(buf) + len(part) <= _MAX_CHUNK:
            buf += part
        else:
            if buf.strip():
                chunks.append(buf.strip())
            buf = part
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text]


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
        # Step 2: each \n is a split boundary by default
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue

        # Merge consecutive short lines to avoid single-word messages
        merged: list[str] = []
        for line in lines:
            if merged and len(line) < _MIN_CHUNK:
                merged[-1] += "\n" + line
            else:
                merged.append(line)

        # Step 3: split any merged line that's still too long
        for line in merged:
            if len(line) <= _MAX_CHUNK:
                chunks.append(line)
            else:
                sub = _split_on_sentence_end(line)
                for s in sub:
                    if len(s) > _MAX_CHUNK:
                        chunks.extend(_split_long_on_comma(s))
                    else:
                        chunks.append(s)

    # Merge trailing punctuation-only fragment (not hard-split content)
    if len(chunks) >= 2 and len(chunks[-1]) < _MIN_CHUNK and chunks[-1].strip() in "。！？，,；;：:、":
        chunks[-2] += chunks[-1]
        chunks.pop()

    return chunks or [text]


class ToolUse:
    __slots__ = ("id", "input", "name")

    def __init__(self, id: str, name: str, input: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.input = input


def _cached_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def to_anthropic_message(msg: ChatMessage) -> dict[str, Any]:
    return {"role": msg["role"], "content": msg["content"]}


def content_text(content: Content) -> str:
    """Extract plain text from Content, ignoring image blocks."""
    if isinstance(content, str):
        return content
    return " ".join(b["text"] for b in content if b["type"] == "text")


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
) -> dict[str, Any]:
    """Call Anthropic API, parse SSE stream."""
    body: dict[str, Any] = {
        "model": model,
        "system": system_blocks,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        # Cache-control on last tool def so the whole tool set is cached together
        cached_tools = [*tools]
        cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
        body["tools"] = cached_tools

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2024-10-22",
    }

    text_parts: list[str] = []
    tool_uses: list[ToolUse] = []
    thinking_blocks: list[dict[str, Any]] = []
    current_tool: dict[str, str] = {}
    current_thinking: str = ""
    current_thinking_sig: str = ""
    current_block_type: str = ""
    usage: dict[str, int] = {}

    t_ser = time.perf_counter()
    payload_bytes = json.dumps(body).encode()
    payload = io.BytesIO(payload_bytes)
    _log_debug.trace(
        "api payload serialized | size={}KB elapsed={:.1f}ms",
        len(payload_bytes) // 1024,
        (time.perf_counter() - t_ser) * 1000,
    )
    async with session.post(f"{base_url}/v1/messages", data=payload, headers=headers) as resp:
        if resp.status == 429:
            body_text = await resp.text()
            raise RateLimitError(f"HTTP 429: {body_text}")
        if resp.status >= 400:
            body_text = await resp.text()
            logger.error("API {} | body={}", resp.status, body_text[:500])
            resp.raise_for_status()
        async for raw_line in resp.content:
            line = raw_line.decode().strip()
            if not line.startswith("data: "):
                continue
            data: dict[str, Any] = json.loads(line[6:])
            event_type = data.get("type", "")

            if event_type == "message_start":
                msg_usage: dict[str, Any] = data.get("message", {}).get("usage", {})
                usage = {k: v for k, v in msg_usage.items() if isinstance(v, int)}
            elif event_type == "content_block_start":
                block: dict[str, Any] = data.get("content_block", {})
                current_block_type = block.get("type", "")
                if current_block_type == "tool_use":
                    current_tool = {"id": block["id"], "name": block["name"], "input_json": ""}
                elif current_block_type == "thinking":
                    current_thinking = ""
                    current_thinking_sig = block.get("signature", "")
            elif event_type == "content_block_delta":
                delta: dict[str, Any] = data.get("delta", {})
                if delta.get("type") == "text_delta" and current_block_type != "thinking":
                    text_parts.append(delta["text"])
                elif delta.get("type") == "thinking_delta":
                    current_thinking += delta.get("thinking", "")
                elif delta.get("type") == "input_json_delta":
                    current_tool["input_json"] += delta.get("partial_json", "")
            elif event_type == "content_block_stop":
                if current_tool:
                    input_data: dict[str, Any] = (
                        json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                    )
                    tool_uses.append(ToolUse(id=current_tool["id"], name=current_tool["name"], input=input_data))
                    current_tool = {}
                elif current_block_type == "thinking":
                    tb: dict[str, Any] = {"type": "thinking", "thinking": current_thinking}
                    if current_thinking_sig:
                        tb["signature"] = current_thinking_sig
                    thinking_blocks.append(tb)
                    current_thinking = ""
                    current_thinking_sig = ""
            elif event_type == "message_delta":
                delta_usage: dict[str, Any] = data.get("usage", {})
                for k, v in delta_usage.items():
                    if isinstance(v, int):
                        usage[k] = v
            elif event_type == "error":
                error_data = data.get("error", {})
                error_msg = error_data.get("message", str(data))
                if "rate limit" in error_msg.lower():
                    raise RateLimitError(f"Anthropic API stream error: {error_msg}")
                raise RuntimeError(f"Anthropic API stream error: {error_msg}")

    # Token stats
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)
    input_tokens = usage.get("input_tokens", 0)
    total_input = input_tokens + cache_read + cache_create
    output_tokens = usage.get("output_tokens", 0)

    return {
        "text": "".join(text_parts),
        "tool_uses": tool_uses,
        "thinking_blocks": thinking_blocks,
        "input_tokens": total_input,
        "output_tokens": output_tokens,
        "cache_read": cache_read,
        "cache_create": cache_create,
    }


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
        bus: object | None = None,
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
        self._bus = bus

    async def close(self) -> None:
        await self._session.close()

    async def _call(
        self, system_blocks: list[dict[str, Any]], messages: list[Any], tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        return await call_api(
            self._session, self._base_url, self._api_key, self._model,
            system_blocks, messages, max_tokens=max_tokens, tools=tools,
        )

    def _record_usage(
        self,
        *,
        call_type: str,
        user_id: str,
        group_id: str | None,
        input_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        output_tokens: int,
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
            model=self._model,
            input_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            output_tokens=output_tokens,
            tool_rounds=tool_rounds,
            elapsed_s=elapsed_s,
            error=error,
        ))

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

        is_group = group_id is not None and self._timeline is not None

        if is_group:
            assert group_id is not None
            assert self._timeline is not None
            # Group: messages already added to timeline by group_listener
            if self._timeline.needs_compact(group_id, self._max_context_tokens, self._compact_ratio):
                _log_compact.info(
                    "compact triggering | group={} input_tokens={} threshold={}",
                    group_id, self._timeline.get_input_tokens(group_id),
                    int(self._max_context_tokens * self._compact_ratio),
                )
                await self._compact_group(group_id, identity)
            messages = self._build_group_messages(group_id)
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
            if self._short_term.needs_compact(session_id, self._max_context_tokens, self._compact_ratio):
                _log_compact.info(
                    "compact triggering | session={} input_tokens={} threshold={}",
                    session_id, self._short_term.get_input_tokens(session_id),
                    int(self._max_context_tokens * self._compact_ratio),
                )
                await self._compact(session_id)
            messages = self._build_private_messages(session_id)

        # Extract conversation text for retrieval gating
        if is_group and self._timeline is not None:
            conversation_text = self._timeline.get_recent_text(group_id, last_n=3)
        else:
            conversation_text = content_text(user_content) if user_content else ""

        if self._card_store:
            try:
                # Collect plugin blocks via bus.on_pre_prompt
                plugin_static: list[dict[str, Any]] = []
                plugin_stable: list[dict[str, Any]] = []
                plugin_dynamic: list[dict[str, Any]] = []
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

                system_blocks = await self._prompt.build_blocks(
                    user_id=user_id,
                    group_id=group_id,
                    card_store=self._card_store,
                    privacy_mask=privacy_mask,
                    session_id=session_id,
                    conversation_text=conversation_text,
                    plugin_static=plugin_static or None,
                    plugin_stable=plugin_stable or None,
                    plugin_dynamic=plugin_dynamic or None,
                )
            except Exception:
                logger.exception("build_blocks failed, falling back to static block")
                system_blocks = [self._prompt.static_block]
        else:
            system_blocks = [self._prompt.static_block]

        messages, image_tag_map = await resolve_image_refs(messages, self._image_cache)

        tool_defs: list[dict[str, Any]] | None = None
        if not self._tools.empty:
            tool_defs = _to_anthropic_tools(self._tools.to_openai_tools())
        # pass_turn is always available
        tool_defs = [*(tool_defs or []), _PASS_TURN_TOOL]

        # Debug: hash system blocks and tools to diagnose cache misses
        _log_cache_debug(session_id, system_blocks, tool_defs, len(messages))

        # Token accumulators across tool rounds
        acc_input = 0
        acc_output = 0
        acc_cache_read = 0
        acc_cache_create = 0

        # Pre-reply thinking phase: decide reply / wait / search before the tool loop
        if self._thinker_enabled and not force_reply:
            # Extract mood and affection context from system blocks so the
            # thinker can make mood-aware decisions (energy/valence/tension).
            mood_text = ""
            affection_text = ""
            state_board_text = ""
            for block in system_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "")
                    if t.startswith("【当前时间】"):
                        mood_text = t
                    elif t.startswith("【与当前用户的关系】"):
                        affection_text = t
                    elif t.startswith("【当前群聊状态】"):
                        state_board_text = t
            if state_board_text:
                mood_text = state_board_text + "\n\n" + mood_text
            decision = await think(
                self._call, messages,
                max_tokens=self._thinker_max_tokens,
                mood_text=mood_text,
                affection_text=affection_text,
                identity_name=identity.name,
            )
            thinker_usage = decision.usage or {}
            thinker_elapsed = time.monotonic() - t0
            # Record thinker call as a separate row for cache/usage visibility
            self._record_usage(
                call_type="thinker",
                user_id=user_id, group_id=group_id,
                input_tokens=thinker_usage.get("input_tokens", 0),
                cache_read_tokens=thinker_usage.get("cache_read", 0),
                cache_create_tokens=thinker_usage.get("cache_create", 0),
                output_tokens=thinker_usage.get("output_tokens", 0),
                tool_rounds=0, elapsed_s=thinker_elapsed,
            )
            if decision.action == "wait":
                _log_msg_out.info(
                    "thinker wait | session={} thought={!r} elapsed={:.1f}s",
                    session_id, decision.thought, thinker_elapsed,
                )
                if is_group and group_id is not None and self._timeline is not None:
                    self._timeline.set_input_tokens(group_id, 0)
                self._prompt.rewind_retrieval_turn(session_id)
                return None
            # Append thought as a system hint so sticker rules still apply
            _log_thinking.info(
                "thinker | action={} thought={!r} session={}",
                decision.action, decision.thought, session_id,
            )
            system_blocks = [*system_blocks, {
                "type": "text",
                "text": f"[思考指引] {decision.thought}\n"
                        f"注意：以上是思考方向，具体回复时仍需遵循所有指令（包括表情包使用规则）。",
            }]

        # force_reply: skip thinker and ensure a response (used for @ mentions / debug).
        # The debug block injection is handled separately by ChatPlugin._handle_debug
        # which passes force_reply=True with a pre-built debug system block.
        # Note: force_reply no longer bypasses _split_naturally — all replies are segmented.

        _sticker_sent = False

        for round_i in range(MAX_TOOL_ROUNDS):
            result = await self._call(system_blocks, messages, tools=tool_defs)
            acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
            acc_output += result.get("output_tokens", 0)
            acc_cache_read += result.get("cache_read", 0)
            acc_cache_create += result.get("cache_create", 0)
            text: str = result["text"]
            tool_uses: list[ToolUse] = result["tool_uses"]

            # Check for pass_turn
            pass_turn = next((tu for tu in tool_uses if tu.name == "pass_turn"), None)
            if pass_turn:
                reason = pass_turn.input.get("reason", "")
                elapsed = time.monotonic() - t0
                _log_msg_out.info("pass_turn | session={} reason={!r} elapsed={:.1f}s", session_id, reason, elapsed)
                if is_group and group_id is not None and self._timeline is not None:
                    self._timeline.set_input_tokens(group_id, result["input_tokens"])
                self._record_usage(
                    call_type="proactive",
                    user_id=user_id, group_id=group_id,
                    input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                    cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                    tool_rounds=round_i, elapsed_s=elapsed,
                )
                return None

            if not tool_uses:
                reply = _strip_markdown(text or "...")

                # Kaomoji→sticker enforcement: if the LLM used emoticons but
                # didn't send a sticker, force one more tool round.
                if not force_reply and not _sticker_sent and _text_has_kaomoji(reply) and round_i + 1 < MAX_TOOL_ROUNDS:
                    assistant_content = []
                    for tb in result.get("thinking_blocks", []):
                        assistant_content.append(tb)
                    if text:
                        assistant_content.append({"type": "text", "text": text})
                    messages.append({"role": "assistant", "content": assistant_content})
                    system_blocks = [*system_blocks, {
                        "type": "text",
                        "text": (
                            "[系统指令] 你的上一条消息使用了颜文字（如(≧▽≦)/等），"
                            "必须立即调用 send_sticker 发送一个表情包来配合你的语气。"
                            "选择与当前情绪最匹配的表情包，只调用 send_sticker，不要再输出文字。"
                        ),
                    }]
                    _log_thinking.info(
                        "sticker enforcement | kaomoji detected, forcing round | session={}",
                        session_id,
                    )
                    _sticker_sent = True  # Prevent repeated enforcement loops
                    continue

                segments = _split_naturally(reply)
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
                full_reply = "\n".join(segments)
                elapsed = time.monotonic() - t0
                preview = full_reply[:120] + "…" if len(full_reply) > 120 else full_reply
                _log_msg_out.info(
                    "{!r} | sticker={} len={} segments={} elapsed={:.1f}s",
                    preview, "sent" if _sticker_sent else "none",
                    len(full_reply), len(segments), elapsed,
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
                    input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                    cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                    tool_rounds=round_i, elapsed_s=elapsed,
                )

                # Fire post_reply hook for plugins (affection, memo, etc.)
                if self._bus is not None:
                    from kernel.types import ReplyContext
                    reply_ctx = ReplyContext(
                        session_id=session_id,
                        group_id=group_id,
                        user_id=user_id,
                        reply_content=full_reply,
                        elapsed_ms=elapsed * 1000,
                    )
                    await self._bus.fire_on_post_reply(reply_ctx)

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
                _log_thinking.debug(
                    "tool_result | name={} result={}",
                    tu.name, result_text[:200].replace("{", "{{").replace("}", "}}"),
                )
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result_text})
            messages.append({"role": "user", "content": tool_results})

        _log_thinking.warning("tool loop exhausted | session={} rounds={}", session_id, MAX_TOOL_ROUNDS)
        result = await self._call(system_blocks, messages)
        acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
        acc_output += result.get("output_tokens", 0)
        acc_cache_read += result.get("cache_read", 0)
        acc_cache_create += result.get("cache_create", 0)
        reply = _strip_markdown(result["text"] or "...")
        segments = _split_naturally(reply)
        if on_segment and len(segments) > 1:
            for seg in segments[:-1]:
                await on_segment(seg)
                await asyncio.sleep(_SEGMENT_DELAY)
            last_seg = segments[-1] if segments else reply
        elif not on_segment and len(segments) > 1:
            last_seg = "\n".join(segments)
        else:
            last_seg = segments[-1] if segments else reply
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
        self._record_usage(
            call_type="proactive" if is_group else "chat",
            user_id=user_id, group_id=group_id,
            input_tokens=acc_input, cache_read_tokens=acc_cache_read,
            cache_create_tokens=acc_cache_create, output_tokens=acc_output,
            tool_rounds=MAX_TOOL_ROUNDS, elapsed_s=elapsed,
        )
        # Fire post_reply hook for plugins
        if self._bus is not None:
            from kernel.types import ReplyContext
            reply_ctx = ReplyContext(
                session_id=session_id,
                group_id=group_id,
                user_id=user_id,
                reply_content=full_reply,
                elapsed_ms=elapsed * 1000,
            )
            await self._bus.fire_on_post_reply(reply_ctx)
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
        memo_writes = 0

        for round_i in range(_MAX_COMPACT_TOOL_ROUNDS):
            result = await call_api(
                self._session, self._base_url, self._api_key, self._model,
                system, messages, max_tokens=1024, tools=tools,
            )
            acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
            acc_output += result.get("output_tokens", 0)
            acc_cache_read += result.get("cache_read", 0)
            acc_cache_create += result.get("cache_create", 0)

            text: str = result["text"].strip()
            tool_uses: list[ToolUse] = result["tool_uses"]

            if not tool_uses:
                self._record_usage(
                    call_type="compact", user_id="", group_id=group_id,
                    input_tokens=acc_input, cache_read_tokens=acc_cache_read,
                    cache_create_tokens=acc_cache_create, output_tokens=acc_output,
                    tool_rounds=round_i, elapsed_s=0.0,
                )
                return text, memo_writes

            # Build assistant message with text + tool_use blocks
            assistant_content: list[dict[str, Any]] = []
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
        result = await call_api(
            self._session, self._base_url, self._api_key, self._model,
            system, messages, max_tokens=1024,
        )
        acc_input += result["input_tokens"] - result.get("cache_read", 0) - result.get("cache_create", 0)
        acc_output += result.get("output_tokens", 0)
        acc_cache_read += result.get("cache_read", 0)
        acc_cache_create += result.get("cache_create", 0)
        self._record_usage(
            call_type="compact", user_id="", group_id=group_id,
            input_tokens=acc_input, cache_read_tokens=acc_cache_read,
            cache_create_tokens=acc_cache_create, output_tokens=acc_output,
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
                system, compress_messages, source, group_id=None,
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


