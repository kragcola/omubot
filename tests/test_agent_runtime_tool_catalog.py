"""P2 contracts for migrating the real ToolRegistry catalog to Tool ABI v2."""

from typing import Any, cast

import pytest

from kernel.types import (
    Tool,
    ToolApproval,
    ToolConcurrency,
    ToolContext,
    ToolEffect,
    ToolIdempotency,
    ToolRetryPolicy,
)
from plugins.slang.plugin import SlangLookupTool
from services.media.sticker_store import StickerStore
from services.memory.card_store import CardStore
from services.slang.store import SlangStore
from services.tools.affection_tools import SetNicknameTool
from services.tools.datetime_tool import DateTimeTool
from services.tools.group_admin import MuteUserTool, SendGroupMsgTool, SetTitleTool
from services.tools.http_api import HttpApiTool
from services.tools.interaction_tools import QQInteractionTool
from services.tools.memo_tools import CardLookupTool, CardUpdateTool
from services.tools.sticker_tools import (
    ManageStickerTool,
    SaveStickerTool,
    SendStickerTool,
)
from services.tools.web_fetch import WebFetchTool
from services.tools.web_search import WebSearchTool

RUNTIME_TOOL_NAMES = frozenset(
    {
        "get_datetime",
        "lookup_cards",
        "update_cards",
        "set_nickname",
        "slang_lookup",
        "web_fetch",
        "web_search",
        "http_api",
        "mute_user",
        "set_title",
        "send_group_msg",
        "save_sticker",
        "manage_sticker",
        "send_sticker",
        "poke_user",
        "react_to_message",
    }
)


def _local_read_tools() -> tuple[Tool, ...]:
    return (
        DateTimeTool(),
        CardLookupTool(cast(CardStore, object())),
        SlangLookupTool(cast(SlangStore, object())),
    )


