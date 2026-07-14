"""Contract tests for the global OneBot group outbound access guard."""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kernel.config import GroupAccessConfig, GroupConfig, GroupOverride
from plugins.calendar_context.birthday_greeter import BirthdayGreeter
from services.tools.context import ToolContext
from services.tools.group_admin import SendGroupMsgTool

ALLOWED_GROUP_ID = 100
BLOCKED_GROUP_ID = 200


class _NoneBotLike:
    """Mimic BaseBot's dynamic API attributes without predefining guard markers."""

    def __init__(self) -> None:
        self.self_id = "12345"
        self.call_api = AsyncMock(return_value={"status": "ok"})

    def __getattr__(self, _name: str) -> Any:
        return lambda *args, **kwargs: None


class _ForwardingBot:
    def __init__(self) -> None:
        self.self_id = "12345"
        self.call_api = AsyncMock(return_value={"status": "ok"})

    async def send_group_msg(self, *, group_id: int, message: Any) -> Any:
        return await self.call_api(
            "send_group_msg",
            group_id=group_id,
            message=message,
        )


def _new_guard(policy: GroupAccessConfig) -> Any:
    """Load the guard lazily so an absent module is reported as a RED assertion."""
    try:
        module = importlib.import_module("services.group.outbound_access_guard")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "services.group.outbound_access_guard.OutboundGroupAccessGuard "
            "does not exist yet",
            pytrace=False,
        )
        raise AssertionError from exc  # pragma: no cover
    return module.OutboundGroupAccessGuard(policy)


def _wrapped_bot(
    *, allowed_groups: list[int] | None = None
) -> tuple[SimpleNamespace, AsyncMock, Any]:
    policy = GroupAccessConfig(
        mode="whitelist",
        whitelist=allowed_groups or [ALLOWED_GROUP_ID],
    )
    original_call_api = AsyncMock(return_value={"status": "ok"})
    bot = SimpleNamespace(self_id="12345", call_api=original_call_api)
    guard = _new_guard(policy)
    assert guard.wrap_bot(bot) is True
    return bot, original_call_api, guard


@pytest.mark.asyncio
async def test_send_group_msg_to_non_whitelisted_group_is_rejected() -> None:
    bot, original_call_api, _guard = _wrapped_bot()

    with pytest.raises(PermissionError):
        await bot.call_api(
            "send_group_msg",
            group_id=BLOCKED_GROUP_ID,
            message="must not leave the bot",
        )

    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_send_msg_to_non_whitelisted_group_is_rejected() -> None:
    bot, original_call_api, _guard = _wrapped_bot()

    with pytest.raises(PermissionError):
        await bot.call_api(
            "send_msg",
            message_type="group",
            group_id=BLOCKED_GROUP_ID,
            message="must not leave the bot",
        )

    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_group_forward_msg_to_non_whitelisted_group_is_rejected() -> None:
    bot, original_call_api, _guard = _wrapped_bot()

    with pytest.raises(PermissionError):
        await bot.call_api(
            "send_group_forward_msg",
            group_id=BLOCKED_GROUP_ID,
            messages=[],
        )

    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("send_group_notice", {"group_id": BLOCKED_GROUP_ID, "content": "notice"}),
        (
            "send_forward_msg",
            {"message_type": "group", "group_id": BLOCKED_GROUP_ID, "messages": []},
        ),
    ],
)
async def test_other_group_send_shapes_are_also_rejected(
    action: str,
    params: dict[str, Any],
) -> None:
    bot, original_call_api, _guard = _wrapped_bot()

    with pytest.raises(PermissionError):
        await bot.call_api(action, **params)

    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("send_group_msg", {"group_id": ALLOWED_GROUP_ID, "message": "hello"}),
        ("send_group_forward_msg", {"group_id": ALLOWED_GROUP_ID, "messages": []}),
        ("send_group_notice", {"group_id": ALLOWED_GROUP_ID, "content": "notice"}),
        (
            "send_msg",
            {"message_type": "group", "group_id": ALLOWED_GROUP_ID, "message": "hello"},
        ),
        (
            "send_forward_msg",
            {"message_type": "group", "group_id": ALLOWED_GROUP_ID, "messages": []},
        ),
    ],
)
async def test_whitelisted_group_sends_reach_original_call_api(
    action: str,
    params: dict[str, Any],
) -> None:
    bot, original_call_api, _guard = _wrapped_bot()

    result = await bot.call_api(action, **params)

    assert result == {"status": "ok"}
    original_call_api.assert_awaited_once_with(action, **params)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("send_private_msg", {"user_id": 300, "message": "hello"}),
        ("send_msg", {"message_type": "private", "user_id": 300, "message": "hello"}),
        ("send_forward_msg", {"message_type": "private", "user_id": 300, "messages": []}),
        ("get_group_list", {}),
    ],
)
async def test_private_sends_and_read_only_apis_are_not_filtered(
    action: str,
    params: dict[str, Any],
) -> None:
    bot, original_call_api, _guard = _wrapped_bot()

    result = await bot.call_api(action, **params)

    assert result == {"status": "ok"}
    original_call_api.assert_awaited_once_with(action, **params)


_GROUP_SEND_ACTIONS = [
    ("send_group_msg", {"message": "hello"}),
    ("send_group_forward_msg", {"messages": []}),
    ("send_group_notice", {"content": "notice"}),
    ("send_msg", {"message_type": "group", "message": "hello"}),
    ("send_forward_msg", {"message_type": "group", "messages": []}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "params"), _GROUP_SEND_ACTIONS)
