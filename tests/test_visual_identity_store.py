"""RED→GREEN: durable exact visual-identity store (full SHA-256).

Contract:
- Full normalized SHA-256 is the only exact identity key.
- Persist entity label, correcting_user_id, origin_group_id, visibility,
  source_message_id, provenance, timestamps.
- Lookup positive: same current user + same group (same_group).
- Negatives: another user, another group, private-chat mapping in group.
- Legacy/unknown visibility fail closed.
- Exact lookup is deterministic; perceptual lookup deferred.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from typing import Any

import pytest

from plugins.memo.plugin import MemoConfig, MemoExtractor
from services.memory.card_store import CardStore
from services.memory.visual_identity import (
    NewVisualIdentity,
    VisualIdentityStore,
    normalize_image_sha256,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_UPPER = ("C" * 64)


def _authorized_correction_context(image_sha256: str) -> dict[str, Any]:
    return {
        "visual_evidence": {
            "image_sha256": image_sha256,
            "provenance": "visual_system",
        },
        "trigger": {"mode": "correction", "evidence_count": 1},
    }


@pytest.fixture
async def vi_store(tmp_path) -> AsyncIterator[VisualIdentityStore]:
    store = VisualIdentityStore(db_path=str(tmp_path / "visual_id.db"))
    await store.init()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def card_store(tmp_path) -> AsyncIterator[CardStore]:
    store = CardStore(db_path=str(tmp_path / "cards.db"))
    await store.init()
    try:
        yield store
    finally:
        await store.close()


class FakeLLM:
    def __init__(self, text: str = "无") -> None:
        self.text = text
        self.calls: list = []

    async def __call__(self, request) -> dict[str, str]:
        self.calls.append(request)
        return {"text": self.text}


# ---------------------------------------------------------------------------
# normalize / store / lookup
# ---------------------------------------------------------------------------


def test_normalize_image_sha256_accepts_full_hex_and_prefixes() -> None:
    assert normalize_image_sha256(SHA_A) == SHA_A
    assert normalize_image_sha256(SHA_UPPER) == "c" * 64
    assert normalize_image_sha256(f"sha256:{SHA_A}") == SHA_A
    assert normalize_image_sha256("deadbeef") is None  # short hash display-only
    assert normalize_image_sha256("") is None
    assert normalize_image_sha256(None) is None


@pytest.mark.asyncio
async def test_init_migrates_legacy_two_column_primary_key(tmp_path) -> None:
    db_path = tmp_path / "legacy_visual_id.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE visual_identities (
                image_sha256 TEXT NOT NULL,
                entity_label TEXT NOT NULL,
                correcting_user_id TEXT NOT NULL,
                origin_group_id TEXT,
                visibility TEXT NOT NULL,
                source_message_id TEXT,
                provenance TEXT NOT NULL DEFAULT 'user_correction',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (image_sha256, correcting_user_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO visual_identities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                SHA_A,
                "高松灯",
                "u1",
                "g_alpha",
                "same_group",
                "msg-legacy",
                "user_correction",
                "2026-07-19T00:00:00+08:00",
                "2026-07-19T00:00:00+08:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    store = VisualIdentityStore(db_path=str(db_path))
    await store.init()
    try:
        cursor = await store._db.execute("PRAGMA table_info(visual_identities)")
        rows = await cursor.fetchall()
        primary_key = {
            str(row["name"]): int(row["pk"])
            for row in rows
            if int(row["pk"]) > 0
        }

        assert primary_key == {
            "image_sha256": 1,
            "correcting_user_id": 2,
            "scope_key": 3,
        }
        legacy = await store.lookup_for_context(
            SHA_A,
            current_user_id="u1",
            current_group_id="g_alpha",
        )
        assert legacy is not None
        assert legacy.entity_label == "高松灯"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_upsert_and_exact_lookup_deterministic(vi_store: VisualIdentityStore) -> None:
    rec = await vi_store.upsert(
        NewVisualIdentity(
            image_sha256=SHA_UPPER,
            entity_label="高松灯",
            correcting_user_id="u1",
            origin_group_id="g_alpha",
            visibility="same_group",
            source_message_id="msg-1",
            provenance="user_correction",
        )
    )
    assert rec.image_sha256 == "c" * 64
    assert rec.entity_label == "高松灯"
    assert rec.correcting_user_id == "u1"
    assert rec.origin_group_id == "g_alpha"
    assert rec.visibility == "same_group"
    assert rec.source_message_id == "msg-1"
    assert rec.provenance == "user_correction"
    assert rec.created_at
    assert rec.updated_at

    hit = await vi_store.get_exact("c" * 64, correcting_user_id="u1")
    assert hit is not None
    assert hit.entity_label == "高松灯"
    # Deterministic re-lookup
    hit2 = await vi_store.get_exact(SHA_UPPER, correcting_user_id="u1")
    assert hit2 is not None
    assert hit2.entity_label == hit.entity_label
    assert hit2.image_sha256 == hit.image_sha256


@pytest.mark.asyncio
async def test_lookup_positive_same_user_same_group(vi_store: VisualIdentityStore) -> None:
    await vi_store.store_correction(
        image_sha256=SHA_A,
        entity_label="守岸人",
        correcting_user_id="u_speaker",
        origin_group_id="g_alpha",
        visibility="same_group",
        source_message_id="m1",
        **_authorized_correction_context(SHA_A),
    )
    hit = await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_speaker",
        current_group_id="g_alpha",
    )
    assert hit is not None
    assert hit.entity_label == "守岸人"


@pytest.mark.asyncio
async def test_lookup_negative_other_user(vi_store: VisualIdentityStore) -> None:
    await vi_store.store_correction(
        image_sha256=SHA_A,
        entity_label="守岸人",
        correcting_user_id="u_speaker",
        origin_group_id="g_alpha",
        visibility="same_group",
        **_authorized_correction_context(SHA_A),
    )
    hit = await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_other",
        current_group_id="g_alpha",
    )
    assert hit is None


@pytest.mark.asyncio
async def test_lookup_negative_other_group(vi_store: VisualIdentityStore) -> None:
    await vi_store.store_correction(
        image_sha256=SHA_A,
        entity_label="守岸人",
        correcting_user_id="u_speaker",
        origin_group_id="g_alpha",
        visibility="same_group",
        **_authorized_correction_context(SHA_A),
    )
    hit = await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_speaker",
        current_group_id="g_beta",
    )
    assert hit is None


@pytest.mark.asyncio
async def test_same_user_same_image_corrections_coexist_across_groups(
    vi_store: VisualIdentityStore,
) -> None:
    """A correction in group B must not erase the same user's group A mapping."""
    await vi_store.store_correction(
        image_sha256=SHA_A,
        entity_label="群A角色",
        correcting_user_id="u_speaker",
        origin_group_id="g_alpha",
        visibility="same_group",
        source_message_id="m-alpha",
        **_authorized_correction_context(SHA_A),
    )
    await vi_store.store_correction(
        image_sha256=SHA_A,
        entity_label="群B角色",
        correcting_user_id="u_speaker",
        origin_group_id="g_beta",
        visibility="same_group",
        source_message_id="m-beta",
        **_authorized_correction_context(SHA_A),
    )

    alpha = await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_speaker",
        current_group_id="g_alpha",
    )
    beta = await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_speaker",
        current_group_id="g_beta",
    )

    assert alpha is not None
    assert alpha.entity_label == "群A角色"
    assert beta is not None
    assert beta.entity_label == "群B角色"


