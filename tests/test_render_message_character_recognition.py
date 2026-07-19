from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import aiohttp
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from kernel.router import _render_message
from services.llm.client import content_text
from services.media.character_recognizer import CharacterRecognition
from services.media.visual_evidence import (
    collect_image_ref_sidechannels,
    format_visual_evidence_system_block,
)


def _text_of(rendered: Any) -> str:
    if isinstance(rendered, str):
        return rendered
    return content_text(rendered)


def _image_refs(rendered: Any) -> list[dict[str, Any]]:
    if not isinstance(rendered, list):
        return []
    return [
        block
        for block in rendered
        if isinstance(block, dict) and block.get("type") == "image_ref"
    ]


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

    text = _text_of(rendered)
    assert "凤笑梦" not in text
    assert "开心地跳起来" not in text
    assert "«图片»" in text
    refs = _image_refs(rendered)
    assert refs
    ref = refs[0]
    assert ref.get("provenance") == "visual_system"
    assert "凤笑梦" in " ".join(ref.get("visual_identity") or [])
    assert "开心地跳起来" in (ref.get("visual_observation") or ref.get("visual_summary") or "")
    assert len(str(ref.get("image_sha256") or "")) == 64


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

    text = _text_of(rendered)
    assert "星尘" not in text or text.strip() in {"«图片»", ""}
    assert "«图片»" in text
    refs = _image_refs(rendered)
    assert refs
    identity = " ".join(refs[0].get("visual_identity") or [])
    summary = str(refs[0].get("visual_summary") or "")
    assert "星尘（中V / 五维介质）" in identity or "星尘（中V / 五维介质）" in summary
    assert "星尘（中V）" not in identity and "星尘（中V）" not in summary


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

    text = _text_of(rendered)
    assert recognizer.calls == 1
    assert "瞳孔地震" not in text
    assert "«图片»" in text
    refs = _image_refs(rendered)
    assert refs
    blob = " ".join(
        [
            " ".join(refs[0].get("visual_identity") or []),
            str(refs[0].get("visual_summary") or ""),
            str(refs[0].get("visual_observation") or ""),
        ]
    )
    assert "初音未来（Project SEKAI / Virtual Singer）" in blob
    assert "瞳孔地震" not in blob


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
                    difference=0.120,
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

    text = _text_of(rendered)
    assert "检测到" not in text
    assert "置信" not in text
    assert "0.231" not in text
    assert "«图片»" in text
    refs = _image_refs(rendered)
    assert refs
    summary = str(refs[0].get("visual_summary") or "")
    obs = str(refs[0].get("visual_observation") or "")
    blob = f"{summary} {obs}"
    assert "画面中约有4个" in blob or "未能确认" in blob or "初音未来" in blob
    assert "低置信候选" not in blob
    assert "置信阈值" not in blob
    assert "0.231" not in blob
    assert "0.247" not in blob
    assert "0.225" not in blob
    # system block composition must also stay clean
    block = format_visual_evidence_system_block(refs, user_text="图里是什么") or ""
    for bad in ("低置信候选", "置信阈值", "0.231", "threshold=", "distance="):
        assert bad not in block


class _Sender:
    def __init__(self, user_id: str, nickname: str) -> None:
        self.user_id = user_id
        self.nickname = nickname


class _Reply:
    def __init__(self, message: Message, *, message_id: int, sender: _Sender) -> None:
        self.message = message
        self.message_id = message_id
        self.sender = sender


class _FakeSession:
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

    text = _text_of(rendered)
    assert "QUOTED_MSG" in text
    assert "[图片]" in text
    assert "凤笑梦" not in text  # not user-authored prose
    refs = _image_refs(rendered)
    assert refs, "quoted image_ref with side-channel must be present"
    blob = " ".join(
        [
            " ".join(refs[0].get("visual_identity") or []),
            str(refs[0].get("visual_summary") or ""),
        ]
    )
    assert "凤笑梦" in blob
    assert refs[0].get("provenance") == "visual_system"


@pytest.mark.asyncio
async def test_quoted_reply_image_refetches_stale_url() -> None:
    quoted = Message([MessageSegment("image", {"file": "q.png", "summary": "[动画表情]"})])
    reply = _Reply(quoted, message_id=678, sender=_Sender("99999", "群友"))
    message = Message([MessageSegment.text("这是谁")])

    class _FakeBot:
        def __init__(self) -> None:
            self.called_with: int | None = None

        async def get_msg(self, message_id: int):
            self.called_with = message_id
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

    text = _text_of(rendered)
    assert bot.called_with == 678
    assert "凤笑梦" not in text
    refs = _image_refs(rendered)
    assert refs
    assert "凤笑梦" in " ".join(refs[0].get("visual_identity") or [])


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

    text = _text_of(rendered)
    assert image_cache.calls == [(raw_payload, "quoted-file")]
    assert recognizer.seen_payloads == [normalized_payload]
    assert recognizer.seen_media_types == ["image/png"]
    assert "凤笑梦" not in text
    assert isinstance(rendered, list)
    assert any(
        block.get("type") == "image_ref"
        and block.get("path") == str(tmp_path / "quoted.png")
        and block.get("media_type") == "image/png"
        and block.get("provenance") == "visual_system"
        for block in rendered
    )


