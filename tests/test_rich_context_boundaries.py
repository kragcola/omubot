from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

import kernel.router as router
from kernel.router import _render_message
from services.media.character_recognizer import CharacterRecognition


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _record(
    message_id: int,
    nickname: str,
    user_id: int,
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "sender": {"user_id": user_id, "nickname": nickname},
        "message": segments,
    }


class _Bot:
    def __init__(self, messages: dict[int, object]) -> None:
        self.messages = messages
        self.get_msg_ids: list[int] = []
        self.call_api_calls: list[tuple[str, dict[str, Any]]] = []

    async def get_msg(self, message_id: int) -> object:
        assert isinstance(message_id, int)
        self.get_msg_ids.append(message_id)
        result = self.messages[message_id]
        if isinstance(result, Exception):
            raise result
        return result

    async def call_api(self, api: str, **data: Any) -> object:
        self.call_api_calls.append((api, data))
        raise AssertionError(f"reply completion must not use call_api: {api}")


def _reply(message_id: int, segments: list[MessageSegment]) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        sender=SimpleNamespace(user_id="30", nickname="Parent"),
        message=Message(segments),
    )


@pytest.mark.asyncio
async def test_active_reply_chain_keeps_three_quoted_layers_in_ancestor_order() -> None:
    parent = _reply(
        300,
        [
            MessageSegment("reply", {"id": "200"}),
            MessageSegment.text("parent body"),
        ],
    )
    bot = _Bot(
        {
            200: _record(
                200,
                "Grand",
                20,
                [
                    {"type": "reply", "data": {"id": "100"}},
                    {"type": "text", "data": {"text": "grand body"}},
                ],
            ),
            100: _record(
                100,
                "GreatGrand",
                10,
                [{"type": "text", "data": {"text": "great-grand body"}}],
            ),
        }
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current body")]),
        reply=parent,
        self_id="42",
        bot=bot,  # type: ignore[arg-type]
        vision_enabled=False,
    )
    text = _content_text(rendered)

    expected = ["great-grand body", "grand body", "parent body", "current body"]
    assert all(fragment in text for fragment in expected)
    assert [text.index(fragment) for fragment in expected] == sorted(
        text.index(fragment) for fragment in expected
    )
    assert bot.get_msg_ids == [200, 100]
    assert bot.call_api_calls == []


@pytest.mark.asyncio
async def test_active_reply_ancestor_renders_json_and_embedded_forward_without_api() -> None:
    card = json.dumps(
        {"meta": {"detail_1": {"title": "ancestor card", "desc": "card detail"}}}
    )
    parent = _reply(
        300,
        [
            MessageSegment("reply", {"id": "100"}),
            MessageSegment.text("parent body"),
        ],
    )
    bot = _Bot(
        {
            100: _record(
                100,
                "Grand",
                10,
                [
                    {"type": "json", "data": {"data": card}},
                    {
                        "type": "forward",
                        "data": {
                            "id": "embedded-wins",
                            "content": [
                                {
                                    "sender": {"user_id": 88, "nickname": "Forwarder"},
                                    "content": [
                                        {"type": "text", "data": {"text": "forward body"}}
                                    ],
                                }
                            ],
                        },
                    },
                ],
            )
        }
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current body")]),
        reply=parent,
        self_id="42",
        bot=bot,  # type: ignore[arg-type]
        vision_enabled=False,
    )
    text = _content_text(rendered)

    assert "ancestor card" in text and "card detail" in text
    assert "Forwarder" in text and "88" in text and "forward body" in text
    assert "parent body" in text and "current body" in text
    assert bot.get_msg_ids == [100]
    assert bot.call_api_calls == []


@pytest.mark.asyncio
async def test_active_reply_fetch_failure_keeps_parent_and_current_bodies() -> None:
    parent = _reply(
        300,
        [
            MessageSegment("reply", {"id": "404"}),
            MessageSegment.text("parent survives"),
        ],
    )
    bot = _Bot({404: RuntimeError("get_msg unavailable")})

    rendered = await _render_message(
        Message([MessageSegment.text("current survives")]),
        reply=parent,
        self_id="42",
        bot=bot,  # type: ignore[arg-type]
        vision_enabled=False,
    )
    text = _content_text(rendered)

    assert "#404" in text and "无法获取" in text
    assert "parent survives" in text and "current survives" in text
    assert bot.get_msg_ids == [404]


