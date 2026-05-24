from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api import create_api_router
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults


def test_persona_importer_api_round_trip(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(MINIMAL_SOURCE, encoding="utf-8")

    app = FastAPI()
    ctx = SimpleNamespace(persona_root=persona_root, persona_defaults_dir=defaults)
    app.include_router(create_api_router(ctx=ctx))
    client = TestClient(app)

    imported = client.post("/api/admin/persona/import", json={"persona_id": "fengxiaomeng"}).json()
    assert imported["ok"] is True
    assert imported["persona_id"] == "fengxiaomeng-v2"
    assert imported["report"]["status"] == "ok"

    draft = client.get("/api/admin/persona/draft/fengxiaomeng").json()
    assert draft["ok"] is True
    assert "persona.yaml" in draft["files"]
    assert "_import_report.json" in draft["files"]

    no_confirm = client.post("/api/admin/persona/freeze/fengxiaomeng", json={}).json()
    assert no_confirm["ok"] is False
    assert "confirm=true" in no_confirm["error"]

    frozen = client.post("/api/admin/persona/freeze/fengxiaomeng", json={"confirm": True}).json()
    assert frozen["ok"] is True
    assert frozen["mode"] == "pending_freeze"
    assert (source_dir / "_pending_freeze" / "source.frozen.md").is_file()


def test_persona_source_api_read_write_then_import(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)

    app = FastAPI()
    ctx = SimpleNamespace(persona_root=persona_root, persona_defaults_dir=defaults)
    app.include_router(create_api_router(ctx=ctx))
    client = TestClient(app)

    empty = client.get("/api/admin/persona/source/fengxiaomeng").json()
    assert empty["ok"] is True
    assert empty["persona_id"] == "fengxiaomeng-v2"
    assert empty["exists"] is False
    assert empty["content"] == ""

    saved = client.put(
        "/api/admin/persona/source/fengxiaomeng",
        json={"content": MINIMAL_SOURCE},
    ).json()
    assert saved["ok"] is True
    assert saved["exists"] is True
    assert saved["bytes"] == len(MINIMAL_SOURCE.encode("utf-8"))

    source_path = persona_root / "fengxiaomeng-v2" / "source.md"
    assert source_path.read_text(encoding="utf-8") == MINIMAL_SOURCE

    loaded = client.get("/api/admin/persona/source/fengxiaomeng").json()
    assert loaded["ok"] is True
    assert loaded["content"] == MINIMAL_SOURCE

    imported = client.post("/api/admin/persona/import", json={"persona_id": "fengxiaomeng"}).json()
    assert imported["ok"] is True
    assert (persona_root / "fengxiaomeng-v2" / ".draft" / "_import_report.json").is_file()


def test_persona_source_api_rejects_path_like_persona_id(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)

    app = FastAPI()
    ctx = SimpleNamespace(persona_root=persona_root, persona_defaults_dir=defaults)
    app.include_router(create_api_router(ctx=ctx))
    client = TestClient(app)

    payload = client.put(
        "/api/admin/persona/source/..%5Cescape",
        json={"content": MINIMAL_SOURCE},
    ).json()

    assert payload["ok"] is False
    assert "invalid" in payload["error"]
    assert not (tmp_path / "escape-v2" / "source.md").exists()
