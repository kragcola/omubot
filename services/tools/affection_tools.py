"""Affection-related tools: set_nickname."""

from collections.abc import Callable
from typing import Any, Protocol

from loguru import logger

from services.tools.base import Tool
from services.tools.context import ToolContext

_L = logger.bind(channel="affection")

_ALIAS_CONFIDENCE = 0.9


class AffectionEnginePort(Protocol):
    def set_group_nickname(
        self,
        user_id: str,
        nickname: str,
        *,
        group_id: str | None = None,
        pool_ids: list[str] | None = None,
    ) -> Any: ...

    def set_nickname(self, user_id: str, nickname: str) -> Any: ...

    def set_suffix(self, user_id: str, suffix: str) -> Any: ...


class SetNicknameTool(Tool):
    """Allow the LLM to store a preferred nickname for a user."""

    def __init__(
        self,
        engine: AffectionEnginePort,
        *,
        entity_alias_store: Any = None,
        entity_alias_store_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._engine = engine
        self._entity_alias_store = entity_alias_store
        self._entity_alias_store_getter = entity_alias_store_getter

    def _resolve_alias_store(self) -> Any:
        if self._entity_alias_store is not None:
            return self._entity_alias_store
        getter = self._entity_alias_store_getter
        if callable(getter):
            try:
                return getter()
            except Exception as exc:
                _L.warning("entity_alias_store_getter failed | err={}", exc)
                return None
        return None

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
                self._engine.set_group_nickname(user_id, nickname, group_id=ctx.group_id)
            else:
                self._engine.set_nickname(user_id, nickname)
            if suffix:
                self._engine.set_suffix(user_id, suffix)
            scope = "群聊" if in_group else "私聊"
            _L.info("nickname set | user={} nickname={} suffix={} scope={}", user_id, nickname, suffix, scope)
        except Exception as e:
            _L.error("set_nickname failed | user={} error={}", user_id, e)
            return f"设置昵称失败: {e}"

        await self._seed_entity_alias(ctx, user_id=user_id, nickname=nickname)
        return f"已记住（{scope}），以后称呼 {user_id} 为「{nickname}」"

    async def _seed_entity_alias(
        self,
        ctx: ToolContext,
        *,
        user_id: str,
        nickname: str,
    ) -> None:
        """Best-effort EntityAliasStore seed after a successful nickname set."""
        canonical = str(user_id or "").strip()
        if not canonical.isdigit() or int(canonical) <= 0:
            return
        # Normalize leading zeros while preserving positive decimal digits.
        canonical = str(int(canonical))
        store = self._resolve_alias_store()
        if store is None:
            return
        alias = str(nickname or "").strip()
        if not alias:
            return
        if ctx.group_id:
            scope = "group"
            scope_id = str(ctx.group_id)
        else:
            scope = "user"
            scope_id = canonical
        try:
            await store.observe(
                entity_key=f"user:qq:{canonical}",
                alias=alias,
                scope=scope,
                scope_id=scope_id,
                confidence=_ALIAS_CONFIDENCE,
                source="affection_nickname",
            )
        except Exception as exc:
            _L.warning(
                "affection nickname alias seed failed | user={} alias={} err={}",
                canonical,
                alias,
                exc,
            )
