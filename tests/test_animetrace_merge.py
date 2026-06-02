from __future__ import annotations

import asyncio
import json
from typing import Any

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


class _SlowAT:
    """AnimeTrace stub that records whether its (slow) call was awaited to
    completion vs cancelled by the merge short-circuit."""

    def __init__(self, match: AnimeTraceMatch | None, *, delay: float = 0.2) -> None:
        self._match = match
        self._delay = delay
        self.completed = False
        self.cancelled = False

    async def identify(self, image_data: bytes, *, media_type: str = "image/jpeg"):
        del image_data, media_type
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True
        return self._match


class _MergeRecognizer(CharacterRecognizer):
    """CCIP stub controllable per-test via ``ccip_payload``.

    ``multi_char_enabled=False`` keeps the stub on the old single-char path
    so it doesn't try to call the (non-existent) sidecar /identify-multi.
    """
    ccip_payload: dict | None = None

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("multi_char_enabled", False)
        super().__init__(**kwargs)

    async def _request_identify(self, image_data, *, media_type="image/jpeg"):
        del image_data, media_type
        return type(self).ccip_payload


def _ccip_hit(cid="emu", name="凤笑梦"):
    return {"matched": True, "character_id": cid, "character_name": name,
            "difference": 0.02, "threshold": 0.18, "source": "ccip-sidecar"}


def _ccip_miss():
    return {"matched": False, "character_id": None, "character_name": None,
            "difference": 0.9, "threshold": 0.18, "source": "ccip-sidecar"}


async def _identify_single(rec: CharacterRecognizer, data: bytes, **kw: Any) -> CharacterRecognition | None:
    """Call ``identify()`` and unwrap the single result (``multi_char_enabled=False`` path)."""
    results = await rec.identify(data, **kw)
    if not results:
        return None
    return results[0]