@pytest.mark.asyncio
async def test_active_reply_cycle_stops_without_refetching_adapter_parent() -> None:
    parent = _reply(
        300,
        [
            MessageSegment("reply", {"id": "200"}),
            MessageSegment.text("parent body"),
        ],
    )
    bot = _Bot(
        {
            200: _record(
                200,
                "Grand",
                20,
                [
                    {"type": "reply", "data": {"id": "300"}},
                    {"type": "text", "data": {"text": "grand body"}},
                ],
            )
        }
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current body")]),
        reply=parent,
        self_id="42",
        bot=bot,  # type: ignore[arg-type]
        vision_enabled=False,
    )
    text = _content_text(rendered)

    assert "#300" in text and "循环" in text
    assert "grand body" in text and "parent body" in text and "current body" in text
    assert bot.get_msg_ids == [200]


@pytest.mark.asyncio
async def test_active_repeated_reply_id_is_fetched_once() -> None:
    parent = _reply(
        300,
        [
            MessageSegment("reply", {"id": "100"}),
            MessageSegment("reply", {"id": "100"}),
            MessageSegment.text("parent body"),
        ],
    )
    bot = _Bot(
        {
            100: _record(
                100,
                "Grand",
                10,
                [{"type": "text", "data": {"text": "cached body"}}],
            )
        }
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current body")]),
        reply=parent,
        self_id="42",
        bot=bot,  # type: ignore[arg-type]
        vision_enabled=False,
    )
    text = _content_text(rendered)

    assert text.count("cached body") == 2
    assert "parent body" in text and "current body" in text
    assert bot.get_msg_ids == [100]


class _ImageSession:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get(self, url: str) -> object:
        self.urls.append(url)
        payload = self.payload

        class _Response:
            status = 200

            async def read(self) -> bytes:
                return payload

            async def __aenter__(self) -> _Response:
                return self

            async def __aexit__(self, *_exc: object) -> bool:
                return False

        return _Response()


class _QuotedImageCache:
    def __init__(self, path: Path, normalized: bytes) -> None:
        self.path = path
        self.normalized = normalized
        self.calls: list[tuple[bytes, str]] = []

    async def save_bytes(self, payload: bytes, *, file_id: str) -> dict[str, str]:
        self.calls.append((payload, file_id))
        self.path.write_bytes(self.normalized)
        return {
            "type": "image_ref",
            "path": str(self.path),
            "media_type": "image/png",
        }


