"""Explicit chat diagnostics; parsing and public metadata only, no side effects."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from typing import Literal, cast

from .policy import OwnPermissionProjection
from .rich_messages import ImageSegment, ReplySegment, TextSegment
from .stickers import _validate_id  # pyright: ignore[reportPrivateUsage] - shared asset ID contract
from .tools import Tools
from .types import Event, OperationError


@dataclass(frozen=True, slots=True)
class DiagnosticCommand:
    kind: Literal["version", "plugins", "status", "split", "question", "permissions", "save", "send"]
    text: str = ""


def parse_diagnostic_command(
    event: Event, *, bot_id: str, known_bot_ids: list[str],
) -> DiagnosticCommand | None:
    """Only direct human text is a command; reserved actions keep their owner."""
    if (
        event.scope.bot_id != bot_id
        or event.user_id in {bot_id, *known_bot_ids}
    ):
        return None
    if any(not isinstance(segment, (TextSegment, ImageSegment, ReplySegment))
           for segment in event.rich_segments):
        return None
    replies = tuple(segment for segment in event.rich_segments if isinstance(segment, ReplySegment))
    quoted = bool(event.reply_to)
    if replies or quoted:
        if (len(replies) != 1 or replies[0].message_id != event.reply_to
                or any(not isinstance(segment, (TextSegment, ReplySegment))
                       for segment in event.rich_segments)):
            return None
    text = ("".join(segment.text for segment in event.rich_segments if isinstance(segment, TextSegment))
            if event.rich_segments else event.text)
    images = sum(isinstance(segment, ImageSegment) for segment in event.rich_segments)
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    root, value = parts[0], parts[1].strip() if len(parts) == 2 else ""
    if root == "/debug" and value:
        sub = value.split(maxsplit=1)
        arg = sub[1].strip() if len(sub) == 2 else ""
        if sub[0] in {"save", "保存", "收录", "添加表情"} and images <= 2:
            return DiagnosticCommand("save", arg)
        if sub[0] in {"send", "发", "发送"} and not images and not quoted:
            if arg.lower() in {"", "gif", "动图", "动态"}:
                return DiagnosticCommand("send", arg.lower())
            try:
                _validate_id(arg)
            except ValueError:
                return None
            return DiagnosticCommand("send", arg)
    if quoted or images or event.rich_segments and event.text != text:
        return None
    if root == "/version" and not value:
        return DiagnosticCommand("version")
    if root in {"/plugins", "/p", "/plg", "/插件"} and not value:
        return DiagnosticCommand("plugins")
    if root in {"/authority", "/权限", "/授权"} and not value:
        return DiagnosticCommand("permissions")
    if root != "/debug":
        return None
    if not value:
        return DiagnosticCommand("status")
    if value.startswith(("/", "save", "send", "保存", "收录", "添加表情", "发", "发送")):
        return None
    sub = value.split(maxsplit=1)
    if sub[0] in {"split", "分段", "分割"}:
        return DiagnosticCommand("split", sub[1].strip()) if len(sub) == 2 and sub[1].strip() else None
    return DiagnosticCommand("question", value)


def permissions_reply(projection: OwnPermissionProjection) -> str:
    """Only bounded effective action names; full grants stay in the owner proof."""
    text = "本人在当前会话已授权动作：\n" + "、".join(projection.actions[:24])
    if len(projection.actions) > 24:
        text += f"\n（已截断，仅显示前24项；共{len(projection.actions)}项。）"
    return text + "\n具体目标需匹配，详情管理台。"


def diagnostic_reply(
    command: DiagnosticCommand, tools: Tools, *, active: int, waiting: int, held: int,
) -> str:
    """Render registered metadata or this scope's counts, never private state."""
    if command.kind == "version":
        return "Omubot 发行包版本：" + version("omubot-new")
    if command.kind == "plugins":
        lines = ["当前已注册工具（清单不代表调用许可）："]
        for manifest in tools.manifests():
            capabilities = ",".join(cast(list[str], manifest["requested_capabilities"]))
            lines.append(
                f"{manifest['id']} {manifest['version']} [{capabilities}] "
                f"API {manifest['api_version']}，超时 {manifest['timeout_ms']}ms"
            )
        return "\n".join(lines)
    if command.kind == "status":
        return f"本群运行摘要：活动任务 {active}，等待任务 {waiting}，暂存任务 {held}。"
    raise OperationError("invalid_diagnostic_command")
