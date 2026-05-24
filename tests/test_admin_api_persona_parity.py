"""Admin JSON API: GET /api/admin/persona/parity/{persona_id}."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api import create_api_router
from kernel.config import GroupConfig, GroupOverride
from services.identity import Identity
from tests.test_persona_importer import MINIMAL_SOURCE, _write_defaults


class _StubIdentityManager:
    def __init__(self, identity: Identity) -> None:
        self._identity = identity

    def resolve(self) -> Identity:
        return self._identity


def _seed_persona(persona_root: Path, *, source: str = MINIMAL_SOURCE) -> Path:
    defaults = _write_defaults(persona_root)
    source_dir = persona_root / "fengxiaomeng-v2"
    source_dir.mkdir(parents=True)
    (source_dir / "source.md").write_text(source, encoding="utf-8")
    return defaults


def _build_client(
    *,
    persona_root: Path,
    defaults: Path,
    soul_dir: Path,
    identity: Identity | None = None,
    config: object | None = None,
    bot: object | None = None,
) -> TestClient:
    ctx = SimpleNamespace(persona_root=persona_root, persona_defaults_dir=defaults)
    app = FastAPI()
    app.include_router(create_api_router(
        ctx=ctx,
        soul_dir=str(soul_dir),
        identity_mgr=_StubIdentityManager(identity) if identity is not None else None,
        config=config,
        bot=bot,
    ))
    return TestClient(app)


def test_parity_endpoint_happy_path_aligned(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    source_with_instruction = MINIMAL_SOURCE + (
        "\n\n# 8.4 行为指令\n\n- 默认只回一句话\n- 不用 Markdown\n"
    )
    defaults = _seed_persona(persona_root, source=source_with_instruction)
    soul_dir = tmp_path / "soul"
    soul_dir.mkdir()
    (soul_dir / "instruction.md").write_text("默认只回一句话\n不用 Markdown\n", encoding="utf-8")

    identity = Identity(
        id="fengxiaomeng",
        name="凤笑梦",
        personality="一句话角色：群聊中的拟人 bot，元气、反应快、有一点调皮。",
        proactive=None,
    )
    config = SimpleNamespace(
        admins={},
        group=GroupConfig(overrides={}),
    )
    bot = SimpleNamespace(self_id="")

    client = _build_client(
        persona_root=persona_root,
        defaults=defaults,
        soul_dir=soul_dir,
        identity=identity,
        config=config,
        bot=bot,
    )

    imported = client.post(
        "/api/admin/persona/import", json={"persona_id": "fengxiaomeng"}
    ).json()
    assert imported["ok"] is True

    payload = client.get("/api/admin/persona/parity/fengxiaomeng").json()
    assert payload["ok"] is True
    assert payload["persona_id"] == "fengxiaomeng-v2"
    assert payload["as_of"] == "dry_run"
    assert payload["v1_signals"]["bot_self_id"] == ""
    assert payload["v1_signals"]["instruction_present"] is True
    assert payload["v1_signals"]["admins_count"] == 0
    assert payload["v1_signals"]["proactive_present"] is False
    assert payload["v1_signals"]["group_override_group_id"] is None

    assert payload["report"]["persona_id"] == "fengxiaomeng-v2"
    statuses = {f["axis"]: f["status"] for f in payload["report"]["findings"]}
    assert statuses["identity_personality"] == "aligned"
    assert statuses["behavior_instruction"] == "aligned"
    assert payload["report"]["has_divergence"] is False


def test_parity_endpoint_surfaces_group_override(tmp_path: Path) -> None:
    source = MINIMAL_SOURCE.replace(
        "language: zh-CN\n---",
        (
            "language: zh-CN\n"
            "group_profiles:\n"
            "  \"12345\":\n"
            "    reply_style: playful\n"
            "    custom_prompt: 多接梗，少说教。\n"
            "---"
        ),
    )
    persona_root = tmp_path / "persona"
    defaults = _seed_persona(persona_root, source=source)
    soul_dir = tmp_path / "soul"
    soul_dir.mkdir()

    identity = Identity(id="fengxiaomeng", name="凤笑梦", personality="一句话角色", proactive=None)
    overrides = {
        12345: GroupOverride(reply_style="playful", custom_prompt="多接梗，少说教。"),
    }
    config = SimpleNamespace(admins={}, group=GroupConfig(overrides=overrides))

    client = _build_client(
        persona_root=persona_root,
        defaults=defaults,
        soul_dir=soul_dir,
        identity=identity,
        config=config,
    )
    client.post("/api/admin/persona/import", json={"persona_id": "fengxiaomeng"})

    payload = client.get("/api/admin/persona/parity/fengxiaomeng").json()
    assert payload["ok"] is True
    assert payload["v1_signals"]["group_override_group_id"] == "12345"
    statuses = {f["axis"]: f["status"] for f in payload["report"]["findings"]}
    assert statuses["group_profile"] == "aligned"


def test_parity_endpoint_marks_admins_and_proactive_v1_only(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _seed_persona(persona_root)
    soul_dir = tmp_path / "soul"
    soul_dir.mkdir()

    identity = Identity(
        id="fengxiaomeng",
        name="凤笑梦",
        personality="一句话角色：群聊中的拟人 bot",
        proactive="看到群里有人提名字时主动接一句",
    )
    config = SimpleNamespace(
        admins={"123456": "管理员小张"},
        group=GroupConfig(overrides={}),
    )

    client = _build_client(
        persona_root=persona_root,
        defaults=defaults,
        soul_dir=soul_dir,
        identity=identity,
        config=config,
    )
    client.post("/api/admin/persona/import", json={"persona_id": "fengxiaomeng"})

    payload = client.get("/api/admin/persona/parity/fengxiaomeng").json()
    assert payload["ok"] is True
    assert payload["v1_signals"]["admins_count"] == 1
    assert payload["v1_signals"]["proactive_present"] is True
    statuses = {f["axis"]: f["status"] for f in payload["report"]["findings"]}
    assert statuses["admins"] == "v1_only"
    assert statuses["proactive_rules"] == "v1_only"


def test_parity_endpoint_returns_error_when_draft_missing(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    soul_dir = tmp_path / "soul"
    soul_dir.mkdir()

    client = _build_client(
        persona_root=persona_root,
        defaults=defaults,
        soul_dir=soul_dir,
        identity=Identity(id="x", name="x", personality="x", proactive=None),
        config=SimpleNamespace(admins={}, group=GroupConfig(overrides={})),
    )

    payload = client.get("/api/admin/persona/parity/fengxiaomeng").json()
    assert payload["ok"] is False
    assert payload["persona_id"] == "fengxiaomeng-v2"
    assert "error" in payload
    assert payload["compile"]["ok"] is False


def test_parity_endpoint_rejects_path_like_persona_id(tmp_path: Path) -> None:
    persona_root = tmp_path / "persona"
    defaults = _write_defaults(persona_root)
    soul_dir = tmp_path / "soul"
    soul_dir.mkdir()

    client = _build_client(
        persona_root=persona_root,
        defaults=defaults,
        soul_dir=soul_dir,
    )

    payload = client.get("/api/admin/persona/parity/..%5Cescape").json()
    assert payload["ok"] is False
    assert "invalid" in payload["error"]
