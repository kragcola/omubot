"""Pre-reply thinking phase: decide action before generating a response.

Inspired by the old MaiBot Planner architecture:
  Planner (decide action) → Replyer (generate text)

The thinker makes a lightweight LLM call to evaluate whether the bot should
reply or wait (stay silent), and which retrieval mode to use when fetching
context. This prevents rapid-fire multi-message bursts and gives the bot
more natural conversational pacing.

PR5 (2026-05-22): retrieve_mode field added (skip / doc / fact / hybrid).
"search" action removed — retrieval is now driven by retrieve_mode, and
external tool calls (web search, datetime) handled by the main LLM tool loop.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from services.humanization import CLOCK_CURRENT_SLOT, THINKER_LAST_DECISION_SLOT, humanization_source
from services.llm.llm_request import LLMRequest
from services.system_module import Scope

_L = logger.bind(channel="thinking")

_ALLOWED_ACTIONS = {"reply", "wait"}
_ALLOWED_MODES = {"skip", "doc", "fact", "hybrid"}
_ALLOWED_TONES = {"元气", "日常", "安慰", "认真"}
_ALLOWED_TOPIC_INTENT_LABELS = frozenset({
    "闲聊",
    "关心",
    "安抚",
    "打趣",
    "吐槽",
    "询问",
    "提议",
    "信息同步",
    "技术讨论",
    "反对",
})
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

THINKER_SYSTEM_PROMPT = """你是{name}的思考中枢。你需要在回复之前快速判断：现在要不要说话？说什么方向？要不要查外部资料？要不要配表情包？

## 上下文使用须知

- 你处在 Omubot 主对话链路的"决策前"位置：用户消息进来 → **你（thinker）做决策** → 主 LLM 按你的决策生成最终回复（或保持沉默）。
- 你的输出**不会**直接发给用户。你只产出一个 JSON 决策包给主链路消费，主 LLM 看不到你的 thought 文本，只看到你选的 action / topic_intent_label / retrieve_mode / rewritten_query 等结构化字段。
- 你**不需要**写完整的回复内容、不需要解释自己的判断、不需要复述用户消息——这些都是浪费 token。thought 字段只用一两句话写"我打算让主 LLM 表达什么核心意思"即可。
- 主 LLM 拥有完整的人设、记忆、工具链；你只负责"要不要说 / 说什么方向 / 查不查资料 / 资料用什么 query"。**不要**在 thought 里替主 LLM 决定具体措辞、表情包、段落结构。
- 你看到的 "## 当前心情"、"## 与当前用户的关系"、"## 群内黑话提示" 等动态块是**仅供参考**的辅助信息，不要直接复读它们的字面内容到 thought 里。
- retrieve_mode 四档语义对齐主链路 `services/context/service.py` 的 `_MODE_SOURCE_FILTER`：skip 不检索 / doc 只查文档片段 / fact 只查记忆+图谱 / hybrid 全查（默认）。选择 skip 时必须清空 rewritten_query。
- 你的所有判断必须基于**最近的对话内容**，不要凭空推断、不要扩展话题、不要建议用户做某件事。

## 可用行动
- **reply**: 有话要说，准备回复。选择此项时，用 thought 写下你想表达的核心意思（一两句话即可）。
- **wait**: 保持沉默。当前不适合插话，或话题不需要你的回应，或你刚回复过不久。

## 意图标签（topic_intent_label）
从以下标签中选择一个最贴近你这轮想说的话的方向：
- **闲聊**：轻松接话、延续气氛
- **关心**：表达在意、照顾对方感受
- **安抚**：缓和情绪、让对方安心
- **打趣**：轻轻玩梗、带一点俏皮
- **吐槽**：带态度的抱怨或点评
- **询问**：追问细节、确认信息
- **提议**：给出一个小建议或下一步
- **信息同步**：补充事实、更新现状
- **技术讨论**：偏问题分析、配置、实现细节
- **反对**：礼貌表达不同意或纠正

当 action=wait 时，也要给一个最贴近当前沉默理由的标签；拿不准就用 **闲聊**。

## 检索模式（retrieve_mode）
- **skip**: 闲聊/情绪/玩梗/当前话题已经在最近上下文里，不需要再注入外部知识
- **doc**: 涉及项目/工具/系统的"怎么部署/怎么配置/报什么错/命令是什么"——查项目文档
- **fact**: 涉及具体人/QQ号/实体的属性、偏好、历史行为——查记忆卡片和知识图谱
- **hybrid**: 既问项目又问人，或问题跨知识源；不确定时选 hybrid

