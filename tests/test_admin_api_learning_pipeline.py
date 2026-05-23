import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api import create_api_router
from admin.routes.api.learning_pipeline import (
    _extract_all_lock,
    _extract_run_status,
    _run_extract_all,
    _run_style_extract,
)

TZ_SHANGHAI = timezone(timedelta(hours=8))


def _client(storage_dir: Path) -> TestClient:
    return _client_for_ctx(SimpleNamespace(storage_dir=storage_dir))


def _client_for_ctx(ctx: SimpleNamespace) -> TestClient:
    app = FastAPI()
    app.include_router(create_api_router(ctx=ctx))
    return TestClient(app)


def test_learning_pipeline_counts_schema_and_memory_scalars(tmp_path: Path) -> None:
    _seed_slang(tmp_path / "slang.db")
    _seed_style(tmp_path / "style.db")
    _seed_episodes(tmp_path / "episodic.db")
    _seed_memory(tmp_path / "memory_cards.db")
    _seed_consolidator(tmp_path / "consolidator_candidates.db")

    resp = _client(tmp_path).get("/api/admin/learning/pipeline")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["warnings"] == []
    stages = payload["stages"]

    assert set(stages) == {"candidate", "review", "approved", "hits", "archived"}
    for stage in stages.values():
        assert set(stage["by_noun"]) == {
            "slang",
            "style",
            "episode",
            "memory",
            "fact",
            "graph_relation",
        }
        assert stage["total"] == sum(
            value for value in stage["by_noun"].values() if value is not None
        )

    assert stages["candidate"]["by_noun"]["slang"] == 2
    assert stages["review"]["by_noun"]["slang"] == 1
    assert stages["approved"]["by_noun"]["slang"] == 2
    assert stages["hits"]["by_noun"]["slang"] == 2
    assert stages["archived"]["by_noun"]["slang"] == 2

    assert stages["candidate"]["by_noun"]["style"] == 1
    assert stages["review"]["by_noun"]["style"] == 1
    assert stages["approved"]["by_noun"]["style"] == 1
    assert stages["hits"]["by_noun"]["style"] == 1
    assert stages["archived"]["by_noun"]["style"] == 2

    assert stages["candidate"]["by_noun"]["episode"] == 1
    assert stages["review"]["by_noun"]["episode"] == 1
    assert stages["approved"]["by_noun"]["episode"] == 1
    assert stages["hits"]["by_noun"]["episode"] == 1
    assert stages["archived"]["by_noun"]["episode"] == 1

    assert stages["candidate"]["by_noun"]["memory"] is None
    assert stages["review"]["by_noun"]["memory"] is None
    assert stages["approved"]["by_noun"]["memory"] == 2
    assert stages["hits"]["by_noun"]["memory"] is None
    assert stages["archived"]["by_noun"]["memory"] == 1

    assert stages["candidate"]["by_noun"]["fact"] == 1
    assert stages["approved"]["by_noun"]["fact"] == 1
    assert stages["archived"]["by_noun"]["graph_relation"] == 1


def test_learning_items_memory_deeplink_and_date_filter(tmp_path: Path) -> None:
    _seed_memory(tmp_path / "memory_cards.db")

    resp = _client(tmp_path).get("/api/admin/learning/items?stage=approved&noun=memory&date=today")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["warnings"] == []
    assert payload["has_more"] is False
    assert [item["id"] for item in payload["items"]] == ["memory-mem_active"]
    item = payload["items"][0]
    assert item["noun"] == "memory"
    assert item["deep_link"] == "/memory?view=manage&card_id=mem_active"
    assert item["review_drawer"] is None


def test_learning_items_hits_include_style_and_episode_observations(tmp_path: Path) -> None:
    _seed_style(tmp_path / "style.db")
    _seed_episodes(tmp_path / "episodic.db")

    style_resp = _client(tmp_path).get("/api/admin/learning/items?stage=hits&noun=style")
    episode_resp = _client(tmp_path).get("/api/admin/learning/items?stage=hits&noun=episode")

    assert style_resp.status_code == 200
    assert episode_resp.status_code == 200
    style_items = style_resp.json()["items"]
    episode_items = episode_resp.json()["items"]
    assert len(style_items) == 1
    assert style_items[0]["id"] == "style-style_approved"
    assert style_items[0]["status_label"] == "今日命中"
    assert len(episode_items) == 1
    assert episode_items[0]["id"] == "episode-ep_enabled"
    assert episode_items[0]["status_label"] == "今日命中"


