"""Build bounded OneBot messages without interpreting model text as CQ codes."""

from __future__ import annotations

import base64
import json
import re
from typing import Literal

from pydantic import JsonValue

from .echo import RichEchoPayload
from .rich_messages import AtSegment, FaceSegment, TextSegment
from .types import MAX_STICKER_BYTES as _MAX_STICKER_BYTES
from .types import (
    ContactAddressee,
    ConversationScope,
    OperationError,
    PrivateScope,
    QQTransportError,
    QQTransportEvidence,
    QQWriteGrant,
    QQWriteSpec,
    ReplyTarget,
    Scope,
    StickerImage,
    qq_params_hash,
)

MAX_STICKER_BYTES = _MAX_STICKER_BYTES
_RICH_MEDIA_FAILURE = re.compile(
    r"^EventChecker Failed: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg "
    r"ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate EventRet:\s*"
    r'\{\s*"result"\s*:\s*(-?\d{1,10})\s*,\s*'
    r'"errMsg"\s*:\s*"rich media transfer failed"\s*\}\s*$'
)


def _sticker_file(image: StickerImage) -> str:
    """Encode validated core bytes at the OneBot wire boundary only."""

    return "base64://" + base64.b64encode(image.data).decode("ascii")


def _reply_segments(scope: ConversationScope, target: ReplyTarget | None) -> list[JsonValue]:
    if target is None:
        return []
    if target.scope != scope or re.fullmatch(r"-?\d+", target.message_id) is None:
        raise OperationError("invalid_reply_target")
    return [{"type": "reply", "data": {"id": target.message_id}}]


def send_action(scope: ConversationScope) -> Literal["send_group_msg", "send_private_msg"]:
    return "send_group_msg" if scope.kind == "group" else "send_private_msg"


def _recipient(scope: ConversationScope) -> dict[str, JsonValue]:
    if scope.kind == "group":
        return {"group_id": scope.group_id}
    return {"user_id": scope.private_user_id}


def send_params(
    scope: ConversationScope, text: str, target: ReplyTarget | None,
    *, addressee: ContactAddressee | None = None,
) -> dict[str, JsonValue]:
    if addressee is not None:
        if (target is not None or scope.kind != "group" or addressee.scope != scope
                or re.fullmatch(r"[0-9]+", addressee.user_id) is None):
            raise OperationError("invalid_contact_addressee")
        return {**_recipient(scope), "message": [
            {"type": "at", "data": {"qq": addressee.user_id}},
            {"type": "text", "data": {"text": text}},
        ], "auto_escape": False}
    if target is None:
        return {**_recipient(scope), "message": text, "auto_escape": True}
    message: list[JsonValue] = _reply_segments(scope, target)
    message.append({"type": "text", "data": {"text": text}})
    return {
        **_recipient(scope),
        "message": message,
        "auto_escape": False,
    }


def sticker_send_params(
    scope: ConversationScope,
    image: StickerImage,
    target: ReplyTarget | None,
) -> dict[str, JsonValue]:
    """Build one reply-optional, image-only message for the validated recipient."""

    if type(image) is not StickerImage:
        raise TypeError("image must be a StickerImage")
    message: list[JsonValue] = _reply_segments(scope, target)
    message.append({"type": "image", "data": {
        "file": _sticker_file(image), "sub_type": 1, "summary": "[动画表情]",
    }})
    return {
        **_recipient(scope),
        "message": message,
        "auto_escape": False,
    }


def echo_send_params(
    scope: ConversationScope, payload: RichEchoPayload, target: ReplyTarget | None,
) -> dict[str, JsonValue]:
    """Encode only admitted original segments and validated local pixels."""
    payload.assert_source(payload.event)
    if scope != payload.event.scope:
        raise OperationError("echo_scope_changed")
    message: list[JsonValue] = _reply_segments(scope, target)
    for segment in payload.segments:
        if isinstance(segment, TextSegment):
            message.append({"type": "text", "data": {"text": segment.text}})
        elif isinstance(segment, FaceSegment):
            message.append({"type": "face", "data": {"id": segment.face_id}})
        elif isinstance(segment, AtSegment):
            message.append({"type": "at", "data": {"qq": segment.target_id}})
        else:
            message.append({"type": "image", "data": {
                "file": _sticker_file(segment.image), "sub_type": 1 if segment.is_sticker else 0,
            }})
    return {**_recipient(scope), "message": message, "auto_escape": False}


