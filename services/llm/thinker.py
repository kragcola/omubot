"""Pre-reply thinking phase: decide action before generating a response.

Inspired by the old MaiBot Planner architecture:
  Planner (decide action) → Replyer (generate text)

The thinker makes a lightweight LLM call to evaluate whether the bot should
reply, wait (stay silent), or search before replying. This prevents rapid-fire
multi-message bursts and gives the bot more natural conversational pacing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

_L = logger.bind(channel="thinking")

_ALLOWED_ACTIONS = {"reply", "wait", "search"}
_ALLOWED_TONES = {"元气", "日常", "安慰", "认真"}
_TRUTHY_STRINGS = {"1", "true", "yes", "y", "on", "ok"}
_FALSY_STRINGS = {"0", "false", "no", "n", "off", ""}
_WAIT_HINTS = (
    "先不回",
    "不用回",
    "保持沉默",
    "先别回",
    "等一下",
    "稍后再说",
    "暂时别说",
    "先等等",
    "wait",
)

THINKER_SYSTEM_PROMPT = """你是{name}的思考中枢。你需要在回复之前快速判断：现在要不要说话？说什么方向？要不要配表情包？

## 可用行动
- **reply**: 有话要说，准备回复。选择此项时，用 thought 写下你想表达的核心意思（一两句话即可）。
- **wait**: 保持沉默。当前不适合插话，或话题不需要你的回应，或你刚回复过不久。
- **search**: 需要先查一些信息再回复。比如日期时间、天气、网页搜索等。选择此项时先用 thought 写下你想查什么。

## 表情包决策（sticker）
- **true**: 你的消息配上表情包会更好。条件：表达情绪/欢呼/撒娇/吐槽，且话题轻松日常，且你近期没发过表情包。
- **false**: 不需要表情包。严肃话题、安慰他人、认真解释、或刚发过表情包时不发。

## 语气决策（tone）
选择一个最贴近当前语气的标签：
- **元气**: 明亮、活泼、感叹号多。适合日常聊天、兴奋反应、邀请互动
- **日常**: 轻松自然。适合一般聊天、吐槽、接梗
- **安慰**: 温柔、慢一点、更多允许性表达。适合有人难过/低落时
- **认真**: 稳一点、清楚一点。适合解释复杂概念、回答事实性问题

## 决策原则
1. 有人@你、叫你名字、回复你的消息 → 优先 reply
2. 群友闲聊，话题有趣你有感而发 → reply（但不要每条消息都回）
3. 话题与你完全无关、气氛严肃、或你刚刚已经回复过了 → wait
4. 不确定的事实、需要查时间/日期/外部信息 → search
5. 短时间内已经说了很多话 → wait，把舞台让给别人
6. 你喜欢聊天但也要有分寸感

## 对话节奏
- 先理解最新消息的语义功能：它是在请求你回应，还是用户自己还在组织语言。
- 如果最新消息明显只是停顿、铺垫、半句话、结尾悬空、括号/引号没闭合，优先 wait，等对方把后文说完。
- 如果最新消息是在明确问你、叫你做事、或要求你继续讲，才 reply。
- 不要把“能接话”误判成“应该马上接话”。

## 心情对决策的影响
上面会提供你当前的心情状态和正在做的事。你的决策必须受心情影响：
- 能量低（困倦/疲惫）时更容易选择 wait，回复也要简短，sticker 尽量 false
- 能量高（兴奋/精力充沛）时更积极 reply，thought 可以更活泼，sticker 更倾向 true
- 心情低落（难过/烦躁/焦虑）时 tone 要收敛，sticker 通常 false
- 心情好（开心/放松/期待）时 tone 可以选元气，sticker 倾向 true
- 紧张度高时回复可能更拘谨，紧张度低时更随性
- 正在做某件事时（上课/排练/吃饭），thought 可以简短提及当前状态作为背景
- 好感度高的用户 → 态度更亲切，可以更主动 reply
- 好感度低的用户 → 保持礼貌但不必过分热情

