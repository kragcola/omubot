from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api import characters as characters_api
from admin.routes.api.characters import create_characters_router


class _Registry:
    def __init__(self) -> None:
        self.scanned: str | None = None

    async def list_all(self) -> list[dict[str, Any]]:
        return [{"character_id": "emu", "name": "凤笑梦", "relation": "known", "aliases": []}]

    async def scan_and_sync(self, packs_dir: str) -> dict[str, int]:
        self.scanned = packs_dir
        return {"packs": 1, "inserted": 1, "skipped": 0}


class _Response:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text)


class _FakeAsyncClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def get(self, url: str) -> _Response:
        return _Response({"status": "ok", "character_count": 1, "pack_count": 1})

    async def post(self, url: str, *, data: dict[str, Any], files: list[Any]) -> _Response:
        del url, data, files
        return _Response(_built_pack_payload())


def _built_pack_payload() -> dict[str, Any]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "series.charpack/manifest.json",
            json.dumps(
                {
                    "pack": "series",
                    "series": "series",
                    "work": "Series",
                    "characters": [{"character_id": "emu", "name": "凤笑梦"}],
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("series.charpack/embeddings.npz", b"npz")
        zf.writestr("series.charpack/samples/emu/0.jpg", b"jpg")
    return {
        "charpack_zip_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "pack_dir": "series.charpack",
        "pack": "series",
        "series": "series",
        "character_count": 1,
        "embedded": 1,
        "total": 1,
        "samples": 1,
        "characters": [{"character_id": "emu", "embedded": 1, "total": 1, "samples": 1}],
    }


def _write_npz(path: Path, key: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{key}.npy", b"dummy-npy")


def _write_single_pack(packs: Path, cid: str, *, work: str | None = None) -> Path:
    pack = packs / f"{cid}.charpack"
    pack.mkdir(parents=True)
    char: dict[str, Any] = {
        "character_id": cid,
        "name": cid,
        "embedding_key": cid,
        "aliases": [],
    }
    if work is not None:
        char["work"] = work
    (pack / "manifest.json").write_text(
        json.dumps({"pack": cid, "characters": [char]}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_npz(pack / "embeddings.npz", cid)
    return pack


def _app(registry: _Registry) -> FastAPI:
    app = FastAPI()
    ctx = SimpleNamespace(character_registry_db=registry, recognition_cache=None)
    app.include_router(create_characters_router(ctx=ctx, sidecar_url="http://sidecar"))
    return app


def test_admin_build_series_lands_pack_and_syncs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(characters_api, "_PACKS_DIR", tmp_path)
    monkeypatch.setattr(characters_api.httpx, "AsyncClient", _FakeAsyncClient)
    registry = _Registry()

    resp = TestClient(_app(registry)).post(
        "/characters/build-series",
        data={
            "pack_name": "series",
            "series": "series",
            "work": "Series",
            "relation_default": "known",
            "characters_json": '[{"character_id":"emu","name":"凤笑梦"}]',
        },
        files=[("images", ("emu_0.jpg", b"image", "image/jpeg"))],
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["pack"] == "series"
    assert (tmp_path / "series.charpack" / "manifest.json").exists()
    assert registry.scanned == str(tmp_path)


def test_admin_list_and_sample_support_series_pack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(characters_api, "_PACKS_DIR", tmp_path)
    monkeypatch.setattr(characters_api.httpx, "AsyncClient", _FakeAsyncClient)
    pack = tmp_path / "series.charpack"
    (pack / "samples" / "emu").mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "pack": "series",
                "series": "series",
                "work": "Series",
                "characters": [{"character_id": "emu", "name": "凤笑梦"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pack / "samples" / "emu" / "0.jpg").write_bytes(b"jpg")
    registry = _Registry()
    client = TestClient(_app(registry))

    listed = client.get("/characters").json()
    assert listed["characters"][0]["pack"] == "series"
    assert listed["characters"][0]["series"] == "series"
    assert listed["characters"][0]["work"] == "Series"
    assert listed["characters"][0]["pack_character_count"] == 1
    assert listed["characters"][0]["mergeable"] is False
    assert listed["characters"][0]["has_sample"] is True
    assert client.get("/characters/emu/sample").status_code == 200


def test_admin_list_marks_complete_single_pack_mergeable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(characters_api, "_PACKS_DIR", tmp_path)
    monkeypatch.setattr(characters_api.httpx, "AsyncClient", _FakeAsyncClient)
    _write_single_pack(tmp_path, "emu", work="Series")
    registry = _Registry()

    listed = TestClient(_app(registry)).get("/characters").json()

    assert listed["characters"][0]["pack"] == "emu"
    assert listed["characters"][0]["pack_character_count"] == 1
    assert listed["characters"][0]["mergeable"] is True


def test_admin_list_marks_character_in_multi_pack_not_mergeable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(characters_api, "_PACKS_DIR", tmp_path)
    monkeypatch.setattr(characters_api.httpx, "AsyncClient", _FakeAsyncClient)
    _write_single_pack(tmp_path, "emu", work="Series")
    pack = tmp_path / "series.charpack"
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "pack": "series",
                "work": "Series",
                "characters": [{"character_id": "emu"}, {"character_id": "tsukasa"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = _Registry()

    listed = TestClient(_app(registry)).get("/characters").json()

    assert listed["characters"][0]["pack"] == "series"
    assert listed["characters"][0]["pack_character_count"] == 2
    assert listed["characters"][0]["mergeable"] is False


def test_admin_merge_series_merges_existing_single_packs_and_syncs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(characters_api, "_PACKS_DIR", tmp_path)
    monkeypatch.setattr(characters_api.httpx, "AsyncClient", _FakeAsyncClient)
    _write_single_pack(tmp_path, "emu", work=None)
    _write_single_pack(tmp_path, "tsukasa", work=None)
    registry = _Registry()

    resp = TestClient(_app(registry)).post(
        "/characters/merge-series",
        json={
            "pack_name": "project_sekai",
            "series": "pjsk",
            "work": "プロジェクトセカイ",
            "relation_default": "known",
            "character_ids": ["emu", "tsukasa"],
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["pack"] == "project_sekai"
    assert payload["series"] == "pjsk"
    assert payload["character_count"] == 2
    assert payload["archived"] == 2
    assert (tmp_path / "project_sekai.charpack" / "manifest.json").exists()
    assert not (tmp_path / "emu.charpack").exists()
    assert not (tmp_path / "tsukasa.charpack").exists()
    assert registry.scanned == str(tmp_path)
