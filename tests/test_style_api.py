from types import SimpleNamespace

import anyio
from fastapi import FastAPI
from starlette.testclient import TestClient

from admin.routes.api.style import create_style_router
from services.conversation_archive import ConversationArchive
from services.slang import SlangStore
from services.style import NewStyleExpression, StyleStore


def test_style_api_readonly_lists_summary_and_details(tmp_path) -> None:
    async def _seed() -> StyleStore:
        store = StyleStore(tmp_path / "style.db")
        await store.init()
        expression = await store.upsert_expression(
            NewStyleExpression(
                situation="大家在轻松吐槽",
                style="理解尖锐吐槽但输出时转译",
                group_id="100",
                risk_tags=["profanity"],
                output_policy="transform",
            ),
            evidence={
                "group_id": "100",
                "speaker": "Alice(1)",
                "raw_text": "这也太离谱了吧",
                "context": "Alice(1): 这也太离谱了吧",
            },
        )
        await store.set_status(expression.expression_id, "approved", actor="tester")
        return store

    store = anyio.run(_seed)
    app = FastAPI()
    app.include_router(create_style_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    summary = client.get("/api/admin/style/summary").json()
    assert summary["total"] == 1
    assert summary["approved"] == 1
    assert summary["risk_tagged"] == 1

    listing = client.get("/api/admin/style/expressions", params={"status": "approved"}).json()
    assert listing["total"] == 1
    expression = listing["expressions"][0]
    assert expression["situation"] == "大家在轻松吐槽"
    assert expression["risk_tags"] == ["profanity"]
    assert expression["output_policy"] == "transform"

    detail = client.get(f"/api/admin/style/expressions/{expression['expression_id']}").json()
    assert detail["ok"] is True
    assert detail["expression"]["style"] == "理解尖锐吐槽但输出时转译"

    evidence = client.get(f"/api/admin/style/expressions/{expression['expression_id']}/evidence").json()
    assert evidence["evidence"][0]["raw_text"] == "这也太离谱了吧"

    revisions = client.get(f"/api/admin/style/expressions/{expression['expression_id']}/revisions").json()
    assert [item["action"] for item in revisions["revisions"]] == ["update", "create"]
    anyio.run(store.close)


def test_style_api_lazy_store_uses_context_storage(tmp_path) -> None:
    app = FastAPI()
    ctx = SimpleNamespace(storage_dir=tmp_path)
    app.include_router(create_style_router(ctx=ctx), prefix="/api/admin")
    client = TestClient(app)

    summary = client.get("/api/admin/style/summary").json()
    assert summary["total"] == 0
    assert ctx.style_store.db_path == str(tmp_path / "style.db")
    anyio.run(ctx.style_store.close)


def test_style_api_manual_extract_can_write_global_pool(tmp_path) -> None:
    class _MessageLog:
        async def list_group_ids(self) -> list[str]:
            return ["100", "200"]

        async def query_recent(self, group_id: str, limit: int = 20) -> list[dict[str, object]]:
            del limit
            return [
                {
                    "role": "user",
                    "speaker": f"User({group_id})",
                    "content_text": f"{group_id} 这也太离谱了吧",
                    "message_id": int(group_id),
                }
            ]

    class _LLM:
        async def _call(self, request):
            del request
            return {
                "text": (
                    '{"expressions":[{"situation":"大家在轻松吐槽",'
                    '"style":"先短促附和，再转成符合凤笑梦人设的回应",'
                    '"evidence":"这也太离谱了吧","confidence":0.9,'
                    '"risk_tags":["sarcasm"],"output_policy":"transform",'
                    '"persona_fit":0.8,"mood_fit":0.8,"reason":"可复用吐槽接话方式"}]}'
                )
            }

    async def _init_store() -> StyleStore:
        store = StyleStore(tmp_path / "style.db")
        await store.init()
        return store

    store = anyio.run(_init_store)
    app = FastAPI()
    app.include_router(
        create_style_router(store=store, message_log=_MessageLog(), llm_client=_LLM()),
        prefix="/api/admin",
    )
    client = TestClient(app)

    payload = client.post(
        "/api/admin/style/extract/run",
        json={"scope": "global", "auto_approve": True, "limit": 20},
    ).json()

    assert payload["ok"] is True
    assert payload["groups"] == ["100", "200"]
    assert payload["saved"] == 2
    assert payload["approved"] == 2
    assert [
        {
            "group_id": item["group_id"],
            "scanned": item["scanned"],
            "extracted": item["extracted"],
            "saved": item["saved"],
            "approved": item["approved"],
            "pending": item["pending"],
        }
        for item in payload["per_group"]
    ] == [
        {"group_id": "100", "scanned": 1, "extracted": 1, "saved": 1, "approved": 1, "pending": 0},
        {"group_id": "200", "scanned": 1, "extracted": 1, "saved": 1, "approved": 1, "pending": 0},
    ]

    listing = client.get("/api/admin/style/expressions", params={"scope": "global"}).json()
    assert listing["total"] == 1
    expression = listing["expressions"][0]
    assert expression["group_id"] == "global"
    assert expression["count"] == 2
    assert expression["status"] == "approved"

    evidence = client.get(f"/api/admin/style/expressions/{expression['expression_id']}/evidence").json()
    assert {item["group_id"] for item in evidence["evidence"]} == {"100", "200"}
    anyio.run(store.close)


def test_style_api_manual_extract_reports_groups_with_no_candidates(tmp_path) -> None:
    class _MessageLog:
        async def list_group_ids(self) -> list[str]:
            return ["100", "200"]

        async def query_recent(self, group_id: str, limit: int = 20) -> list[dict[str, object]]:
            del limit
            text = "可学习：这也太离谱了吧" if group_id == "100" else "普通闲聊没有明显表达"
            return [{
                "role": "user",
                "speaker": f"User({group_id})",
                "content_text": text,
                "message_id": int(group_id),
            }]

    class _LLM:
        async def _call(self, request):
            body = str(request.user_messages[0]["content"])
            if "可学习" not in body:
                return {"text": '{"expressions":[]}'}
            return {
                "text": (
                    '{"expressions":[{"situation":"大家在轻松吐槽",'
                    '"style":"先短促附和，再转成符合凤笑梦人设的回应",'
                    '"evidence":"这也太离谱了吧","confidence":0.8,'
                    '"risk_tags":[],"output_policy":"allow_use",'
                    '"persona_fit":0.8,"mood_fit":0.8,"reason":"可复用吐槽接话方式"}]}'
                )
            }

    async def _init_store() -> StyleStore:
        store = StyleStore(tmp_path / "style.db")
        await store.init()
        return store

    store = anyio.run(_init_store)
    app = FastAPI()
    app.include_router(
        create_style_router(store=store, message_log=_MessageLog(), llm_client=_LLM()),
        prefix="/api/admin",
    )
    client = TestClient(app)

    payload = client.post("/api/admin/style/extract/run", json={}).json()

    assert payload["ok"] is True
    assert payload["scanned"] == 2
    assert payload["extracted"] == 1
    assert payload["saved"] == 1
    assert [
        {
            "group_id": item["group_id"],
            "scanned": item["scanned"],
            "extracted": item["extracted"],
            "saved": item["saved"],
        }
        for item in payload["per_group"]
    ] == [
        {"group_id": "100", "scanned": 1, "extracted": 1, "saved": 1},
        {"group_id": "200", "scanned": 1, "extracted": 0, "saved": 0},
    ]
    anyio.run(store.close)


def test_style_api_manual_extract_uses_archive_cursor_when_available(tmp_path) -> None:
    class _LLM:
        async def _call(self, request):
            body = str(request.user_messages[0]["content"])
            if "新样本" not in body:
                return {"text": '{"expressions":[]}'}
            return {
                "text": (
                    '{"expressions":[{"situation":"大家在轻松吐槽",'
                    '"style":"先短促附和，再转成符合凤笑梦人设的回应",'
                    '"evidence":"新样本 这也太离谱了吧","confidence":0.8,'
                    '"risk_tags":[],"output_policy":"allow_use",'
                    '"persona_fit":0.8,"mood_fit":0.8,"reason":"可复用吐槽接话方式"}]}'
                )
            }

    async def _init() -> tuple[StyleStore, ConversationArchive]:
        style_store = StyleStore(tmp_path / "style.db")
        await style_store.init()
        archive = ConversationArchive(db_path=str(tmp_path / "messages.db"))
        await archive.init()
        for idx, text in enumerate(["旧消息", "新样本 这也太离谱了吧"], start=1):
            await archive.record(
                group_id="100",
                role="user",
                speaker=f"User({idx})",
                content_text=text,
                content_json=None,
                message_id=idx,
                created_at=float(idx),
            )
        return style_store, archive

    store, archive = anyio.run(_init)
    app = FastAPI()
    app.include_router(
        create_style_router(store=store, message_log=archive, llm_client=_LLM()),
        prefix="/api/admin",
    )
    client = TestClient(app)

    first = client.post("/api/admin/style/extract/run", json={"group_id": "100", "limit": 20}).json()
    assert first["ok"] is True
    assert first["saved"] == 1
    assert first["per_group"][0]["scan_source"] == "archive"

    second = client.post("/api/admin/style/extract/run", json={"group_id": "100", "limit": 20}).json()
    assert second["ok"] is True
    assert second["scanned"] == 0
    assert second["saved"] == 0

    anyio.run(store.close)
    anyio.run(archive.close)


def test_style_api_manual_extract_consumes_multiple_archive_batches(tmp_path) -> None:
    class _LLM:
        async def _call(self, request):
            body = str(request.user_messages[0]["content"])
            if "新样本" not in body:
                return {"text": '{"expressions":[]}'}
            return {
                "text": (
                    '{"expressions":[{"situation":"大家在轻松吐槽",'
                    '"style":"先短促附和，再转成符合凤笑梦人设的回应",'
                    '"evidence":"新样本 这也太离谱了吧","confidence":0.8,'
                    '"risk_tags":[],"output_policy":"allow_use",'
                    '"persona_fit":0.8,"mood_fit":0.8,"reason":"可复用吐槽接话方式"}]}'
                )
            }

    async def _init() -> tuple[StyleStore, ConversationArchive]:
        style_store = StyleStore(tmp_path / "style.db")
        await style_store.init()
        archive = ConversationArchive(db_path=str(tmp_path / "messages.db"))
        await archive.init()
        for idx in range(1, 8):
            text = "新样本 这也太离谱了吧" if idx in {2, 5, 7} else ""
            await archive.record(
                group_id="100",
                role="user",
                speaker=f"User({idx})",
                content_text=text,
                content_json=None,
                message_id=idx,
                created_at=float(idx),
            )
        await archive.upsert_cursor(
            scanner_name="style_manual_extract",
            chat_type="group",
            chat_id="100",
            scope_key="chat",
            required=True,
            last_message_pk=0,
            last_created_at=0.0,
            scanner_version="v1",
            params_hash="",
            status="active",
        )
        return style_store, archive

    store, archive = anyio.run(_init)
    app = FastAPI()
    app.include_router(
        create_style_router(store=store, message_log=archive, llm_client=_LLM()),
        prefix="/api/admin",
    )
    client = TestClient(app)

    payload = client.post(
        "/api/admin/style/extract/run",
        json={"group_id": "100", "limit": 2, "max_batches": 3, "target_text_rows": 3},
    ).json()

    assert payload["ok"] is True
    assert payload["raw_scanned"] == 6
    assert payload["text_scanned"] == 2
    assert payload["scanned"] == 2
    assert payload["backlog_raw"] == 1
    assert payload["backlog_text"] == 1
    assert payload["has_more"] is True
    assert payload["per_group"][0]["batches"] == 3
    assert payload["per_group"][0]["raw_scanned"] == 6
    assert payload["per_group"][0]["text_scanned"] == 2
    assert payload["per_group"][0]["has_more"] is True

    anyio.run(store.close)
    anyio.run(archive.close)


def test_style_api_manual_extract_filters_known_slang_terms(tmp_path) -> None:
    class _MessageLog:
        async def query_recent(self, group_id: str, limit: int = 20) -> list[dict[str, object]]:
            del group_id, limit
            return [{
                "role": "user",
                "speaker": "User(100)",
                "content_text": "emu ymy emu是谁",
                "message_id": 100,
            }]

    class _LLM:
        async def _call(self, request):
            del request
            return {
                "text": (
                    '{"expressions":[{"situation":"对方连续发送无意义的重复短词（如 emu、ymy）时",'
                    '"style":"先重复对方的词，再反问是谁",'
                    '"evidence":"emu ymy emu是谁","confidence":0.8,'
                    '"risk_tags":[],"output_policy":"allow_use",'
                    '"persona_fit":0.8,"mood_fit":0.8,"reason":"错误地把黑话当表达"}]}'
                )
            }

    async def _init_stores() -> tuple[StyleStore, SlangStore]:
        style_store = StyleStore(tmp_path / "style.db")
        await style_store.init()
        slang_store = SlangStore(tmp_path / "slang.db")
        await slang_store.init()
        await slang_store.create_term(
            term="emu",
            meaning="凤笑梦",
            aliases=["ymy"],
            group_id="100",
            status="candidate",
        )
        return style_store, slang_store

    style_store, slang_store = anyio.run(_init_stores)
    app = FastAPI()
    app.include_router(
        create_style_router(
            store=style_store,
            message_log=_MessageLog(),
            llm_client=_LLM(),
            slang_store=slang_store,
        ),
        prefix="/api/admin",
    )
    client = TestClient(app)

    payload = client.post("/api/admin/style/extract/run", json={"group_id": "100"}).json()

    assert payload["ok"] is True
    assert payload["extracted"] == 1
    assert payload["filtered"] == 1
    assert payload["saved"] == 0
    assert payload["per_group"][0]["filtered"] == 1
    listing = client.get("/api/admin/style/expressions", params={"group_id": "100"}).json()
    assert listing["total"] == 0
    anyio.run(style_store.close)
    anyio.run(slang_store.close)


def test_style_api_manual_extract_can_promote_existing_pending(tmp_path) -> None:
    class _MessageLog:
        async def query_recent(self, group_id: str, limit: int = 20) -> list[dict[str, object]]:
            del group_id, limit
            return [{
                "role": "user",
                "speaker": "User(100)",
                "content_text": "这也太离谱了吧",
                "message_id": 100,
            }]

    class _LLM:
        async def _call(self, request):
            del request
            return {
                "text": (
                    '{"expressions":[{"situation":"大家在轻松吐槽",'
                    '"style":"先短促附和，再转成符合凤笑梦人设的回应",'
                    '"evidence":"这也太离谱了吧","confidence":0.92,'
                    '"risk_tags":[],"output_policy":"allow_use",'
                    '"persona_fit":0.9,"mood_fit":0.8,"reason":"高置信表达习惯"}]}'
                )
            }

    async def _init_store() -> StyleStore:
        store = StyleStore(tmp_path / "style.db")
        await store.init()
        return store

    store = anyio.run(_init_store)
    app = FastAPI()
    app.include_router(
        create_style_router(store=store, message_log=_MessageLog(), llm_client=_LLM()),
        prefix="/api/admin",
    )
    client = TestClient(app)

    first = client.post("/api/admin/style/extract/run", json={"group_id": "100"}).json()
    assert first["pending"] == 1
    second = client.post(
        "/api/admin/style/extract/run",
        json={"group_id": "100", "auto_approve": True},
    ).json()

    assert second["approved"] == 1
    listing = client.get("/api/admin/style/expressions", params={"group_id": "100"}).json()
    assert listing["total"] == 1
    assert listing["expressions"][0]["status"] == "approved"
    assert listing["expressions"][0]["count"] == 2
    anyio.run(store.close)


def test_style_api_feedback_and_profile_lifecycle(tmp_path) -> None:
    async def _seed() -> tuple[StyleStore, str]:
        store = StyleStore(tmp_path / "style.db")
        await store.init()
        expression = await store.upsert_expression(
            NewStyleExpression(
                situation="大家在轻松吐槽",
                style="先短促附和，再转成符合凤笑梦人设的回应",
                group_id="100",
                confidence=0.8,
            )
        )
        await store.set_status(expression.expression_id, "approved")
        return store, expression.expression_id

    store, expression_id = anyio.run(_seed)
    app = FastAPI()
    app.include_router(create_style_router(store=store), prefix="/api/admin")
    client = TestClient(app)

    feedback = client.post(
        f"/api/admin/style/expressions/{expression_id}/feedback",
        json={"rating": "positive", "reason": "更自然"},
    ).json()
    assert feedback["ok"] is True
    assert feedback["expression"]["confidence"] > 0.8

    muted = client.post(
        f"/api/admin/style/expressions/{expression_id}/status",
        json={"status": "muted", "reason": "暂不注入"},
    ).json()
    assert muted["ok"] is True
    assert muted["expression"]["status"] == "muted"
    approved = client.post(
        f"/api/admin/style/expressions/{expression_id}/status",
        json={"status": "approved"},
    ).json()
    assert approved["ok"] is True

    feedback_list = client.get("/api/admin/style/feedback", params={"target_id": expression_id}).json()
    assert feedback_list["total"] == 1
    assert feedback_list["feedback"][0]["rating"] == "positive"

    generated = client.post(
        "/api/admin/style/profiles/generate",
        json={"group_id": "100", "enable": True},
    ).json()
    assert generated["ok"] is True
    profile_id = generated["profile"]["profile_id"]
    assert generated["profile"]["status"] == "enabled"

    current = client.get("/api/admin/style/profiles/current", params={"group_id": "100"}).json()
    assert current["ok"] is True
    assert current["profiles"][0]["profile_id"] == profile_id
    assert "【当前动态风格档案】" in current["prompt_block"]

    disabled = client.post(f"/api/admin/style/profiles/{profile_id}/disable", json={}).json()
    assert disabled["ok"] is True
    assert disabled["profile"]["status"] == "disabled"

    anyio.run(store.close)
