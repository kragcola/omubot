from __future__ import annotations

import pytest

from services.media.animetrace_client import AnimeTraceClient, AnimeTraceMatch
from services.media.character_recognizer import CharacterRecognition, CharacterRecognizer


def test_animetrace_parse_hit() -> None:
    payload = {
        "code": 0,
        "data": [
            {"box": [0, 0, 1, 1], "box_id": "b1",
             "character": [{"character": "初音ミク", "work": "VOCALOID"}]},
        ],
        "ai": False,
        "trace_id": "t1",
    }
    m = AnimeTraceClient._parse(payload)
    assert m == AnimeTraceMatch(character_name="初音ミク", work="VOCALOID", box_id="b1")


def test_animetrace_parse_rate_limited_and_empty() -> None:
    assert AnimeTraceClient._parse({"code": 17737}) is None
    assert AnimeTraceClient._parse({"code": 0, "data": []}) is None
    assert AnimeTraceClient._parse({"code": 1, "data": [{"character": [{"character": "x"}]}]}) is None
    assert AnimeTraceClient._parse("nonsense") is None


class _StubAT:
    def __init__(self, match: AnimeTraceMatch | None) -> None:
        self._match = match

    async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg"):
        del image_data, media_type
        return self._match


class _MergeRecognizer(CharacterRecognizer):
    """CCIP stub controllable per-test via `ccip_payload`."""
    ccip_payload: dict | None = None

    async def _request_identify(self, image_data, *, media_type="image/jpeg"):
        del image_data, media_type
        return type(self).ccip_payload


def _ccip_hit(cid="emu", name="凤笑梦"):
    return {"matched": True, "character_id": cid, "character_name": name,
            "difference": 0.02, "threshold": 0.18, "source": "ccip-sidecar"}


def _ccip_miss():
    return {"matched": False, "character_id": None, "character_name": None,
            "difference": 0.9, "threshold": 0.18, "source": "ccip-sidecar"}


@pytest.mark.asyncio
async def test_merge_ccip_hit_wins_over_animetrace(tmp_path) -> None:
    _MergeRecognizer.ccip_payload = _ccip_hit()
    rec = _MergeRecognizer(
        base_url="http://x", packs_dir=tmp_path,
        animetrace_client=_StubAT(AnimeTraceMatch("MikuWrong", "SomeAnime")),
    )
    r = await rec.identify(b"img")
    assert r is not None and r.matched
    assert r.character_id == "emu"  # CCIP identity wins
    assert r.source == "ccip-sidecar"
    assert r.work == "SomeAnime"  # but borrows AnimeTrace work for context


@pytest.mark.asyncio
async def test_merge_animetrace_fills_when_ccip_miss(tmp_path) -> None:
    _MergeRecognizer.ccip_payload = _ccip_miss()
    rec = _MergeRecognizer(
        base_url="http://x", packs_dir=tmp_path,
        animetrace_client=_StubAT(AnimeTraceMatch("初音ミク", "VOCALOID")),
    )
    r = await rec.identify(b"img")
    assert r is not None and r.matched
    assert r.character_id is None
    assert r.character_name == "初音ミク"
    assert r.relation == "known"
    assert r.work == "VOCALOID"
    assert r.source == "animetrace"


@pytest.mark.asyncio
async def test_merge_both_miss_returns_unmatched(tmp_path) -> None:
    _MergeRecognizer.ccip_payload = _ccip_miss()
    rec = _MergeRecognizer(
        base_url="http://x", packs_dir=tmp_path,
        animetrace_client=_StubAT(None),
    )
    r = await rec.identify(b"img")
    assert r is not None and r.matched is False


@pytest.mark.asyncio
async def test_merge_animetrace_disabled_is_ccip_only(tmp_path) -> None:
    _MergeRecognizer.ccip_payload = _ccip_hit()
    rec = _MergeRecognizer(base_url="http://x", packs_dir=tmp_path)
    r = await rec.identify(b"img")
    assert isinstance(r, CharacterRecognition)
    assert r.character_id == "emu" and r.work is None
