"""Pre-reply thinking phase: decide action before generating a response.

Inspired by the old MaiBot Planner architecture:
  Planner (decide action) → Replyer (generate text)

The thinker makes a lightweight LLM call to evaluate whether the bot should
reply, wait (stay silent), or search before replying. This prevents rapid-fire
multi-message bursts and gives the bot more natural conversational pacing.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

_L = logger.bind(channel="thinking")

THINKER_SYSTEM_PROMPT = """你是{name}的思考中枢。你需要在回复之前快速判断：现在要不要说话？说什么方向？

## 可用行动
- **reply**: 有话要说，准备回复。选择此项时，用 thought 写下你想表达的核心意思（一两句话即可）。
- **wait**: 保持沉默。当前不适合插话，或话题不需要你的回应，或你刚回复过不久。
- **search**: 需要先查一些信息再回复。比如日期时间、天气、网页搜索等。选择此项时先用 thought 写下你想查什么。

## 决策原则
1. 有人@你、叫你名字、回复你的消息 → 优先 reply
2. 群友闲聊，话题有趣你有感而发 → reply（但不要每条消息都回）
3. 话题与你完全无关、气氛严肃、或你刚刚已经回复过了 → wait
4. 不确定的事实、需要查时间/日期/外部信息 → search
5. 短时间内已经说了很多话 → wait，把舞台让给别人
6. 你喜欢聊天但也要有分寸感

## 心情对决策的影响
上面会提供你当前的心情状态和正在做的事。你的决策必须受心情影响：
- 能量低（困倦/疲惫）时更容易选择 wait，回复也要简短
- 能量高（兴奋/精力充沛）时更积极 reply，thought 可以更活泼
- 心情低落（难过/烦躁/焦虑）时 tone 要收敛
- 心情好（开心/放松/期待）时自然流露开心，thought 可以更热情
- 紧张度高时回复可能更拘谨，紧张度低时更随性
- 正在做某件事时（上课/排练/吃饭），thought 可以简短提及当前状态作为背景
- 好感度高的用户 → 态度更亲切，可以更主动 reply
- 好感度低的用户 → 保持礼貌但不必过分热情

## 输出格式
只输出一行 JSON：
{{"action": "reply|wait|search", "thought": "你的简短思考（30字以内）"}}"""


class ThinkDecision:
    """Parsed thinker output."""
    __slots__ = ("action", "thought", "usage")

    def __init__(self, action: str, thought: str, usage: dict[str, int] | None = None) -> None:
        self.action = action
        self.thought = thought
        self.usage = usage or {}

    def __repr__(self) -> str:
        return f"ThinkDecision(action={self.action!r}, thought={self.thought!r})"


def parse_think_output(text: str) -> ThinkDecision | None:
    """Parse the thinker's JSON output. Returns None on failure."""
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
        action = data.get("action", "reply")
        thought = data.get("thought", "")
        if action not in ("reply", "wait", "search"):
            action = "reply"
        return ThinkDecision(action=action, thought=thought)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


async def think(
    api_call: Any,
    recent_messages: list[dict[str, Any]],
    max_tokens: int = 256,
    mood_text: str = "",
    affection_text: str = "",
    identity_name: str = "Bot",
) -> ThinkDecision:
    """Call the thinker LLM to decide the next action.

    Args:
        api_call: The LLM API caller (LLMClient._call or equivalent).
        recent_messages: Recent conversation messages in Anthropic format.
        max_tokens: Max tokens for the thinker call.
        mood_text: Current mood + schedule context (from MoodEngine).
        affection_text: Per-user relationship context (from AffectionEngine).
        identity_name: The bot's name from its identity config.

    Returns:
        ThinkDecision with the chosen action and thought.
        Defaults to reply on parse failure.
    """
    system_text = THINKER_SYSTEM_PROMPT.format(name=identity_name)
    if mood_text:
        system_text = mood_text + "\n\n" + system_text
    if affection_text:
        system_text = system_text + "\n\n" + affection_text

    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    user_msg = "以下是最近的聊天内容。请决定下一步行动。"
    messages = [*recent_messages, {"role": "user", "content": user_msg}]

    usage: dict[str, int] = {}
    try:
        result = await api_call(system, messages, tools=None, max_tokens=max_tokens)
        usage = {
            "input_tokens": result.get("input_tokens", 0),
            "cache_read": result.get("cache_read", 0),
            "cache_create": result.get("cache_create", 0),
            "output_tokens": result.get("output_tokens", 0),
        }
    except Exception:
        _L.warning("thinker call failed, defaulting to reply")
        return ThinkDecision(action="reply", thought="(thinker error, fallback to reply)", usage=usage)

    text: str = result.get("text", "")
    decision = parse_think_output(text)
    if decision is None:
        _L.warning("thinker parse failed, defaulting to reply | raw={}", text[:200])
        return ThinkDecision(action="reply", thought="(parse error, fallback to reply)", usage=usage)

    decision.usage = usage
    _L.info("thinker | action={} thought={!r}", decision.action, decision.thought)
    return decision