@pytest.mark.asyncio
async def test_lookup_negative_private_mapping_in_group(
    vi_store: VisualIdentityStore,
) -> None:
    await vi_store.store_correction(
        image_sha256=SHA_A,
        entity_label="私聊纠正角色",
        correcting_user_id="u_speaker",
        origin_group_id=None,
        visibility="private",
        **_authorized_correction_context(SHA_A),
    )
    # Private correction must not surface in group context.
    hit = await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_speaker",
        current_group_id="g_alpha",
    )
    assert hit is None
    # Same user in private chat may see it.
    hit_private = await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_speaker",
        current_group_id=None,
    )
    assert hit_private is not None
    assert hit_private.entity_label == "私聊纠正角色"


@pytest.mark.asyncio
async def test_lookup_fail_closed_unknown_visibility(
    vi_store: VisualIdentityStore,
) -> None:
    # Bypass NewVisualIdentity validation to plant an unknown visibility row.
    db = vi_store._require_db()  # deliberate fixture: plant unknown visibility
    await db.execute(
        "INSERT INTO visual_identities ("
        "image_sha256, entity_label, correcting_user_id, scope_key, origin_group_id, "
        "visibility, source_message_id, provenance, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            SHA_B,
            "legacy-label",
            "u_speaker",
            "group:g_alpha",
            "g_alpha",
            "legacy_unknown",
            None,
            "legacy",
            "2020-01-01T00:00:00+08:00",
            "2020-01-01T00:00:00+08:00",
        ),
    )
    await db.commit()
    hit = await vi_store.lookup_for_context(
        SHA_B,
        current_user_id="u_speaker",
        current_group_id="g_alpha",
    )
    assert hit is None