ONEBOT_READ_APIS = frozenset({
    "get_login_info", "get_status", "get_msg", "get_image", "get_group_msg_history",
})
ONEBOT_WRITE_APIS = frozenset({
    "send_group_msg", "send_private_msg", "send_poke", "set_msg_emoji_like", "set_group_ban",
})


def canonical_onebot_params(params: dict[str, JsonValue]) -> str:
    """One stable wire representation shared by preparation and consumption."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _wire_mention_targets(params: dict[str, JsonValue]) -> tuple[str, ...] | None:
    message = params.get("message")
    if isinstance(message, str):
        return () if params.get("auto_escape") is True else None
    if not isinstance(message, list):
        return None
    targets: list[str] = []
    for segment in message:
        if not isinstance(segment, dict):
            return None
        if segment.get("type") == "at":
            data = segment.get("data")
            if not isinstance(data, dict):
                return None
            recipient = data.get("qq")
            if isinstance(recipient, str):
                targets.append(recipient)
            elif type(recipient) is int:
                targets.append(str(recipient))
            else:
                return None
        elif segment.get("type") not in {"text", "reply", "image", "face"}:
            return None
    return tuple(targets)


def qq_write_spec(scope: ConversationScope, api: str, params: dict[str, JsonValue]) -> QQWriteSpec:
    """Describe an explicitly supported write and bind its actual wire recipient."""
    if api not in ONEBOT_WRITE_APIS:
        raise OperationError("unsupported_onebot_api")
    if api == "send_private_msg":
        if scope.kind != "private" or params.get("user_id") != scope.private_user_id:
            raise OperationError("qq_write_scope_mismatch")
    elif api != "set_msg_emoji_like":
        if scope.kind != "group" or params.get("group_id") != scope.group_id:
            raise OperationError("qq_write_scope_mismatch")
    elif scope.kind != "group":
        raise OperationError("qq_write_scope_mismatch")
    return QQWriteSpec(
        account_id=scope.bot_id, scope_key=scope.key, api=api,
        params_hash=qq_params_hash(params), cost=1,
        mention_targets=_wire_mention_targets(params) if api == "send_group_msg" else (),
    )


def granted_write_spec(
    bot_id: str, api: str, params: dict[str, JsonValue], grant: QQWriteGrant | None,
) -> QQWriteSpec | None:
    """Classify API locally; a read declaration cannot widen this allowlist."""
    if api in ONEBOT_READ_APIS:
        return None
    if api not in ONEBOT_WRITE_APIS:
        raise OperationError("unsupported_onebot_api")
    if grant is None:
        raise OperationError("qq_write_grant_required")
    account_id, kind, target_id = grant.spec.scope_key
    if account_id != bot_id or grant.spec.account_id != bot_id:
        raise OperationError("wrong_bot")
    scope: ConversationScope = (
        Scope(bot_id=account_id, group_id=target_id) if kind == "group"
        else PrivateScope(bot_id=account_id, kind="private", private_user_id=target_id)
    )
    return qq_write_spec(scope, api, params)


class OneBotEnvelope(dict[str, JsonValue]):
    """Wire dictionary plus bounded transport metadata, never upstream error text."""

    def __init__(self, body: dict[str, JsonValue], evidence: QQTransportEvidence) -> None:
        super().__init__(body)
        self.evidence = evidence


def onebot_response_evidence(
    body: dict[str, JsonValue], transport: Literal["http", "ws"], *, http_status: int | None = None,
) -> QQTransportEvidence:
    status = body.get("status")
    retcode = body.get("retcode")
    platform_result = None
    reliable_code = None
    data = body.get("data")
    if isinstance(data, dict):
        result = data.get("result")
        if type(result) is int and -(2**31) <= result < 2**31:
            platform_result = result
    message = body.get("message")
    if isinstance(message, str) and (match := _RICH_MEDIA_FAILURE.fullmatch(message)):
        result = int(match[1])
        if -(2**31) <= result < 2**31:
            platform_result = result
            reliable_code = "rich_media_transfer_failed"
    return QQTransportEvidence(
        transport=transport, phase="acknowledged", http_status=http_status,
        onebot_status=status if status in ("ok", "failed", "async") else None,
        retcode=retcode if type(retcode) is int else None,
        platform_result=platform_result, reliable_code=reliable_code,
    )


def onebot_response_error(
    code: str, body: dict[str, JsonValue], transport: Literal["http", "ws"],
) -> QQTransportError:
    evidence = (body.evidence if isinstance(body, OneBotEnvelope)
                else onebot_response_evidence(body, transport))
    return QQTransportError(code, evidence)
