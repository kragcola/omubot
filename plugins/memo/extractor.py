"""Post-turn memory extraction: observe and record typed cards after each conversation turn.

Unlike the old MaiBot's batch HippoMemorizer (topic-clustered, delayed batches),
this runs incrementally after every turn — observations are available immediately
on the next conversation, not minutes/hours later.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from services.memory.card_store import CardStore, NewCard

_L = logger.bind(channel="debug")

_EXTRACT_SYSTEM = """你是一个信息观察助手。分析以下一轮对话，提取关于用户的需要记住的新事实。

规则：
- 只提取事实性信息：称呼偏好、性格特点、兴趣爱好、身份背景、重要观点、关系变化
- 每条一行，格式："[category] 事实描述"
- category 必须是以下之一：
  preference（偏好/喜好/称呼）| boundary（边界/不喜欢的）| relationship（关系/角色）
  | event（发生的重要事情）| promise（承诺/答应了什么）| fact（一般事实/背景/兴趣）| status（当前状态/情绪）
- 不要记流水账（如"用户打了招呼"、"用户问了问题"）
- 不要重复显而易见的信息（如"用户正在和助手聊天"）
- 如果没有值得记录的新信息，输出"无"（就一个字）
- 最多输出3条，宁缺毋滥

示例输出：
[preference] 用户偏好被称呼为"帆酱"
[fact] 用户喜欢玩音游，最近在玩啤酒烧烤
[relationship] 用户是群管理员，负责维护秩序"""


class MemoExtractor:
    """Extract observations after each conversation turn, writing typed cards to CardStore."""

    def __init__(
        self,
        card_store: CardStore,
        api_call: Any,
    ) -> None:
        self._store = card_store
        self._call = api_call

    async def extract_after_turn(
        self,
        user_id: str,
        group_id: str | None,
        user_msg: str,
        bot_reply: str,
    ) -> None:
        """Extract key facts from a turn and write typed cards.

        Runs as a fire-and-forget background task — failures are logged
        but never surface to the user.
        """
        if not user_id or user_id == "0":
            return

        user_msg_clean = user_msg[:300].replace("\n", " ")
        bot_reply_clean = bot_reply[:300].replace("\n", " ")
        conversation = (
            f"用户({user_id}): {user_msg_clean}\n"
            f"助手: {bot_reply_clean}"
        )

        try:
            result = await self._call(
                [{"type": "text", "text": _EXTRACT_SYSTEM}],
                [{"role": "user", "content": conversation}],
                max_tokens=256,
            )
        except Exception:
            _L.debug("memo extractor LLM call failed | user={}", user_id)
            return

        text: str = result.get("text", "").strip()
        if not text or text == "无":
            return

        written = 0
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("[") or "]" not in line:
                continue
            bracket_end = line.index("]")
            category = line[1:bracket_end].strip()
            content = line[bracket_end + 1:].strip()
            if not content or content == "无":
                continue
            try:
                await self._store.add_card(NewCard(
                    category=category,
                    scope="user",
                    scope_id=user_id,
                    content=content,
                    confidence=0.6,
                    source="extractor",
                ))
                written += 1
            except (ValueError, Exception):
                _L.warning("memo extractor add_card failed | user={} category={}", user_id, category)

        if written:
            _L.debug(
                "cards extracted | user={} count={}",
                user_id, written,
            )
