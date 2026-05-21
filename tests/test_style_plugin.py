import json
from pathlib import Path

import pytest

from kernel.types import Identity, PluginContext, PromptContext, ReplyContext
from plugins.style.plugin import StyleConfig, StylePlugin
from services.style import NewStyleExpression, StyleStore


def test_style_manifest_declares_reply_permission() -> None:
    manifest_path = Path(__file__).resolve().parent.parent / "plugins" / "style" / "plugin.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "reply" in data["permissions"]


@pytest.fixture
async def style_store(tmp_path) -> StyleStore:
    store = StyleStore(tmp_path / "style.db")
    await store.init()
    yield store
    await store.close()


def _prompt_ctx(group_id: str, text: str) -> PromptContext:
    return PromptContext(
        session_id=f"group_{group_id}",
        group_id=group_id,
        user_id="10001",
        identity=Identity(name="凤笑梦"),
        conversation_text=text,
    )


def _plugin_ctx(store: StyleStore) -> PluginContext:
    ctx = PluginContext()
    ctx.style_store = store
    return ctx


@pytest.mark.asyncio
async def test_style_plugin_injects_approved_group_expression(style_store: StyleStore) -> None:
    expression = await style_store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            group_id="100",
            confidence=0.8,
        )
    )
    await style_store.set_status(expression.expression_id, "approved")
    plugin = StylePlugin(StyleConfig())
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    prompt_ctx = _prompt_ctx("100", "大家在轻松吐槽这件事")
    await plugin.on_pre_prompt(prompt_ctx)
    await plugin.on_shutdown(plugin_ctx)

    assert [block.label for block in prompt_ctx.blocks] == ["表达习惯参考"]
    assert "不要照抄" in prompt_ctx.blocks[0].text
    assert "大家在轻松吐槽" in prompt_ctx.blocks[0].text


@pytest.mark.asyncio
async def test_style_plugin_global_pool_only_for_enabled_groups(style_store: StyleStore) -> None:
    expression = await style_store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            scope="global",
            group_id="100",
            confidence=0.8,
        )
    )
    await style_store.set_status(expression.expression_id, "approved")
    plugin = StylePlugin(StyleConfig(global_enabled_group_ids=["200"]))
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    closed_ctx = _prompt_ctx("100", "大家在轻松吐槽")
    opened_ctx = _prompt_ctx("200", "大家在轻松吐槽")
    await plugin.on_pre_prompt(closed_ctx)
    await plugin.on_pre_prompt(opened_ctx)
    await plugin.on_shutdown(plugin_ctx)

    assert closed_ctx.blocks == []
    assert [block.label for block in opened_ctx.blocks] == ["表达习惯参考"]


@pytest.mark.asyncio
async def test_style_plugin_injects_enabled_profile_before_expression_block(style_store: StyleStore) -> None:
    expression = await style_store.upsert_expression(
        NewStyleExpression(
            situation="大家在轻松吐槽",
            style="先短促附和，再转成符合凤笑梦人设的回应",
            group_id="100",
            confidence=0.8,
        )
    )
    await style_store.set_status(expression.expression_id, "approved")
    await style_store.generate_profile(group_id="100", actor="tester", enable=True)
    plugin = StylePlugin(StyleConfig())
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    prompt_ctx = _prompt_ctx("100", "大家在轻松吐槽")
    await plugin.on_pre_prompt(prompt_ctx)
    await plugin.on_shutdown(plugin_ctx)

    assert [block.label for block in prompt_ctx.blocks] == ["动态风格档案", "表达习惯参考"]
    assert "不得改变核心人设" in prompt_ctx.blocks[0].text


@pytest.mark.asyncio
async def test_style_plugin_records_bot_reply_feedback(style_store: StyleStore) -> None:
    plugin = StylePlugin(StyleConfig())
    plugin_ctx = _plugin_ctx(style_store)
    await plugin.on_startup(plugin_ctx)

    await plugin.on_post_reply(
        ReplyContext(
            session_id="group_100",
            group_id="100",
            user_id="10001",
            reply_content="哇，听起来很有意思！",
            user_msg="我刚刚打了新成绩",
            elapsed_ms=123.0,
            thinker_action="reply",
        )
    )
    await plugin.on_shutdown(plugin_ctx)

    feedback, total = await style_store.list_feedback(target_type="reply", group_id="100")
    assert total == 1
    assert feedback[0].rating == "neutral"
    assert feedback[0].source == "weak_signal"
    assert feedback[0].raw_text == "哇，听起来很有意思！"


@pytest.mark.asyncio
async def test_style_plugin_disabled_does_not_create_block(style_store: StyleStore) -> None:
    plugin = StylePlugin(StyleConfig(enabled=False))
    await plugin.on_startup(_plugin_ctx(style_store))

    prompt_ctx = _prompt_ctx("100", "大家在轻松吐槽")
    await plugin.on_pre_prompt(prompt_ctx)

    assert prompt_ctx.blocks == []