@pytest.mark.asyncio
async def test_rejects_short_hash(vi_store: VisualIdentityStore) -> None:
    with pytest.raises(ValueError, match="64-char"):
        await vi_store.upsert(
            NewVisualIdentity(
                image_sha256="abcd1234",
                entity_label="x",
                correcting_user_id="u1",
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("visual_evidence", "trigger"),
    [
        (None, None),
        (
            {"image_sha256": SHA_A, "provenance": "visual_system"},
            {"mode": "normal", "evidence_count": 1},
        ),
        (
            {"image_sha256": SHA_A, "provenance": "user_text"},
            {"mode": "correction", "evidence_count": 1},
        ),
        (
            {"image_sha256": SHA_B, "provenance": "visual_system"},
            {"mode": "correction", "evidence_count": 1},
        ),
        (
            {"image_sha256": SHA_A, "provenance": "visual_system"},
            {"mode": "correction", "evidence_count": 2},
        ),
    ],
)
async def test_store_correction_rejects_invalid_authorization_context(
    vi_store: VisualIdentityStore,
    visual_evidence: dict[str, object] | None,
    trigger: dict[str, object] | None,
) -> None:
    with pytest.raises(ValueError, match="authorized correction context"):
        await vi_store.store_correction(
            image_sha256=SHA_A,
            entity_label="高松灯",
            correcting_user_id="u_speaker",
            origin_group_id="g_alpha",
            visibility="same_group",
            visual_evidence=visual_evidence,
            trigger=trigger,
        )

    assert await vi_store.lookup_for_context(
        SHA_A,
        current_user_id="u_speaker",
        current_group_id="g_alpha",
    ) is None


# ---------------------------------------------------------------------------
# Extractor-facing API: visual correction is NOT a preference card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_store_visual_correction_not_preference_card(
    card_store: CardStore,
    vi_store: VisualIdentityStore,
) -> None:
    llm = FakeLLM("无")
    extractor = MemoExtractor(
        card_store=card_store,
        api_call=llm,
        config=MemoConfig(write_policy_enabled=False),
        visual_identity_store=vi_store,
    )
    rec = await extractor.store_visual_correction(
        image_sha256=SHA_A,
        entity_label="高松灯",
        user_id="u_speaker",
        group_id="g_alpha",
        source_message_id="msg-vi-1",
        **_authorized_correction_context(SHA_A),
    )
    assert rec is not None
    assert rec.entity_label == "高松灯"
    # Must not misclassify as a preference/memory card.
    cards = await card_store.get_entity_cards("user", "u_speaker")
    assert cards == []
    assert extractor.stats.get("visual_correction", 0) == 1
