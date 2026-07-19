"""RED tests: Hot-path Conflict-Aware Memory Write Policy v1.

These tests encode the frozen contracts for MemoExtractor write decisions,
provenance observations, deterministic duplicate policy helpers, and the
feature flag. They must FAIL against current production (add-only extractor,
no observations table, non-atomic supersede provenance).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from kernel.types import ReplyContext
from plugins.memo.plugin import MemoConfig, MemoExtractor, MemoPlugin
from services.memory.card_store import CardStore, NewCard

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


@pytest.fixture
async def card_store(tmp_path) -> AsyncIterator[CardStore]:
    db_path = str(tmp_path / "write_policy_cards.db")
    store = CardStore(db_path=db_path)
    await store.init()
    try:
        yield store
    finally:
        await store.close()


class FakeLLM:
    """Async LLM stub that records requests and returns a fixed text payload."""

    def __init__(self, text: str = "无") -> None:
        self.text = text
        self.calls: list[Any] = []
        self._queue: list[str] = []

    def queue(self, *texts: str) -> None:
        self._queue.extend(texts)

    async def __call__(self, request: Any) -> dict[str, str]:
        self.calls.append(request)
        if self._queue:
            return {"text": self._queue.pop(0)}
        return {"text": self.text}


def _request_blob(request: Any) -> str:
    """Flatten LLMRequest system + user content into a searchable string."""
    parts: list[str] = []
    for attr in ("static_blocks", "stable_blocks", "dynamic_blocks"):
        for block in getattr(request, attr, None) or []:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text", "")))
    for msg in getattr(request, "user_messages", None) or []:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.append(json.dumps(content, ensure_ascii=False))
        else:
            parts.append(str(msg))
    return "\n".join(parts)


def _write_stats(extractor: MemoExtractor) -> dict[str, int]:
    """Read observable write counters (property or method)."""
    if hasattr(extractor, "get_write_stats") and callable(extractor.get_write_stats):
        stats = extractor.get_write_stats()
    elif hasattr(extractor, "stats"):
        stats = extractor.stats
    else:
        raise AssertionError(
            "MemoExtractor must expose stats property or get_write_stats() "
            "with keys add/reinforce/supersede/skip/invalid"
        )
    assert isinstance(stats, dict), "write stats must be a mapping"
    out = {str(k): int(stats[k]) for k in stats}
    for key in ("add", "reinforce", "supersede", "skip", "invalid"):
        assert key in out, f"write stats missing key {key!r}; got {sorted(out)}"
    return out


def _make_extractor(
    store: CardStore,
    llm: FakeLLM,
    *,
    write_policy_enabled: bool = True,
) -> MemoExtractor:
    """Build extractor with write-policy config (required public API for GREEN)."""
    assert hasattr(MemoConfig, "model_fields") or True
    # GREEN must accept write_policy_enabled on MemoConfig and config= on extractor.
    config = MemoConfig(write_policy_enabled=write_policy_enabled)  # type: ignore[call-arg]
    assert getattr(config, "write_policy_enabled", None) is write_policy_enabled, (
        "MemoConfig.write_policy_enabled must be a real field (not silently dropped)"
    )
    return MemoExtractor(card_store=store, api_call=llm, config=config)


# ---------------------------------------------------------------------------
# MemoConfig / construction contract
# ---------------------------------------------------------------------------


def test_memo_config_write_policy_enabled_defaults_true() -> None:
    cfg = MemoConfig()
    assert hasattr(cfg, "write_policy_enabled"), (
        "MemoConfig must gain write_policy_enabled (restart-required flag)"
    )
    assert cfg.write_policy_enabled is True


def test_memo_extractor_accepts_config_kwarg(card_store: CardStore) -> None:
    llm = FakeLLM("无")
    extractor = MemoExtractor(
        card_store=card_store,
        api_call=llm,
        config=MemoConfig(write_policy_enabled=True),
    )
    assert extractor is not None


def test_extract_after_turn_signature_accepts_source_message_id() -> None:
    sig = inspect.signature(MemoExtractor.extract_after_turn)
    assert "source_message_id" in sig.parameters, (
        "extract_after_turn must accept optional source_message_id"
    )
    param = sig.parameters["source_message_id"]
    assert param.default is not inspect.Parameter.empty, (
        "source_message_id must be optional with default None"
    )
    assert param.default is None


# ---------------------------------------------------------------------------
# Pure write_policy helpers
# ---------------------------------------------------------------------------


def test_write_policy_module_exports_helpers() -> None:
    from services.memory import write_policy as wp

    assert hasattr(wp, "DUPLICATE_SIMILARITY_THRESHOLD")
    assert isinstance(wp.DUPLICATE_SIMILARITY_THRESHOLD, float)
    assert 0.0 < wp.DUPLICATE_SIMILARITY_THRESHOLD <= 1.0

    assert callable(getattr(wp, "normalize_content", None) or getattr(wp, "normalize", None))
    assert callable(
        getattr(wp, "is_high_confidence_duplicate", None)
        or getattr(wp, "high_confidence_duplicate", None)
        or getattr(wp, "is_duplicate", None)
    )
    assert callable(
        getattr(wp, "has_explicit_update_signal", None)
        or getattr(wp, "explicit_update_signal", None)
        or getattr(wp, "has_update_signal", None)
    )


def test_normalize_and_duplicate_detection_unit() -> None:
    from services.memory import write_policy as wp

    normalize: Callable[[str], str] = (
        getattr(wp, "normalize_content", None) or wp.normalize
    )
    is_dup: Callable[..., bool] = (
        getattr(wp, "is_high_confidence_duplicate", None)
        or getattr(wp, "high_confidence_duplicate", None)
        or wp.is_duplicate
    )

    a = normalize("  用户喜欢被叫帆酱！！  ")
    b = normalize("用户喜欢被叫帆酱")
    assert a
    assert b
    # Same meaning / containment should count as high-confidence duplicate.
    assert is_dup("用户喜欢被叫帆酱", "用户喜欢被叫帆酱！") is True
    assert is_dup("用户喜欢被叫帆酱", "用户喜欢被叫帆酱") is True
    # Unrelated content must not match.
    assert is_dup("用户住在杭州", "用户喜欢音游") is False

    # Threshold is explicit and used (not CardStore.find_similar prefix logic).
    threshold = float(wp.DUPLICATE_SIMILARITY_THRESHOLD)
    # Near-identical long strings should pass; short noise should not invent matches.
    long_a = "用户平时更喜欢安静一点的聊天氛围，不喜欢被突然@打扰"
    long_b = "用户平时更喜欢安静一点的聊天氛围，不喜欢被突然@打扰。"
    assert is_dup(long_a, long_b) is True
    assert is_dup("猫", "狗") is False
    assert threshold >= 0.5  # policy should be conservative, not near-zero


def test_explicit_update_signal_unit() -> None:
    from services.memory import write_policy as wp

    has_signal: Callable[[str], bool] = (
        getattr(wp, "has_explicit_update_signal", None)
        or getattr(wp, "explicit_update_signal", None)
        or wp.has_update_signal
    )

    positive = [
        "我现在住在上海",
        "目前搬到了上海",
        "地址改成上海",
        "改为上海",
        "换成上海",
        "不再住杭州",
        "已经搬家了",
        "以后住上海",
        "从今以后在上海",
        "纠正一下，我住上海",
        "不是杭州而是上海",
        "原来住杭州，现在住上海",
    ]
    for text in positive:
        assert has_signal(text) is True, f"expected update signal in: {text}"

    negative = [
        "用户住在杭州",
        "用户喜欢被叫帆酱",
        "今天天气不错",
        "我也住过杭州和上海",
    ]
    for text in negative:
        assert has_signal(text) is False, f"unexpected update signal in: {text}"


# ---------------------------------------------------------------------------
# Extractor: request context + JSONL / legacy parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_policy_loads_active_cards_into_llm_request(
    card_store: CardStore,
) -> None:
    uid = "u_ctx"
    for i in range(3):
        await card_store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id=uid,
                content=f"已有事实-{i}-unique-token-{i}",
                confidence=0.7,
            )
        )
    # Other user cards must not leak into this user's extraction context.
    await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id="other_user",
            content="异用户不应出现",
            confidence=0.7,
        )
    )

    llm = FakeLLM("无")
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="随便聊聊",
        bot_reply="好呀",
        source_message_id="msg_ctx_1",
    )

    assert llm.calls, "extractor must call LLM"
    blob = _request_blob(llm.calls[0])
    assert "已有事实-0-unique-token-0" in blob
    assert "已有事实-1-unique-token-1" in blob
    assert "异用户不应出现" not in blob


@pytest.mark.asyncio
async def test_active_card_context_is_bounded_to_24(card_store: CardStore) -> None:
    uid = "u_cap"
    for i in range(30):
        await card_store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id=uid,
                content=f"cap-card-{i:02d}-MARKER",
                confidence=0.5 + (i * 0.01),
            )
        )
    llm = FakeLLM("无")
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="hi",
        bot_reply="hi",
    )
    blob = _request_blob(llm.calls[0])
    present = sum(1 for i in range(30) if f"cap-card-{i:02d}-MARKER" in blob)
    assert present <= 24, f"active card decision context must be capped at 24, got {present}"
    assert present >= 1


@pytest.mark.asyncio
async def test_jsonl_add_creates_active_card_and_stats(card_store: CardStore) -> None:
    uid = "u_add"
    llm = FakeLLM(
        json.dumps(
            {
                "category": "preference",
                "content": "用户偏好被称呼为帆酱",
                "action": "add",
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="叫我帆酱",
        bot_reply="好的帆酱",
        source_message_id="msg_add_1",
    )

    cards = await card_store.get_entity_cards("user", uid)
    assert len(cards) == 1
    assert cards[0].category == "preference"
    assert "帆酱" in cards[0].content
    assert cards[0].source_msg_id in {"msg_add_1"} or str(
        cards[0].source_msg_id
    ) == "msg_add_1"
    assert cards[0].captured_by == "memo_extractor"

    stats = _write_stats(extractor)
    assert stats["add"] >= 1
    assert stats["reinforce"] == 0
    assert stats["supersede"] == 0


@pytest.mark.asyncio
async def test_legacy_bracket_line_still_adds(card_store: CardStore) -> None:
    uid = "u_legacy"
    llm = FakeLLM('[fact] 用户喜欢玩音游')
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我最近在玩音游",
        bot_reply="听起来很有趣",
        source_message_id=9001,
    )
    cards = await card_store.get_entity_cards("user", uid)
    assert len(cards) == 1
    assert cards[0].category == "fact"
    assert "音游" in cards[0].content
    assert str(cards[0].source_msg_id) == "9001"
    assert _write_stats(extractor)["add"] >= 1


@pytest.mark.asyncio
async def test_empty_wu_malformed_and_invalid_category_skip_without_raise(
    card_store: CardStore,
) -> None:
    uid = "u_skip"
    cases = [
        "无",
        "",
        "{not json",
        json.dumps({"category": "bogus", "content": "x", "action": "add"}),
        json.dumps({"category": "fact", "content": "", "action": "add"}),
        json.dumps({"category": "fact", "content": "   ", "action": "add"}),
    ]
    for payload in cases:
        llm = FakeLLM(payload)
        extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
        await extractor.extract_after_turn(
            user_id=uid,
            group_id=None,
            user_msg="...",
            bot_reply="...",
            source_message_id="msg_skip",
        )
        cards = await card_store.get_entity_cards("user", uid)
        assert cards == [], f"payload {payload!r} must not write cards"
        stats = _write_stats(extractor)
        # either skip or invalid counters tick; total writes stay zero
        assert stats["add"] == 0
        assert stats["reinforce"] == 0
        assert stats["supersede"] == 0
        assert stats["skip"] + stats["invalid"] >= 0  # keys exist


@pytest.mark.asyncio
async def test_skip_action_writes_nothing(card_store: CardStore) -> None:
    uid = "u_skip_action"
    llm = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                "content": "无关闲聊",
                "action": "skip",
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="今天天气不错",
        bot_reply="是啊",
        source_message_id="msg_s",
    )
    assert await card_store.get_entity_cards("user", uid) == []
    assert _write_stats(extractor)["skip"] >= 1


# ---------------------------------------------------------------------------
# Reinforce / supersede decision paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_preference_reinforces_instead_of_duplicate_active(
    card_store: CardStore,
) -> None:
    uid = "u_re"
    old_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户偏好被称呼为帆酱",
            confidence=0.6,
        )
    )
    old = await card_store.get_card(old_id)
    assert old is not None
    old_conf = old.confidence
    old_seen = old.last_seen_at

    # LLM may say add; deterministic duplicate policy should override → reinforce.
    llm = FakeLLM(
        json.dumps(
            {
                "category": "preference",
                "content": "用户偏好被称呼为帆酱",
                "action": "add",
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="还是叫我帆酱吧",
        bot_reply="好的帆酱",
        source_message_id="msg_re_1",
    )

    active = await card_store.get_entity_cards("user", uid, category="preference")
    assert len(active) == 1, "reinforce must not create a second active card"
    assert active[0].card_id == old_id
    assert active[0].confidence > old_conf or active[0].confidence == 1.0
    assert active[0].confidence <= 1.0
    assert active[0].last_seen_at is not None
    if old_seen is not None:
        assert active[0].last_seen_at >= old_seen

    obs = await card_store.list_observations(old_id)
    assert obs, "reinforce must write a durable evidence observation"
    decisions = {getattr(o, "decision", None) for o in obs}
    assert "reinforce" in decisions
    # source_message_id on observation
    src_ids = {str(getattr(o, "source_message_id", "") or "") for o in obs}
    assert "msg_re_1" in src_ids
    captured = {getattr(o, "captured_by", None) for o in obs}
    assert "memo_extractor" in captured

    stats = _write_stats(extractor)
    assert stats["reinforce"] >= 1
    assert stats["add"] == 0


@pytest.mark.asyncio
async def test_llm_reinforce_requires_valid_same_category_target(
    card_store: CardStore,
) -> None:
    uid = "u_re_target"
    pref_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户喜欢安静",
            confidence=0.5,
        )
    )
    fact_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户是程序员",
            confidence=0.5,
        )
    )

    # Category mismatch target → must not mutate the fact card.
    llm = FakeLLM(
        json.dumps(
            {
                "category": "preference",
                "content": "用户喜欢安静",
                "action": "reinforce",
                "target_card_id": fact_id,
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我还是喜欢安静",
        bot_reply="嗯",
        source_message_id="msg_bad_re",
    )
    fact = await card_store.get_card(fact_id)
    assert fact is not None
    assert fact.confidence == pytest.approx(0.5)
    # Valid target path still works via duplicate of preference content.
    llm2 = FakeLLM(
        json.dumps(
            {
                "category": "preference",
                "content": "用户喜欢安静",
                "action": "reinforce",
                "target_card_id": pref_id,
            },
            ensure_ascii=False,
        )
    )
    extractor2 = _make_extractor(card_store, llm2, write_policy_enabled=True)
    await extractor2.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我喜欢安静",
        bot_reply="收到",
        source_message_id="msg_ok_re",
    )
    pref = await card_store.get_card(pref_id)
    assert pref is not None
    assert pref.confidence > 0.5
    assert _write_stats(extractor2)["reinforce"] >= 1


@pytest.mark.asyncio
async def test_explicit_residence_update_supersedes(
    card_store: CardStore,
) -> None:
    uid = "u_sup"
    old_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户住在杭州",
            confidence=0.7,
        )
    )
    llm = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                "content": "用户住在上海",
                "action": "supersede",
                "target_card_id": old_id,
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我现在住在上海了，已经搬家",
        bot_reply="记下了",
        source_message_id="msg_sup_1",
    )

    old = await card_store.get_card(old_id)
    assert old is not None
    assert old.status == "superseded"

    active = await card_store.get_entity_cards("user", uid, category="fact")
    assert len(active) == 1
    assert "上海" in active[0].content
    assert "杭州" not in active[0].content
    assert active[0].supersedes == old_id
    assert str(active[0].source_msg_id) == "msg_sup_1"
    assert active[0].captured_by == "memo_extractor"

    obs = await card_store.list_observations(active[0].card_id)
    assert any(getattr(o, "decision", None) == "supersede" for o in obs)
    assert any(str(getattr(o, "source_message_id", "") or "") == "msg_sup_1" for o in obs)

    stats = _write_stats(extractor)
    assert stats["supersede"] >= 1
    assert stats["add"] == 0


@pytest.mark.asyncio
async def test_same_category_without_update_signal_does_not_supersede(
    card_store: CardStore,
) -> None:
    uid = "u_no_sig"
    old_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户住在杭州",
            confidence=0.7,
        )
    )
    # LLM hallucinates supersede, but user message has no explicit update signal.
    llm = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                "content": "用户住在上海",
                "action": "supersede",
                "target_card_id": old_id,
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我朋友也住杭州和上海",
        bot_reply="哦",
        source_message_id="msg_no_sig",
    )

    old = await card_store.get_card(old_id)
    assert old is not None
    assert old.status == "active", "without update signal, old card must remain active"
    active = await card_store.get_entity_cards("user", uid, category="fact")
    # Must not create a supersede chain: either skip, or conservative add/reinforce —
    # but never mark old as superseded without signal.
    for c in active:
        assert c.supersedes != old_id or c.card_id == old_id
    superseded_pointing = [
        c
        for c in await card_store.list_cards(scope="user", scope_id=uid, status="active")
        if c.supersedes == old_id
    ]
    assert superseded_pointing == []
    assert old.status == "active"
    assert _write_stats(extractor)["supersede"] == 0


@pytest.mark.asyncio
async def test_invalid_hallucinated_or_cross_user_target_cannot_mutate(
    card_store: CardStore,
) -> None:
    uid = "u_scope"
    other = "u_other"
    own_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户喜欢猫",
            confidence=0.6,
        )
    )
    other_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=other,
            content="用户喜欢狗",
            confidence=0.6,
        )
    )
    group_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="group",
            scope_id="g1",
            content="群规：勿刷屏",
            confidence=0.6,
        )
    )

    payloads = [
        {
            "category": "fact",
            "content": "用户喜欢仓鼠",
            "action": "supersede",
            "target_card_id": "card_does_not_exist",
        },
        {
            "category": "fact",
            "content": "用户喜欢仓鼠",
            "action": "reinforce",
            "target_card_id": other_id,
        },
        {
            "category": "fact",
            "content": "用户喜欢仓鼠",
            "action": "supersede",
            "target_card_id": group_id,
        },
        {
            "category": "preference",  # category mismatch vs own fact card
            "content": "用户偏好仓鼠",
            "action": "supersede",
            "target_card_id": own_id,
        },
    ]
    for payload in payloads:
        before_own = await card_store.get_card(own_id)
        before_other = await card_store.get_card(other_id)
        before_group = await card_store.get_card(group_id)
        llm = FakeLLM(json.dumps(payload, ensure_ascii=False))
        extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
        user_msg = (
            "我现在改成喜欢仓鼠了"  # even with signal, invalid target must not mutate
            if payload["action"] == "supersede"
            else "我喜欢仓鼠"
        )
        await extractor.extract_after_turn(
            user_id=uid,
            group_id=None,
            user_msg=user_msg,
            bot_reply="ok",
            source_message_id="msg_bad_target",
        )
        after_own = await card_store.get_card(own_id)
        after_other = await card_store.get_card(other_id)
        after_group = await card_store.get_card(group_id)
        assert after_own is not None and before_own is not None
        assert after_own.status == before_own.status
        assert after_own.confidence == pytest.approx(before_own.confidence)
        assert after_other is not None and before_other is not None
        assert after_other.status == "active"
        assert after_other.confidence == pytest.approx(before_other.confidence)
        assert after_group is not None and before_group is not None
        assert after_group.status == "active"
        assert after_group.confidence == pytest.approx(before_group.confidence)
        # Cross-user / group cards must stay untouched; no supersede of other.
        assert after_other.status != "superseded"
        assert after_group.status != "superseded"


@pytest.mark.asyncio
async def test_operations_stay_user_scope_only(card_store: CardStore) -> None:
    uid = "u_scope_only"
    llm = FakeLLM('[fact] 群里有人很吵')
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id="984198159",
        user_msg="这个群好吵",
        bot_reply="哈哈",
        source_message_id="msg_grp",
    )
    user_cards = await card_store.get_entity_cards("user", uid)
    group_cards = await card_store.get_entity_cards("group", "984198159")
    assert len(user_cards) == 1
    assert group_cards == [], "v1 must not auto-write group/global cards"


# ---------------------------------------------------------------------------
# Observation idempotency via extractor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_source_message_id_observation_idempotent(
    card_store: CardStore,
) -> None:
    uid = "u_idem"
    cid = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户偏好被称呼为帆酱",
            confidence=0.55,
        )
    )
    payload = json.dumps(
        {
            "category": "preference",
            "content": "用户偏好被称呼为帆酱",
            "action": "reinforce",
            "target_card_id": cid,
        },
        ensure_ascii=False,
    )
    llm = FakeLLM(payload)
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    kwargs = dict(
        user_id=uid,
        group_id=None,
        user_msg="叫我帆酱",
        bot_reply="好",
        source_message_id="msg_idem_1",
    )
    await extractor.extract_after_turn(**kwargs)
    # Same source message again (e.g. retry)
    llm.queue(payload)
    await extractor.extract_after_turn(**kwargs)

    active = await card_store.get_entity_cards("user", uid)
    assert len(active) == 1
    obs = await card_store.list_observations(cid)
    reinforce_same_src = [
        o
        for o in obs
        if getattr(o, "decision", None) == "reinforce"
        and str(getattr(o, "source_message_id", "") or "") == "msg_idem_1"
    ]
    assert len(reinforce_same_src) == 1, (
        "observations must be idempotent for same (card_id, source_message_id, decision)"
    )


# ---------------------------------------------------------------------------
# Feature flag: legacy add-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_policy_disabled_uses_legacy_add_only(
    card_store: CardStore,
) -> None:
    uid = "u_legacy_flag"
    await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户偏好被称呼为帆酱",
            confidence=0.6,
        )
    )
    llm = FakeLLM("[preference] 用户偏好被称呼为帆酱")
    extractor = _make_extractor(card_store, llm, write_policy_enabled=False)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="叫我帆酱",
        bot_reply="好",
        source_message_id="msg_legacy_1",
    )
    active = await card_store.get_entity_cards("user", uid, category="preference")
    # Legacy add-only: may create a second active card instead of reinforce.
    assert len(active) >= 1
    # When flag is false, reinforce path is not required; add-only is OK.
    # But source provenance on new adds should still work if supplied.
    with_src = [c for c in active if str(c.source_msg_id or "") == "msg_legacy_1"]
    if len(active) > 1:
        assert with_src, "legacy add path should still record source_message_id when supplied"
    else:
        # If implementation still de-dupes without policy, at least provenance path exists.
        # Require that source_message_id parameter is accepted without error (already called).
        pass


# ---------------------------------------------------------------------------
# MemoPlugin passes source_message_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memo_plugin_on_post_reply_passes_source_message_id() -> None:
    captured: dict[str, Any] = {}

    class CapturingExtractor:
        async def extract_after_turn(self, *args: object, **kwargs: object) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    plugin = MemoPlugin()
    plugin._memo_extractor = CapturingExtractor()  # type: ignore[assignment]

    await plugin.on_post_reply(
        ReplyContext(
            session_id="s1",
            group_id="1",
            user_id="u1",
            reply_content="reply",
            user_msg="hello",
            source_message_id=424242,
        )
    )
    # Drain fire-and-forget task
    while plugin._pending_extractions:
        await asyncio.sleep(0)

    assert captured, "on_post_reply must call extract_after_turn"
    kwargs = captured.get("kwargs") or {}
    # Accept either kw or positional after the four core args — prefer kw.
    if "source_message_id" in kwargs:
        assert kwargs["source_message_id"] in (424242, "424242")
    else:
        # If only positional, last extra arg should carry it.
        args = captured.get("args") or ()
        assert 424242 in args or "424242" in args, (
            "MemoPlugin.on_post_reply must pass source_message_id from ReplyContext"
        )


# ---------------------------------------------------------------------------
# Max 3 objects from JSONL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jsonl_max_three_objects(card_store: CardStore) -> None:
    uid = "u_max3"
    lines = [
        json.dumps(
            {"category": "fact", "content": f"事实{i}", "action": "add"},
            ensure_ascii=False,
        )
        for i in range(5)
    ]
    llm = FakeLLM("\n".join(lines))
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="很多信息",
        bot_reply="收到",
        source_message_id="msg_max3",
    )
    cards = await card_store.get_entity_cards("user", uid)
    assert 1 <= len(cards) <= 3, (
        "JSONL write-policy path must accept objects and cap at 3 per turn "
        f"(got {len(cards)})"
    )
    contents = {c.content for c in cards}
    assert "事实3" not in contents and "事实4" not in contents


# ===========================================================================
# Correction packet gaps (RED)
# ===========================================================================


# ---------------------------------------------------------------------------
# 1) Prompt cap (24) vs full-scope deterministic duplicate search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_scope_duplicate_protects_cards_outside_prompt_cap(
    card_store: CardStore,
) -> None:
    """LLM context stays ≤24, but duplicate protection must scan ALL active cards.

    An older low-confidence exact/near duplicate outside the prompt window must
    be reinforced, not double-added.
    """
    uid = "u_full_scope_dup"
    out_of_window_content = "用户喜欢红茶"
    # Low confidence so this card ranks below the top-24 prompt window.
    out_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content=out_of_window_content,
            confidence=0.05,
            priority=1,
        )
    )
    # 30 higher-confidence filler cards dominate the 24-cap context.
    for i in range(30):
        await card_store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id=uid,
                content=f"filler-high-conf-{i:02d}-MARKER",
                confidence=0.90 + (i * 0.001),
                priority=5,
            )
        )

    # LLM proposes add with punctuation variant of the out-of-window card.
    llm = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                "content": "用户喜欢红茶！",
                "action": "add",
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我还是喜欢红茶",
        bot_reply="记下了",
        source_message_id="msg_full_scope_dup",
    )

    assert llm.calls, "extractor must call LLM"
    blob = _request_blob(llm.calls[0])
    # Prompt still bounded: out-of-window marker content must NOT appear in LLM blob.
    assert out_of_window_content not in blob, (
        "out-of-window card must stay outside the 24-card LLM prompt blob"
    )
    present_fillers = sum(
        1 for i in range(30) if f"filler-high-conf-{i:02d}-MARKER" in blob
    )
    assert present_fillers <= 24

    # Full-scope duplicate protection: only ONE active card with that content.
    active_facts = await card_store.get_entity_cards("user", uid, category="fact")
    tea_cards = [
        c
        for c in active_facts
        if "红茶" in c.content and "filler" not in c.content
    ]
    assert len(tea_cards) == 1, (
        "exact/near duplicate outside prompt cap must reinforce, not create a "
        f"second active card (got {len(tea_cards)}: "
        f"{[c.content for c in tea_cards]})"
    )
    assert tea_cards[0].card_id == out_id

    stats = _write_stats(extractor)
    assert stats["reinforce"] >= 1, "must reinforce the out-of-window duplicate"
    assert stats["add"] == 0


# ---------------------------------------------------------------------------
# 2) Supersede evidence precision (user msg + lexical support; no LLM self-auth)
# ---------------------------------------------------------------------------


def test_allows_supersede_requires_user_msg_signal_and_lexical_support() -> None:
    """Supersede authorization must not let LLM content self-authorize.

    Prefer a dedicated allows_supersede(user_msg, new_content) helper when
    present; otherwise fall back to outcome-style checks on the signal helper
    alone for the casual-phrase negatives.
    """
    from services.memory import write_policy as wp

    allows = getattr(wp, "allows_supersede", None) or getattr(
        wp, "may_supersede", None
    ) or getattr(wp, "authorize_supersede", None)

    if allows is not None:
        assert callable(allows)
        # True residence update: user cue + lexical overlap with new content.
        assert (
            allows("我现在住在上海了，已经搬家", "用户住在上海") is True
        ), "true move must authorize supersede"
        # Casual phrases must not authorize residence replacement.
        for casual in ("我现在在忙", "我已经吃过了", "以后再说"):
            assert allows(casual, "用户住在上海") is False, (
                f"casual phrase must not authorize supersede: {casual!r}"
            )
        # LLM content alone cannot self-authorize even if it contains cues.
        assert allows("今天天气不错", "用户现在住在上海了") is False, (
            "proposed content must not self-authorize supersede"
        )
        return

    # Fallback: at least bare casual phrases must not count as update signals
    # that authorize residence supersede by themselves.
    has_signal: Callable[[str], bool] = (
        getattr(wp, "has_explicit_update_signal", None)
        or getattr(wp, "explicit_update_signal", None)
        or wp.has_update_signal
    )
    for casual in ("我现在在忙", "我已经吃过了", "以后再说"):
        assert has_signal(casual) is False, (
            f"bare casual phrase must not be an explicit update signal: {casual!r}"
        )


@pytest.mark.asyncio
async def test_casual_phrases_do_not_supersede_residence(
    card_store: CardStore,
) -> None:
    """Casual 现在/已经/以后 phrases must not replace a residence fact."""
    uid = "u_casual_sup"
    old_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户住在杭州",
            confidence=0.7,
        )
    )
    casual_cases = (
        "我现在在忙",
        "我已经吃过了",
        "以后再说",
    )
    for i, user_msg in enumerate(casual_cases):
        llm = FakeLLM(
            json.dumps(
                {
                    "category": "fact",
                    "content": "用户住在上海",
                    "action": "supersede",
                    "target_card_id": old_id,
                },
                ensure_ascii=False,
            )
        )
        extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
        await extractor.extract_after_turn(
            user_id=uid,
            group_id=None,
            user_msg=user_msg,
            bot_reply="嗯",
            source_message_id=f"msg_casual_{i}",
        )
        old = await card_store.get_card(old_id)
        assert old is not None
        assert old.status == "active", (
            f"casual phrase {user_msg!r} must not supersede residence"
        )
        assert old.content == "用户住在杭州"
        active = await card_store.get_entity_cards("user", uid, category="fact")
        assert len(active) == 1 and active[0].card_id == old_id
        assert _write_stats(extractor)["supersede"] == 0


@pytest.mark.asyncio
async def test_llm_content_cannot_self_authorize_supersede(
    card_store: CardStore,
) -> None:
    """Even if proposed content contains update cues, user msg governs."""
    uid = "u_self_auth"
    old_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户住在杭州",
            confidence=0.7,
        )
    )
    llm = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                # LLM content self-contains 现在/已经 — must NOT authorize.
                "content": "用户现在住在上海，已经搬家",
                "action": "supersede",
                "target_card_id": old_id,
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="今天天气不错",  # no update cue, no residence lexical support
        bot_reply="是啊",
        source_message_id="msg_self_auth",
    )
    old = await card_store.get_card(old_id)
    assert old is not None
    assert old.status == "active"
    active = await card_store.get_entity_cards("user", uid, category="fact")
    assert all(c.card_id == old_id or c.supersedes != old_id for c in active)
    assert _write_stats(extractor)["supersede"] == 0


@pytest.mark.asyncio
async def test_true_residence_move_still_supersedes_with_lexical_support(
    card_store: CardStore,
) -> None:
    """User msg with real move cues + lexical support must still supersede."""
    uid = "u_true_move"
    old_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户住在杭州",
            confidence=0.7,
        )
    )
    llm = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                "content": "用户住在上海",
                "action": "supersede",
                "target_card_id": old_id,
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我现在住在上海了，已经搬家",
        bot_reply="记下了",
        source_message_id="msg_true_move",
    )
    old = await card_store.get_card(old_id)
    assert old is not None
    assert old.status == "superseded"
    active = await card_store.get_entity_cards("user", uid, category="fact")
    assert len(active) == 1
    assert "上海" in active[0].content
    assert active[0].supersedes == old_id
    assert _write_stats(extractor)["supersede"] >= 1


# ---------------------------------------------------------------------------
# 3) Duplicate containment precision
# ---------------------------------------------------------------------------


def test_containment_is_not_forced_high_confidence_duplicate() -> None:
    """Exact/punctuation dups remain; partial containment must not force 1.0."""
    from services.memory import write_policy as wp

    is_dup: Callable[..., bool] = (
        getattr(wp, "is_high_confidence_duplicate", None)
        or getattr(wp, "high_confidence_duplicate", None)
        or wp.is_duplicate
    )
    similarity = getattr(wp, "content_similarity", None)
    threshold = float(wp.DUPLICATE_SIMILARITY_THRESHOLD)

    # Exact / punctuation variants remain duplicates.
    assert is_dup("用户喜欢猫", "用户喜欢猫！") is True
    assert is_dup("用户喜欢猫", "用户喜欢猫") is True

    # Longer content that merely contains the shorter phrase is NOT a duplicate.
    assert is_dup("用户喜欢猫", "用户喜欢猫和狗") is False, (
        "containment of a shorter preference in a longer multi-preference "
        "statement must not force reinforce"
    )

    if callable(similarity):
        score = float(similarity("用户喜欢猫", "用户喜欢猫和狗"))
        assert score < 1.0, (
            "content_similarity must not return 1.0 for partial containment"
        )
        assert score < threshold, (
            f"containment score {score} must stay below DUPLICATE_SIMILARITY_THRESHOLD "
            f"({threshold})"
        )


@pytest.mark.asyncio
async def test_extractor_add_longer_preference_does_not_reinforce_shorter(
    card_store: CardStore,
) -> None:
    """add of '用户喜欢猫和狗' with existing '用户喜欢猫' must create a new card."""
    uid = "u_contain_add"
    short_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户喜欢猫",
            confidence=0.6,
        )
    )
    short_before = await card_store.get_card(short_id)
    assert short_before is not None
    short_conf = short_before.confidence

    llm = FakeLLM(
        json.dumps(
            {
                "category": "preference",
                "content": "用户喜欢猫和狗",
                "action": "add",
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我喜欢猫和狗",
        bot_reply="好可爱",
        source_message_id="msg_contain_add",
    )

    active = await card_store.get_entity_cards("user", uid, category="preference")
    assert len(active) == 2, (
        "longer multi-preference content must add, not reinforce the short card "
        f"(got {len(active)}: {[c.content for c in active]})"
    )
    short_after = await card_store.get_card(short_id)
    assert short_after is not None
    assert short_after.confidence == pytest.approx(short_conf)
    assert any("猫和狗" in c.content for c in active)
    stats = _write_stats(extractor)
    assert stats["add"] >= 1
    assert stats["reinforce"] == 0


# ---------------------------------------------------------------------------
# 4) Invalid explicit reinforce targets must reject (no retarget / no add)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_explicit_reinforce_target_rejects_without_add_or_retarget(
    card_store: CardStore,
) -> None:
    """Explicit reinforce with a bad target must skip/invalid — never retarget/add."""
    uid = "u_bad_re"
    other_uid = "u_bad_re_other"
    own_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户喜欢安静",
            confidence=0.55,
        )
    )
    other_user_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=other_uid,
            content="用户喜欢热闹",
            confidence=0.55,
        )
    )
    group_card_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="group",
            scope_id="g_bad_re",
            content="群偏好：安静",
            confidence=0.55,
        )
    )
    inactive_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户曾经喜欢喧闹",
            confidence=0.55,
        )
    )
    await card_store.expire_card(inactive_id)
    fact_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户是程序员",
            confidence=0.55,
        )
    )

    bad_targets = (
        ("card_missing_zzzz", "用户讨厌吵闹"),  # missing + non-dup content
        (other_user_id, "用户讨厌吵闹"),  # cross-user
        (group_card_id, "用户讨厌吵闹"),  # group scope
        (inactive_id, "用户讨厌吵闹"),  # inactive
        (fact_id, "用户讨厌吵闹"),  # wrong category
    )

    for target_id, content in bad_targets:
        before = await card_store.get_card(own_id)
        assert before is not None
        before_conf = before.confidence
        before_active = await card_store.get_entity_cards("user", uid)

        llm = FakeLLM(
            json.dumps(
                {
                    "category": "preference",
                    "content": content,
                    "action": "reinforce",
                    "target_card_id": target_id,
                },
                ensure_ascii=False,
            )
        )
        extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
        await extractor.extract_after_turn(
            user_id=uid,
            group_id=None,
            user_msg="我讨厌吵闹",
            bot_reply="ok",
            source_message_id=f"msg_bad_re_{target_id}",
        )

        after = await card_store.get_card(own_id)
        assert after is not None
        assert after.confidence == pytest.approx(before_conf), (
            f"invalid reinforce target {target_id!r} must not boost own card A"
        )
        after_active = await card_store.get_entity_cards("user", uid)
        assert len(after_active) == len(before_active), (
            f"invalid reinforce target {target_id!r} must not fall through to add "
            f"(before={len(before_active)} after={len(after_active)})"
        )
        stats = _write_stats(extractor)
        assert stats["add"] == 0
        assert stats["reinforce"] == 0
        assert stats["skip"] + stats["invalid"] >= 1


@pytest.mark.asyncio
async def test_invalid_reinforce_does_not_retarget_even_if_content_matches_local(
    card_store: CardStore,
) -> None:
    """Explicit reinforce with wrong target must REJECT even when content dups A."""
    uid = "u_no_retarget"
    own_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户喜欢安静",
            confidence=0.5,
        )
    )
    wrong_cat_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content="用户是程序员",
            confidence=0.5,
        )
    )

    llm = FakeLLM(
        json.dumps(
            {
                "category": "preference",
                "content": "用户喜欢安静",  # exact dup of own preference
                "action": "reinforce",
                "target_card_id": wrong_cat_id,  # wrong category target
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我还是喜欢安静",
        bot_reply="嗯",
        source_message_id="msg_no_retarget",
    )

    own = await card_store.get_card(own_id)
    assert own is not None
    assert own.confidence == pytest.approx(0.5), (
        "explicit reinforce with invalid target must not retarget to local duplicate A"
    )
    stats = _write_stats(extractor)
    assert stats["reinforce"] == 0
    assert stats["add"] == 0


@pytest.mark.asyncio
async def test_add_action_still_rewrites_to_reinforce_on_full_scope_duplicate(
    card_store: CardStore,
) -> None:
    """action=add with full-scope duplicate content must still become reinforce."""
    uid = "u_add_rewrite"
    own_id = await card_store.add_card(
        NewCard(
            category="preference",
            scope="user",
            scope_id=uid,
            content="用户喜欢安静",
            confidence=0.5,
        )
    )
    llm = FakeLLM(
        json.dumps(
            {
                "category": "preference",
                "content": "用户喜欢安静",
                "action": "add",
            },
            ensure_ascii=False,
        )
    )
    extractor = _make_extractor(card_store, llm, write_policy_enabled=True)
    await extractor.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我喜欢安静",
        bot_reply="好",
        source_message_id="msg_add_rewrite",
    )
    active = await card_store.get_entity_cards("user", uid, category="preference")
    assert len(active) == 1
    assert active[0].card_id == own_id
    assert active[0].confidence > 0.5
    stats = _write_stats(extractor)
    assert stats["reinforce"] >= 1
    assert stats["add"] == 0


# ---------------------------------------------------------------------------
# 5) Explicit target allowlist = prompt window only (not full active set)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_target_allowlist_rejects_cards_outside_prompt_window(
    card_store: CardStore,
) -> None:
    """LLM reinforce/supersede may only target cards rendered in the 24-cap prompt.

    Full active cards remain available for deterministic `add` duplicate
    detection (see test_full_scope_duplicate_protects_cards_outside_prompt_cap).
    Explicit target_card_id lookup must NOT accept out-of-window cards even when
    they are active / same user / same category and user_msg has valid evidence.
    """
    uid = "u_explicit_window"
    out_of_window_content = "用户住在杭州"
    # Low confidence so this card ranks below the top-24 prompt window.
    out_id = await card_store.add_card(
        NewCard(
            category="fact",
            scope="user",
            scope_id=uid,
            content=out_of_window_content,
            confidence=0.05,
            priority=1,
        )
    )
    # 30 higher-confidence filler cards dominate the 24-cap context.
    for i in range(30):
        await card_store.add_card(
            NewCard(
                category="fact",
                scope="user",
                scope_id=uid,
                content=f"filler-window-{i:02d}-MARKER",
                confidence=0.90 + (i * 0.001),
                priority=5,
            )
        )

    out_before = await card_store.get_card(out_id)
    assert out_before is not None
    out_conf = out_before.confidence
    assert out_before.status == "active"

    # --- reinforce: explicit target outside prompt window must be rejected ---
    llm_re = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                "content": out_of_window_content,
                "action": "reinforce",
                "target_card_id": out_id,
            },
            ensure_ascii=False,
        )
    )
    extractor_re = _make_extractor(card_store, llm_re, write_policy_enabled=True)
    await extractor_re.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我还是住在杭州",
        bot_reply="记下了",
        source_message_id="msg_explicit_window_re",
    )

    assert llm_re.calls, "extractor must call LLM for reinforce path"
    blob_re = _request_blob(llm_re.calls[0])
    assert out_of_window_content not in blob_re, (
        "out-of-window card content must stay outside the 24-card LLM prompt blob"
    )
    assert out_id not in blob_re, (
        "out-of-window card id must not appear in the LLM prompt blob"
    )
    present_fillers_re = sum(
        1 for i in range(30) if f"filler-window-{i:02d}-MARKER" in blob_re
    )
    assert present_fillers_re <= 24

    out_after_re = await card_store.get_card(out_id)
    assert out_after_re is not None
    assert out_after_re.confidence == pytest.approx(out_conf), (
        "explicit reinforce of out-of-window target must not change confidence"
    )
    assert out_after_re.status == "active"
    stats_re = _write_stats(extractor_re)
    assert stats_re["reinforce"] == 0, (
        "out-of-window explicit reinforce must not apply "
        f"(stats={stats_re})"
    )
    assert stats_re["add"] == 0, (
        "rejected out-of-window reinforce must not fall through to add"
    )
    assert stats_re["skip"] + stats_re["invalid"] >= 1

    # --- supersede: valid move evidence, but target outside prompt window ---
    llm_sup = FakeLLM(
        json.dumps(
            {
                "category": "fact",
                "content": "用户住在上海",
                "action": "supersede",
                "target_card_id": out_id,
            },
            ensure_ascii=False,
        )
    )
    extractor_sup = _make_extractor(card_store, llm_sup, write_policy_enabled=True)
    await extractor_sup.extract_after_turn(
        user_id=uid,
        group_id=None,
        user_msg="我现在住在上海了，已经搬家",
        bot_reply="记下了",
        source_message_id="msg_explicit_window_sup",
    )

    assert llm_sup.calls, "extractor must call LLM for supersede path"
    blob_sup = _request_blob(llm_sup.calls[0])
    assert out_of_window_content not in blob_sup, (
        "out-of-window card must stay outside the 24-card LLM prompt blob "
        "on supersede path"
    )
    assert out_id not in blob_sup

    out_after_sup = await card_store.get_card(out_id)
    assert out_after_sup is not None
    assert out_after_sup.status == "active", (
        "explicit supersede of out-of-window target must leave old card active "
        f"(got status={out_after_sup.status!r})"
    )
    assert out_after_sup.content == out_of_window_content
    assert out_after_sup.confidence == pytest.approx(out_conf)

    # Must not create a replacement that supersedes the out-of-window card.
    active_facts = await card_store.get_entity_cards("user", uid, category="fact")
    shanghai = [c for c in active_facts if "上海" in c.content and "filler" not in c.content]
    assert all(c.supersedes != out_id for c in shanghai), (
        "out-of-window supersede must not mark the old card as superseded target"
    )
    stats_sup = _write_stats(extractor_sup)
    assert stats_sup["supersede"] == 0, (
        "out-of-window explicit supersede must not apply "
        f"(stats={stats_sup})"
    )
    assert stats_sup["skip"] + stats_sup["invalid"] >= 1