def test_learning_items_cursor_paginates_merged_candidates(tmp_path: Path) -> None:
    _seed_slang(tmp_path / "slang.db")
    _seed_style(tmp_path / "style.db")
    _seed_episodes(tmp_path / "episodic.db")
    _seed_consolidator(tmp_path / "consolidator_candidates.db")
    client = _client(tmp_path)

    first = client.get("/api/admin/learning/items?stage=candidate&noun=all&limit=2")

    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"]
    assert len(first_payload["items"]) == 2

    second = client.get(
        "/api/admin/learning/items",
        params={
            "stage": "candidate",
            "noun": "all",
            "limit": "2",
            "cursor": first_payload["next_cursor"],
        },
    )
    assert second.status_code == 200
    first_ids = {item["id"] for item in first_payload["items"]}
    second_ids = {item["id"] for item in second.json()["items"]}
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


def test_learning_today_schema_stays_dashboard_compatible(tmp_path: Path) -> None:
    resp = _client(tmp_path).get("/api/admin/learning/today")

    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) == {
        "as_of",
        "total_new",
        "total_reviewed",
        "slang",
        "style",
        "stickers",
    }
    assert {"approved_today", "reviewed_today", "pending", "today_hits", "latest"} <= set(payload["slang"])
    assert {"approved_today", "reviewed_today", "pending", "latest"} <= set(payload["style"])
    assert {"added_today", "total", "latest", "samples"} <= set(payload["stickers"])


async def test_learning_extract_all_runners_return_partial_failure() -> None:
    async def slang(**kwargs):
        return {"ok": True, "run_id": "slang-run", "limit": kwargs["limit"]}

    async def style(**kwargs):
        raise RuntimeError("style failed")

    async def consolidator(**kwargs):
        return {"ok": True, "run_id": "consolidator-run", "group_id": kwargs["group_id"]}

    payload = await _run_extract_all(
        ctx=SimpleNamespace(
            learning_extract_runners={
                "slang": slang,
                "style": style,
                "consolidator": consolidator,
            },
        ),
        group_id="100",
        limit=42,
        timeout_seconds=1,
    )

    assert payload["ok"] is True
    assert payload["run_id"].startswith("learn_ext_")
    assert payload["status"] == "partial_failed"
    assert payload["nouns"]["slang"]["status"] == "completed"
    assert payload["nouns"]["style"]["status"] == "failed"
    assert payload["results"]["slang"]["run_id"] == "slang-run"
    assert payload["results"]["slang"]["limit"] == 42
    assert payload["results"]["style"]["ok"] is False
    assert payload["results"]["style"]["error"] == "style failed"
    assert payload["results"]["consolidator"]["group_id"] == "100"


async def test_learning_extract_all_lock_rejects_second_call_and_releases() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(**kwargs):
        del kwargs
        started.set()
        await release.wait()
        return {"ok": True}

    ctx = SimpleNamespace(
        learning_extract_runners={
            "slang": slow,
            "style": slow,
            "consolidator": slow,
        },
    )

    first = asyncio.create_task(_run_extract_all(ctx=ctx, timeout_seconds=1))
    await started.wait()
    second = await _run_extract_all(ctx=ctx, timeout_seconds=1)
    release.set()
    first_payload = await first
    third = await _run_extract_all(ctx=ctx, timeout_seconds=1)

    assert second["ok"] is False
    assert second["error"] == "already_running"
    assert second["run_id"].startswith("learn_ext_")
    assert second["status"] in {"queued", "running"}
    assert first_payload["ok"] is True
    assert first_payload["status"] == "completed"
    assert third["ok"] is True
    assert not _extract_all_lock.locked()


async def test_learning_extract_all_timeout_is_per_noun() -> None:
    async def ok(**kwargs):
        del kwargs
        return {"ok": True}

    async def slow(**kwargs):
        del kwargs
        await asyncio.sleep(1)
        return {"ok": True}

    payload = await _run_extract_all(
        ctx=SimpleNamespace(
            learning_extract_runners={
                "slang": ok,
                "style": slow,
                "consolidator": ok,
            },
        ),
        timeout_seconds=0.01,
    )

    assert payload["ok"] is True
    assert payload["status"] == "partial_failed"
    assert payload["nouns"]["style"]["status"] == "timeout"
    assert payload["results"]["slang"]["ok"] is True
    assert payload["results"]["style"] == {"ok": False, "error": "timeout", "noun": "style"}
    assert payload["results"]["consolidator"]["ok"] is True