def _write_pack(packs_dir, cid, name, relation, work=None) -> None:
    """Write a minimal charpack manifest so the recognizer catalog resolves
    relation/work for the given character_id."""
    pack = packs_dir / f"{cid}.charpack"
    pack.mkdir(parents=True, exist_ok=True)
    char: dict = {"character_id": cid, "name": name, "relation": relation}
    if work:
        char["work"] = work
    (pack / "manifest.json").write_text(
        json.dumps({"pack": cid, "characters": [char]}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_merge_ccip_self_does_not_borrow_animetrace_work(tmp_path) -> None:
    """relation=self: identity is definitively ours, so AnimeTrace's (often
    wrong) work must NOT be borrowed — this is the 凤笑梦→がっこうぐらし！ bug."""
    _write_pack(tmp_path, "emu", "凤笑梦", "self")
    _MergeRecognizer.ccip_payload = _ccip_hit()
    rec = _MergeRecognizer(
        base_url="http://x", packs_dir=tmp_path,
        animetrace_client=_StubAT(AnimeTraceMatch("MikuWrong", "がっこうぐらし！")),
    )
    r = await _identify_single(rec, b"img")
    assert r is not None and r.matched
    assert r.character_id == "emu"
    assert r.relation == "self"
    assert r.source == "ccip-sidecar"
    assert r.work is None  # NOT borrowed


@pytest.mark.asyncio
async def test_merge_ccip_known_borrows_animetrace_work(tmp_path) -> None:
    """relation=known with no manifest work: borrowing AnimeTrace work for
    context is allowed (the character isn't one of ours)."""
    _write_pack(tmp_path, "emu", "某角色", "known")
    _MergeRecognizer.ccip_payload = _ccip_hit()
    rec = _MergeRecognizer(
        base_url="http://x", packs_dir=tmp_path,
        animetrace_client=_StubAT(AnimeTraceMatch("某角色", "SomeAnime")),
    )
    r = await _identify_single(rec, b"img")
    assert r is not None and r.matched
    assert r.character_id == "emu"
    assert r.relation == "known"
    assert r.work == "SomeAnime"  # borrowed for context


@pytest.mark.asyncio
async def test_merge_ccip_manifest_work_wins_over_animetrace(tmp_path) -> None:
    """When the charpack supplies a work, it wins — AnimeTrace is never consulted
    for the work field even for relation=known."""
    _write_pack(tmp_path, "emu", "凤笑梦", "known", work="プロジェクトセカイ")
    _MergeRecognizer.ccip_payload = _ccip_hit()
    rec = _MergeRecognizer(
        base_url="http://x", packs_dir=tmp_path,
        animetrace_client=_StubAT(AnimeTraceMatch("MikuWrong", "がっこうぐらし！")),
    )
    r = await _identify_single(rec, b"img")
    assert r is not None and r.work == "プロジェクトセカイ"


@pytest.mark.asyncio
async def test_merge_animetrace_fills_when_ccip_miss(tmp_path) -> None:
    _MergeRecognizer.ccip_payload = _ccip_miss()
    rec = _MergeRecognizer(
        base_url="http://x", packs_dir=tmp_path,
        animetrace_client=_StubAT(AnimeTraceMatch("初音ミク", "VOCALOID")),
    )
    r = await _identify_single(rec, b"img")
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
    r = await _identify_single(rec, b"img")
    assert r is not None and r.matched is False


@pytest.mark.asyncio
async def test_merge_animetrace_disabled_is_ccip_only(tmp_path) -> None:
    _MergeRecognizer.ccip_payload = _ccip_hit()
    rec = _MergeRecognizer(base_url="http://x", packs_dir=tmp_path)
    r = await _identify_single(rec, b"img")
    assert isinstance(r, CharacterRecognition)
    assert r.character_id == "emu" and r.work is None


@pytest.mark.asyncio
async def test_merge_self_short_circuits_slow_animetrace(tmp_path) -> None:
    """A relation=self CCIP hit never borrows AnimeTrace's work, so the merge
    must cancel the slow online call instead of blocking ingest on it (the CCIP
    latency fix). The result is final from CCIP alone."""
    _write_pack(tmp_path, "emu", "凤笑梦", "self")
    _MergeRecognizer.ccip_payload = _ccip_hit()
    slow = _SlowAT(AnimeTraceMatch("MikuWrong", "がっこうぐらし！"), delay=0.5)
    rec = _MergeRecognizer(base_url="http://x", packs_dir=tmp_path, animetrace_client=slow)
    r = await _identify_single(rec, b"img")
    assert r is not None and r.relation == "self" and r.work is None
    # AnimeTrace must have been cancelled, not awaited to completion.
    assert slow.completed is False
    assert slow.cancelled is True


@pytest.mark.asyncio
async def test_merge_known_with_manifest_work_short_circuits(tmp_path) -> None:
    """relation=known WITH a manifest work is also final — no borrow, so the
    slow online call is cancelled."""
    _write_pack(tmp_path, "emu", "凤笑梦", "known", work="プロジェクトセカイ")
    _MergeRecognizer.ccip_payload = _ccip_hit()
    slow = _SlowAT(AnimeTraceMatch("MikuWrong", "がっこうぐらし！"), delay=0.5)
    rec = _MergeRecognizer(base_url="http://x", packs_dir=tmp_path, animetrace_client=slow)
    r = await _identify_single(rec, b"img")
    assert r is not None and r.work == "プロジェクトセカイ"
    assert slow.cancelled is True


@pytest.mark.asyncio
async def test_merge_known_without_work_still_awaits_animetrace(tmp_path) -> None:
    """relation=known WITHOUT a manifest work is the one case that genuinely
    needs AnimeTrace — the merge must NOT short-circuit; it awaits + borrows."""
    _write_pack(tmp_path, "emu", "某角色", "known")
    _MergeRecognizer.ccip_payload = _ccip_hit()
    slow = _SlowAT(AnimeTraceMatch("某角色", "SomeAnime"), delay=0.05)
    rec = _MergeRecognizer(base_url="http://x", packs_dir=tmp_path, animetrace_client=slow)
    r = await _identify_single(rec, b"img")
    assert r is not None and r.work == "SomeAnime"
    assert slow.completed is True
    assert slow.cancelled is False
