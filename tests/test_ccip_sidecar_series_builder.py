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


def _image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="JPEG")
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