async def test_learning_extract_all_async_run_can_be_polled() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(**kwargs):
        del kwargs
        started.set()
        await release.wait()
        return {"ok": True}

    payload = await _run_extract_all(
        ctx=SimpleNamespace(
            learning_extract_runners={
                "slang": slow,
                "style": slow,
                "consolidator": slow,
            },
        ),
        wait=False,
        timeout_seconds=1,
    )

    assert payload["ok"] is True
    assert payload["status"] == "queued"
    run_id = payload["run_id"]
    await started.wait()
    running = _extract_run_status(run_id)
    assert running["status"] == "running"
    assert any(noun["status"] == "running" for noun in running["nouns"].values())

    release.set()
    for _ in range(20):
        finished = _extract_run_status(run_id)
        if finished["status"] == "completed":
            break
        await asyncio.sleep(0.01)

    finished = _extract_run_status(run_id)
    assert finished["status"] == "completed"
    assert all(noun["status"] == "completed" for noun in finished["nouns"].values())


def test_learning_extract_all_status_endpoint() -> None:
    async def ok(**kwargs):
        return {"ok": True, "run_id": f"{kwargs.get('group_id') or 'global'}-run"}

    client = _client_for_ctx(SimpleNamespace(
        learning_extract_runners={
            "slang": ok,
            "style": ok,
            "consolidator": ok,
        },
    ))

    resp = client.post("/api/admin/learning/extract-all", json={"group_id": "100"})

    assert resp.status_code == 200
    payload = resp.json()
    run_id = payload["run_id"]
    assert payload["status"] == "completed"
    status_resp = client.get(f"/api/admin/learning/extract-all/{run_id}")
    missing_resp = client.get("/api/admin/learning/extract-all/missing-run")
    assert status_resp.status_code == 200
    assert status_resp.json()["run_id"] == run_id
    assert status_resp.json()["nouns"]["slang"]["status"] == "completed"
    assert missing_resp.status_code == 200
    assert missing_resp.json()["error"] == "not_found"


async def test_learning_style_extract_uses_production_runner(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "saved": 3}

    monkeypatch.setattr(
        "admin.routes.api.learning_pipeline.run_style_manual_extract",
        fake_runner,
    )
    style_store = SimpleNamespace(initialized=True)
    ctx = SimpleNamespace(
        style_store=style_store,
        msg_log=object(),
        llm_client=object(),
        slang_store=object(),
    )

    payload = await _run_style_extract(ctx, group_id="100", limit=40, max_batches=2)

    assert payload == {"ok": True, "saved": 3}
    assert calls[0]["style_store"] is style_store
    assert calls[0]["group_id"] == "100"
    assert calls[0]["limit"] == 40
    assert calls[0]["max_batches"] == 2


def _seed_slang(path: Path) -> None:
    today = datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE slang_terms (
                term_id TEXT PRIMARY KEY,
                term TEXT NOT NULL DEFAULT '',
                meaning TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}'
            )"""
        )
        db.execute(
            """CREATE TABLE slang_observations (
                term_id TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL
            )"""
        )
        rows = [
            ("cand_plain", "猫饼", "离谱但可爱", "candidate", "100", 0.71, {}),
            (
                "cand_ai_kept",
                "猫车",
                "群内接梗",
                "candidate",
                "100",
                0.73,
                {"ai_review": {"status": "done", "decision": "keep"}},
            ),
            ("review_ai", "夜猫", "熬夜的人", "approved", "100", 0.82, {"ai_review": {"status": "done"}}),
            (
                "approved_human",
                "早睡局",
                "提醒早睡的局",
                "approved",
                "100",
                0.91,
                {"ai_review": {"status": "done"}, "human_review": {"status": "approved"}},
            ),
            ("muted", "旧梗", "不再使用", "muted", "100", 0.4, {}),
            ("expired", "过期梗", "已过期", "expired", "100", 0.3, {}),
        ]
        db.executemany(
            """INSERT INTO slang_terms
               (term_id, term, meaning, status, group_id, confidence, created_at, updated_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (term_id, term, meaning, status, group_id, confidence, today, today, json.dumps(meta))
                for term_id, term, meaning, status, group_id, confidence, meta in rows
            ],
        )
        db.executemany(
            "INSERT INTO slang_observations (term_id, group_id, observed_at) VALUES (?, ?, ?)",
            [("review_ai", "100", today), ("approved_human", "100", today)],
        )