@pytest.mark.asyncio
async def test_quoted_reply_keeps_image_ref_when_description_pipeline_fails(tmp_path: Path) -> None:
    image_cache = _FakeQuotedImageCache(tmp_path / "quoted.png", b"normalized-cache-image")
    quoted = Message(
        [MessageSegment("image", {"url": "http://example.invalid/q.png", "file": "quoted-file.png"})]
    )
    reply = _Reply(quoted, message_id=2468, sender=_Sender("99999", "群友"))

    class _BrokenRecognizer:
        async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg"):
            del image_data, media_type
            raise RuntimeError("recognizer unavailable")

    rendered = await _render_message(
        Message([MessageSegment.text("这是谁")]),
        reply=reply,
        session=cast(aiohttp.ClientSession, _FakeSession()),
        self_id="384801062",
        vision_client=_FakeVisionClient(),
        character_recognizer=_BrokenRecognizer(),
        image_cache=image_cache,
        vision_enabled=True,
    )

    assert isinstance(rendered, list)
    assert any(
        block.get("type") == "image_ref"
        and block.get("path") == str(tmp_path / "quoted.png")
        for block in rendered
    )


@pytest.mark.asyncio
async def test_borderline_character_hit_is_not_rendered_as_trusted_identity(tmp_path: Path) -> None:
    image_path = tmp_path / "purisesu.jpg"
    image_path.write_bytes(b"fake-purisesu-image")

    class _BorderlineRecognizer:
        async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg"):
            del image_data, media_type
            return [CharacterRecognition(
                matched=True,
                character_id="fuji_miyako",
                character_name="藤 都子",
                relation="known",
                context_label="BanG Dream! / 夢限大みゅーたいぷ",
                difference=0.17129391431808472,
                threshold=0.17847511429108218,
                detection_count=1,
            )]

    rendered = await _render_message(
        Message([
            MessageSegment("image", {"url": "http://example.invalid/p.jpg", "file": "p.jpg"}),
            MessageSegment.text("这是谁"),
        ]),
        session=cast(aiohttp.ClientSession, object()),
        vision_client=_FakeVisionClient(),
        character_recognizer=_BorderlineRecognizer(),
        vision_enabled=True,
        image_cache=_FakeImageCache(image_path),
    )

    text = _text_of(rendered)
    assert "藤 都子" not in text
    assert "«图片»" in text
    refs = _image_refs(rendered)
    assert refs
    identity = refs[0].get("visual_identity") or []
    summary = str(refs[0].get("visual_summary") or "")
    assert "藤 都子" not in identity
    assert "藤 都子" not in summary
    assert "不确定" in summary or "未能确认" in summary or not identity
    # describe/identify intent can expose observation without trusted label
    assert "开心地跳起来" in (refs[0].get("visual_observation") or summary or "")


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
                    difference=0.120,
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

    text = _text_of(rendered)
    assert "检测到4个角色/头像" not in text
    assert "0.231" not in text
    refs = _image_refs(rendered)
    assert refs
    summary = str(refs[0].get("visual_summary") or "")
    assert "Virtual …" not in summary
    assert "低置信候选" not in summary
    assert "置信阈值" not in summary
    assert "0.231" not in summary
    assert "初音未来" in summary or "初音未来" in " ".join(refs[0].get("visual_identity") or [])


@pytest.mark.asyncio
async def test_content_text_never_includes_vision_prose(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"fake-png")
    message = Message([
        MessageSegment("image", {"url": "http://example.invalid/sample.png", "file": "sample.png"}),
        MessageSegment.text("看看这是谁"),
    ])
    rendered = await _render_message(
        message,
        session=cast(aiohttp.ClientSession, object()),
        vision_client=_FakeVisionClient(),
        character_recognizer=_FakeCharacterRecognizer(),
        vision_enabled=True,
        image_cache=_FakeImageCache(image_path),
    )
    text = content_text(rendered) if not isinstance(rendered, str) else rendered
    assert "看看这是谁" in text
    assert "凤笑梦" not in text
    assert "开心地跳起来" not in text
    assert "«图片" in text or "«图片»" in text
    # collect sidechannels still works for client inject
    refs = collect_image_ref_sidechannels(rendered if isinstance(rendered, list) else [])
    assert refs and refs[0].get("provenance") == "visual_system"
