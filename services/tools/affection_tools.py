"""Affection-related tools: set_nickname."""

from typing import Any

from loguru import logger

from plugins.affection.engine import AffectionEngine
from services.tools.base import Tool
from services.tools.context import ToolContext

_L = logger.bind(channel="affection")


class SetNicknameTool(Tool):
    """Allow the LLM to store a preferred nickname for a user."""

    def __init__(self, engine: AffectionEngine) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "set_nickname"

    @property
    def description(self) -> str:
        return (
            "为用户设置你称呼他时使用的昵称。"
            "当用户明确说'叫我xx''以后就叫我xx''你可以叫我xx'之类的话时调用。"
            "昵称应简短自然，如'司君''宁宁酱'，不要包含QQ号或特殊字符。"
            "私聊和群聊的昵称是分开存储的，互不影响。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户的QQ号，从对话记录的「昵称(QQ号)」中提取",
                },
                "nickname": {
                    "type": "string",
                    "description": "你之后称呼该用户时使用的昵称，如 司君、宁宁酱",
                },
                "suffix": {
                    "type": "string",
                    "description": "称呼后缀偏好，如 君、酱、同学、さん。可选，不填则自动选择。",
                },
            },
            "required": ["user_id", "nickname"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        user_id: str = kwargs["user_id"]
        nickname: str = kwargs["nickname"]
        suffix: str | None = kwargs.get("suffix")

        in_group = bool(ctx.group_id)
        try:
            if in_group:
                self._engine.set_group_nickname(user_id, nickname)
            else:
                self._engine.set_nickname(user_id, nickname)
            if suffix:
                self._engine.set_suffix(user_id, suffix)
            scope = "群聊" if in_group else "私聊"
            _L.info("nickname set | user={} nickname={} suffix={} scope={}", user_id, nickname, suffix, scope)
            return f"已记住（{scope}），以后称呼 {user_id} 为「{nickname}」"
        except Exception as e:
            _L.error("set_nickname failed | user={} error={}", user_id, e)
            return f"设置昵称失败: {e}"
