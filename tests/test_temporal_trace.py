"""RED tests for temporal memory trace (Option A).

Encodes R1–R12 + R14 assembler/store/PromptContext contracts.
GREEN implements services.memory.temporal_trace and CardStore chain primitives.
Production code is intentionally absent; these tests must fail until GREEN lands.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from kernel.types import Identity, PromptContext
from services.memory.card_store import CardStore, NewCard
from services.memory.temporal_trace import (
    TemporalTraceAssembler,
    TemporalTraceConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path) -> AsyncIterator[CardStore]:
    db_path = str(tmp_path / "temporal_trace_cards.db")
    s = CardStore(db_path=db_path)
    await s.init()
    try:
        yield s
    finally:
        await s.close()


async def _hangzhou_shanghai_chain(
    store: CardStore,
    *,
    scope: str = "user",
    scope_id: str = "123",
    category: str = "fact",
) -> tuple[str, str]:
    """Build 杭州 → 上海 supersede chain. Returns (hangzhou_id, shanghai_id)."""
    hangzhou = await store.add_card(
        NewCard(
            category=category,
            scope=scope,
            scope_id=scope_id,
            content="用户住在杭州",
            source="memo_extractor",
        ),
        source_msg_id="m_hz",
        captured_at="2026-01-10T10:00:00+08:00",
        captured_by="memo_extractor",
    )
    shanghai = await store.supersede_card(
        hangzhou,
        NewCard(
            category=category,
            scope=scope,
            scope_id=scope_id,
            content="用户住在上海",
            source="memo_extractor",
        ),
        source_msg_id="m_sh",
        evidence_text="搬到上海了",
        captured_by="memo_extractor",
    )
    return hangzhou, shanghai


def _assembler(
    store: CardStore,
    *,
    enabled: bool = True,
    max_heads: int = 24,
    max_kps: int = 2,
    max_depth: int = 4,
    max_chars: int = 600,
    group_memory_config: Any = None,
) -> Any:
    cfg = TemporalTraceConfig(
        enabled=enabled,
        max_heads=max_heads,
        max_kps=max_kps,
        max_depth=max_depth,
        max_chars=max_chars,
    )
    return TemporalTraceAssembler(
        store,
        config=cfg,
        group_memory_config=group_memory_config,
    )


def _kp_fields(kp: Any) -> dict[str, Any]:
    """Normalize knowledge-point DTO whether dataclass or dict-like."""
    if isinstance(kp, dict):
        return kp
    out: dict[str, Any] = {}
    for key in (
        "current",
        "earlier",
        "trajectory",
        "evidence_refs",
        "reason",
        "confidence",
        "valid_to",
    ):
        if hasattr(kp, key):
            out[key] = getattr(kp, key)
    return out


def _current_content(kp: Any) -> str:
    fields = _kp_fields(kp)
    current = fields.get("current")
    if current is None:
        return ""
    if isinstance(current, str):
        return current
    if isinstance(current, dict):
        return str(current.get("content") or current.get("text") or "")
    return str(getattr(current, "content", None) or getattr(current, "text", None) or current)


def _earlier_contents(kp: Any) -> list[str]:
    fields = _kp_fields(kp)
    earlier = fields.get("earlier")
    if earlier is None:
        return []
    if isinstance(earlier, str):
        return [earlier]
    if isinstance(earlier, dict):
        return [str(earlier.get("content") or earlier.get("text") or "")]
    if isinstance(earlier, (list, tuple)):
        out: list[str] = []
        for item in earlier:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(str(item.get("content") or item.get("text") or ""))
            else:
                out.append(
                    str(getattr(item, "content", None) or getattr(item, "text", None) or item)
                )
        return out
    return [str(getattr(earlier, "content", None) or getattr(earlier, "text", None) or earlier)]


def _kp_reason(kp: Any) -> str:
    fields = _kp_fields(kp)
    return str(fields.get("reason") or "")


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    text = getattr(result, "text", None)
    if text is not None:
        return str(text)
    render = getattr(result, "render", None)
    if callable(render):
        return str(render())
    return ""


def _result_kps(result: Any) -> list[Any]:
    if result is None:
        return []
    kps = getattr(result, "knowledge_points", None)
    if kps is None:
        return []
    return list(kps)


def _result_reason(result: Any) -> str:
    if result is None:
        return ""
    top = getattr(result, "reason", None)
    if top:
        return str(top)
    kps = _result_kps(result)
    if not kps:
        return ""
    return _kp_reason(kps[0])


def _flatten_text(result: Any) -> str:
    parts = [_result_text(result)]
    for kp in _result_kps(result):
        parts.append(_current_content(kp))
        parts.extend(_earlier_contents(kp))
        traj = _kp_fields(kp).get("trajectory")
        if traj is not None:
            parts.append(str(traj))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PromptContext.current_message (R4/R5 authorization surface)
# ---------------------------------------------------------------------------


def test_prompt_context_current_message_defaults_empty() -> None:
    """R4 surface: PromptContext.current_message must exist and default to ''."""
    ctx = PromptContext(
        session_id="private_123",
        group_id=None,
        user_id="123",
        identity=Identity(id="bot", name="Bot", personality="test"),
        conversation_text="buffer text",
    )
    assert hasattr(ctx, "current_message")
    assert ctx.current_message == ""


def test_prompt_context_current_message_is_independent_of_conversation_text() -> None:
    ctx = PromptContext(
        session_id="private_123",
        group_id=None,
        user_id="123",
        identity=Identity(id="bot", name="Bot", personality="test"),
        conversation_text="aggregated buffer 以前住哪",
        current_message="我现在住在哪里",
    )
    assert ctx.current_message == "我现在住在哪里"
    assert "以前" in ctx.conversation_text
    assert "以前" not in ctx.current_message


# ---------------------------------------------------------------------------
# TemporalTraceConfig bounds (R7/R10 schema surface)
# ---------------------------------------------------------------------------


def test_temporal_trace_config_defaults() -> None:
    cfg = TemporalTraceConfig()
    assert cfg.enabled is True
    assert cfg.max_heads == 24
    assert cfg.max_kps == 2
    assert cfg.max_depth == 4
    assert cfg.max_chars == 600


# ---------------------------------------------------------------------------
# R1 — plain “现在” does not trigger; ordinary active-only remains Shanghai
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_plain_now_query_no_trace_ordinary_active_only(store: CardStore) -> None:
    _hangzhou, shanghai = await _hangzhou_shanghai_chain(store)

    # Ordinary active-only surface: get_entity_cards never returns superseded 杭州.
    active = await store.get_entity_cards("user", "123")
    assert len(active) == 1
    assert active[0].card_id == shanghai
    assert "上海" in active[0].content
    assert all("杭州" not in c.content for c in active)

    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我现在住在哪里",
        rewritten_query="用户现居住地",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is None or not _result_kps(result) or not _result_text(result).strip()
    flat = _flatten_text(result)
    assert "杭州" not in flat
    assert "记忆时间轨迹" not in flat  # assembler returns body only; label is plugin-side


# ---------------------------------------------------------------------------
# R2 — premise conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_premise_conflict_trace_current_shanghai_earlier_hangzhou(
    store: CardStore,
) -> None:
    hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我不是还住杭州吗",
        rewritten_query="用户是否仍住杭州",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is not None
    text = _result_text(result)
    assert text.strip()
    assert len(text) <= 600
    assert "上海" in text or "上海" in _flatten_text(result)
    assert "杭州" in text or "杭州" in _flatten_text(result)
    # Current-first guidance (Chinese authoritative wording).
    assert any(
        marker in text
        for marker in ("以当前为准", "当前为准", "当前事实", "当前有效", "以现行")
    ) or any(
        marker in text
        for marker in ("current", "authoritative")
    )

    reason = _result_reason(result)
    assert reason == "premise_conflict"
    kps = _result_kps(result)
    assert len(kps) >= 1
    assert any("上海" in _current_content(kp) for kp in kps)
    assert any(any("杭州" in e for e in _earlier_contents(kp)) for kp in kps)

    # Ordinary active pack still excludes 杭州.
    active = await store.get_entity_cards("user", "123")
    assert all(c.card_id != hangzhou for c in active)
    assert all("杭州" not in c.content for c in active)


# ---------------------------------------------------------------------------
# R3 — historical intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r3_historical_intent_earlier_hangzhou_ordinary_active_only(
    store: CardStore,
) -> None:
    hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="用户历史居住地",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is not None
    assert _result_reason(result) == "historical_intent"
    flat = _flatten_text(result)
    assert "杭州" in flat
    kps = _result_kps(result)
    assert any(any("杭州" in e for e in _earlier_contents(kp)) for kp in kps)

    active = await store.get_entity_cards("user", "123")
    assert len(active) == 1
    assert active[0].card_id == shanghai
    assert "杭州" not in active[0].content
    assert hangzhou  # keep fixture used for clarity


# ---------------------------------------------------------------------------
# R4 — rewritten_query cannot self-authorize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r4_rewritten_query_historical_words_cannot_authorize(
    store: CardStore,
) -> None:
    _hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我现在住在哪里",  # no historical/premise trigger
        rewritten_query="用户以前住在哪里 曾经居住地",  # rewrite injects 以前/曾经
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is None or not _result_text(result).strip()
    assert "杭州" not in _flatten_text(result)


# ---------------------------------------------------------------------------
# R5 — current_message authorizes even if rewrite drops trigger words
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r5_current_message_historical_intent_still_triggers(
    store: CardStore,
) -> None:
    _hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="用户居住地查询",  # rewrite dropped 以前
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is not None
    assert _result_reason(result) == "historical_intent"
    assert "杭州" in _flatten_text(result)


# ---------------------------------------------------------------------------
# R6 — single active / no supersedes → no trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r6_single_active_no_supersedes_no_trace(store: CardStore) -> None:
    only = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="用户住在上海",
        ),
        source_msg_id="m_only",
        captured_by="memo_extractor",
    )
    assembler = _assembler(store)
    for msg in ("我以前住在哪里", "我不是还住杭州吗", "还记得我住哪吗"):
        result = await assembler.assemble(
            current_message=msg,
            rewritten_query=msg,
            session_id="private_123",
            user_id="123",
            group_id=None,
            active_memory_hits=[only],
        )
        assert result is None or not _result_text(result).strip(), msg


# ---------------------------------------------------------------------------
# R7 — broken / missing / cycle / expired / cross-category fail closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r7_broken_missing_parent_no_partial_trace(store: CardStore) -> None:
    # Manually insert active head pointing at missing parent.
    head_id = "card_broken_head"
    await store._db.execute(
        "INSERT INTO memory_cards ("
        "card_id, category, scope, scope_id, content, confidence, "
        "status, priority, supersedes, source, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            head_id,
            "fact",
            "user",
            "123",
            "用户住在上海",
            0.7,
            "active",
            5,
            "card_missing_parent_xxx",
            "manual",
            "2026-03-01T00:00:00+08:00",
            "2026-03-01T00:00:00+08:00",
        ),
    )
    await store._db.commit()

    walk = await store.walk_supersedes_chain(head_id, max_depth=4)
    # Fail-closed: empty list OR dedicated error type treated as omit by assembler.
    assert walk == [] or walk is None

    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[head_id],
    )
    assert result is None or not _result_text(result).strip() or (
        getattr(result, "omitted_reason", None) is not None
        or any(
            getattr(kp, "omitted", False) or "omit" in str(_kp_fields(kp)).lower()
            for kp in _result_kps(result)
        )
    )
    # Never partially leak head content as a completed trajectory without parent.
    if result is not None and _result_kps(result):
        for kp in _result_kps(result):
            earlier = _earlier_contents(kp)
            # No fabricated earlier node from missing parent id.
            assert all("card_missing_parent" not in e for e in earlier)


@pytest.mark.asyncio
async def test_r7_cycle_chain_fail_closed(store: CardStore) -> None:
    # Two cards cycling via supersedes pointers; statuses forced for the walk.
    a_id = "card_cycle_a"
    b_id = "card_cycle_b"
    ts = "2026-03-01T00:00:00+08:00"
    await store._db.execute(
        "INSERT INTO memory_cards ("
        "card_id, category, scope, scope_id, content, confidence, "
        "status, priority, supersedes, source, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (a_id, "fact", "user", "123", "节点A住杭州", 0.7, "active", 5, b_id, "manual", ts, ts),
    )
    await store._db.execute(
        "INSERT INTO memory_cards ("
        "card_id, category, scope, scope_id, content, confidence, "
        "status, priority, supersedes, source, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (b_id, "fact", "user", "123", "节点B住上海", 0.7, "superseded", 5, a_id, "manual", ts, ts),
    )
    await store._db.commit()

    walk = await store.walk_supersedes_chain(a_id, max_depth=4)
    assert walk == [] or walk is None

    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[a_id],
    )
    assert result is None or not _result_text(result).strip()


@pytest.mark.asyncio
async def test_r7_expired_parent_fail_closed(store: CardStore) -> None:
    hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    # Expire the parent — chain integrity requires superseded parents, not expired.
    ok = await store.expire_card(hangzhou)
    assert ok
    parent = await store.get_card(hangzhou)
    assert parent is not None
    assert parent.status == "expired"

    walk = await store.walk_supersedes_chain(shanghai, max_depth=4)
    assert walk == [] or walk is None

    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is None or not _result_text(result).strip()
    assert "杭州" not in _flatten_text(result)


# ---------------------------------------------------------------------------
# R8 — cross-user / group→user poison never appears
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r8_cross_user_poison_never_in_dto(store: CardStore) -> None:
    # Victim user 123 chain (杭州→上海).
    _hz, sh = await _hangzhou_shanghai_chain(store, scope_id="123")
    # Attacker user 999 unrelated poisonous chain with lexical overlap.
    poison_old = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="999",
            content="用户住在杭州并且密码是secret999",
        ),
        source_msg_id="m_poison_old",
        captured_by="memo_extractor",
    )
    poison_new = await store.supersede_card(
        poison_old,
        NewCard(
            category="fact",
            scope="user",
            scope_id="999",
            content="用户住在上海并且密钥是leak999",
        ),
        source_msg_id="m_poison_new",
        evidence_text="poison",
        captured_by="memo_extractor",
    )

    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        # Even if retrieval mistakenly hands a foreign head, assembler must not leak it.
        active_memory_hits=[sh, poison_new],
    )
    flat = _flatten_text(result)
    assert "secret999" not in flat
    assert "leak999" not in flat
    assert "999" not in flat or "secret999" not in flat
    # Evidence refs must not include poison card ids.
    if result is not None:
        for kp in _result_kps(result):
            refs = _kp_fields(kp).get("evidence_refs") or []
            ref_text = str(refs)
            assert poison_old not in ref_text
            assert poison_new not in ref_text


@pytest.mark.asyncio
async def test_r8_group_scope_never_reads_user_private_chain(store: CardStore) -> None:
    # Private user chain.
    _hz, sh_user = await _hangzhou_shanghai_chain(store, scope="user", scope_id="123")
    # Group-local chain (different content).
    g_old = await store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="984198159",
            content="群约定聚餐在杭州",
        ),
        source_msg_id="m_g_old",
        captured_by="memo_extractor",
    )
    g_new = await store.supersede_card(
        g_old,
        NewCard(
            category="fact",
            scope="group",
            scope_id="984198159",
            content="群约定聚餐在上海",
        ),
        source_msg_id="m_g_new",
        evidence_text="改地方了",
        captured_by="memo_extractor",
    )

    assembler = _assembler(store)
    # Group session must only resolve group/pool scopes — never user/private.
    result = await assembler.assemble(
        current_message="以前群聚餐定在哪里",
        rewritten_query="",
        session_id="group_984198159",
        user_id="123",
        group_id="984198159",
        active_memory_hits=[g_new, sh_user],  # sh_user is poisoned cross-scope hit
    )
    flat = _flatten_text(result)
    # Private user content must not appear when assembling for group.
    # Group chain may still produce a valid group trace.
    assert "用户住在" not in flat
    if result is not None:
        for kp in _result_kps(result):
            refs = str(_kp_fields(kp).get("evidence_refs") or [])
            assert sh_user not in refs
            assert _hz not in refs


# ---------------------------------------------------------------------------
# R9 — ambiguous weak matches fail closed (or single clear highest chain)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r9_two_weak_ambiguous_matches_fail_closed_or_single_clear(
    store: CardStore,
) -> None:
    # Two independent fact chains with similar weak lexical anchors.
    a_old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="用户喜欢猫"),
        source_msg_id="m_a1",
        captured_by="memo_extractor",
    )
    a_new = await store.supersede_card(
        a_old,
        NewCard(category="fact", scope="user", scope_id="123", content="用户喜欢狗"),
        source_msg_id="m_a2",
        evidence_text="改喜欢狗了",
        captured_by="memo_extractor",
    )
    b_old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="用户养了鱼"),
        source_msg_id="m_b1",
        captured_by="memo_extractor",
    )
    b_new = await store.supersede_card(
        b_old,
        NewCard(category="fact", scope="user", scope_id="123", content="用户养了鸟"),
        source_msg_id="m_b2",
        evidence_text="改养鸟了",
        captured_by="memo_extractor",
    )

    assembler = _assembler(store)
    # Ambiguous historical query that weakly overlaps both chains without a clear winner.
    result = await assembler.assemble(
        current_message="我以前养过什么 / 以前喜欢什么",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[a_new, b_new],
    )
    if result is None or not _result_text(result).strip():
        return  # fail-closed OK
    kps = _result_kps(result)
    # Must not mix both chains into one KP trajectory.
    flat = _flatten_text(result)
    mixed = ("猫" in flat or "狗" in flat) and ("鱼" in flat or "鸟" in flat)
    if mixed:
        # Allowed only if each KP is pure and total KP count respects max_kps,
        # but for two *weak* matches contract prefers fail-closed / single clear.
        # Enforce: not both chains present unless one KP only (single highest).
        assert len(kps) == 1, "ambiguous weak matches must not emit mixed multi-chain KPs"
        only = _flatten_text(type("R", (), {"text": "", "knowledge_points": kps})())
        has_a = "猫" in only or "狗" in only
        has_b = "鱼" in only or "鸟" in only
        assert has_a ^ has_b, "single KP must not mix both ambiguous chains"


# ---------------------------------------------------------------------------
# R10 — hard bounds: depth/heads/kps/chars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r10_max_depth_truncates_chain_walk(store: CardStore) -> None:
    # Build chain of depth 6 (5 supersedes) — walk max_depth=4 → head + 3 ancestors.
    root = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="居住地V0北京"),
        source_msg_id="m_d0",
        captured_by="memo_extractor",
    )
    prev = root
    contents = ["居住地V0北京"]
    for i, city in enumerate(["天津", "南京", "杭州", "苏州", "上海"], start=1):
        content = f"居住地V{i}{city}"
        contents.append(content)
        prev = await store.supersede_card(
            prev,
            NewCard(category="fact", scope="user", scope_id="123", content=content),
            source_msg_id=f"m_d{i}",
            evidence_text=f"搬到{city}",
            captured_by="memo_extractor",
        )
    head = prev
    walk = await store.walk_supersedes_chain(head, max_depth=4)
    assert walk is not None
    assert len(walk) <= 4
    assert walk[0].card_id == head
    # Oldest beyond depth must not appear.
    walked_ids = {c.card_id for c in walk}
    assert root not in walked_ids or len(walk) <= 4

    assembler = _assembler(store, max_depth=4)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[head],
    )
    assert result is not None
    flat = _flatten_text(result)
    # Depth bound: earliest V0 may be omitted; current head must be present.
    assert "上海" in flat or "V5" in flat
    assert len(_result_text(result)) <= 600


@pytest.mark.asyncio
async def test_r10_max_kps_and_max_chars_hard_cap(store: CardStore) -> None:
    heads: list[str] = []
    for i in range(4):
        old = await store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id="123",
                content=f"用户以前的爱好{i}是旧爱好{i}",
            ),
            source_msg_id=f"m_kp_old_{i}",
            captured_by="memo_extractor",
        )
        new = await store.supersede_card(
            old,
            NewCard(
                category="fact",
                scope="user",
                scope_id="123",
                content=f"用户现在的爱好{i}是新爱好{i}",
            ),
            source_msg_id=f"m_kp_new_{i}",
            evidence_text=f"改了爱好{i}",
            captured_by="memo_extractor",
        )
        heads.append(new)

    assembler = _assembler(store, max_kps=2, max_chars=600)
    result = await assembler.assemble(
        current_message="我以前的爱好都是什么",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=heads,
    )
    if result is None:
        return
    assert len(_result_kps(result)) <= 2
    assert len(_result_text(result)) <= 600


@pytest.mark.asyncio
async def test_r10_max_heads_scan_cap_on_list_active_chain_heads(store: CardStore) -> None:
    # Insert >24 active heads with supersedes in same scope.
    for i in range(30):
        old = await store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id="123",
                content=f"旧事实{i:02d}",
            ),
        )
        await store.supersede_card(
            old,
            NewCard(
                category="fact",
                scope="user",
                scope_id="123",
                content=f"新事实{i:02d}",
            ),
        )
    heads = await store.list_active_chain_heads("user", "123", limit=24)
    assert len(heads) <= 24
    assert all(c.status == "active" for c in heads)
    assert all(c.supersedes for c in heads)


# ---------------------------------------------------------------------------
# R11 — evidence refs real; valid_to from successor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r11_evidence_refs_real_and_valid_to_from_successor(
    store: CardStore,
) -> None:
    hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    sh_card = await store.get_card(shanghai)
    assert sh_card is not None
    hz_card = await store.get_card(hangzhou)
    assert hz_card is not None

    obs = await store.list_observations(shanghai)
    assert obs, "supersede with provenance must create observation"

    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is not None
    kps = _result_kps(result)
    assert kps
    all_refs: list[str] = []
    for kp in kps:
        refs = _kp_fields(kp).get("evidence_refs") or []
        if isinstance(refs, (list, tuple)):
            all_refs.extend(str(r) for r in refs)
        else:
            all_refs.append(str(refs))
        # earlier.valid_to derived from successor captured_at or created_at
        earlier = _kp_fields(kp).get("earlier")
        items = earlier if isinstance(earlier, (list, tuple)) else ([earlier] if earlier else [])
        for item in items:
            if item is None:
                continue
            valid_to = (
                item.get("valid_to")
                if isinstance(item, dict)
                else getattr(item, "valid_to", None)
            )
            if valid_to:
                # Must match successor provenance timestamp (captured_at preferred).
                successor_ts = sh_card.captured_at or sh_card.created_at
                assert str(valid_to) == str(successor_ts) or successor_ts in str(valid_to)

    ref_blob = " ".join(all_refs)
    # Typed refs only: card:<id>, message:<id>, obs:<id>
    assert any(r.startswith("card:") for r in all_refs) or any(
        r.startswith("message:") for r in all_refs
    ) or any(r.startswith("obs:") for r in all_refs)
    for r in all_refs:
        assert r.startswith(("card:", "message:", "obs:")), r
        # bare ids are not allowed
        assert not r.startswith("card_"), r
    assert f"card:{shanghai}" in all_refs or f"card:{hangzhou}" in all_refs
    assert "fake_" not in ref_blob
    # Deduped
    assert len(all_refs) == len(set(all_refs))


# ---------------------------------------------------------------------------
# R12 — store exception → empty; CancelledError propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r12_store_exception_yields_empty_result(store: CardStore) -> None:
    hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    del hangzhou
    assembler = _assembler(store)

    async def boom(*_a: Any, **_k: Any) -> list[Any]:
        raise RuntimeError("simulated store failure")

    # Prefer patching chain walk; assembler must not raise ordinary Exception outward
    # as a partial DTO — it returns empty/None.
    original = store.walk_supersedes_chain
    store.walk_supersedes_chain = boom  # type: ignore[method-assign]
    try:
        result = await assembler.assemble(
            current_message="我以前住在哪里",
            rewritten_query="",
            session_id="private_123",
            user_id="123",
            group_id=None,
            active_memory_hits=[shanghai],
        )
    finally:
        store.walk_supersedes_chain = original  # type: ignore[method-assign]

    assert result is None or not _result_text(result).strip()


@pytest.mark.asyncio
async def test_r12_cancelled_error_propagates(store: CardStore) -> None:
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)

    async def cancel(*_a: Any, **_k: Any) -> list[Any]:
        raise asyncio.CancelledError()

    original = store.walk_supersedes_chain
    store.walk_supersedes_chain = cancel  # type: ignore[method-assign]
    try:
        with pytest.raises(asyncio.CancelledError):
            await assembler.assemble(
                current_message="我以前住在哪里",
                rewritten_query="",
                session_id="private_123",
                user_id="123",
                group_id=None,
                active_memory_hits=[shanghai],
            )
    finally:
        store.walk_supersedes_chain = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# R14 — Dream fact→status trusted correction: store works; temporal no-ops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r14_dream_category_correction_store_ok_temporal_noops(
    store: CardStore,
) -> None:
    old_id = await store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="123",
            content="用户住在杭州",
        ),
        source_msg_id="m_fact",
        captured_by="memo_extractor",
    )
    new_id = await store.supersede_card(
        old_id,
        NewCard(
            category="status",
            scope="user",
            scope_id="123",
            content="身份: 研究生（已更新）",
        ),
        source_msg_id="msg_category_correction",
        evidence_text="trusted category correction",
        captured_by="dream",
    )
    old = await store.get_card(old_id)
    assert old is not None and old.status == "superseded"
    active = await store.get_entity_cards("user", "123")
    assert len(active) == 1
    assert active[0].card_id == new_id
    assert active[0].category == "status"

    # Store walk may return empty for cross-category (fail-closed) OR return chain
    # but temporal assembler v1 MUST no-op / omit that KP.
    walk = await store.walk_supersedes_chain(new_id, max_depth=4)
    # Either fail-closed at store layer or non-empty for Dream visibility — both OK.
    if walk:
        assert walk[0].card_id == new_id

    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[new_id],
    )
    # Temporal v1 no-ops cross-category chains — no partial 杭州 trajectory.
    flat = _flatten_text(result)
    assert result is None or not _result_text(result).strip() or "杭州" not in flat


# ---------------------------------------------------------------------------
# Trigger lexicon smoke (closed-list intent / premise)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_lexicon_historical_and_premise_closed_list(
    store: CardStore,
) -> None:
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)

    historical = ["以前", "之前", "曾经", "原来", "当时", "过去"]
    # Concrete messages for premise patterns that need surrounding context.
    premise_msgs = [
        "还记得我住杭州吗",
        "我不是还住杭州吗",
        "明明住在杭州啊",
        "我记得我住杭州",
        "我仍然住杭州吧",
    ]

    for word in historical:
        result = await assembler.assemble(
            current_message=f"我{word}住在哪里",
            rewritten_query="",
            session_id="private_123",
            user_id="123",
            group_id=None,
            active_memory_hits=[shanghai],
        )
        assert result is not None and _result_text(result).strip(), word
        assert _result_reason(result) == "historical_intent", word

    for msg in premise_msgs:
        result = await assembler.assemble(
            current_message=msg,
            rewritten_query="",
            session_id="private_123",
            user_id="123",
            group_id=None,
            active_memory_hits=[shanghai],
        )
        assert result is not None and _result_text(result).strip(), msg
        assert _result_reason(result) == "premise_conflict", msg

    # Non-triggers
    for msg in ("我现在住在哪里", "目前住哪", "今天天气怎么样"):
        result = await assembler.assemble(
            current_message=msg,
            rewritten_query="",
            session_id="private_123",
            user_id="123",
            group_id=None,
            active_memory_hits=[shanghai],
        )
        assert result is None or not _result_text(result).strip(), msg


# ---------------------------------------------------------------------------
# Deterministic render structure (R7 DTO fields)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_dto_fields_present_no_llm_call(store: CardStore) -> None:
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    # Ensure assemble is a plain coroutine (no LLM client injected).
    sig = inspect.signature(assembler.assemble)
    assert "llm" not in sig.parameters
    assert "client" not in sig.parameters

    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is not None
    kps = _result_kps(result)
    assert kps
    for kp in kps:
        fields = _kp_fields(kp)
        assert "current" in fields or _current_content(kp)
        assert "earlier" in fields or _earlier_contents(kp)
        assert "trajectory" in fields or "earlier" in fields
        assert "evidence_refs" in fields
        assert "confidence" in fields or hasattr(kp, "confidence")
        assert _kp_reason(kp) in {"historical_intent", "premise_conflict", ""} or (
            _result_reason(result) in {"historical_intent", "premise_conflict"}
        )


# ---------------------------------------------------------------------------
# Trace is not a ContextHit / never enters ordinary pack text (R1 companion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_not_context_hit_shape(store: CardStore) -> None:
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is not None
    # Must not look like ContextHit (no type=memory_card / score fusion surface).
    assert not hasattr(result, "score") or getattr(result, "type", None) not in {
        "memory_card",
        "doc_chunk",
        "graph_fact",
    }
    assert getattr(result, "type", None) != "memory_card"
    # No graph facts in trace
    flat = _flatten_text(result)
    assert "graph_fact" not in flat
    assert "图谱" not in flat


# ---------------------------------------------------------------------------
# LLMClient wiring: PromptContext must receive current_message (R2/R4 surface)
# ---------------------------------------------------------------------------


def test_llm_client_prompt_context_passes_current_message() -> None:
    """LLMClient must supply latest human message into PromptContext.current_message.

    Source-level contract: the PromptContext(...) construction site near
    fire_on_pre_prompt must include current_message= (not conversation_text alone).
    """
    import services.llm.client as client_mod

    src = inspect.getsource(client_mod)
    # Must construct PromptContext with current_message kwarg.
    assert "PromptContext(" in src
    assert "current_message=" in src
    # The on_pre_prompt path must not only pass conversation_text.
    # Find a window around PromptContext construction used for bus fire.
    idx = src.find("fire_on_pre_prompt")
    assert idx != -1
    window = src[max(0, idx - 800) : idx + 200]
    assert "PromptContext(" in window
    assert "current_message=" in window
    # Fragile locals().get wiring is forbidden; use one explicit current-human value.
    assert 'locals().get("current_msg"' not in src
    assert "locals().get('current_msg'" not in src


def test_resolve_current_human_message_private_from_user_content() -> None:
    """Behavior: private / thinker-off path uses user_content text once."""
    from services.llm.client import resolve_current_human_message

    assert (
        resolve_current_human_message(
            "我以前住在哪里",
            is_group=False,
            timeline=None,
            group_id=None,
        )
        == "我以前住在哪里"
    )


def test_resolve_current_human_message_group_pending_fallback() -> None:
    """Behavior: empty user_content in group falls back to latest pending human msg."""
    from services.llm.client import resolve_current_human_message

    class _TL:
        def get_pending(self, _gid: str) -> list[dict[str, Any]]:
            return [
                {"content": "旧消息 以前住哪", "trigger_reason": None},
                {"content": "我不是还住杭州吗", "trigger_reason": None},
            ]

    assert (
        resolve_current_human_message(
            "",
            is_group=True,
            timeline=_TL(),
            group_id="984198159",
        )
        == "我不是还住杭州吗"
    )


# ---------------------------------------------------------------------------
# Deploy-blocker regressions (group pools / premise / render / score / refs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_pool_mode_resolves_pool_scope_not_raw_group_id(
    store: CardStore,
) -> None:
    """Pool cards live under scope_id=pool_*; raw group_id must not miss them."""
    from kernel.config import GroupMemoryConfig, MemoryModeConfig, PoolConfig

    g_old = await store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="pool_alpha",
            content="群约定聚餐在杭州",
        ),
        source_msg_id="m_pool_old",
        captured_by="memo_extractor",
    )
    g_new = await store.supersede_card(
        g_old,
        NewCard(
            category="fact",
            scope="group",
            scope_id="pool_alpha",
            content="群约定聚餐在上海",
        ),
        source_msg_id="m_pool_new",
        evidence_text="改地方了",
        captured_by="memo_extractor",
    )
    gmc = GroupMemoryConfig(
        memory=MemoryModeConfig(
            mode="pool",
            pools={
                "pool_alpha": PoolConfig(name="alpha", groups=["984198159"]),
            },
        ),
    )
    assembler = _assembler(store, group_memory_config=gmc)
    result = await assembler.assemble(
        current_message="以前群聚餐定在哪里",
        rewritten_query="",
        session_id="group_984198159",
        user_id="123",
        group_id="984198159",
        active_memory_hits=[g_new],
    )
    assert result is not None and _result_text(result).strip()
    flat = _flatten_text(result)
    assert "杭州" in flat
    assert "上海" in flat


@pytest.mark.asyncio
async def test_group_global_mode_uses_global_pool_scope(store: CardStore) -> None:
    from kernel.config import GroupMemoryConfig, MemoryModeConfig

    g_old = await store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="__global__",
            content="全局群记忆旧：聚餐杭州",
        ),
        source_msg_id="m_glob_old",
        captured_by="memo_extractor",
    )
    g_new = await store.supersede_card(
        g_old,
        NewCard(
            category="fact",
            scope="group",
            scope_id="__global__",
            content="全局群记忆新：聚餐上海",
        ),
        source_msg_id="m_glob_new",
        evidence_text="改了",
        captured_by="memo_extractor",
    )
    gmc = GroupMemoryConfig(memory=MemoryModeConfig(mode="global"))
    assembler = _assembler(store, group_memory_config=gmc)
    result = await assembler.assemble(
        current_message="以前群聚餐定在哪里",
        rewritten_query="",
        session_id="group_1",
        user_id="123",
        group_id="1",
        active_memory_hits=[g_new],
    )
    assert result is not None and _result_text(result).strip()
    assert "杭州" in _flatten_text(result)


@pytest.mark.asyncio
async def test_group_max_heads_budget_is_global_across_pools(store: CardStore) -> None:
    """max_heads is one shared budget across all resolved pool scope_ids."""
    from kernel.config import GroupMemoryConfig, MemoryModeConfig, PoolConfig

    for pool in ("pool_a", "pool_b"):
        for i in range(20):
            old = await store.add_card(
                NewCard(
                    category="fact",
                    scope="group",
                    scope_id=pool,
                    content=f"{pool}-旧{i:02d}-居住",
                ),
            )
            await store.supersede_card(
                old,
                NewCard(
                    category="fact",
                    scope="group",
                    scope_id=pool,
                    content=f"{pool}-新{i:02d}-居住",
                ),
            )
    gmc = GroupMemoryConfig(
        memory=MemoryModeConfig(
            mode="pool",
            pools={
                "pool_a": PoolConfig(name="a", groups=["g1"]),
                "pool_b": PoolConfig(name="b", groups=["g1"]),
            },
        ),
    )
    assembler = _assembler(store, max_heads=10, group_memory_config=gmc)
    # Internal head collection is exactly the configured global max_heads budget.
    heads = await assembler._collect_head_ids(
        scope="group",
        scope_ids=["pool_a", "pool_b"],
        active_memory_hits=None,
    )
    assert len(heads) == 10


@pytest.mark.asyncio
async def test_head_eligibility_does_not_let_malformed_row_starve_fresh_head(
    store: CardStore,
) -> None:
    """Malformed rows must not consume the usable head budget."""
    bad_parent, bad_head = await _hangzhou_shanghai_chain(
        store,
        category="status",
        scope_id="123",
    )
    fresh_parent, fresh_head = await _hangzhou_shanghai_chain(
        store,
        category="status",
        scope_id="123",
    )
    del bad_parent, fresh_parent

    db = store._require_db()
    await db.execute(
        "UPDATE memory_cards SET updated_at = ? WHERE card_id = ?",
        ("zzzz", bad_head),
    )
    await db.execute(
        "UPDATE memory_cards SET updated_at = ? WHERE card_id = ?",
        ("2026-07-17T00:00:00+00:00", fresh_head),
    )
    await db.commit()

    assembler = TemporalTraceAssembler(
        store,
        config=TemporalTraceConfig(max_heads=1),
        now_provider=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    heads = await assembler._collect_head_ids(
        scope="user",
        scope_ids=["123"],
        active_memory_hits=None,
    )
    assert heads == [fresh_head]


@pytest.mark.asyncio
async def test_premise_current_state_still_live_shanghai_does_not_expose_hangzhou(
    store: CardStore,
) -> None:
    """『我现在还住在上海吗』 must not open earlier 杭州 via the 还住 marker alone."""
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我现在还住在上海吗",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is None
    assert "杭州" not in _flatten_text(result)


@pytest.mark.asyncio
async def test_premise_rewritten_query_cannot_supply_earlier_only_token(
    store: CardStore,
) -> None:
    """rewritten_query may help chain match but must never complete premise evidence."""
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我还记得吗",  # premise marker, no place token
        rewritten_query="杭州 居住地 以前",  # rewrite injects earlier-only 杭州
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is None
    assert "杭州" not in _flatten_text(result)


@pytest.mark.asyncio
async def test_premise_valid_still_live_hangzhou_and_rhetorical_preserved(
    store: CardStore,
) -> None:
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store)
    for msg in ("我不是还住杭州吗", "我仍然住杭州吧"):
        result = await assembler.assemble(
            current_message=msg,
            rewritten_query="",
            session_id="private_123",
            user_id="123",
            group_id=None,
            active_memory_hits=[shanghai],
        )
        assert result is not None and _result_text(result).strip(), msg
        assert _result_reason(result) == "premise_conflict", msg
        assert "杭州" in _flatten_text(result), msg


@pytest.mark.asyncio
async def test_score_chain_no_hardcoded_topic_shape_fallback(store: CardStore) -> None:
    """Behavioral: lexical match only — topic-shaped messages without overlap score 0."""
    from services.memory import temporal_trace as tt

    old = await store.add_card(
        NewCard(category="fact", scope="user", scope_id="123", content="用户养了鱼"),
    )
    new = await store.supersede_card(
        old,
        NewCard(category="fact", scope="user", scope_id="123", content="用户养了鸟"),
    )
    chain = await store.walk_supersedes_chain(new, max_depth=4)
    # Completely unrelated query → 0 (no free pass for 养/住-style shapes).
    assert tt._score_chain(chain, set(), "完全无关的问题xyz", "") == 0.0
    # Topic-shaped strings that do not appear in chain content still score 0.
    for msg in (
        "我以前喜欢什么",
        "以前爱好是什么",
        "以前聚餐定在哪里",
        "还住在哪里",
    ):
        assert tt._score_chain(chain, set(), msg, "") == 0.0, msg
    # Real lexical overlap still scores positively.
    assert tt._score_chain(chain, set(), "以前养了什么", "") > 0.0


@pytest.mark.asyncio
async def test_render_fail_closed_below_100_and_coherent_at_bounds(
    store: CardStore,
) -> None:
    """max_chars < 100 → empty; 100/600 → 当前: + complete 说明, within budget."""
    from services.memory.temporal_trace import (
        _GUIDANCE_LINE,
        KnowledgePointTrace,
        TraceNode,
        _render_text,
    )

    del store  # pure render contract
    long_earlier = "更早内容" + ("甲" * 200)
    long_current = "当前内容" + ("乙" * 200)
    kp = KnowledgePointTrace(
        current=TraceNode(
            card_id="card_cur",
            content=long_current,
            status="active",
            confidence=0.9,
            source_message_id="m_cur",
        ),
        earlier=[
            TraceNode(
                card_id="card_old",
                content=long_earlier,
                status="superseded",
                confidence=0.7,
                source_message_id="m_old",
            ),
        ],
        trajectory=f"{long_earlier[:40]} → {long_current[:40]}",
        evidence_refs=["card:card_cur", "message:m_cur", "card:card_old", "message:m_old"],
        reason="historical_intent",
        confidence=0.7,
    )
    for tiny in (1, 2, 10, 50, 99):
        text = _render_text([kp], max_chars=tiny)
        assert text == "", f"budget {tiny} must fail closed, got {text!r}"

    for budget in (100, 600):
        text = _render_text([kp], max_chars=budget)
        assert text.strip(), budget
        assert len(text) <= budget
        assert text.lstrip().startswith("当前:")
        assert _GUIDANCE_LINE in text.splitlines()
        # Never mid-cut guidance fragments.
        for line in text.splitlines():
            if line.startswith("说明"):
                assert line == _GUIDANCE_LINE, line
            if line.startswith("依据:"):
                body = line[len("依据:") :].strip()
                if body:
                    for tok in body.split():
                        assert tok.startswith(("card:", "message:", "obs:")), tok

    # Hard max 600 even if caller passes higher.
    text_hi = _render_text([kp], max_chars=9999)
    assert len(text_hi) <= 600
    assert text_hi.lstrip().startswith("当前:")
    assert any(ln == _GUIDANCE_LINE for ln in text_hi.splitlines())


@pytest.mark.asyncio
async def test_assembler_max_chars_below_100_returns_none(store: CardStore) -> None:
    """Programmatic TemporalTraceConfig with max_chars < 100 must not emit partial trace."""
    _hz, shanghai = await _hangzhou_shanghai_chain(store)
    assembler = _assembler(store, max_chars=1)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is None


@pytest.mark.asyncio
async def test_group_memory_config_in_place_mutation_visible_to_assembler(
    store: CardStore,
) -> None:
    """Admin-style in-place mutation of shared GroupMemoryConfig is visible without rebuild.

    Existing GREEN if TemporalTraceAssembler holds a reference to the same object.
    """
    from kernel.config import GroupMemoryConfig, MemoryModeConfig, PoolConfig

    g_old = await store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="pool_mut",
            content="群约定聚餐在杭州",
        ),
        source_msg_id="m_mut_old",
        captured_by="memo_extractor",
    )
    g_new = await store.supersede_card(
        g_old,
        NewCard(
            category="fact",
            scope="group",
            scope_id="pool_mut",
            content="群约定聚餐在上海",
        ),
        source_msg_id="m_mut_new",
        evidence_text="改了",
        captured_by="memo_extractor",
    )
    # Start with empty pool map (group resolves to nothing useful / raw id only).
    gmc = GroupMemoryConfig(
        memory=MemoryModeConfig(
            mode="pool",
            pools={},
        ),
    )
    assembler = _assembler(store, group_memory_config=gmc)
    # Admin-style mutation: keep the shared GroupMemoryConfig object but replace
    # its ``memory`` field with the newly validated value.
    gmc.memory = MemoryModeConfig(
        mode="pool",
        pools={"pool_mut": PoolConfig(name="mut", groups=["984198159"])},
    )
    result = await assembler.assemble(
        current_message="以前群聚餐定在哪里",
        rewritten_query="",
        session_id="group_984198159",
        user_id="123",
        group_id="984198159",
        active_memory_hits=[g_new],
    )
    assert result is not None and _result_text(result).strip()
    flat = _flatten_text(result)
    assert "杭州" in flat
    assert "上海" in flat


@pytest.mark.asyncio
async def test_evidence_refs_typed_and_observation_reads_bounded(
    store: CardStore,
) -> None:
    hangzhou, shanghai = await _hangzhou_shanghai_chain(store)
    # Create many observations on head to ensure bound.
    for i in range(12):
        await store.reinforce(
            shanghai,
            boost=0.0,
            source_message_id=f"m_obs_{i}",
            evidence_text=f"obs{i}",
            decision="reinforce",
            captured_by="test",
        )
    assembler = _assembler(store)
    result = await assembler.assemble(
        current_message="我以前住在哪里",
        rewritten_query="",
        session_id="private_123",
        user_id="123",
        group_id=None,
        active_memory_hits=[shanghai],
    )
    assert result is not None
    for kp in _result_kps(result):
        refs = list(_kp_fields(kp).get("evidence_refs") or [])
        assert refs
        assert all(str(r).startswith(("card:", "message:", "obs:")) for r in refs)
        assert f"card:{shanghai}" in refs or f"card:{hangzhou}" in refs
        # Bounded: not unbounded obs dump
        obs_refs = [r for r in refs if str(r).startswith("obs:")]
        assert len(obs_refs) <= 4
        assert len(refs) <= 12


@pytest.mark.asyncio
async def test_real_context_plugin_on_startup_cardstore_pool_integration(
    tmp_path,
) -> None:
    """True on_startup path: ContextPlugin builds assembler; pool trace + active pack."""
    from types import SimpleNamespace

    from kernel.config import GroupMemoryConfig, MemoryModeConfig, PoolConfig
    from plugins.context.plugin import ContextPlugin
    from services.context.types import ContextHit, ContextPack

    db_path = str(tmp_path / "integration_trace.db")
    store = CardStore(db_path=db_path)
    await store.init()
    try:
        g_old = await store.add_card(
            NewCard(
                category="fact",
                scope="group",
                scope_id="pool_int",
                content="群约定聚餐在杭州",
            ),
            source_msg_id="m_int_old",
            captured_by="memo_extractor",
        )
        g_new = await store.supersede_card(
            g_old,
            NewCard(
                category="fact",
                scope="group",
                scope_id="pool_int",
                content="群约定聚餐在上海",
            ),
            source_msg_id="m_int_new",
            evidence_text="改了",
            captured_by="memo_extractor",
        )
        gmc = GroupMemoryConfig(
            memory=MemoryModeConfig(
                mode="pool",
                pools={"pool_int": PoolConfig(name="int", groups=["984198159"])},
            ),
        )
        ctx = SimpleNamespace(
            context_service=object(),
            bus=None,
            card_store=store,
            knowledge_base=None,
            knowledge_graph=None,
            group_memory_config=gmc,
            memo=None,
        )
        plugin = ContextPlugin()
        # Do not assign _temporal_trace_assembler manually — on_startup must build it.
        await plugin.on_startup(ctx)  # type: ignore[arg-type]
        assert plugin._temporal_trace_assembler is not None

        class _Svc:
            async def build_prompt_context(self, query: str, **kwargs: Any) -> ContextPack:
                del query, kwargs
                return ContextPack(
                    text="【记忆卡片】\n- [群] 统一记忆资料",
                    hits=[
                        ContextHit(
                            id=g_new,
                            type="memory_card",
                            content="群约定聚餐在上海",
                            score=0.9,
                            source="test",
                        ),
                    ],
                )

        plugin._service = _Svc()
        plugin._graph_auto_extract = False
        prompt_ctx = PromptContext(
            session_id="group_984198159",
            group_id="984198159",
            user_id="123",
            identity=Identity(id="bot", name="Bot", personality="test"),
            conversation_text="以前群聚餐定在哪里",
            current_message="以前群聚餐定在哪里",
            retrieve_mode="hybrid",
        )
        await plugin.on_pre_prompt(prompt_ctx)
        labels = [b.label for b in prompt_ctx.blocks]
        assert "上下文资料" in labels
        pack = [b for b in prompt_ctx.blocks if b.label == "上下文资料"]
        assert pack and "杭州" not in pack[0].text  # ordinary pack remains active-only
        trace = [b for b in prompt_ctx.blocks if b.label == "记忆时间轨迹"]
        assert len(trace) == 1
        assert "杭州" in trace[0].text
        assert "上海" in trace[0].text
        assert any(
            marker in trace[0].text
            for marker in ("以当前为准", "当前为准")
        )
        assert trace[0].text.lstrip().startswith("当前:")
    finally:
        await store.close()
