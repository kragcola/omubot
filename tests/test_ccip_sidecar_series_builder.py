from __future__ import annotations

import importlib.util
import io
import json
import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import pytest
from PIL import Image


def _load_sidecar(monkeypatch: pytest.MonkeyPatch) -> Any:
    np = pytest.importorskip("numpy")
    imgutils = types.ModuleType("imgutils")
    detect = types.ModuleType("imgutils.detect")
    metrics = types.ModuleType("imgutils.metrics")
    ccip = types.ModuleType("imgutils.metrics.ccip")
    detect.detect_heads = lambda image: []
    ccip.ccip_extract_feature = lambda image, model=None: np.ones(4, dtype=np.float32)
    ccip.ccip_batch_extract_features = lambda crops, model=None: [np.ones(4, dtype=np.float32) for _ in crops]
    ccip.ccip_difference = lambda left, right, model=None: 0.0
    monkeypatch.setitem(sys.modules, "imgutils", imgutils)
    monkeypatch.setitem(sys.modules, "imgutils.detect", detect)
    monkeypatch.setitem(sys.modules, "imgutils.metrics", metrics)
    monkeypatch.setitem(sys.modules, "imgutils.metrics.ccip", ccip)

    path = Path(__file__).resolve().parents[1] / "ccip-sidecar" / "server.py"
    spec = importlib.util.spec_from_file_location("ccip_sidecar_server_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "ccip_sidecar_server_test", module)
    spec.loader.exec_module(module)
    return module


def _image_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="JPEG")
    return buf.getvalue()


def test_build_series_pack_writes_manifest_npz_and_nested_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _load_sidecar(monkeypatch)

    payload = sidecar._build_series_pack(
        [("a_0.jpg", _image_bytes()), ("b-0.jpg", _image_bytes())],
        pack_name="my_series",
        series="series_slug",
        work="Series",
        relation_default="known",
        characters_json=json.dumps([
            {"character_id": "a", "name": "A"},
            {"character_id": "b", "name": "B", "relation": "friend"},
        ]),
    )

    raw = payload["charpack_zip_b64"]
    import base64
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(raw))) as zf:
        manifest = json.loads(zf.read("my_series.charpack/manifest.json").decode("utf-8"))
        assert manifest["work"] == "Series"
        assert manifest["series"] == "series_slug"
        assert {item["character_id"] for item in manifest["characters"]} == {"a", "b"}
        b_entry = next(item for item in manifest["characters"] if item["character_id"] == "b")
        assert b_entry["relation"] == "friend"
        names = set(zf.namelist())
        assert "my_series.charpack/samples/a/0.jpg" in names
        assert "my_series.charpack/samples/b/0.jpg" in names
        with zipfile.ZipFile(io.BytesIO(zf.read("my_series.charpack/embeddings.npz"))) as npz:
            assert {"a.npy", "b.npy"}.issubset(set(npz.namelist()))


def test_build_pack_preserves_context_label(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _load_sidecar(monkeypatch)

    payload = sidecar._build_pack(
        [_image_bytes()],
        character_id="xingchen",
        name="星尘",
        relation="known",
        work="中V",
        context_label="中V / 五维介质",
    )

    import base64
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload["charpack_zip_b64"]))) as zf:
        manifest = json.loads(zf.read("xingchen.charpack/manifest.json").decode("utf-8"))
    assert manifest["work"] == "中V"
    assert manifest["characters"][0]["context_label"] == "中V / 五维介质"


def test_build_series_pack_preserves_per_character_context_label(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _load_sidecar(monkeypatch)

    payload = sidecar._build_series_pack(
        [("a_0.jpg", _image_bytes())],
        pack_name="my_series",
        series="series_slug",
        work="Series",
        relation_default="known",
        characters_json=json.dumps([
            {"character_id": "a", "name": "A", "context_label": "Series / Unit A"},
        ]),
    )

    import base64
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload["charpack_zip_b64"]))) as zf:
        manifest = json.loads(zf.read("my_series.charpack/manifest.json").decode("utf-8"))
    assert manifest["characters"][0]["context_label"] == "Series / Unit A"


def test_build_series_pack_preserves_training_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _load_sidecar(monkeypatch)

    payload = sidecar._build_series_pack(
        [("a_0.jpg", _image_bytes()), ("a_1.jpg", _image_bytes())],
        pack_name="my_series",
        series="series_slug",
        work="Series",
        relation_default="known",
        characters_json=json.dumps([
            {
                "character_id": "a",
                "name": "A",
                "training_stats": {
                    "forms": {"full_body": 1, "expression": 1},
                    "sources": ["official_full", "stamp_001001"],
                    "missing_forms": ["chibi"],
                },
            },
        ]),
    )

    import base64
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload["charpack_zip_b64"]))) as zf:
        manifest = json.loads(zf.read("my_series.charpack/manifest.json").decode("utf-8"))
    stats = manifest["characters"][0]["training_stats"]
    assert stats["image_count"] == 2
    assert stats["embedded_count"] == 2
    assert stats["sample_count"] == 2
    assert stats["forms"] == {"full_body": 1, "expression": 1}
    assert stats["sources"] == ["official_full", "stamp_001001"]
    assert stats["missing_forms"] == ["chibi"]
    assert payload["characters"][0]["training_stats"]["image_count"] == 2