## 检索 query（rewritten_query）
- 当 retrieve_mode 是 doc / fact / hybrid 时，**必须**输出一句自包含的检索 query：
  - 把代词、省略、上下文都展开成命名实体（"它支持什么参数" → "Claude API 工具调用 tools 字段支持什么参数"）
  - 长度 30-80 字；保留原文里的时间、数字、QQ 号、@、英文术语
  - 写成检索友好的陈述句或疑问句，不要写成"用户在问 ..."这种元描述
- 当 retrieve_mode=skip 或 action=wait 时，**留空**（""）

## 判断依据
1. 没有具体实体/概念，纯情绪或承接话题 → skip
2. 出现项目名 + 操作动词（部署/配置/启动/报错） → doc
3. 出现 QQ号/@某人 + 属性问句（喜欢什么/什么时候/谁是） → fact
4. 同时命中 doc 和 fact 特征，或问题复杂 → hybrid
5. action=wait 时 retrieve_mode 必须是 skip（不回复就不必检索）

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
4. 短时间内已经说了很多话 → wait，把舞台让给别人
5. 你喜欢聊天但也要有分寸感

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
{{"action": "reply|wait", "topic_intent_label": "闲聊|关心|安抚|打趣|吐槽|询问|提议|信息同步|技术讨论|反对", "retrieve_mode": "skip|doc|fact|hybrid", "rewritten_query": "查询语句或空", "thought": "你的简短思考（30字以内）", "sticker": true/false, "tone": "元气|日常|安慰|认真"}}"""  # noqa: E501


class ThinkDecision:
    """Parsed thinker output."""
    __slots__ = (
        "action",
        "retrieve_mode",
        "rewritten_query",
        "sticker",
        "thought",
        "tone",
        "topic_intent_label",
        "usage",
    )

    def __init__(
        self,
        action: str,
        thought: str,
        topic_intent_label: str = "闲聊",
        sticker: bool = False,
        tone: str = "日常",
        retrieve_mode: str = "hybrid",
        rewritten_query: str = "",
        usage: dict[str, int] | None = None,
    ) -> None:
        self.action = action
        self.thought = thought
        self.topic_intent_label = topic_intent_label
        self.sticker = sticker
        self.tone = tone
        self.retrieve_mode = retrieve_mode
        self.rewritten_query = rewritten_query
        self.usage = usage or {}

    def __repr__(self) -> str:
        return (
            f"ThinkDecision(action={self.action!r}, thought={self.thought!r}, "
            f"topic_intent_label={self.topic_intent_label!r}, "
            f"sticker={self.sticker}, tone={self.tone!r}, "
            f"retrieve_mode={self.retrieve_mode!r}, "
            f"rewritten_query={self.rewritten_query!r})"
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


def _normalize_rewritten_query(value: Any) -> str:
    """Trim + collapse whitespace + cap at 160 chars to defend against runaway LLM output."""
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:160]


def _normalize_topic_intent_label(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in _ALLOWED_TOPIC_INTENT_LABELS else "闲聊"


def _decision_from_data(data: Any) -> ThinkDecision | None:
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "reply")).strip().lower()
    if action not in _ALLOWED_ACTIONS:
        action = "reply"
    tone = str(data.get("tone", "日常")).strip()
    if tone not in _ALLOWED_TONES:
        tone = "日常"
    retrieve_mode = str(data.get("retrieve_mode", "hybrid")).strip().lower()
    if retrieve_mode not in _ALLOWED_MODES:
        retrieve_mode = "hybrid"
    if action == "wait":
        retrieve_mode = "skip"
    thought = _normalize_thought(data.get("thought", ""))
    topic_intent_label = _normalize_topic_intent_label(data.get("topic_intent_label", "闲聊"))
    sticker = _coerce_sticker(data.get("sticker", False))
    rewritten_query = _normalize_rewritten_query(data.get("rewritten_query", ""))
    if action == "wait" or retrieve_mode == "skip":
        rewritten_query = ""
    return ThinkDecision(
        action=action,
        thought=thought,
        topic_intent_label=topic_intent_label,
        sticker=sticker,
        tone=tone,
        retrieve_mode=retrieve_mode,
        rewritten_query=rewritten_query,
    )


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
    retrieve_mode = "skip" if action == "wait" else "hybrid"
    return ThinkDecision(
        action=action,
        thought=thought,
        topic_intent_label="闲聊",
        sticker=False,
        tone="日常",
        retrieve_mode=retrieve_mode,
    )


def _parse_structured_think_output_details(text: str) -> tuple[ThinkDecision | None, str]:
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

    return None, "failed"


def _parse_think_output_details(text: str) -> tuple[ThinkDecision | None, str]:
    decision, mode = _parse_structured_think_output_details(text)
    if decision is not None:
        return decision, mode

    stripped = text.strip()
    heuristic = _heuristic_decision(stripped)
    if heuristic is not None:
        return heuristic, "heuristic"
    return None, "failed"


def parse_think_output(text: str) -> ThinkDecision | None:
    """Parse the thinker's JSON output. Returns None on failure."""
    decision, _mode = _parse_think_output_details(text)
    return decision


