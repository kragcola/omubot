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


class _FakeQuotedImageCache:
    def __init__(self, image_path: Path, normalized_payload: bytes) -> None:
        self._image_path = image_path
        self._normalized_payload = normalized_payload
        self.calls: list[tuple[bytes, str]] = []
        self._image_path.write_bytes(normalized_payload)

    async def save_bytes(self, image_data: bytes, file_id: str) -> dict[str, str]:
        self.calls.append((image_data, file_id))
        self._image_path.write_bytes(self._normalized_payload)
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


class _FakeStickerStore:
    def __init__(self, *, description: str, source: str = "auto") -> None:
        self._description = description
        self._source = source

    def lookup_by_hash(self, image_data: bytes) -> str | None:
        del image_data
        return "stk_test"

    def get(self, sticker_id: str) -> dict[str, str] | None:
        assert sticker_id == "stk_test"
        return {
            "description": self._description,
            "usage_hint": "",
            "ocr_text": "",
            "source": self._source,
        }


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


@pytest.mark.asyncio
async def test_render_message_sticker_hit_does_not_short_circuit_identity(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-png")
    message = Message([
        MessageSegment("image", {"url": "http://example.invalid/sample.png", "file": "sample.png"})
    ])

    class _Recognizer:
        def __init__(self) -> None:
            self.calls = 0

        async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg") -> list[CharacterRecognition]:
            del image_data, media_type
            self.calls += 1
            return [CharacterRecognition(
                matched=True,
                character_id="hatsune_miku",
                character_name="初音未来",
                relation="known",
                context_label="Project SEKAI / Virtual Singer",
                difference=0.04,
                threshold=0.18,
            )]

    recognizer = _Recognizer()
    rendered = await _render_message(
        message,
        session=cast(aiohttp.ClientSession, object()),
        vision_client=_FakeVisionClient(),
        character_recognizer=recognizer,
        sticker_store=_FakeStickerStore(
            description="旧bot迁移 | 使用31次 | 表情包版吓尿了,震惊到瞳孔地震",
            source="migrated:v1:usage_31",
        ),
        vision_enabled=True,
        image_cache=_FakeImageCache(image_path),
    )

    text = rendered if isinstance(rendered, str) else "".join(
        block.get("text", "") for block in rendered if isinstance(block, dict)
    )
    assert recognizer.calls == 1
    assert "初音未来（Project SEKAI / Virtual Singer）" in text
    assert "瞳孔地震" not in text


@pytest.mark.asyncio
async def test_render_message_surfaces_partial_multi_character_identity(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-png")
    message = Message([
        MessageSegment("image", {"url": "http://example.invalid/sample.png", "file": "sample.png"})
    ])

    class _Recognizer:
        async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg") -> list[CharacterRecognition]:
            del image_data, media_type
            return [
                CharacterRecognition(
                    matched=True,
                    character_id="hatsune_miku",
                    character_name="初音未来",
                    relation="known",
                    context_label="Project SEKAI / Virtual Singer",
                    difference=0.162,
                    threshold=0.178,
                    detection_count=4,
                ),
                CharacterRecognition(
                    matched=False,
                    candidate_character_id="kasane_teto",
                    candidate_character_name="重音テト",
                    difference=0.231,
                    threshold=0.178,
                    detection_count=4,
                ),
                CharacterRecognition(
                    matched=False,
                    candidate_character_id="tsurumaki_maki",
                    candidate_character_name="弦巻マキ",
                    difference=0.247,
                    threshold=0.178,
                    detection_count=4,
                ),
                CharacterRecognition(
                    matched=False,
                    candidate_character_id="one",
                    candidate_character_name="ONE",
                    difference=0.225,
                    threshold=0.178,
                    detection_count=4,
                ),
            ]

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
    assert "检测到4个角色/头像" in text
    assert "可信识别：初音未来（Project SEKAI / Virtual Singer）" in text
    assert "其余3个未达到置信阈值" in text
    assert "低置信候选：重音テト 0.231、弦巻マキ 0.247、ONE 0.225" in text
    assert "开心地跳起来" in text


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


@pytest.mark.asyncio
async def test_quoted_reply_image_uses_normalized_image_cache_bytes(tmp_path: Path) -> None:
    raw_payload = b"raw-quoted-image"
    normalized_payload = b"normalized-cache-image"
    image_cache = _FakeQuotedImageCache(tmp_path / "quoted.png", normalized_payload)
    quoted = Message(
        [MessageSegment("image", {"url": "http://example.invalid/q.png", "file": "quoted-file.png"})]
    )
    reply = _Reply(quoted, message_id=2468, sender=_Sender("99999", "群友"))
    message = Message([MessageSegment.text("这是谁")])

    class _Recognizer:
        def __init__(self) -> None:
            self.seen_payloads: list[bytes] = []
            self.seen_media_types: list[str] = []

        async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg") -> list[CharacterRecognition]:
            self.seen_payloads.append(image_data)
            self.seen_media_types.append(media_type)
            return [CharacterRecognition(
                matched=True,
                character_id="emu_otori",
                character_name="凤笑梦",
                relation="self",
                context_label="世界计划 / ワンダショ",
            )]

    recognizer = _Recognizer()
    rendered = await _render_message(
        message,
        reply=reply,
        session=cast(aiohttp.ClientSession, _FakeSession(payload=raw_payload)),
        self_id="384801062",
        vision_client=_FakeVisionClient(),
        character_recognizer=recognizer,
        image_cache=image_cache,
        vision_enabled=True,
    )

    text = rendered if isinstance(rendered, str) else "".join(
        block.get("text", "") for block in rendered if isinstance(block, dict)
    )
    assert image_cache.calls == [(raw_payload, "quoted-file")]
    assert recognizer.seen_payloads == [normalized_payload]
    assert recognizer.seen_media_types == ["image/png"]
    assert "凤笑梦" in text


@pytest.mark.asyncio
async def test_quoted_reply_visual_evidence_is_not_truncated_to_generic_preview_cap() -> None:
    quoted = Message(
        [MessageSegment("image", {"url": "http://example.invalid/q.png", "file": "q.png"})]
    )
    reply = _Reply(quoted, message_id=13579, sender=_Sender("99999", "群友"))
    message = Message([MessageSegment.text("这是谁")])

    class _Recognizer:
        async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg") -> list[CharacterRecognition]:
            del image_data, media_type
            return [
                CharacterRecognition(
                    matched=True,
                    character_id="hatsune_miku",
                    character_name="初音未来",
                    relation="known",
                    context_label="Project SEKAI / Virtual Singer",
                    difference=0.162,
                    threshold=0.178,
                    detection_count=4,
                ),
                CharacterRecognition(
                    matched=False,
                    candidate_character_id="kasane_teto",
                    candidate_character_name="重音テト",
                    difference=0.231,
                    threshold=0.178,
                    detection_count=4,
                ),
                CharacterRecognition(
                    matched=False,
                    candidate_character_id="tsurumaki_maki",
                    candidate_character_name="弦巻マキ",
                    difference=0.247,
                    threshold=0.178,
                    detection_count=4,
                ),
                CharacterRecognition(
                    matched=False,
                    candidate_character_id="one",
                    candidate_character_name="ONE",
                    difference=0.225,
                    threshold=0.178,
                    detection_count=4,
                ),
            ]

    rendered = await _render_message(
        message,
        reply=reply,
        session=cast(aiohttp.ClientSession, _FakeSession()),
        self_id="384801062",
        vision_client=_FakeVisionClient(),
        character_recognizer=_Recognizer(),
        vision_enabled=True,
    )

    text = rendered if isinstance(rendered, str) else "".join(
        block.get("text", "") for block in rendered if isinstance(block, dict)
    )
    assert "检测到4个角色/头像" in text
    assert "其余3个未达到置信阈值" in text
    assert "低置信候选：重音テト 0.231、弦巻マキ 0.247、ONE 0.225" in text
    assert "Virtual …" not in text