class _AffectionEngineStub:
    def set_group_nickname(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_nickname(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_suffix(self, *args: Any, **kwargs: Any) -> None:
        return None


def _local_write_tools() -> tuple[Tool, ...]:
    return (
        CardUpdateTool(cast(CardStore, object())),
        SetNicknameTool(_AffectionEngineStub()),
        SaveStickerTool(cast(StickerStore, object()), superusers=set()),
        ManageStickerTool(cast(StickerStore, object()), superusers=set()),
    )


def _external_read_tools() -> tuple[Tool, ...]:
    return (WebSearchTool(), WebFetchTool())


@pytest.mark.parametrize(
    ("tool_name", "owner", "required_scope", "data_classification"),
    [
        ("get_datetime", "datetime", "time:read", "local_time"),
        ("lookup_cards", "memo", "memory:read", "memory_pii"),
        ("slang_lookup", "slang", "slang:read", "group_slang"),
    ],
)
def test_low_risk_local_reads_have_explicit_governed_specs(
    tool_name: str,
    owner: str,
    required_scope: str,
    data_classification: str,
) -> None:
    tool = next(tool for tool in _local_read_tools() if tool.name == tool_name)
    schema_before: dict[str, Any] = tool.to_openai_tool()

    spec = tool.spec

    assert spec.name in RUNTIME_TOOL_NAMES
    assert spec.name == tool.name
    assert spec.description == tool.description
    assert spec.input_schema == tool.parameters
    assert spec.owner == owner
    assert spec.effect is ToolEffect.READ
    assert spec.required_scopes == (required_scope,)
    assert spec.approval is ToolApproval.NEVER
    assert spec.idempotency is ToolIdempotency.NOT_NEEDED
    assert spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
    assert spec.concurrency is ToolConcurrency.PARALLEL
    assert spec.binding_required is False
    assert spec.data_classification == (data_classification,)
    assert tool.to_openai_tool() == schema_before


@pytest.mark.parametrize(
    ("tool_name", "owner", "required_scope", "data_classification"),
    [
        ("update_cards", "memo", "memory:write", "memory_pii"),
        ("set_nickname", "affection", "affection:write", "user_preference"),
        ("save_sticker", "sticker", "sticker:write", "local_sticker"),
        ("manage_sticker", "sticker", "sticker:write", "local_sticker"),
    ],
)
def test_local_write_tools_have_explicit_governed_specs_and_bindings(
    tool_name: str,
    owner: str,
    required_scope: str,
    data_classification: str,
) -> None:
    tool = next(tool for tool in _local_write_tools() if tool.name == tool_name)
    schema_before: dict[str, Any] = tool.to_openai_tool()

    spec = tool.spec

    assert spec.name in RUNTIME_TOOL_NAMES
    assert spec.name == tool.name
    assert spec.description == tool.description
    assert spec.input_schema == tool.parameters
    assert spec.owner == owner
    assert spec.effect is ToolEffect.WRITE_LOCAL
    assert spec.required_scopes == (required_scope,)
    assert spec.approval is ToolApproval.POLICY
    assert spec.idempotency is ToolIdempotency.RECONCILE_ONLY
    assert spec.retry_policy is ToolRetryPolicy.NEVER
    assert spec.concurrency is ToolConcurrency.KEYED_SERIAL
    assert spec.binding_required is True
    assert spec.data_classification == (data_classification,)
    assert tool.to_openai_tool() == schema_before


@pytest.mark.parametrize(
    ("tool_name", "owner", "required_scope"),
    [
        ("web_search", "web_search", "network:search"),
        ("web_fetch", "web_fetch", "network:fetch"),
    ],
)
def test_external_read_tools_have_explicit_governed_specs_and_bindings(
    tool_name: str,
    owner: str,
    required_scope: str,
) -> None:
    tool = next(tool for tool in _external_read_tools() if tool.name == tool_name)
    schema_before: dict[str, Any] = tool.to_openai_tool()

    spec = tool.spec

    assert spec.name in RUNTIME_TOOL_NAMES
    assert spec.name == tool.name
    assert spec.description == tool.description
    assert spec.input_schema == tool.parameters
    assert spec.owner == owner
    assert spec.effect is ToolEffect.EXTERNAL_READ
    assert spec.required_scopes == (required_scope,)
    assert spec.approval is ToolApproval.NEVER
    assert spec.idempotency is ToolIdempotency.NOT_NEEDED
    assert spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
    assert spec.concurrency is ToolConcurrency.PARALLEL
    assert spec.timeout_ms == 15_000
    assert spec.binding_required is True
    assert spec.data_classification == ("public_web",)
    assert tool.to_openai_tool() == schema_before


def test_external_read_bindings_use_fixed_search_and_canonical_fetch_origin() -> None:
    search = WebSearchTool()
    fetch = WebFetchTool()
    ctx = ToolContext(user_id="10001")

    search_binding = search.bind_invocation(ctx, {"query": "latest docs"})
    fetch_binding = fetch.bind_invocation(
        ctx,
        {"url": "HTTPS://Example.COM:443/docs?q=1#section"},
    )

    assert (search_binding.target_ref, search_binding.concurrency_key) == (
        "network:web-search",
        "",
    )
    assert (fetch_binding.target_ref, fetch_binding.concurrency_key) == (
        "network:web-origin:https://example.com",
        "",
    )
    assert fetch.parameters["properties"]["url"]["maxLength"] == 8192


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "https://user:secret@example.com/private",
        "http://localhost:8080/admin",
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://198.18.0.1/internal",
        "http://[::ffff:127.0.0.1]/admin",
        r"https://example.com\@127.0.0.1/admin",
    ],
)
def test_web_fetch_binding_rejects_non_public_http_targets(url: str) -> None:
    with pytest.raises(ValueError, match="public HTTP"):
        WebFetchTool().bind_invocation(ToolContext(user_id="10001"), {"url": url})


def test_http_api_remains_fail_closed_until_read_write_transport_is_split() -> None:
    mixed = HttpApiTool()
    post_only = HttpApiTool(allowed_methods=["POST"])
    empty = HttpApiTool(allowed_methods=[])
    partially_invalid = HttpApiTool(allowed_methods=["GET", "DELETE"])

    assert mixed.spec.effect is ToolEffect.LEGACY_UNCLASSIFIED
    assert mixed.spec.binding_required is False
    assert post_only.spec.effect is ToolEffect.LEGACY_UNCLASSIFIED
    assert post_only.spec.binding_required is False
    assert empty.spec.effect is ToolEffect.LEGACY_UNCLASSIFIED
    assert partially_invalid.spec.effect is ToolEffect.LEGACY_UNCLASSIFIED