def write_thinker_decision_state(
    bus: Any,
    decision: ThinkDecision,
    *,
    session_id: str,
    group_id: str | None,
    user_id: str,
    turn_id: str,
) -> None:
    """Write the latest thinker decision into RuntimeStateBus.

    The plugin hook remains the extension surface; this state slot is the
    production read path for later humanization providers.
    """
    if bus is None:
        return
    try:
        bus.set(
            THINKER_LAST_DECISION_SLOT,
            {
                "action": decision.action,
                "thought": decision.thought,
                "topic_intent_label": decision.topic_intent_label,
                "retrieve_mode": decision.retrieve_mode,
                "rewritten_query": decision.rewritten_query,
                "sticker": decision.sticker,
                "tone": decision.tone,
                "usage": dict(decision.usage),
            },
            scope=Scope(
                session_id=session_id,
                group_id=group_id,
                user_id=user_id,
                turn_id=turn_id,
            ),
            source=humanization_source("thinker:last_decision"),
            confidence=1.0,
        )
    except Exception as exc:
        _L.warning("thinker runtime-state write failed: {}", exc)


def build_thinker_time_text(features: dict[str, Any] | None) -> str:
    """Render a compact runtime-clock block for the pre-reply thinker."""
    if not features:
        return ""
    date = str(features.get("date", "") or "").strip()
    hour = _coerce_int(features.get("hour"))
    minute = _coerce_int(features.get("minute"))
    weekday = str(features.get("weekday_cn", "") or "").strip()
    if not date and hour is None and not weekday:
        return ""

    day_flags = ["节假日" if features.get("is_holiday") else "周末" if features.get("is_weekend") else "工作日"]
    time_part = ""
    if hour is not None:
        time_part = f"{hour:02d}:{(minute or 0):02d}"
    headline = " ".join(part for part in (date, time_part, weekday) if part)
    lines = [f"【当前时间】{headline}（{'/'.join(day_flags)}）"]

    slot_time = str(features.get("slot_time", "") or "").strip()
    slot_activity = str(features.get("slot_activity", "") or "").strip()
    slot_mood_hint = str(features.get("slot_mood_hint", "") or "").strip()
    if slot_time or slot_activity or slot_mood_hint:
        slot = " ".join(part for part in (slot_time, slot_activity) if part)
        if slot_mood_hint:
            slot = f"{slot}（心情提示：{slot_mood_hint}）" if slot else f"心情提示：{slot_mood_hint}"
        lines.append(f"当前日程：{slot}")
    return "\n".join(lines)


def write_clock_state(
    bus: Any,
    features: dict[str, Any] | None,
    *,
    session_id: str,
    group_id: str | None,
    user_id: str,
    turn_id: str,
) -> None:
    """Write the runtime clock snapshot for this thinker turn."""
    if bus is None or not features:
        return
    value = _normalize_clock_features(features)
    try:
        bus.set(
            CLOCK_CURRENT_SLOT,
            value,
            scope=Scope(
                session_id=session_id,
                group_id=group_id,
                user_id=user_id,
                turn_id=turn_id,
            ),
            source=humanization_source("runtime_clock:current"),
            confidence=1.0,
        )
    except Exception as exc:
        _L.warning("clock runtime-state write failed: {}", exc)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_clock_features(features: dict[str, Any]) -> dict[str, Any]:
    hour = _coerce_int(features.get("hour"))
    minute = _coerce_int(features.get("minute"))
    weekday = _coerce_int(features.get("weekday"))
    return {
        "date": str(features.get("date", "") or ""),
        "hour": hour if hour is not None else 0,
        "minute": minute if minute is not None else 0,
        "weekday": weekday if weekday is not None else 0,
        "weekday_cn": str(features.get("weekday_cn", "") or ""),
        "is_weekend": bool(features.get("is_weekend")),
        "is_holiday": bool(features.get("is_holiday")),
        "slot_time": str(features.get("slot_time", "") or ""),
        "slot_activity": str(features.get("slot_activity", "") or ""),
        "slot_mood_hint": str(features.get("slot_mood_hint", "") or ""),
    }


def _extract_result_text(result: dict[str, Any]) -> str:
    text = str(result.get("text", "") or "")
    if text.strip():
        return text
    content = result.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = str(block.get("text", "") or "")
                if value:
                    text_parts.append(value)
        if text_parts:
            return "".join(text_parts)
    for block in result.get("thinking_blocks", []):
        if isinstance(block, dict) and block.get("type") == "thinking":
            value = str(block.get("thinking", "") or "")
            if value.strip():
                return value
    return ""