@pytest.mark.parametrize("bad_group_id", [None, "", "not-a-group-id", {"bad": "shape"}])
async def test_group_sends_with_missing_or_malformed_group_id_fail_closed(
    action: str,
    params: dict[str, Any],
    bad_group_id: Any,
) -> None:
    bot, original_call_api, _guard = _wrapped_bot()
    call_params = dict(params)
    if bad_group_id is not None:
        call_params["group_id"] = bad_group_id

    with pytest.raises(PermissionError):
        await bot.call_api(action, **call_params)

    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_wrap_is_idempotent() -> None:
    bot, original_call_api, guard = _wrapped_bot()

    assert guard.wrap_bot(bot) is False
    await bot.call_api(
        "send_group_msg",
        group_id=ALLOWED_GROUP_ID,
        message="only one wrapper should run",
    )

    original_call_api.assert_awaited_once_with(
        "send_group_msg",
        group_id=ALLOWED_GROUP_ID,
        message="only one wrapper should run",
    )


@pytest.mark.asyncio
async def test_muted_group_send_is_rejected_at_shared_protocol_boundary() -> None:
    guard_type = type(_new_guard(GroupAccessConfig()))
    assert "is_group_muted" in inspect.signature(guard_type).parameters
    original_call_api = AsyncMock(return_value={"status": "ok"})
    bot = SimpleNamespace(self_id="12345", call_api=original_call_api)
    checked: list[str] = []

    def is_group_muted(group_id: str) -> bool:
        checked.append(group_id)
        return True

    guard = guard_type(
        GroupAccessConfig(mode="whitelist", whitelist=[ALLOWED_GROUP_ID]),
        is_group_muted=is_group_muted,
    )
    assert guard.wrap_bot(bot) is True

    with pytest.raises(PermissionError):
        await bot.call_api(
            "send_group_msg",
            group_id=ALLOWED_GROUP_ID,
            message="must stay local while muted",
        )

    assert checked == [str(ALLOWED_GROUP_ID)]
    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_mute_checker_failure_blocks_send_fail_closed() -> None:
    original_call_api = AsyncMock(return_value={"status": "ok"})
    bot = SimpleNamespace(self_id="12345", call_api=original_call_api)

    def broken_mute_checker(_group_id: str) -> bool:
        raise RuntimeError("scheduler unavailable")

    guard = type(_new_guard(GroupAccessConfig()))(
        GroupAccessConfig(mode="whitelist", whitelist=[ALLOWED_GROUP_ID]),
        is_group_muted=broken_mute_checker,
    )
    assert guard.wrap_bot(bot) is True

    with pytest.raises(PermissionError):
        await bot.call_api(
            "send_group_msg",
            group_id=ALLOWED_GROUP_ID,
            message="must stay local when mute state is unknown",
        )

    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_admin_cross_group_send_cannot_bypass_target_mute() -> None:
    guard_type = type(_new_guard(GroupAccessConfig()))
    assert "is_group_muted" in inspect.signature(guard_type).parameters
    bot = _ForwardingBot()
    original_call_api = bot.call_api
    guard = guard_type(
        GroupAccessConfig(mode="whitelist", whitelist=[ALLOWED_GROUP_ID]),
        is_group_muted=lambda group_id: group_id == str(ALLOWED_GROUP_ID),
    )
    assert guard.wrap_bot(bot) is True
    tool = SendGroupMsgTool({"admin"})

    with pytest.raises(PermissionError):
        await tool.execute(
            ToolContext(bot=bot, user_id="admin", group_id="999"),
            group_id=str(ALLOWED_GROUP_ID),
            message="must stay local while target is muted",
        )

    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group_config",
    [
        pytest.param(
            GroupConfig(
                access=GroupAccessConfig(mode="blacklist", blacklist=[ALLOWED_GROUP_ID]),
            ),
            id="blocked",
        ),
        pytest.param(
            GroupConfig(
                access=GroupAccessConfig(mode="whitelist", whitelist=[ALLOWED_GROUP_ID]),
                overrides={ALLOWED_GROUP_ID: GroupOverride(presence_mode="off")},
            ),
            id="off",
        ),
        pytest.param(
            GroupConfig(
                access=GroupAccessConfig(mode="whitelist", whitelist=[ALLOWED_GROUP_ID]),
                overrides={ALLOWED_GROUP_ID: GroupOverride(presence_mode="silent_learn")},
            ),
            id="silent-learn",
        ),
    ],
)
async def test_birthday_send_cannot_bypass_non_active_group_policy(
    tmp_path,
    group_config: GroupConfig,
) -> None:
    bot = _ForwardingBot()
    original_call_api = bot.call_api
    guard = type(_new_guard(GroupAccessConfig()))(group_config)
    assert guard.wrap_bot(bot) is True
    greeter = BirthdayGreeter(tmp_path / "birthdays.json")
    greeter.add_member(
        "123",
        "测试成员",
        greeter._today_mmdd(),
        [str(ALLOWED_GROUP_ID)],
    )

    greeted = await greeter.check_and_greet(bot)

    assert greeted == []
    assert greeter.sent_log == {}
    original_call_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_dynamic_unknown_attributes_do_not_look_like_existing_wrap_marker() -> None:
    policy = GroupAccessConfig(mode="whitelist", whitelist=[ALLOWED_GROUP_ID])
    bot = _NoneBotLike()
    original_call_api = bot.call_api
    guard = _new_guard(policy)

    assert guard.wrap_bot(bot) is True
    result = await bot.call_api(
        "send_group_msg",
        group_id=ALLOWED_GROUP_ID,
        message="allowed",
    )

    assert result == {"status": "ok"}
    original_call_api.assert_awaited_once_with(
        "send_group_msg",
        group_id=ALLOWED_GROUP_ID,
        message="allowed",
    )
