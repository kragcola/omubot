"""RED regressions for plugin business constraints and persistence."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.plugins import create_plugins_router
from bootstrap import application as application_module
from kernel.bus import PluginBus
from kernel.types import AmadeusPlugin, PluginContext
from plugins.bilibili import BilibiliPlugin, _VideoId
from plugins.food.plugin import FoodPlugin
from plugins.slang.plugin import SlangPlugin
from services.memory.card_store import CardStore
from services.plugin_config import PluginConfigStore
from services.plugin_state import PluginStateStore


def _food(name: str) -> dict[str, str]:
    return {
        "name": name,
        "brand": "",
        "taste": "清淡",
        "category": "主食",
        "staple": "面",
        "cooking_method": "煮",
        "temperature": "热",
        "available_time": "不限",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link",
    [
        "https://www.bilibili.com/bangumi/play/ep123456",
        "https://www.bilibili.com/bangumi/play/ss12345",
    ],
    ids=["ep", "ss"],
)
async def test_bilibili_bangumi_link_injects_summary_and_reply_trigger(link: str) -> None:
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    plugin = BilibiliPlugin()
    plugin._enabled = True
    plugin._cache_ttl = 3600.0
    plugin._cover_timeout = 10.0
    plugin._reply_mode = "always"
    plugin._bilibili_talk_value = 0.8
    plugin._vision_client = None
    plugin._llm_client = None
    plugin._cache = {}

    ctx = MagicMock()
    ctx.raw_message = {
        "plain_text": link,
        "segments": Message([MessageSegment.text(link)]),
    }
    ctx.group_id = "123456"
    ctx.user_id = "789"
    fake_info = {
        "title": "测试番剧",
        "duration": 1440,
        "pic": "",
        "stat": {"view": 50000},
        "desc": "番剧简介",
        "tname": "番剧",
        "owner": {"name": "哔哩哔哩番剧"},
    }

    with (
        patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as resolve_text,
        patch.object(plugin, "_resolve_urls_to_vid", new_callable=AsyncMock) as resolve_url,
        patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as get_info,
    ):
        resolve_text.return_value = link
        resolve_url.return_value = _VideoId(bvid="BV1xx1234567")
        get_info.return_value = fake_info
        result = await plugin.on_message(ctx)

    assert result is False
    assert "[B站视频]" in ctx.raw_message["segments"][0].data["text"]
    assert "《测试番剧》" in ctx.raw_message["segments"][0].data["text"]
    reply_hint = ctx.raw_message.get("_bilibili_reply")
    assert reply_hint is not None
    assert reply_hint["mode"] == "always"
    assert reply_hint["bilibili_talk_value"] == 0.8
    assert reply_hint["video_title"] == "测试番剧"
    assert ctx.trigger.mode == "video_always"
    assert ctx.trigger.reason == "视频分享:《测试番剧》"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("toggle", "expected_enabled"),
    [("on", True), ("off", False)],
    ids=["on", "off"],
)
async def test_food_admin_search_toggle_persists_and_survives_restart(
    tmp_path: Path,
    toggle: str,
    expected_enabled: bool,
) -> None:
    config_store = PluginConfigStore(tmp_path / "plugin-config.json")
    config_store.set_values(
        "food",
        {
            "food_library_max_items": 17,
            "search_enabled": not expected_enabled,
        },
    )
    ctx = PluginContext(
        plugin_config_store=config_store,
        admins={"123": "test-admin"},
    )
    plugin = FoodPlugin()
    plugin._ctx = ctx
    plugin._search_enabled = not expected_enabled
    sent: list[str] = []
    plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

    await plugin._handle_search_toggle(
        _food_cmd_ctx(args=toggle, is_admin=True),
    )

    assert plugin._search_enabled is expected_enabled
    assert config_store.get("food") == {
        "food_library_max_items": 17,
        "search_enabled": expected_enabled,
    }

    restarted = FoodPlugin()
    await restarted.on_startup(ctx)

    assert restarted._search_enabled is expected_enabled


def test_food_admin_http_search_toggle_hot_applies_to_running_plugin(
    tmp_path: Path,
) -> None:
    config_store = PluginConfigStore(
        tmp_path / "config",
        plugin_root=Path("plugins"),
    )
    bus = PluginBus()
    plugin = FoodPlugin()
    bus.register(plugin)
    plugin._search_enabled = False
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        "/api/admin/plugins/food/settings",
        json={"values": {"search_enabled": True}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["applied"] is True
    assert payload["requires_restart"] is False
    assert plugin._search_enabled is True
    assert config_store.get("food") == {"search_enabled": True}

    settings = TestClient(app).get("/api/admin/plugins/food/settings").json()
    assert settings["effective_values"]["search_enabled"] is True
    assert "enabled" not in settings["schema"]["properties"]
    assert "enabled" not in settings["defaults"]
    assert "enabled" not in settings["effective_values"]


def test_food_admin_http_mixed_settings_split_hot_and_restart_fields(
    tmp_path: Path,
) -> None:
    config_store = PluginConfigStore(tmp_path / "config", plugin_root=Path("plugins"))
    bus = PluginBus()
    plugin = FoodPlugin()
    bus.register(plugin)
    plugin._search_enabled = False
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        "/api/admin/plugins/food/settings",
        json={"values": {"search_enabled": True, "recent_max": 9}},
    )

    payload = response.json()
    assert payload["ok"] is True
    assert payload["applied_fields"] == ["search_enabled"]
    assert payload["restart_required_fields"] == ["recent_max"]
    assert payload["requires_restart"] is True
    assert plugin._search_enabled is True
    assert plugin._max_recent == 5
    assert config_store.get("food") == {
        "search_enabled": True,
        "recent_max": 9,
    }


def test_food_admin_http_apply_failure_rolls_back_live_and_store(
    tmp_path: Path,
) -> None:
    config_store = PluginConfigStore(tmp_path / "config", plugin_root=Path("plugins"))
    bus = PluginBus()
    plugin = FoodPlugin()
    bus.register(plugin)
    plugin._search_enabled = False
    apply_calls = 0

    def fail_first_apply(
        values: dict[str, Any],
        *,
        changed_fields: frozenset[str],
    ) -> frozenset[str]:
        nonlocal apply_calls
        apply_calls += 1
        plugin._search_enabled = bool(values["search_enabled"])
        if apply_calls == 1:
            raise RuntimeError("injected apply failure")
        return changed_fields

    plugin.apply_runtime_settings = fail_first_apply  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        "/api/admin/plugins/food/settings",
        json={"values": {"search_enabled": True}},
    )

    payload = response.json()
    assert payload["ok"] is False
    assert "injected apply failure" in payload["error"]
    assert apply_calls == 2
    assert plugin._search_enabled is False
    assert config_store.get("food") == {}


def test_admin_hot_settings_without_runtime_hook_fail_closed(tmp_path: Path) -> None:
    plugin = AmadeusPlugin()
    plugin.name = "hot_without_hook"
    plugin.config_spec = {"apply_mode": "hot", "restart_required_fields": []}
    bus = PluginBus()
    bus.register(plugin)
    config_store = PluginConfigStore(tmp_path / "config", plugin_root=Path("plugins"))
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )

    response = TestClient(app).post(
        "/api/admin/plugins/hot_without_hook/settings",
        json={"values": {"enabled": True}},
    )

    payload = response.json()
    assert payload["ok"] is False
    assert "no runtime apply hook" in payload["error"]
    assert config_store.get("hot_without_hook") == {}


def test_slang_generic_config_surface_is_read_only(tmp_path: Path) -> None:
    bus = PluginBus()
    bus.register(SlangPlugin())
    config_store = PluginConfigStore(tmp_path / "config", plugin_root=Path("plugins"))
    app = FastAPI()
    app.include_router(
        create_plugins_router(bus=bus, plugin_config_store=config_store),
        prefix="/api/admin",
    )
    client = TestClient(app)

    settings = client.get("/api/admin/plugins/slang/settings").json()
    response = client.post(
        "/api/admin/plugins/slang/settings",
        json={"values": {"enabled": False}},
    )

    assert settings["apply_mode"] == "read_only"
    assert response.json()["ok"] is False
    assert config_store.get("slang") == {}


def test_food_web_search_dependency_is_optional() -> None:
    bus = PluginBus()
    plugin = FoodPlugin()
    bus.register(plugin)

    bus._resolve_dependencies()

    assert plugin.enabled is True
    assert bus._required_dependencies(plugin) == {}
    assert bus._optional_dependencies(plugin) == {"web_search": ">=0.1.0"}
    [health] = bus.plugin_health()
    assert health["dependency_blocked"] is False


@pytest.mark.asyncio
async def test_food_startup_does_not_mutate_plugin_governance_enabled(
    tmp_path: Path,
) -> None:
    plugin = FoodPlugin()
    plugin.enabled = False
    ctx = PluginContext(
        plugin_config_store=PluginConfigStore(
            tmp_path / "config",
            plugin_root=Path("plugins"),
        ),
    )

    await plugin.on_startup(ctx)

    assert plugin.enabled is False


def test_legacy_food_enabled_override_migrates_to_plugin_state(tmp_path: Path) -> None:
    config_store = PluginConfigStore(tmp_path / "config", plugin_root=Path("plugins"))
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    config_store.set_values(
        "food",
        {"enabled": False, "search_enabled": True},
    )
    migrate = getattr(
        application_module,
        "migrate_legacy_plugin_enabled_override",
        None,
    )
    assert callable(migrate)

    assert migrate(config_store, state_store, plugin_name="food") is True

    assert state_store.get("food") is False
    assert config_store.get("food") == {"search_enabled": True}


def test_legacy_food_enabled_override_does_not_replace_canonical_state(
    tmp_path: Path,
) -> None:
    config_store = PluginConfigStore(tmp_path / "config", plugin_root=Path("plugins"))
    state_store = PluginStateStore(tmp_path / "plugin-state.json")
    config_store.set_values("food", {"enabled": False})
    state_store.set_enabled("food", True)
    migrate = getattr(
        application_module,
        "migrate_legacy_plugin_enabled_override",
        None,
    )
    assert callable(migrate)

    assert migrate(config_store, state_store, plugin_name="food") is True

    assert state_store.get("food") is True
    assert config_store.get("food") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("llm_text", "args", "library", "expected"),
    [
        ("宇宙石头汤", "", [_food("热汤面")], "热汤面"),
        ("鸡蛋羹", "不要鸡蛋羹", [_food("鸡蛋羹"), _food("热汤面")], "热汤面"),
    ],
    ids=["not-a-candidate", "explicitly-excluded"],
)
async def test_food_illegal_llm_choice_uses_only_legal_fallback(
    tmp_path: Path,
    llm_text: str,
    args: str,
    library: list[dict[str, str]],
    expected: str,
) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(store, _ConstantLLM(llm_text), library)
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_food_cmd_ctx(args=args))

        assert sent == [expected]
        cards = await _served_cards(store)
        assert len(cards) == 1
        assert expected in cards[0].content
        assert llm_text not in cards[0].content
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_recent_llm_choice_falls_back_and_records_only_new_item(tmp_path: Path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(
            store,
            _ConstantLLM("鸡蛋羹"),
            [_food("鸡蛋羹"), _food("热汤面")],
        )
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_eat(_food_cmd_ctx())
        await plugin._handle_eat(_food_cmd_ctx())

        assert sent == ["鸡蛋羹", "热汤面"]
        cards = await _served_cards(store)
        assert len(cards) == 2
        assert "推荐了鸡蛋羹" in cards[0].content
        assert "推荐了热汤面" in cards[1].content
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_food_disliked_llm_choice_falls_back_to_legal_item(tmp_path: Path) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        plugin = _food_plugin(
            store,
            _ConstantLLM("鸡蛋羹"),
            [_food("鸡蛋羹"), _food("热汤面")],
        )
        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]

        await plugin._handle_dislike(_food_cmd_ctx(args="鸡蛋羹"))
        sent.clear()
        await plugin._handle_eat(_food_cmd_ctx())

        assert sent == ["热汤面"]
        cards = await _served_cards(store)
        assert len(cards) == 1
        assert "热汤面" in cards[0].content
        assert "鸡蛋羹" not in cards[0].content
    finally:
        await store.close()


def _food_plugin(
    store: CardStore,
    llm_client: Any,
    library: list[dict[str, str]],
) -> FoodPlugin:
    plugin = FoodPlugin()
    plugin._ctx = SimpleNamespace(
        card_store=store,
        llm_client=llm_client,
        tool_registry=None,
    )
    plugin._search_enabled = False
    plugin._food_library_max_items = 40
    plugin._food_library = library
    plugin._tutorial_shown.add("123")
    return plugin


def _food_cmd_ctx(
    *,
    args: str = "",
    user_id: str = "123",
    is_admin: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        args=args,
        is_admin=is_admin,
        is_private=False,
        group_id="456",
        event=SimpleNamespace(message_id=1),
        bot=SimpleNamespace(),
    )


def _capture_reply(sent: list[str]):
    async def capture(cmd_ctx: Any, text: str) -> None:
        del cmd_ctx
        sent.append(text)

    return capture


async def _served_cards(store: CardStore) -> list[Any]:
    series = await store.get_series_by_key("food_served:123")
    assert series is not None
    return await store.get_series_cards(series.series_id)


class _ConstantLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def _call(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        del args, kwargs
        return {"text": self._text}


@pytest.mark.asyncio
async def test_food_web_results_are_context_only_and_local_candidates_remain_legal(
    tmp_path: Path,
) -> None:
    store = CardStore(str(tmp_path / "memory.db"))
    await store.init()
    try:
        class _SearchTool:
            async def execute(self, _ctx: Any, **_kwargs: Any) -> str:
                return "网络结果：宇宙石头汤"

        class _PromptCaptureLLM:
            def __init__(self) -> None:
                self.messages: list[Any] = []

            async def _call(self, _system: Any, messages: Any, **_kwargs: Any) -> dict[str, str]:
                self.messages.extend(messages)
                return {"text": "热汤面"}

        llm = _PromptCaptureLLM()
        plugin = _food_plugin(store, llm, [_food("热汤面")])
        plugin._search_enabled = True
        plugin._ctx.tool_registry = SimpleNamespace(get=lambda name: _SearchTool() if name == "web_search" else None)

        sent: list[str] = []
        plugin._send_reply = _capture_reply(sent)  # type: ignore[method-assign]
        await plugin._handle_eat(_food_cmd_ctx())

        prompt = str(llm.messages[0]["content"])
        assert "热汤面" in prompt
        assert "宇宙石头汤" in prompt
        assert "仅用于补充判断" in prompt
        assert sent == ["热汤面"]
    finally:
        await store.close()