class _AncestorRecognizer:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    async def identify(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> list[CharacterRecognition]:
        self.payloads.append(image_data)
        assert media_type == "image/png"
        return [
            CharacterRecognition(
                matched=True,
                character_id="ancestor_hero",
                character_name="Ancestor Hero",
                relation="known",
                context_label="Ancestor Work",
                difference=0.02,
                threshold=0.18,
            )
        ]


@pytest.mark.asyncio
async def test_active_reply_ancestor_image_uses_existing_enrichment_and_keeps_ref(
    tmp_path: Path,
) -> None:
    raw = b"raw-ancestor-image"
    normalized = b"normalized-ancestor-image"
    session = _ImageSession(raw)
    image_cache = _QuotedImageCache(tmp_path / "ancestor.png", normalized)
    recognizer = _AncestorRecognizer()
    parent = _reply(300, [MessageSegment("reply", {"id": "100"})])
    bot = _Bot(
        {
            100: _record(
                100,
                "Grand",
                10,
                [
                    {
                        "type": "image",
                        "data": {
                            "url": "https://example.test/ancestor.png",
                            "file": "ancestor-source.png",
                        },
                    }
                ],
            )
        }
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current body")]),
        reply=parent,
        session=session,  # type: ignore[arg-type]
        self_id="42",
        character_recognizer=recognizer,
        bot=bot,  # type: ignore[arg-type]
        image_cache=image_cache,
        vision_enabled=True,
    )
    text = _content_text(rendered)

    assert "Ancestor Hero" in text and "Ancestor Work" in text
    assert "current body" in text
    assert isinstance(rendered, list)
    assert any(
        block.get("type") == "image_ref" and block.get("path") == str(image_cache.path)
        for block in rendered
    )
    assert session.urls == ["https://example.test/ancestor.png"]
    assert image_cache.calls == [(raw, "ancestor-source")]
    assert recognizer.payloads == [normalized]
    assert bot.get_msg_ids == [100]


@pytest.mark.asyncio
async def test_two_missing_url_images_from_same_quote_refetch_source_once() -> None:
    parent = _reply(
        300,
        [
            MessageSegment("image", {"file": "one.png", "summary": "[one]"}),
            MessageSegment("image", {"file": "two.png", "summary": "[two]"}),
            MessageSegment.text("parent body"),
        ],
    )
    bot = _Bot(
        {
            300: {
                "message": [
                    {
                        "type": "image",
                        "data": {"url": "https://example.test/refetched.png"},
                    }
                ]
            }
        }
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current body")]),
        reply=parent,
        self_id="42",
        bot=bot,  # type: ignore[arg-type]
        vision_enabled=True,
    )
    text = _content_text(rendered)

    assert "parent body" in text and "current body" in text
    assert bot.get_msg_ids == [300]


@pytest.mark.asyncio
async def test_missing_url_image_refetch_timeout_is_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_limits = router.RichRenderLimits
    monkeypatch.setattr(
        router,
        "RichRenderLimits",
        lambda **kwargs: real_limits(resolve_timeout_s=0.001, **kwargs),
    )
    parent = _reply(
        300,
        [
            MessageSegment("image", {"file": "slow.png", "summary": "[slow image]"}),
            MessageSegment.text("parent survives"),
        ],
    )

    class _SlowBot(_Bot):
        async def get_msg(self, message_id: int) -> object:
            self.get_msg_ids.append(message_id)
            await asyncio.sleep(0.05)
            return {"message": []}

    bot = _SlowBot({})
    try:
        rendered = await asyncio.wait_for(
            _render_message(
                Message([MessageSegment.text("current survives")]),
                reply=parent,
                self_id="42",
                bot=bot,  # type: ignore[arg-type]
                vision_enabled=True,
            ),
            timeout=0.02,
        )
    except TimeoutError:
        pytest.fail("quoted image refetch timeout escaped instead of degrading locally")
    text = _content_text(rendered)

    assert "slow image" in text
    assert "parent survives" in text and "current survives" in text
    assert bot.get_msg_ids == [300]


@pytest.mark.asyncio
@pytest.mark.parametrize("stall", ["recognizer", "vision"])
async def test_quoted_image_enrichment_timeout_keeps_saved_image_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stall: str,
) -> None:
    real_limits = router.RichRenderLimits

    def short_image_limits(**kwargs: Any) -> object:
        kwargs["image_timeout_s"] = 0.001
        return real_limits(**kwargs)

    monkeypatch.setattr(router, "RichRenderLimits", short_image_limits)
    raw = b"raw-timeout-image"
    normalized = b"normalized-timeout-image"
    session = _ImageSession(raw)
    image_cache = _QuotedImageCache(tmp_path / f"{stall}.png", normalized)

    class _Recognizer:
        async def identify(
            self,
            _image_data: bytes,
            *,
            media_type: str = "image/jpeg",
        ) -> list[CharacterRecognition]:
            if stall == "recognizer":
                await asyncio.sleep(0.05)
            return []

    class _Vision:
        async def describe_image(
            self,
            _image_data: bytes,
            media_type: str = "image/jpeg",
            prompt: str | None = None,
        ) -> str:
            if stall == "vision":
                await asyncio.sleep(0.05)
            return "late description"

    parent = _reply(
        300,
        [
            MessageSegment(
                "image",
                {
                    "url": "https://example.test/timeout.png",
                    "file": "timeout-source.png",
                    "summary": "[timeout image]",
                },
            ),
            MessageSegment.text("parent survives"),
        ],
    )

    rendered = await _render_message(
        Message([MessageSegment.text("current survives")]),
        reply=parent,
        session=session,  # type: ignore[arg-type]
        self_id="42",
        vision_client=_Vision(),
        character_recognizer=_Recognizer(),
        image_cache=image_cache,
        vision_enabled=True,
    )
    text = _content_text(rendered)

    assert "parent survives" in text and "current survives" in text
    assert image_cache.calls == [(raw, "timeout-source")]
    assert image_cache.path.exists()
    assert isinstance(rendered, list)
    assert any(
        block.get("type") == "image_ref" and block.get("path") == str(image_cache.path)
        for block in rendered
    )
