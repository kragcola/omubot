from __future__ import annotations

from pathlib import Path
from typing import cast

import aiohttp
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from kernel.router import _render_message
from services.media.character_recognizer import CharacterRecognition


class _FakeImageCache:
    def __init__(self, image_path: Path) -> None:
        self._image_path = image_path

    async def save(self, session, url: str, file_id: str) -> dict[str, str]:
        del session, url, file_id
        return {
            "type": "image_ref",
            "path": str(self._image_path),
            "media_type": "image/png",
        }


class _FakeCharacterRecognizer:
    async def identify(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> list[CharacterRecognition]:
        del image_data, media_type
        return [CharacterRecognition(
            matched=True,
            character_id="emu_otori",
            character_name="凤笑梦",
            relation="self",
            context_label="世界计划 / ワンダショ",
            difference=0.02,
            threshold=0.18,
        )]

    # Also expose a single-result wrapper used by some tests
    async def identify_single(
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> CharacterRecognition | None:
        del image_data, media_type
        return CharacterRecognition(
            matched=True,
            character_id="emu_otori",
            character_name="凤笑梦",
            relation="self",
            context_label="世界计划 / ワンダショ",
            difference=0.02,
            threshold=0.18,
        )


class _FakeVisionClient:
    async def describe_image(
        self,
        image_data: bytes,
        media_type: str = "image/jpeg",
        prompt: str | None = None,
    ) -> str | None:
        del image_data, media_type, prompt
        return "开心地跳起来"


@pytest.mark.asyncio
async def test_render_message_prefixes_vl_description_with_character_name(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-png")
    message = Message(
        [
            MessageSegment(
                "image",
                {
                    "url": "http://example.invalid/sample.png",
                    "file": "sample.png",
                },
            )
        ]
    )

    rendered = await _render_message(
        message,
        session=cast(aiohttp.ClientSession, object()),
        vision_client=_FakeVisionClient(),
        character_recognizer=_FakeCharacterRecognizer(),
        vision_enabled=True,
        image_cache=_FakeImageCache(image_path),
    )

    text = rendered if isinstance(rendered, str) else "".join(
        block.get("text", "") for block in rendered if isinstance(block, dict)
    )
    assert "凤笑梦" in text
    assert "世界计划 / ワンダショ" in text
    assert "开心地跳起来" in text


@pytest.mark.asyncio
async def test_render_message_prefers_context_label_over_broad_work(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-png")
    message = Message([
        MessageSegment("image", {"url": "http://example.invalid/sample.png", "file": "sample.png"})
    ])

    class _Recognizer:
        async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg") -> list[CharacterRecognition]:
            del image_data, media_type
            return [CharacterRecognition(
                matched=True,
                character_id="xingchen",
                character_name="星尘",
                relation="known",
                work="中V",
                context_label="中V / 五维介质",
            )]

    rendered = await _render_message(
        message,
        session=cast(aiohttp.ClientSession, object()),
        vision_client=_FakeVisionClient(),
        character_recognizer=_Recognizer(),
        vision_enabled=True,
        image_cache=_FakeImageCache(image_path),
    )

    text = rendered if isinstance(rendered, str) else "".join(
        block.get("text", "") for block in rendered if isinstance(block, dict)
    )
    assert "星尘（中V / 五维介质）" in text
    assert "星尘（中V）" not in text


class _Sender:
    def __init__(self, user_id: str, nickname: str) -> None:
        self.user_id = user_id
        self.nickname = nickname


class _Reply:
    """Minimal stand-in for nonebot's Reply object."""

    def __init__(self, message: Message, *, message_id: int, sender: _Sender) -> None:
        self.message = message
        self.message_id = message_id
        self.sender = sender


class _FakeSession:
    """aiohttp.ClientSession.get(url) stand-in returning fixed image bytes."""

    def __init__(self, payload: bytes = b"fake-quoted-png") -> None:
        self._payload = payload

    def get(self, url: str):
        del url
        payload = self._payload

        class _Resp:
            status = 200

            async def read(self) -> bytes:
                return payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> bool:
                return False

        return _Resp()


@pytest.mark.asyncio
async def test_quoted_reply_image_runs_character_recognition() -> None:
    """A quoted image (@bot 引用图 这是谁) must go through CCIP recognition,
    not just plain VL — previously the quoted branch skipped the recognizer."""
    quoted = Message(
        [MessageSegment("image", {"url": "http://example.invalid/q.png", "file": "q.png"})]
    )
    reply = _Reply(quoted, message_id=12345, sender=_Sender("99999", "群友"))
    message = Message([MessageSegment.text("这是谁")])

    rendered = await _render_message(
        message,
        reply=reply,
        session=cast(aiohttp.ClientSession, _FakeSession()),
        self_id="384801062",
        vision_client=_FakeVisionClient(),
        character_recognizer=_FakeCharacterRecognizer(),
        vision_enabled=True,
    )

    text = rendered if isinstance(rendered, str) else "".join(
        block.get("text", "") for block in rendered if isinstance(block, dict)
    )
    # Quoted preview carries the recognized character name + VL desc.
    assert "凤笑梦" in text
    assert "QUOTED_MSG" in text


@pytest.mark.asyncio
async def test_quoted_reply_image_refetches_stale_url() -> None:
    """When the quoted image segment has no url, _render_message must re-fetch
    the original message by message_id via bot.get_msg to recover it."""
    # Quoted segment carries NO url (stale) — only a summary.
    quoted = Message([MessageSegment("image", {"file": "q.png", "summary": "[动画表情]"})])
    reply = _Reply(quoted, message_id=678, sender=_Sender("99999", "群友"))
    message = Message([MessageSegment.text("这是谁")])

    class _FakeBot:
        def __init__(self) -> None:
            self.called_with: int | None = None

        async def get_msg(self, message_id: int):
            self.called_with = message_id
            # Authoritative copy now has a working url.
            return {
                "message": [
                    {"type": "image", "data": {"url": "http://example.invalid/fresh.png"}}
                ]
            }

    bot = _FakeBot()
    rendered = await _render_message(
        message,
        reply=reply,
        session=cast(aiohttp.ClientSession, _FakeSession()),
        self_id="384801062",
        vision_client=_FakeVisionClient(),
        character_recognizer=_FakeCharacterRecognizer(),
        bot=cast(object, bot),  # type: ignore[arg-type]
        vision_enabled=True,
    )

    text = rendered if isinstance(rendered, str) else "".join(
        block.get("text", "") for block in rendered if isinstance(block, dict)
    )
    assert bot.called_with == 678  # get_msg was invoked to recover the url
    assert "凤笑梦" in text
