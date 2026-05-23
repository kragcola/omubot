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
