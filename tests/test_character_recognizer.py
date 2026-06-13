from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.media.character_recognizer import CharacterRecognizer


class _StubCharacterRecognizer(CharacterRecognizer):
    async def _request_identify(  # type: ignore[override]
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> dict[str, object] | None:
        del image_data, media_type
        return {
            "matched": True,
            "character_id": "emu_otori",
            "character_name": "remote-name",
            "difference": 0.02,
            "threshold": 0.18,
            "cache_hit": False,
            "registry_version": "test-version",
            "api_version": "2026-05-31.v1",
            "source": "ccip-sidecar",
        }


class _StubMultiCharacterRecognizer(CharacterRecognizer):
    async def _request_identify_multi(  # type: ignore[override]
        self,
        image_data: bytes,
        *,
        media_type: str = "image/jpeg",
    ) -> dict[str, object] | None:
        del image_data, media_type
        return {
            "matched": True,
            "detection_count": 2,
            "threshold": 0.178,
            "registry_version": "multi-test-version",
            "api_version": "2026-06-01.v1",
            "source": "ccip-sidecar",
            "characters": [
                {
                    "matched": True,
                    "character_id": "hatsune_miku",
                    "character_name": "remote-miku",
                    "candidate_character_id": "hatsune_miku",
                    "candidate_character_name": "remote-miku",
                    "difference": 0.12,
                    "detection_score": 0.91,
                    "bbox": [10, 20, 30, 40],
                    "crop_padding": 0.0,
                    "crop_bbox": [10, 20, 30, 40],
                },
                {
                    "matched": False,
                    "character_id": None,
                    "character_name": None,
                    "candidate_character_id": "kasane_teto",
                    "candidate_character_name": "重音テト",
                    "difference": 0.231,
                    "detection_score": 0.88,
                    "bbox": [50, 60, 70, 80],
                    "crop_padding": 0.3,
                    "crop_bbox": [44, 54, 76, 86],
                },
            ],
        }


@pytest.mark.asyncio
async def test_character_recognizer_prefers_local_name_and_relation(tmp_path: Path) -> None:
    pack_dir = tmp_path / "character_packs" / "pjsk.charpack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "character_id": "emu_otori",
                        "name": "凤笑梦",
                        "relation": "self",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    recognizer = _StubCharacterRecognizer(
        base_url="http://127.0.0.1:8620",
        packs_dir=tmp_path / "character_packs",
        multi_char_enabled=False,  # test single-char path
    )

    result = await recognizer.identify(b"fake-image")

    assert result is not None
    assert len(result) == 1
    r = result[0]
    assert r.matched is True
    assert r.character_id == "emu_otori"
    assert r.character_name == "凤笑梦"
    assert r.relation == "self"
    assert r.registry_version == "test-version"
    assert r.api_version == "2026-05-31.v1"


@pytest.mark.asyncio
async def test_character_recognizer_inherits_manifest_work_and_relation(tmp_path: Path) -> None:
    pack_dir = tmp_path / "character_packs" / "pjsk.charpack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pack": "pjsk",
                "work": "プロジェクトセカイ",
                "relation_default": "known",
                "characters": [
                    {
                        "character_id": "emu_otori",
                        "name": "凤笑梦",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    recognizer = _StubCharacterRecognizer(
        base_url="http://127.0.0.1:8620",
        packs_dir=tmp_path / "character_packs",
        multi_char_enabled=False,
    )

    result = await recognizer.identify(b"fake-image")

    assert len(result) == 1
    r = result[0]
    assert r.relation == "known"
    assert r.work == "プロジェクトセカイ"


@pytest.mark.asyncio
async def test_multi_character_recognizer_preserves_low_confidence_candidate(tmp_path: Path) -> None:
    pack_dir = tmp_path / "character_packs" / "pjsk.charpack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pack": "pjsk",
                "work": "Project SEKAI",
                "characters": [
                    {
                        "character_id": "hatsune_miku",
                        "name": "初音未来",
                        "relation": "known",
                        "context_label": "Project SEKAI / Virtual Singer",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    recognizer = _StubMultiCharacterRecognizer(
        base_url="http://127.0.0.1:8620",
        packs_dir=tmp_path / "character_packs",
        multi_char_enabled=True,
    )

    result = await recognizer.identify(b"fake-image")

    assert len(result) == 2
    assert result[0].matched is True
    assert result[0].character_name == "初音未来"
    assert result[0].candidate_character_name == "初音未来"
    assert result[0].detection_count == 2
    assert result[0].bbox == (10.0, 20.0, 30.0, 40.0)
    assert result[0].crop_padding == pytest.approx(0.0)
    assert result[0].crop_bbox == (10.0, 20.0, 30.0, 40.0)
    assert result[1].matched is False
    assert result[1].character_id is None
    assert result[1].candidate_character_id == "kasane_teto"
    assert result[1].candidate_character_name == "重音テト"
    assert result[1].difference == pytest.approx(0.231)
    assert result[1].bbox == (50.0, 60.0, 70.0, 80.0)
    assert result[1].crop_padding == pytest.approx(0.3)
    assert result[1].crop_bbox == (44.0, 54.0, 76.0, 86.0)
