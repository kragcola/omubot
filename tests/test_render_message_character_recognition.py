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
    ) -> CharacterRecognition | None:
        del image_data, media_type
        return CharacterRecognition(
            matched=True,
            character_id="emu_otori",
            character_name="凤笑梦",
            relation="self",
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
    assert "开心地跳起来" in text