def test_http_api_get_only_has_governed_read_spec_and_origin_binding() -> None:
    tool = HttpApiTool(allowed_methods=["GET"])
    schema_before = tool.to_openai_tool()

    spec = tool.spec
    binding = tool.bind_invocation(
        ToolContext(user_id="10001"),
        {
            "method": "GET",
            "url": "HTTPS://API.Example.COM:443/v1/data?q=1#fragment",
            "headers": {"Accept": "application/json"},
        },
    )

    assert spec.owner == "http_api"
    assert "仅支持 GET" in tool.description
    assert spec.effect is ToolEffect.EXTERNAL_READ
    assert spec.required_scopes == ("network:http-api:read",)
    assert spec.approval is ToolApproval.NEVER
    assert spec.idempotency is ToolIdempotency.NOT_NEEDED
    assert spec.retry_policy is ToolRetryPolicy.SAFE_TRANSIENT
    assert spec.concurrency is ToolConcurrency.PARALLEL
    assert spec.timeout_ms == 15_000
    assert spec.binding_required is True
    assert spec.data_classification == ("public_api",)
    assert (binding.target_ref, binding.concurrency_key) == (
        "network:http-api-origin:https://api.example.com",
        "",
    )
    assert "body" not in tool.parameters["properties"]
    assert tool.to_openai_tool() == schema_before


