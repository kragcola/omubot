from __future__ import annotations

from tools import enroll_pjsk_pack as pack


def test_training_stats_counts_cutout_as_full_body_and_normal_proportion() -> None:
    stats = pack.training_stats([
        ("hoshino_ichika_cutout_00.png", b"cutout", "cutout_00"),
        ("hoshino_ichika_chibi_00.png", b"chibi", "chibi_00"),
        ("hoshino_ichika_stamp_stamp0008.png", b"stamp", "stamp_stamp0008"),
    ])

    assert stats["image_count"] == 3
    assert stats["forms"] == {
        "full_body": 1,
        "normal_proportion": 1,
        "chibi": 1,
        "expression": 1,
    }
    assert stats["missing_forms"] == []


def test_unique_stamp_titles_excludes_shared_stamp(monkeypatch) -> None:
    characters = [
        pack.CharacterDef("Hoshino Ichika", "星乃一歌", "hoshino_ichika", "known", "ichika"),
        pack.CharacterDef("Tenma Saki", "天马咲希", "tenma_saki", "known", "saki"),
    ]
    titles_by_full_name = {
        "Hoshino Ichika": ["File:Stamp0008.png", "File:Stamp9999.png"],
        "Tenma Saki": ["File:Stamp0057.png", "File:Stamp9999.png"],
    }

    monkeypatch.setattr(pack, "stamp_file_titles", lambda full_name: titles_by_full_name[full_name])

    assert pack.unique_stamp_titles(characters) == {
        "hoshino_ichika": ["File:Stamp0008.png"],
        "tenma_saki": ["File:Stamp0057.png"],
    }
