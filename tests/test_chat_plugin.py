from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel.types import Identity, PromptContext
from plugins.chat.plugin import ChatPlugin, _load_bot_nicknames, _sanitize_debug_reply
from plugins.sticker.plugin import StickerPlugin
from services.media.sticker_store import StickerStore

_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"silent-sticker-test"


def _jpeg_variant(index: int) -> bytes:
    return b"\xff\xd8\xff\xe0" + bytes([index % 256]) * 64 + f"jpeg-variant-{index}".encode()


def test_sanitize_debug_reply_removes_internal_workflow_terms() -> None:
    text = '我的工具列表中没有音乐播放相关的功能，无法执行"开启音乐播放"这一指令。结束本轮。'

    cleaned = _sanitize_debug_reply(text)

    assert "结束本轮" not in cleaned
    assert "pass_turn" not in cleaned.lower()
    assert "内部原因" not in cleaned
    assert "音乐播放相关的功能" in cleaned


def test_sanitize_debug_reply_returns_fallback_when_only_internal_tokens_remain() -> None:
    cleaned = _sanitize_debug_reply("[pass_turn] 本轮未执行工具", fallback="这次没有执行到可用工具。")

    assert cleaned == "这次没有执行到可用工具。"


def test_load_bot_nicknames_merges_nonebot_and_env() -> None:
    nicknames = _load_bot_nicknames({"姆"}, '["emu", "姆姆", "姆"]')

    assert nicknames == ["姆", "emu", "姆姆"]


@pytest.mark.asyncio
async def test_debug_pass_turn_reply_is_user_facing() -> None:
    plugin = ChatPlugin()
    sent: list[str] = []
    bot = SimpleNamespace()

    async def _send(event, message) -> None:
        del event
        sent.append(str(message))

    bot.send = _send
    plugin._ctx = SimpleNamespace(
        sticker_store=None,
        tool_registry=SimpleNamespace(empty=True, to_openai_tools=lambda: []),
        llm_client=SimpleNamespace(
            _call=_fake_debug_call(
                {
                    "text": "",
                    "tool_uses": [
                        SimpleNamespace(
                            name="pass_turn",
                            input={"reason": "结束本轮。内部原因：无法匹配任何工具"},
                            id="tu_1",
                        ),
                    ],
                    "thinking_blocks": [],
                }
            )
        ),
        humanizer=SimpleNamespace(delay=_noop_delay),
        config=SimpleNamespace(group=None),
        mood_engine=None,
        affection_engine=None,
        schedule_store=None,
        card_store=None,
        short_term=None,
        msg_log=None,
    )

    cmd_ctx = SimpleNamespace(
        bot=bot,
        event=SimpleNamespace(),
        user_id="1",
        group_id="123",
        is_private=False,
        args="开启音乐播放",
    )

    await plugin._handle_debug(cmd_ctx)

    assert sent
    reply = sent[-1]
    assert "调试：" in reply
    assert "pass_turn" not in reply.lower()
    assert "结束本轮" not in reply
    assert "内部原因" not in reply
    assert "现在没有合适的工具可用" in reply


async def _noop_delay(_text: str) -> None:
    return None


def _fake_debug_call(result: dict[str, object]):
    async def _call(*args, **kwargs):
        del args, kwargs
        return result

    return _call


@pytest.mark.asyncio
async def test_sticker_plugin_silent_learn_collects_sticker_without_consuming(tmp_path: Path) -> None:
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(_JPEG_DATA)
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._image_cache = None
    plugin._group_config = SimpleNamespace(
        tools_enabled=True,
        overrides={},
        resolve=lambda _gid: SimpleNamespace(tools_enabled=False, sticker_mode="inherit"),
    )

    seg = SimpleNamespace(
        type="image",
        data={
            "file": "abc.jpg",
            "sub_type": 1,
            "summary": "[动画表情]",
        },
    )

    async def _call_api(name: str, **_kwargs):
        assert name == "get_image"
        return {"file": str(image_path)}

    ctx = SimpleNamespace(
        allow_speaking=False,
        group_presence_mode="silent_learn",
        is_group=True,
        group_id="123",
        raw_message={"segments": [seg]},
        bot=SimpleNamespace(call_api=_call_api),
    )

    consumed = await plugin.on_message(ctx)  # type: ignore[arg-type]

    assert consumed is False
    stickers = store.list_all()
    assert len(stickers) == 1
    entry = next(iter(stickers.values()))
    assert entry["source"] == "stolen_silent_learn"


@pytest.mark.asyncio
async def test_sticker_plugin_silent_learn_respects_explicit_tools_disabled(tmp_path: Path) -> None:
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(_JPEG_DATA)
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._image_cache = None
    plugin._group_config = SimpleNamespace(
        tools_enabled=True,
        overrides={123: SimpleNamespace(tools_enabled=False)},
        resolve=lambda _gid: SimpleNamespace(tools_enabled=False, sticker_mode="inherit"),
    )

    seg = SimpleNamespace(
        type="image",
        data={
            "file": "abc.jpg",
            "sub_type": 1,
            "summary": "[动画表情]",
        },
    )

    async def _call_api(name: str, **_kwargs):
        assert name == "get_image"
        return {"file": str(image_path)}

    ctx = SimpleNamespace(
        allow_speaking=False,
        group_presence_mode="silent_learn",
        is_group=True,
        group_id="123",
        raw_message={"segments": [seg]},
        bot=SimpleNamespace(call_api=_call_api),
    )

    consumed = await plugin.on_message(ctx)  # type: ignore[arg-type]

    assert consumed is False
    assert store.list_all() == {}