@pytest.mark.parametrize(
    "arguments",
    [
        {"method": "POST", "url": "https://api.example.com/data"},
        {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {"Authorization": "Bearer model-secret"},
        },
        {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {"Host": "internal.service"},
        },
        {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {"Accept": "application/json\r\nX-Injected: yes"},
        },
    ],
)
def test_http_api_get_binding_rejects_write_or_unsafe_headers(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        HttpApiTool(allowed_methods=["GET"]).bind_invocation(
            ToolContext(user_id="10001"),
            arguments,
        )


@pytest.mark.parametrize(
    ("tool", "effect", "scope", "classification"),
    [
        (
            MuteUserTool({"admin"}),
            ToolEffect.EXTERNAL_REVERSIBLE,
            "onebot:group:moderate",
            "qq_group_state",
        ),
        (
            SetTitleTool({"admin"}),
            ToolEffect.EXTERNAL_REVERSIBLE,
            "onebot:group:title",
            "qq_group_state",
        ),
        (
            SendGroupMsgTool({"admin"}),
            ToolEffect.EXTERNAL_IRREVERSIBLE,
            "onebot:group:message",
            "qq_message",
        ),
    ],
)
def test_group_admin_tools_have_explicit_external_effect_specs(
    tool: Tool,
    effect: ToolEffect,
    scope: str,
    classification: str,
) -> None:
    spec = tool.spec

    assert spec.owner == "group_admin"
    assert spec.effect is effect
    assert spec.required_scopes == (scope,)
    assert spec.approval is ToolApproval.ALWAYS
    assert spec.idempotency is ToolIdempotency.RECONCILE_ONLY
    assert spec.retry_policy is ToolRetryPolicy.NEVER
    assert spec.concurrency is ToolConcurrency.KEYED_SERIAL
    assert spec.binding_required is True
    assert spec.data_classification == (classification,)


def test_group_admin_bindings_use_trusted_admin_and_onebot_targets() -> None:
    ctx = ToolContext(bot=object(), user_id="admin", group_id="20002")

    mute = MuteUserTool({"admin"}).bind_invocation(
        ctx,
        {"user_id": "30003", "duration": 60},
    )
    title = SetTitleTool({"admin"}).bind_invocation(
        ctx,
        {"user_id": "30003", "title": "群星"},
    )
    message = SendGroupMsgTool({"admin"}).bind_invocation(
        ctx,
        {"group_id": "40004", "message": "notice"},
    )

    assert (mute.target_ref, mute.concurrency_key) == (
        "onebot:group:20002:member:30003:mute",
        "onebot:group:20002:member:30003:mute",
    )
    assert (title.target_ref, title.concurrency_key) == (
        "onebot:group:20002:member:30003:title",
        "onebot:group:20002:member:30003:title",
    )
    assert (message.target_ref, message.concurrency_key) == (
        "onebot:group:40004:message",
        "onebot:group:40004:message",
    )


@pytest.mark.parametrize(
    ("tool", "ctx", "arguments"),
    [
        (
            MuteUserTool({"admin"}),
            ToolContext(bot=object(), user_id="regular", group_id="20002"),
            {"user_id": "30003", "duration": 60},
        ),
        (
            SetTitleTool({"admin"}),
            ToolContext(bot=object(), user_id="admin", group_id=None),
            {"user_id": "30003", "title": "群星"},
        ),
        (
            SendGroupMsgTool({"admin"}),
            ToolContext(bot=None, user_id="admin", group_id="20002"),
            {"group_id": "40004", "message": "notice"},
        ),
        (
            SendGroupMsgTool({"admin"}),
            ToolContext(bot=object(), user_id="admin", group_id="20002"),
            {"group_id": "not-a-group", "message": "notice"},
        ),
    ],
)
def test_group_admin_bindings_reject_untrusted_context_or_target(
    tool: Tool,
    ctx: ToolContext,
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        tool.bind_invocation(ctx, arguments)


def test_poke_user_has_external_effect_spec_and_trusted_binding() -> None:
    tool = QQInteractionTool("poke")
    ctx = ToolContext(
        bot=object(),
        user_id="200",
        group_id="100",
        extra={"humanization_profile": "performance"},
    )

    spec = tool.spec
    binding = tool.bind_invocation(ctx, {"user_id": "300", "group_id": "100"})

    assert spec.owner == "qq_interaction"
    assert spec.effect is ToolEffect.EXTERNAL_IRREVERSIBLE
    assert spec.required_scopes == ("onebot:interaction:poke",)
    assert spec.approval is ToolApproval.ALWAYS
    assert spec.idempotency is ToolIdempotency.RECONCILE_ONLY
    assert spec.retry_policy is ToolRetryPolicy.NEVER
    assert spec.concurrency is ToolConcurrency.KEYED_SERIAL
    assert spec.binding_required is True
    assert spec.data_classification == ("qq_interaction",)
    assert (binding.target_ref, binding.concurrency_key) == (
        "onebot:group:100:user:300:poke",
        "onebot:group:100:user:300:poke",
    )


def test_reaction_has_external_effect_spec_and_trusted_message_binding() -> None:
    tool = QQInteractionTool("reaction")
    ctx = ToolContext(
        bot=object(),
        user_id="200",
        group_id="100",
        extra={
            "humanization_profile": "performance",
            "onebot_message_refs": ["onebot:group:100:message:9001"],
        },
    )

    spec = tool.spec
    binding = tool.bind_invocation(
        ctx,
        {"message_id": "9001", "emoji_code": "66"},
    )

    assert spec.owner == "qq_interaction"
    assert spec.effect is ToolEffect.EXTERNAL_IRREVERSIBLE
    assert spec.required_scopes == ("onebot:interaction:reaction",)
    assert spec.approval is ToolApproval.ALWAYS
    assert spec.idempotency is ToolIdempotency.RECONCILE_ONLY
    assert spec.retry_policy is ToolRetryPolicy.NEVER
    assert spec.concurrency is ToolConcurrency.KEYED_SERIAL
    assert spec.binding_required is True
    assert spec.data_classification == ("qq_interaction",)
    assert (binding.target_ref, binding.concurrency_key) == (
        "onebot:group:100:message:9001:reaction:66",
        "onebot:group:100:message:9001:reaction:66",
    )


@pytest.mark.parametrize(
    ("ctx", "arguments"),
    [
        (
            ToolContext(
                bot=object(),
                user_id="200",
                group_id="100",
                extra={"humanization_profile": "performance"},
            ),
            {"message_id": "9001", "emoji_code": "66"},
        ),
        (
            ToolContext(
                bot=object(),
                user_id="200",
                group_id="100",
                extra={
                    "humanization_profile": "performance",
                    "onebot_message_refs": [
                        "onebot:group:999:message:9001"
                    ],
                },
            ),
            {"message_id": "9001", "emoji_code": "66"},
        ),
        (
            ToolContext(
                bot=object(),
                user_id="200",
                group_id="100",
                extra={
                    "humanization_profile": "performance",
                    "onebot_message_refs": ["not-a-message-ref"],
                },
            ),
            {"message_id": "9001", "emoji_code": "66"},
        ),
        (
            ToolContext(
                bot=object(),
                user_id="200",
                group_id="100",
                extra={
                    "humanization_profile": "performance",
                    "onebot_message_refs": [
                        "onebot:group:100:message:9001"
                    ],
                },
            ),
            {"message_id": "9001", "emoji_code": "invalid"},
        ),
    ],
)
def test_reaction_binding_rejects_untrusted_or_invalid_message_target(
    ctx: ToolContext,
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        QQInteractionTool("reaction").bind_invocation(ctx, arguments)


def test_send_sticker_has_external_spec_and_resolved_durable_binding(
    tmp_path: Any,
) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_id, _ = store.add(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
        "挥手告别",
        "适合说再见",
    )
    tool = SendStickerTool(store)
    ctx = ToolContext(
        bot=object(),
        user_id="200",
        group_id="100",
    )

    spec = tool.spec
    explicit = tool.bind_invocation(ctx, {"sticker_id": sticker_id})
    resolved = tool.bind_invocation(ctx, {"intent": "告别"})
    store.close()

    assert spec.owner == "sticker"
    assert spec.effect is ToolEffect.EXTERNAL_IRREVERSIBLE
    assert spec.required_scopes == ("onebot:sticker:send",)
    assert spec.approval is ToolApproval.ALWAYS
    assert spec.idempotency is ToolIdempotency.RECONCILE_ONLY
    assert spec.retry_policy is ToolRetryPolicy.NEVER
    assert spec.concurrency is ToolConcurrency.KEYED_SERIAL
    assert spec.binding_required is True
    assert spec.data_classification == ("qq_sticker",)
    assert explicit.target_ref == (
        f"onebot:group:100:sticker:{sticker_id}:send"
    )
    assert resolved.target_ref == explicit.target_ref
    assert explicit.concurrency_key == "onebot:group:100:sticker-send"
    assert resolved.concurrency_key == explicit.concurrency_key


@pytest.mark.parametrize(
    ("ctx", "arguments"),
    [
        (ToolContext(bot=None, user_id="200", group_id="100"), {}),
        (ToolContext(bot=object(), user_id="200", group_id="100"), {}),
        (
            ToolContext(bot=object(), user_id="200", group_id="100"),
            {"sticker_id": "stk_00000000"},
        ),
    ],
)
def test_send_sticker_binding_rejects_unresolved_or_untrusted_target(
    tmp_path: Any,
    ctx: ToolContext,
    arguments: dict[str, Any],
) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    try:
        with pytest.raises(ValueError):
            SendStickerTool(store).bind_invocation(ctx, arguments)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("ctx", "arguments"),
    [
        (
            ToolContext(
                bot=object(),
                user_id="200",
                group_id="100",
                extra={"humanization_profile": "performance"},
            ),
            {"user_id": "300", "group_id": "999"},
        ),
        (
            ToolContext(
                bot=None,
                user_id="200",
                group_id="100",
                extra={"humanization_profile": "performance"},
            ),
            {"user_id": "300"},
        ),
        (
            ToolContext(
                bot=object(),
                user_id="200",
                group_id="100",
                extra={"humanization_profile": "economy"},
            ),
            {"user_id": "300"},
        ),
    ],
)
def test_poke_binding_rejects_untrusted_group_bot_or_profile(
    ctx: ToolContext,
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        QQInteractionTool("poke").bind_invocation(ctx, arguments)


def test_update_cards_binding_uses_trusted_scope_and_card_targets() -> None:
    tool = CardUpdateTool(cast(CardStore, object()))
    trusted = ToolContext(user_id="10001", group_id="20002", session_id="g:20002")

    add_user = tool.bind_invocation(
        trusted,
        {
            "action": "add",
            "scope": "user",
            "scope_id": "10001",
            "category": "fact",
            "content": "likes tea",
        },
    )
    add_group = tool.bind_invocation(
        trusted,
        {
            "action": "add",
            "scope": "group",
            "scope_id": "20002",
            "category": "fact",
            "content": "group event",
        },
    )
    update = tool.bind_invocation(
        trusted,
        {"action": "update", "card_id": "card-9", "content": "updated"},
    )
    expire = tool.bind_invocation(trusted, {"action": "expire", "card_id": "card-9"})
    supersede = tool.bind_invocation(
        trusted,
        {
            "action": "supersede",
            "card_id": "card-9",
            "scope": "user",
            "scope_id": "10001",
            "category": "fact",
            "content": "new fact",
        },
    )

    assert (add_user.target_ref, add_user.concurrency_key) == (
        "memory:user:10001",
        "memory:user:10001",
    )
    assert (add_group.target_ref, add_group.concurrency_key) == (
        "memory:group:20002",
        "memory:group:20002",
    )
    assert (update.target_ref, update.concurrency_key) == (
        "memory:card:card-9",
        "memory:card:card-9",
    )
    assert (expire.target_ref, expire.concurrency_key) == (
        "memory:card:card-9",
        "memory:card:card-9",
    )
    assert (supersede.target_ref, supersede.concurrency_key) == (
        "memory:user:10001",
        "memory:card:card-9",
    )
    with pytest.raises(ValueError):
        tool.bind_invocation(
            trusted,
            {
                "action": "add",
                "scope": "user",
                "scope_id": "99999",
                "category": "fact",
                "content": "spoof",
            },
        )


def test_set_nickname_binding_requires_trusted_user_id() -> None:
    tool = SetNicknameTool(_AffectionEngineStub())
    group_ctx = ToolContext(user_id="10001", group_id="20002")
    private_ctx = ToolContext(user_id="10001", group_id=None)

    group_binding = tool.bind_invocation(
        group_ctx,
        {"user_id": "10001", "nickname": "司君"},
    )
    private_binding = tool.bind_invocation(
        private_ctx,
        {"user_id": "10001", "nickname": "司君"},
    )

    assert (group_binding.target_ref, group_binding.concurrency_key) == (
        "affection:group:20002:user:10001",
        "affection:group:20002:user:10001",
    )
    assert (private_binding.target_ref, private_binding.concurrency_key) == (
        "affection:user:10001",
        "affection:user:10001",
    )
    with pytest.raises(ValueError):
        tool.bind_invocation(
            group_ctx,
            {"user_id": "99999", "nickname": "spoof"},
        )


def test_sticker_write_bindings_guard_requester_and_targets() -> None:
    save = SaveStickerTool(cast(StickerStore, object()), superusers=set())
    manage = ManageStickerTool(
        cast(StickerStore, object()),
        superusers={"10001"},
    )
    trusted = ToolContext(
        user_id="10001",
        extra={"image_tags": {"img:1": "/tmp/trusted-image.jpg"}},
    )

    proactive = save.bind_invocation(
        trusted,
        {
            "image_tag": "img:1",
            "description": "cat",
            "usage_hint": "cute",
            "requested_by": "",
        },
    )
    admin_request = save.bind_invocation(
        trusted,
        {
            "image_tag": "img:1",
            "description": "cat",
            "usage_hint": "cute",
            "requested_by": "10001",
        },
    )
    manage_binding = manage.bind_invocation(
        trusted,
        {
            "sticker_id": "stk_abc",
            "action": "update",
            "requested_by": "10001",
            "description": "updated",
        },
    )

    assert (proactive.target_ref, proactive.concurrency_key) == (
        "sticker:library",
        "sticker:library",
    )
    assert (admin_request.target_ref, admin_request.concurrency_key) == (
        "sticker:library",
        "sticker:library",
    )
    assert (manage_binding.target_ref, manage_binding.concurrency_key) == (
        "sticker:stk_abc",
        "sticker:stk_abc",
    )
    with pytest.raises(ValueError):
        save.bind_invocation(
            trusted,
            {
                "image_tag": "img:1",
                "description": "cat",
                "usage_hint": "cute",
                "requested_by": "99999",
            },
        )
    with pytest.raises(ValueError, match="trusted image_tags"):
        save.bind_invocation(
            trusted,
            {
                "image_tag": "img:missing",
                "description": "cat",
                "usage_hint": "cute",
                "requested_by": "",
            },
        )
    with pytest.raises(ValueError):
        manage.bind_invocation(
            trusted,
            {
                "sticker_id": "stk_abc",
                "action": "delete",
                "requested_by": "99999",
            },
        )


def test_manage_sticker_binding_requires_trusted_superuser() -> None:
    tool = ManageStickerTool(
        cast(StickerStore, object()),
        superusers={"admin1"},
    )
    arguments = {
        "sticker_id": "stk_abc",
        "action": "update",
        "requested_by": "admin1",
        "description": "updated",
    }

    with pytest.raises(ValueError, match="trusted superuser"):
        tool.bind_invocation(ToolContext(user_id=""), arguments)
    with pytest.raises(ValueError, match="trusted superuser"):
        tool.bind_invocation(
            ToolContext(user_id="regular_user"),
            {**arguments, "requested_by": "regular_user"},
        )

    binding = tool.bind_invocation(ToolContext(user_id="admin1"), arguments)
    assert (binding.target_ref, binding.concurrency_key) == (
        "sticker:stk_abc",
        "sticker:stk_abc",
    )
