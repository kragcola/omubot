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
    )

    result = await recognizer.identify(b"fake-image")

    assert result is not None
    assert result.matched is True
    assert result.character_id == "emu_otori"
    assert result.character_name == "凤笑梦"
    assert result.relation == "self"
    assert result.registry_version == "test-version"
    assert result.api_version == "2026-05-31.v1"