def _seed_style(path: Path) -> None:
    today = datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE style_expressions (
                expression_id TEXT PRIMARY KEY,
                situation TEXT NOT NULL DEFAULT '',
                style TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        db.execute(
            """CREATE TABLE style_observations (
                expression_id TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL
            )"""
        )
        db.executemany(
            """INSERT INTO style_expressions
               (expression_id, situation, style, status, group_id, confidence, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("style_pending", "有人吐槽", "短句接梗", "pending", "100", 0.65, today, today),
                ("style_approved", "群里开玩笑", "轻轻补刀", "approved", "100", 0.88, today, today),
                ("style_rejected", "严肃话题", "夸张卖萌", "rejected", "100", 0.21, today, today),
                ("style_muted", "争议语气", "阴阳怪气", "muted", "100", 0.3, today, today),
            ],
        )
        db.execute(
            "INSERT INTO style_observations (expression_id, group_id, observed_at) VALUES (?, ?, ?)",
            ("style_approved", "100", today),
        )


def _seed_episodes(path: Path) -> None:
    today = datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE episodes (
                episode_id TEXT PRIMARY KEY,
                episode_state TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT '',
                situation TEXT NOT NULL DEFAULT '',
                reflection TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        db.execute(
            """CREATE TABLE episode_observations (
                episode_id TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL
            )"""
        )
        db.executemany(
            """INSERT INTO episodes
               (episode_id, episode_state, group_id, situation, reflection, confidence, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("ep_candidate", "candidate", "100", "新经验", "待观察", 0.7, today, today),
                ("ep_approved", "approved", "100", "已复盘", "待启用", 0.8, today, today),
                ("ep_enabled", "enabled_for_prompt", "100", "可提示", "已启用", 0.9, today, today),
                ("ep_disabled", "disabled", "100", "旧经验", "不再用", 0.2, today, today),
            ],
        )
        db.execute(
            "INSERT INTO episode_observations (episode_id, group_id, observed_at) VALUES (?, ?, ?)",
            ("ep_enabled", "100", today),
        )


def _seed_memory(path: Path) -> None:
    today = datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")
    old = (datetime.now(TZ_SHANGHAI) - timedelta(days=40)).isoformat(timespec="seconds")
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE memory_cards (
                card_id TEXT PRIMARY KEY,
                category TEXT NOT NULL DEFAULT 'fact',
                status TEXT NOT NULL,
                scope TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                priority INTEGER NOT NULL DEFAULT 5,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        db.executemany(
            """INSERT INTO memory_cards
               (card_id, category, status, scope, scope_id, content, confidence,
                priority, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("mem_active", "fact", "active", "group", "100", "今天活跃记忆", 0.8, 5, "test", today, today),
                ("mem_old", "fact", "active", "group", "100", "旧活跃记忆", 0.7, 5, "test", old, old),
                ("mem_expired", "fact", "expired", "group", "100", "过期记忆", 0.5, 5, "test", today, today),
                ("mem_superseded", "fact", "superseded", "group", "100", "被替代记忆", 0.5, 5, "test", today, today),
            ],
        )


def _seed_consolidator(path: Path) -> None:
    now = datetime.now(TZ_SHANGHAI).timestamp()
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE consolidator_candidates (
                candidate_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'group',
                state TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                created_at REAL NOT NULL DEFAULT 0
            )"""
        )
        fact_payload = json.dumps({"subject": "A", "predicate": "是", "object": "B"})
        graph_payload = json.dumps({
            "subject_node": "A",
            "predicate": "关联",
            "object_node": "B",
        })
        approved_fact_payload = json.dumps({"subject": "C", "predicate": "是", "object": "D"})
        rejected_graph_payload = json.dumps({
            "subject_node": "E",
            "predicate": "关联",
            "object_node": "F",
        })
        slang_payload = json.dumps({"term": "新梗", "meaning": "新意思"})
        db.executemany(
            """INSERT INTO consolidator_candidates
               (candidate_id, domain, scope, state, group_id, payload_json, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("cand_fact", "fact", "group", "dry_run", "100", fact_payload, 0.6, now),
                ("cand_graph", "graph_relation", "group", "queued", "100", graph_payload, 0.61, now),
                ("approved_fact", "fact", "group", "approved", "100", approved_fact_payload, 0.8, now),
                ("rejected_graph", "graph_relation", "group", "rejected", "100", rejected_graph_payload, 0.2, now),
                ("consolidator_slang", "slang", "group", "dry_run", "100", slang_payload, 0.7, now),
            ],
        )