def _usage_from_result(result: dict[str, Any]) -> dict[str, int]:
    usage = result.get("usage")
    if isinstance(usage, dict):
        return {
            "input_tokens": int(result.get("input_tokens", usage.get("input_tokens", 0)) or 0),
            "cache_read": int(result.get("cache_read", usage.get("cache_read", 0)) or 0),
            "cache_create": int(result.get("cache_create", usage.get("cache_create", 0)) or 0),
            "output_tokens": int(result.get("output_tokens", usage.get("output_tokens", 0)) or 0),
        }
    return {
        "input_tokens": int(result.get("input_tokens", 0) or 0),
        "cache_read": int(result.get("cache_read", 0) or 0),
        "cache_create": int(result.get("cache_create", 0) or 0),
        "output_tokens": int(result.get("output_tokens", 0) or 0),
    }


def _merge_usage(total: dict[str, int], delta: dict[str, int]) -> dict[str, int]:
    for key in ("input_tokens", "cache_read", "cache_create", "output_tokens"):
        total[key] = int(total.get(key, 0) or 0) + int(delta.get(key, 0) or 0)
    return total


async def think(
    api_call: Any,
    recent_messages: list[dict[str, Any]],
    max_tokens: int = 256,
    mood_text: str = "",
    affection_text: str = "",
    time_text: str = "",
    identity_name: str = "Bot",
    user_id: str = "",
    group_id: str | None = None,
    slang_hint: str = "",
) -> ThinkDecision:
    """Call the thinker LLM to decide the next action.

    Args:
        api_call: Single-arg callable that accepts an ``LLMRequest`` and
            returns the provider response dict (typically ``LLMClient._call``).
        recent_messages: Recent conversation messages in Anthropic format.
        max_tokens: Max tokens for the thinker call.
        mood_text: Current mood + schedule context (from MoodEngine).
        affection_text: Per-user relationship context (from AffectionEngine).
        time_text: Current date/slot context (from runtime_clock).
        identity_name: The bot's name from its identity config.
        user_id: User id for usage attribution.
        group_id: Group id for usage attribution (None for private chat).

    Returns:
        ThinkDecision with the chosen action and thought.
        Defaults to reply on parse failure.

    Cache layout (P0-A from prompt-cache-research-2026-05-18.md §11.3):
      static_blocks  = [identity-formatted system prompt]   ← stable across calls
      dynamic_blocks = [mood_text, affection_text]          ← per-call, at tail
    The previous in-place ``mood + system + affection`` concatenation
    invalidated the system-prompt prefix every call; moving these to the
    dynamic tail keeps the static prefix cacheable.
    """
    system_text = THINKER_SYSTEM_PROMPT.format(name=identity_name)
    user_msg = "以下是最近的聊天内容。请决定下一步行动。"
    messages = [*recent_messages, {"role": "user", "content": user_msg}]

    dynamic_blocks: list[str | dict[str, Any]] = []
    if time_text:
        dynamic_blocks.append(time_text)
    if mood_text:
        dynamic_blocks.append(mood_text)
    if affection_text:
        dynamic_blocks.append(affection_text)
    if slang_hint:
        dynamic_blocks.append(slang_hint)

    request = LLMRequest(
        task="thinker",
        user_id=user_id,
        group_id=group_id,
        static_blocks=[system_text],
        dynamic_blocks=dynamic_blocks,
        user_messages=messages,
        max_tokens=max_tokens,
        requires_capabilities=("chat",),
    )

    usage: dict[str, int] = {
        "input_tokens": 0,
        "cache_read": 0,
        "cache_create": 0,
        "output_tokens": 0,
    }
    try:
        result = await api_call(request)
        usage = _merge_usage(usage, _usage_from_result(result))
    except Exception:
        _L.warning("thinker call failed, defaulting to reply")
        return ThinkDecision(action="reply", thought="", usage=usage)

    text = _extract_result_text(result)
    decision, mode = _parse_structured_think_output_details(text)
    if decision is None:
        _L.warning("thinker structured parse failed, retrying once | raw={}", text[:200])
        try:
            retry_result = await api_call(request)
            usage = _merge_usage(usage, _usage_from_result(retry_result))
            retry_text = _extract_result_text(retry_result)
        except Exception:
            _L.warning("thinker retry call failed, defaulting to heuristic | raw={}", text[:200])
            retry_text = text
        decision, mode = _parse_structured_think_output_details(retry_text)
        text = retry_text
    if decision is None:
        decision = _heuristic_decision(text)
        mode = "heuristic" if decision is not None else "failed"
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