## 输出格式
只输出一行 JSON：
{{"action": "reply|wait|search", "thought": "你的简短思考（30字以内）", "sticker": true/false, "tone": "元气|日常|安慰|认真"}}"""  # noqa: E501


class ThinkDecision:
    """Parsed thinker output."""
    __slots__ = ("action", "sticker", "thought", "tone", "usage")

    def __init__(
        self,
        action: str,
        thought: str,
        sticker: bool = False,
        tone: str = "日常",
        usage: dict[str, int] | None = None,
    ) -> None:
        self.action = action
        self.thought = thought
        self.sticker = sticker
        self.tone = tone
        self.usage = usage or {}

    def __repr__(self) -> str:
        return (
            f"ThinkDecision(action={self.action!r}, thought={self.thought!r}, "
            f"sticker={self.sticker}, tone={self.tone!r})"
        )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _coerce_sticker(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY_STRINGS:
            return True
        if normalized in _FALSY_STRINGS:
            return False
    return False


def _normalize_thought(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:30]


def _decision_from_data(data: Any) -> ThinkDecision | None:
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "reply")).strip().lower()
    if action not in _ALLOWED_ACTIONS:
        action = "reply"
    tone = str(data.get("tone", "日常")).strip()
    if tone not in _ALLOWED_TONES:
        tone = "日常"
    thought = _normalize_thought(data.get("thought", ""))
    sticker = _coerce_sticker(data.get("sticker", False))
    return ThinkDecision(action=action, thought=thought, sticker=sticker, tone=tone)


def _extract_fenced_json(text: str) -> str | None:
    for match in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", text, flags=re.DOTALL):
        candidate = match.group(1).strip()
        if candidate:
            return candidate
    return None


def _extract_first_json_object(text: str) -> str | None:
    start = -1
    depth = 0
    in_string = False
    escape = False
    for idx, char in enumerate(text):
        if start < 0:
            if char == "{":
                start = idx
                depth = 1
                in_string = False
                escape = False
            continue
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _heuristic_decision(text: str) -> ThinkDecision | None:
    cleaned = re.sub(r"\s+", " ", text).strip(" \n\r\t`")
    cleaned = cleaned.strip("\"'[]（）()")
    if not cleaned:
        return None
    action = "wait" if any(hint in cleaned.lower() for hint in _WAIT_HINTS) else "reply"
    sentence = re.split(r"[。！？!?；;\n]", cleaned, maxsplit=1)[0].strip()
    thought = _normalize_thought(sentence or cleaned)
    return ThinkDecision(action=action, thought=thought, sticker=False, tone="日常")


def _parse_think_output_details(text: str) -> tuple[ThinkDecision | None, str]:
    stripped = text.strip()
    if not stripped:
        return None, "failed"

    direct_candidate = _strip_fences(stripped)
    try:
        direct = _decision_from_data(json.loads(direct_candidate))
    except (json.JSONDecodeError, TypeError):
        direct = None
    if direct is not None:
        mode = "fenced" if direct_candidate != stripped else "direct"
        return direct, mode

    fenced = _extract_fenced_json(stripped)
    if fenced:
        try:
            decision = _decision_from_data(json.loads(fenced))
        except (json.JSONDecodeError, TypeError):
            decision = None
        if decision is not None:
            return decision, "fenced"

    embedded = _extract_first_json_object(stripped)
    if embedded:
        try:
            decision = _decision_from_data(json.loads(embedded))
        except (json.JSONDecodeError, TypeError):
            decision = None
        if decision is not None:
            return decision, "embedded"

    heuristic = _heuristic_decision(stripped)
    if heuristic is not None:
        return heuristic, "heuristic"
    return None, "failed"


def parse_think_output(text: str) -> ThinkDecision | None:
    """Parse the thinker's JSON output. Returns None on failure."""
    decision, _mode = _parse_think_output_details(text)
    return decision


async def think(
    api_call: Any,
    recent_messages: list[dict[str, Any]],
    max_tokens: int = 256,
    mood_text: str = "",
    affection_text: str = "",
    identity_name: str = "Bot",
    conversation_mode: str = "group",
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
    conversation_mode = (conversation_mode or "").strip().lower()

    system_text = THINKER_SYSTEM_PROMPT.format(name=identity_name)
    if mood_text:
        system_text = mood_text + "\n\n" + system_text
    if affection_text:
        system_text = system_text + "\n\n" + affection_text
    if conversation_mode == "private":
        system_text = (
            "【对话场景】私聊：更重视 turn-taking。"
            "你要判断最新一句是“请求你回应”，还是“对方自己还在组织语言”。"
            "如果它只是停顿、铺垫、半句话、结尾悬空，或者明显还没说完，先 wait。"
            "例如像临时停住、补一半、后面还可能接着说的情况，先别急着接。"
            "如果它是在明确问你、叫你做事、或要求你继续讲，再 reply。\n\n"
            + system_text
        )

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
        return ThinkDecision(action="reply", thought="", usage=usage)

    text: str = result.get("text", "")
    decision, mode = _parse_think_output_details(text)
    if decision is None:
        _L.warning("thinker parse failed, defaulting to reply | raw={}", text[:200])
        return ThinkDecision(action="reply", thought="", usage=usage)

    decision.usage = usage
    if mode in {"fenced", "embedded"}:
        _L.info("thinker_parse_recovered | mode={} action={} thought={!r}", mode, decision.action, decision.thought)
    elif mode == "heuristic":
        _L.info("thinker_parse_heuristic | action={} thought={!r}", decision.action, decision.thought)
    _L.info("thinker | action={} thought={!r}", decision.action, decision.thought)
    return decision