@pytest.mark.asyncio
async def test_sticker_plugin_silent_learn_retries_once_on_next_tick(tmp_path: Path) -> None:
    image_path = tmp_path / "retry.jpg"
    image_path.write_bytes(_JPEG_DATA)
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._image_cache = None
    plugin._group_config = SimpleNamespace(
        tools_enabled=True,
        overrides={},
        resolve=lambda _gid: SimpleNamespace(tools_enabled=True, sticker_mode="inherit"),
    )

    seg = SimpleNamespace(
        type="image",
        data={
            "file": "retry.jpg",
            "sub_type": 1,
            "summary": "[动画表情]",
        },
    )
    calls = 0

    async def _call_api(name: str, **_kwargs):
        nonlocal calls
        assert name == "get_image"
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary fetch miss")
        return {"file": str(image_path)}

    bot = SimpleNamespace(call_api=_call_api)
    ctx = SimpleNamespace(
        allow_speaking=False,
        group_presence_mode="silent_learn",
        is_group=True,
        group_id="123",
        message_id=456,
        raw_message={"segments": [seg]},
        bot=bot,
    )

    consumed = await plugin.on_message(ctx)  # type: ignore[arg-type]

    assert consumed is False
    assert store.list_all() == {}
    assert len(plugin._pending_retries) == 1

    await plugin.on_tick(SimpleNamespace(bot=bot))  # type: ignore[arg-type]

    stickers = store.list_all()
    assert len(stickers) == 1
    entry = next(iter(stickers.values()))
    assert entry["source"] == "stolen_silent_retry"
    assert plugin._pending_retries == []


@pytest.mark.asyncio
async def test_sticker_plugin_silent_learn_off_does_not_queue_retry(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._image_cache = None
    plugin._group_config = SimpleNamespace(
        tools_enabled=True,
        overrides={},
        resolve=lambda _gid: SimpleNamespace(tools_enabled=True, sticker_mode="off"),
    )

    seg = SimpleNamespace(
        type="image",
        data={
            "file": "retry.jpg",
            "sub_type": 1,
            "summary": "[动画表情]",
        },
    )

    async def _call_api(name: str, **_kwargs):
        raise AssertionError(f"unexpected API call: {name}")

    ctx = SimpleNamespace(
        allow_speaking=False,
        group_presence_mode="silent_learn",
        is_group=True,
        group_id="123",
        message_id=456,
        raw_message={"segments": [seg]},
        bot=SimpleNamespace(call_api=_call_api),
    )

    consumed = await plugin.on_message(ctx)  # type: ignore[arg-type]

    assert consumed is False
    assert store.list_all() == {}
    assert plugin._pending_retries == []


@pytest.mark.asyncio
async def test_sticker_prompt_uses_scoped_recommendations_and_cooling(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    sticker_ids: list[str] = []
    for i in range(8):
        sid, is_new = store.add(_jpeg_variant(i), f"提示表情{i}", f"提示场景{i}", source="auto")
        assert is_new
        sticker_ids.append(sid)
    store.record_send(sticker_ids[0], group_id="123")
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._sticker_frequency = "normal"
    plugin._group_config = SimpleNamespace(
        tools_enabled=True,
        overrides={},
        resolve=lambda _gid: SimpleNamespace(tools_enabled=True, sticker_mode="inherit"),
    )
    ctx = PromptContext(
        session_id="group_123",
        group_id="123",
        user_id="456",
        identity=Identity(name="凤笑梦"),
    )

    await plugin.on_pre_prompt(ctx)

    library_block = next(block.text for block in ctx.blocks if block.label == "表情包库")
    assert "推荐候选" in library_block
    assert "冷却中，请不要选择" in library_block
    assert sticker_ids[0] in library_block.split("冷却中，请不要选择", 1)[1]


@pytest.mark.asyncio
async def test_sticker_prompt_off_mode_skips_blocks(tmp_path: Path) -> None:
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    store.add(_JPEG_DATA, "提示表情", "提示场景", source="auto")
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._sticker_frequency = "normal"
    plugin._group_config = SimpleNamespace(
        tools_enabled=True,
        overrides={},
        resolve=lambda _gid: SimpleNamespace(tools_enabled=True, sticker_mode="off"),
    )
    ctx = PromptContext(
        session_id="group_123",
        group_id="123",
        user_id="456",
        identity=Identity(name="凤笑梦"),
    )

    await plugin.on_pre_prompt(ctx)

    assert not any(block.label == "表情包库" for block in ctx.blocks)
    assert not any(block.label == "表情包规则" for block in ctx.blocks)


@pytest.mark.asyncio
async def test_sticker_plugin_silent_learn_ignores_plain_images(tmp_path: Path) -> None:
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(_JPEG_DATA)
    store = StickerStore(storage_dir=str(tmp_path / "stickers"))
    plugin = StickerPlugin()
    plugin._sticker_store = store
    plugin._image_cache = object()
    plugin._group_config = SimpleNamespace(
        tools_enabled=True,
        overrides={},
        resolve=lambda _gid: SimpleNamespace(tools_enabled=True, sticker_mode="inherit"),
    )

    seg = SimpleNamespace(
        type="image",
        data={
            "file": "abc.jpg",
            "sub_type": 0,
            "summary": "",
        },
    )

    ctx = SimpleNamespace(
        allow_speaking=False,
        group_presence_mode="silent_learn",
        is_group=True,
        group_id="123",
        raw_message={"segments": [seg]},
        bot=SimpleNamespace(),
    )

    consumed = await plugin.on_message(ctx)  # type: ignore[arg-type]

    assert consumed is False
    assert store.list_all() == {}