def test_build_series_pack_rejects_missing_character_images(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _load_sidecar(monkeypatch)

    with pytest.raises(ValueError, match="no images matched"):
        sidecar._build_series_pack(
            [("a_0.jpg", _image_bytes())],
            pack_name="my_series",
            series="",
            work="Series",
            relation_default="known",
            characters_json=json.dumps([
                {"character_id": "a", "name": "A"},
                {"character_id": "b", "name": "B"},
            ]),
        )


def test_identify_multi_keeps_unmatched_nearest_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _load_sidecar(monkeypatch)
    np = pytest.importorskip("numpy")

    class _Registry:
        def __init__(self) -> None:
            self.entries = [
                sidecar.CharacterEntry(
                    character_id="kasane_teto",
                    character_name="重音テト",
                    embedding=np.zeros(4, dtype=np.float32),
                )
            ]
            self.registry_version = "test-registry"

    sidecar.registry = _Registry()
    sidecar.detect_heads = lambda image: [((0, 0, 4, 4), "head", 0.9), ((4, 4, 8, 8), "head", 0.8)]
    sidecar.ccip_batch_extract_features = lambda crops, model=None: [
        np.ones(4, dtype=np.float32) for _ in crops
    ]
    sidecar.ccip_difference = lambda left, right, model=None: 0.231

    payload = sidecar._identify_multi(_image_bytes())

    assert payload["detection_count"] == 2
    assert payload["matched"] is False
    assert payload["characters"][0]["matched"] is False
    assert payload["characters"][0]["character_id"] is None
    assert payload["characters"][0]["candidate_character_id"] == "kasane_teto"
    assert payload["characters"][0]["candidate_character_name"] == "重音テト"
    assert payload["characters"][0]["difference"] == pytest.approx(0.231)


def test_identify_multi_rescues_tight_miss_with_first_padded_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _load_sidecar(monkeypatch)
    np = pytest.importorskip("numpy")

    class _Registry:
        def __init__(self) -> None:
            self.entries = [
                sidecar.CharacterEntry(
                    character_id="kasane_teto",
                    character_name="重音テト",
                    embedding=np.zeros(4, dtype=np.float32),
                )
            ]
            self.registry_version = "test-registry"

    crop_sizes: list[tuple[int, int]] = []

    def _fake_batch_extract(crops: list[Image.Image], model: str | None = None) -> list[Any]:
        crop_sizes.extend(crop.size for crop in crops)
        return [np.full(4, index, dtype=np.float32) for index, _crop in enumerate(crops)]

    sidecar.registry = _Registry()
    sidecar.detect_heads = lambda image: [((10, 10, 30, 30), "head", 0.9), ((60, 60, 80, 80), "head", 0.8)]
    sidecar.ccip_batch_extract_features = _fake_batch_extract
    sidecar.ccip_difference = lambda left, right, model=None: {
        0: 0.231,
        1: 0.203,
        2: 0.175,
        3: 0.166,
    }.get(int(left[0]), 0.240)

    payload = sidecar._identify_multi(_image_bytes((100, 100)))

    assert crop_sizes[:4] == [(20, 20), (26, 26), (32, 32), (40, 40)]
    assert payload["characters"][0]["matched"] is True
    assert payload["characters"][0]["character_id"] == "kasane_teto"
    assert payload["characters"][0]["difference"] == pytest.approx(0.175)
    assert payload["characters"][0]["crop_padding"] == pytest.approx(0.30)
    assert payload["characters"][0]["crop_bbox"] == [4.0, 4.0, 36.0, 36.0]


def test_identify_multi_keeps_tight_match_when_padded_variant_has_lower_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sidecar = _load_sidecar(monkeypatch)
    np = pytest.importorskip("numpy")

    class _Registry:
        def __init__(self) -> None:
            self.entries = [
                sidecar.CharacterEntry(
                    character_id="project_sekai_miku",
                    character_name="初音未来（Project SEKAI）",
                    embedding=np.zeros(4, dtype=np.float32),
                ),
                sidecar.CharacterEntry(
                    character_id="vocaloid_hatsune_miku",
                    character_name="初音未来（本家）",
                    embedding=np.ones(4, dtype=np.float32),
                ),
            ]
            self.registry_version = "test-registry"

    def _fake_difference(left: Any, right: Any, model: str | None = None) -> float:
        feature_index = int(left[0])
        entry_index = int(right[0])
        if feature_index == 0:
            return 0.160 if entry_index == 0 else 0.260
        if feature_index in (1, 2, 3):
            return 0.220 if entry_index == 0 else 0.100
        return 0.240 if entry_index == 0 else 0.250

    sidecar.registry = _Registry()
    sidecar.detect_heads = lambda image: [((10, 10, 30, 30), "head", 0.9), ((60, 60, 80, 80), "head", 0.8)]
    sidecar.ccip_batch_extract_features = lambda crops, model=None: [
        np.full(4, index, dtype=np.float32) for index, _crop in enumerate(crops)
    ]
    sidecar.ccip_difference = _fake_difference

    payload = sidecar._identify_multi(_image_bytes((100, 100)))

    assert payload["characters"][0]["matched"] is True
    assert payload["characters"][0]["character_id"] == "project_sekai_miku"
    assert payload["characters"][0]["candidate_character_id"] == "project_sekai_miku"
    assert payload["characters"][0]["difference"] == pytest.approx(0.160)
    assert payload["characters"][0]["crop_padding"] == pytest.approx(0.0)
