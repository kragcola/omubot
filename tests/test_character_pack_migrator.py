from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from services.media.character_pack_migrator import auto_merge_series_packs, merge_selected_character_packs


def _write_npz(path: Path, key: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{key}.npy", b"dummy-npy")


def _write_single_pack(
    packs: Path,
    cid: str,
    *,
    work: str | None,
    relation: str = "known",
    sample: bool = True,
) -> Path:
    pack = packs / f"{cid}.charpack"
    pack.mkdir(parents=True)
    char = {
        "character_id": cid,
        "name": cid,
        "embedding_key": cid,
        "relation": relation,
        "aliases": [],
    }
    if work is not None:
        char["work"] = work
    (pack / "manifest.json").write_text(
        json.dumps({"pack": cid, "relation_default": relation, "characters": [char]}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_npz(pack / "embeddings.npz", cid)
    if sample:
        samples = pack / "samples"
        samples.mkdir()
        (samples / "0.jpg").write_bytes(b"jpg")
    return pack


def test_auto_merge_series_packs_merges_same_work_and_archives_sources(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_single_pack(packs, "a", work="プロジェクトセカイ")
    _write_single_pack(packs, "b", work="プロジェクトセカイ", relation="friend")

    result = auto_merge_series_packs(packs)

    assert result["merged"] == 1
    assert result["archived"] == 2
    merged = next(p for p in packs.glob("*.charpack") if p.name.startswith("series_"))
    manifest = json.loads((merged / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["work"] == "プロジェクトセカイ"
    assert {item["character_id"] for item in manifest["characters"]} == {"a", "b"}
    b_entry = next(item for item in manifest["characters"] if item["character_id"] == "b")
    assert b_entry["relation"] == "friend"
    assert (merged / "samples" / "a" / "0.jpg").exists()
    assert not (packs / "a.charpack").exists()
    assert not (packs / "b.charpack").exists()
    assert len(list((packs / ".merged").glob("*/*.charpack"))) == 2

    second = auto_merge_series_packs(packs)
    assert second == {"groups": 0, "merged": 0, "archived": 0, "characters": 0, "skipped": 0}


def test_auto_merge_series_packs_skips_empty_work(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_single_pack(packs, "a", work=None)
    _write_single_pack(packs, "b", work=None)

    result = auto_merge_series_packs(packs)

    assert result["merged"] == 0
    assert (packs / "a.charpack").exists()
    assert (packs / "b.charpack").exists()


def test_auto_merge_series_packs_archives_when_complete_series_exists(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_single_pack(packs, "a", work="Series")
    _write_single_pack(packs, "b", work="Series")
    series = packs / "series.charpack"
    series.mkdir(parents=True)
    (series / "manifest.json").write_text(
        json.dumps(
            {
                "pack": "series",
                "work": "Series",
                "characters": [{"character_id": "a"}, {"character_id": "b"}],
            }
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(series / "embeddings.npz", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.npy", b"dummy-npy")
        zf.writestr("b.npy", b"dummy-npy")

    result = auto_merge_series_packs(packs)

    assert result["merged"] == 0
    assert result["archived"] == 2
    assert (packs / "series.charpack").exists()
    assert not (packs / "a.charpack").exists()
    assert not (packs / "b.charpack").exists()


def test_merge_selected_character_packs_uses_admin_work_and_archives_sources(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_single_pack(packs, "a", work=None)
    _write_single_pack(packs, "b", work=None, relation="friend")

    result = merge_selected_character_packs(
        packs,
        character_ids=["a", "b"],
        pack_name="manual_series",
        series="manual",
        work="Manual Work",
        relation_default="known",
    )

    assert result["pack"] == "manual_series"
    assert result["series"] == "manual"
    assert result["character_count"] == 2
    assert result["archived"] == 2
    merged = packs / "manual_series.charpack"
    manifest = json.loads((merged / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["work"] == "Manual Work"
    assert manifest["series"] == "manual"
    assert {item["character_id"] for item in manifest["characters"]} == {"a", "b"}
    b_entry = next(item for item in manifest["characters"] if item["character_id"] == "b")
    assert b_entry["relation"] == "friend"
    with zipfile.ZipFile(merged / "embeddings.npz") as zf:
        assert {"a.npy", "b.npy"} == set(zf.namelist())
    assert (merged / "samples" / "a" / "0.jpg").exists()
    assert not (packs / "a.charpack").exists()
    assert not (packs / "b.charpack").exists()
    assert len(list((packs / ".merged" / "manual_series").glob("*.charpack"))) == 2


def test_merge_selected_character_packs_infers_shared_work(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_single_pack(packs, "a", work="Series")
    _write_single_pack(packs, "b", work="Series")

    result = merge_selected_character_packs(
        packs,
        character_ids=["a", "b"],
        pack_name="series",
    )

    assert result["work"] == "Series"
    manifest = json.loads((packs / "series.charpack" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["work"] == "Series"


def test_merge_selected_character_packs_rejects_multi_pack_source(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_single_pack(packs, "a", work="Series")
    _write_single_pack(packs, "b", work="Series")
    series = packs / "series.charpack"
    series.mkdir(parents=True)
    (series / "manifest.json").write_text(
        json.dumps({"characters": [{"character_id": "a"}, {"character_id": "c"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already in multi-character pack"):
        merge_selected_character_packs(
            packs,
            character_ids=["a", "b"],
            pack_name="manual",
            work="Series",
        )


def test_merge_selected_character_packs_rejects_existing_target(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_single_pack(packs, "a", work="Series")
    _write_single_pack(packs, "b", work="Series")
    (packs / "series.charpack").mkdir(parents=True)

    with pytest.raises(FileExistsError, match="target pack already exists"):
        merge_selected_character_packs(
            packs,
            character_ids=["a", "b"],
            pack_name="series",
        )
