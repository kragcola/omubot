"""Production post-reply wiring for durable visual identity corrections."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import textwrap
from dataclasses import fields
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from bootstrap.chat_runtime import build_chat_runtime, create_chat_runtime_assembly
from kernel.router import _describe_image_data, _merge_human_corrected_identity
from kernel.types import ReplyContext
from plugins.memo.plugin import MemoExtractor, MemoPlugin
from services.llm.client import LLMClient
from services.llm.prompt_builder import PromptBuilder
from services.media.character_recognizer import CharacterRecognition
from services.media.visual_evidence import VisualEvidence
from services.memory.card_store import CardStore
from services.memory.short_term import ShortTermMemory
from services.memory.visual_identity import VisualIdentityStore
from services.persona import IdentitySnapshot, PersonaRuntime
from services.tools.registry import ToolRegistry


def test_reply_context_exposes_privacy_safe_visual_evidence_and_trigger_mode() -> None:
    ctx = ReplyContext(
        session_id="group_g1",
        group_id="g1",
        user_id="u1",
        reply_content="知道了",
    )

    assert ctx.visual_evidence == []
    assert ctx.trigger_mode == ""


def test_post_reply_boundary_accepts_trigger_mode() -> None:
    parameters = inspect.signature(LLMClient._fire_post_reply).parameters

    assert "trigger_mode" in parameters


@pytest.mark.asyncio
async def test_post_reply_boundary_sanitizes_current_visual_sidechannel() -> None:
    class CapturingBus:
        def __init__(self) -> None:
            self.calls: list[ReplyContext] = []

        async def fire_on_post_reply(self, ctx: ReplyContext) -> None:
            self.calls.append(ctx)

    bus = CapturingBus()
    client = object.__new__(LLMClient)
    client._bus = bus
    image_sha256 = "a" * 64
    user_content: Any = [
        {"type": "text", "text": "[图片]这是高松灯"},
        {
            "type": "image_ref",
            "path": "/private/cache/should-not-leak.png",
            "media_type": "image/png",
            "image_sha256": image_sha256,
            "image_sha256_short": "aaaaaaaa",
            "visual_intent": "identify",
            "visual_observation": "一名黑发少女",
            "visual_identity": [],
            "visual_ocr": "",
            "visual_summary": "一名黑发少女",
            "provenance": "visual_system",
        },
    ]

    await client._fire_post_reply(
        session_id="group_g1",
        group_id="g1",
        user_id="u1",
        user_content=user_content,
        source_message_id=42,
        reply_content="知道了",
        elapsed_ms=12.0,
        thinker_action="reply",
        thinker_thought="",
        tool_calls=[],
        trigger_mode="correction",
    )

    assert len(bus.calls) == 1
    reply_ctx = bus.calls[0]
    assert reply_ctx.trigger_mode == "correction"
    assert reply_ctx.visual_evidence == [
        {
            "image_sha256": image_sha256,
            "image_sha256_short": "aaaaaaaa",
            "visual_intent": "identify",
            "visual_observation": "一名黑发少女",
            "visual_identity": [],
            "visual_ocr": "",
            "visual_summary": "一名黑发少女",
            "provenance": "visual_system",
        }
    ]
    assert "path" not in reply_ctx.visual_evidence[0]
    assert "media_type" not in reply_ctx.visual_evidence[0]


@pytest.mark.asyncio
async def test_real_chat_propagates_trigger_mode_to_post_reply(
    persona_runtime: PersonaRuntime,
    identity_snapshot: IdentitySnapshot,
) -> None:
    class CapturingBus:
        def __init__(self) -> None:
            self.calls: list[ReplyContext] = []

        async def fire_on_pre_prompt(self, _ctx: object) -> None:
            return None

        async def fire_on_post_reply(self, ctx: ReplyContext) -> None:
            self.calls.append(ctx)

    bus = CapturingBus()
    client = LLMClient(
        base_url="http://fake",
        api_key="sk-fake",
        model="test-model",
        prompt_builder=PromptBuilder(persona_runtime=persona_runtime),
        short_term=ShortTermMemory(),
        tools=ToolRegistry(),
        thinker_enabled=False,
        bus=bus,
    )

    async def fake_call_api(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "text": "知道了",
            "tool_uses": [],
            "input_tokens": 20,
            "output_tokens": 4,
            "cache_read": 0,
            "cache_create": 0,
        }

    trigger = SimpleNamespace(
        mode="correction",
        target_message_id=42,
        extra={"correction_type": "amend"},
    )
    try:
        with patch("services.llm.client.call_api", new=fake_call_api):
            reply = await client.chat(
                session_id="private_u1",
                user_id="u1",
                user_content="这是高松灯",
                identity=identity_snapshot,
                trigger=trigger,
                force_reply=True,
            )
    finally:
        await client.close()

    assert reply
    assert len(bus.calls) == 1
    assert bus.calls[0].trigger_mode == "correction"


@pytest.mark.asyncio
async def test_memo_post_reply_routes_explicit_single_image_identity_correction() -> None:
    class CapturingExtractor:
        def __init__(self) -> None:
            self.visual_calls: list[dict[str, Any]] = []
            self.extraction_calls = 0

        async def store_visual_correction(self, **kwargs: Any) -> object:
            self.visual_calls.append(dict(kwargs))
            return object()

        async def extract_after_turn(self, **_kwargs: Any) -> None:
            self.extraction_calls += 1

    extractor = CapturingExtractor()
    plugin = MemoPlugin()
    plugin._memo_extractor = extractor
    image_sha256 = "b" * 64

    await plugin.on_post_reply(
        ReplyContext(
            session_id="group_g1",
            group_id="g1",
            user_id="u1",
            reply_content="知道了",
            user_msg="[图片]这是高松灯",
            source_message_id=42,
            trigger_mode="correction",
            visual_evidence=[
                {
                    "image_sha256": image_sha256,
                    "image_sha256_short": "bbbbbbbb",
                    "visual_intent": "identify",
                    "visual_observation": "一名黑发少女",
                    "visual_identity": [],
                    "visual_ocr": "",
                    "visual_summary": "一名黑发少女",
                    "provenance": "visual_system",
                }
            ],
        )
    )
    while plugin._pending_extractions:
        await asyncio.sleep(0)

    assert extractor.extraction_calls == 0
    assert extractor.visual_calls == [
        {
            "image_sha256": image_sha256,
            "entity_label": "高松灯",
            "user_id": "u1",
            "group_id": "g1",
            "source_message_id": 42,
            "visual_evidence": {
                "image_sha256": image_sha256,
                "image_sha256_short": "bbbbbbbb",
                "visual_intent": "identify",
                "visual_observation": "一名黑发少女",
                "visual_identity": [],
                "visual_ocr": "",
                "visual_summary": "一名黑发少女",
                "provenance": "visual_system",
            },
            "trigger": {"mode": "correction", "evidence_count": 1},
        }
    ]


@pytest.mark.parametrize(
    "invalid_case",
    ["wrong_trigger", "multiple_images", "wrong_provenance", "missing_sha"],
)
@pytest.mark.asyncio
async def test_memo_post_reply_rejects_untrusted_visual_correction_cases(
    invalid_case: str,
) -> None:
    class CapturingExtractor:
        def __init__(self) -> None:
            self.visual_calls = 0
            self.extraction_calls = 0

        async def store_visual_correction(self, **_kwargs: Any) -> object:
            self.visual_calls += 1
            return object()

        async def extract_after_turn(self, **_kwargs: Any) -> None:
            self.extraction_calls += 1

    image_sha256 = "d" * 64
    evidence = {
        "image_sha256": image_sha256,
        "visual_observation": "一名黑发少女",
        "provenance": "visual_system",
    }
    trigger_mode = "correction"
    evidence_items = [evidence]
    if invalid_case == "wrong_trigger":
        trigger_mode = "normal"
    elif invalid_case == "multiple_images":
        evidence_items = [evidence, dict(evidence)]
    elif invalid_case == "wrong_provenance":
        evidence["provenance"] = "user_text"
    elif invalid_case == "missing_sha":
        evidence.pop("image_sha256")

    extractor = CapturingExtractor()
    plugin = MemoPlugin()
    plugin._memo_extractor = extractor
    await plugin.on_post_reply(
        ReplyContext(
            session_id="group_g1",
            group_id="g1",
            user_id="u1",
            reply_content="知道了",
            user_msg="[图片]这是高松灯",
            source_message_id=42,
            trigger_mode=trigger_mode,
            visual_evidence=evidence_items,
        )
    )
    while plugin._pending_extractions:
        await asyncio.sleep(0)

    assert extractor.visual_calls == 0
    assert extractor.extraction_calls == 1


@pytest.mark.asyncio
async def test_memo_post_reply_persists_visual_identity_without_memory_card(
    tmp_path: Any,
) -> None:
    db_path = str(tmp_path / "memory_cards.db")
    card_store = CardStore(db_path=db_path)
    visual_store = VisualIdentityStore(db_path=db_path)
    await card_store.init()
    await visual_store.init()

    async def unexpected_llm_call(_request: object) -> dict[str, str]:
        raise AssertionError("explicit visual corrections must bypass generic extraction")

    extractor = MemoExtractor(
        card_store=card_store,
        api_call=unexpected_llm_call,
        visual_identity_store=visual_store,
    )
    plugin = MemoPlugin()
    plugin._memo_extractor = extractor
    image_sha256 = "c" * 64
    try:
        await plugin.on_post_reply(
            ReplyContext(
                session_id="group_g1",
                group_id="g1",
                user_id="u1",
                reply_content="记住了",
                user_msg="«图片» 这是高松灯。",
                source_message_id=314,
                trigger_mode="correction",
                visual_evidence=[
                    {
                        "image_sha256": image_sha256,
                        "image_sha256_short": "cccccccc",
                        "visual_intent": "identify",
                        "visual_observation": "一名黑发少女",
                        "visual_identity": [],
                        "visual_ocr": "",
                        "visual_summary": "一名黑发少女",
                        "provenance": "visual_system",
                    }
                ],
            )
        )

        recalled = await visual_store.lookup_for_context(
            image_sha256,
            current_user_id="u1",
            current_group_id="g1",
        )
        assert recalled is not None
        assert recalled.entity_label == "高松灯"
        assert await card_store.get_entity_cards("user", "u1") == []
    finally:
        await plugin.on_shutdown(SimpleNamespace())  # type: ignore[arg-type]
        await visual_store.close()
        await card_store.close()


def test_production_builder_initializes_visual_identity_store_on_memory_database() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(build_chat_runtime)))
    constructor_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "VisualIdentityStore"
    ]
    published = any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "ctx"
            and target.attr == "visual_identity_store"
            for target in node.targets
        )
        for node in ast.walk(tree)
    )

    assert {field.name for field in fields(ReplyContext)} >= {
        "visual_evidence",
        "trigger_mode",
    }
    assert len(constructor_calls) == 1
    assert any(
        keyword.arg == "db_path"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "storage/memory_cards.db"
        for keyword in constructor_calls[0].keywords
    )
    assert published is True


@pytest.mark.asyncio
async def test_production_assembly_closes_owned_visual_identity_store_once() -> None:
    class CloseProbe:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    store = CloseProbe()
    ctx = SimpleNamespace(visual_identity_store=None)

    async def builder(_assembly: object) -> None:
        ctx.visual_identity_store = store

    assembly = create_chat_runtime_assembly(ctx, builder)
    await assembly.start()
    await assembly.close()
    await assembly.close()

    assert store.close_calls == 1


def test_human_correction_overrides_wrong_machine_identity() -> None:
    evidence = VisualEvidence(
        image_sha256="d" * 64,
        image_sha256_short="dddddddd",
        recognitions=(
            CharacterRecognition(
                matched=True,
                character_id="wrong",
                character_name="错误角色",
                difference=0.01,
                threshold=0.2,
            ),
        ),
        vision_description="一名黑发少女",
    )

    corrected = _merge_human_corrected_identity(evidence, "高松灯")

    assert corrected.identity_labels == ("高松灯",)
    assert corrected.vision_description == "一名黑发少女"


@pytest.mark.asyncio
async def test_actual_store_recall_enriches_repeated_image_evidence(tmp_path: Any) -> None:
    image_data = b"same-image-bytes"
    image_sha256 = hashlib.sha256(image_data).hexdigest()
    store = VisualIdentityStore(db_path=str(tmp_path / "visual_identity.db"))
    await store.init()
    try:
        await store.store_correction(
            image_sha256=image_sha256,
            entity_label="高松灯",
            correcting_user_id="u1",
            origin_group_id="g1",
            visual_evidence={
                "image_sha256": image_sha256,
                "provenance": "visual_system",
            },
            trigger={"mode": "correction", "evidence_count": 1},
        )

        evidence = await _describe_image_data(
            image_data,
            visual_identity_store=store,
            current_user_id="u1",
            current_group_id="g1",
        )

        assert evidence is not None
        assert evidence.image_sha256 == image_sha256
        assert evidence.identity_labels == ("高松灯",)
    finally:
        await store.close()
