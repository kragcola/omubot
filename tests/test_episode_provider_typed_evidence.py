"""RED: EpisodeProvider appends typed linked evidence for selected episodes.

Compatibility:
- Keep existing prompt prose (block text) and raw episode-id evidence_refs.
- For episodes actually selected into the block, append normalized typed
  linked evidence from Episode.linked_memory_ids / linked_memory_refs via
  services.memory.linked_refs.linked_ref_evidence.
- Dedupe stably. Metadata must distinguish episode ids from typed evidence.
- Malformed linked refs dropped. Cross-group recall unchanged.
"""

from __future__ import annotations

import pytest

from services.block_trace.episode_provider import EpisodeProvider
from services.block_trace.providers import QueryContext
from services.episodic.store import EpisodeStore
from services.memory.linked_refs import linked_ref_evidence


@pytest.fixture
async def episode_store(tmp_path):
    s = EpisodeStore(str(tmp_path / "episodic_typed.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _ctx(*, group_id: str = "g1") -> QueryContext:
    return QueryContext(
        request_id="req_typed",
        session_id="s",
        user_id="u",
        group_id=group_id,
        conversation_text="用户问技术问题",
    )


async def _seed_enabled(
    store: EpisodeStore,
    *,
    group_id: str = "g1",
    situation: str = "用户问技术问题",
    action_taken: str = "直接给结论",
    outcome_signal: str = "negative",
    reflection: str = "下次先确认",
    linked_memory_ids: list | None = None,
    confidence: float = 0.7,
) -> str:
    ep = await store.create_episode(
        situation=situation,
        action_taken=action_taken,
        outcome_signal=outcome_signal,
        reflection=reflection,
        group_id=group_id,
        confidence=confidence,
        linked_memory_ids=list(linked_memory_ids or []),
    )
    await store.transition_state(ep.episode_id, new_state="candidate")
    await store.transition_state(ep.episode_id, new_state="approved")
    await store.transition_state(ep.episode_id, new_state="enabled_for_prompt")
    return ep.episode_id


@pytest.mark.asyncio
async def test_provider_appends_typed_linked_evidence_and_keeps_prompt_prose(
    episode_store: EpisodeStore,
) -> None:
    linked = [
        "entity:user:qq:123",
        "card:card_alpha",
        "fact:fact_bb",
        "message_pk:11",
        "message:9001",
        "boguskind:xyz",  # unsupported kind → dropped by linked_ref_evidence
        "message_pk:-1",  # invalid message_pk → dropped
        "card:card_alpha",  # dedupe
    ]
    ep_id = await _seed_enabled(episode_store, linked_memory_ids=linked)

    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())
    assert len(out) == 1
    block = out[0]

    # Prompt prose unchanged (no typed refs dumped into human-facing text)
    assert block.text.startswith("相关历史反思（从过往同类场景沉淀，仅供参考）：")
    assert "entity:user:qq:123" not in block.text
    assert "card:card_alpha" not in block.text
    assert "曾经在" in block.text

    expected_typed = linked_ref_evidence(linked)
    assert "boguskind:xyz" not in expected_typed
    assert "message_pk:-1" not in expected_typed
    # evidence_refs: original episode ids first, then typed evidence (stable dedupe)
    assert ep_id in block.evidence_refs
    for ref in expected_typed:
        assert ref in block.evidence_refs
    # Malformed never appears
    assert "boguskind:xyz" not in block.evidence_refs
    assert "message_pk:-1" not in block.evidence_refs
    # Dedupe card
    assert list(block.evidence_refs).count("card:card_alpha") == 1

    # Metadata distinguishes episode ids from typed evidence
    meta = block.metadata
    assert meta.get("episode_count") == 1
    # Accept either explicit typed list or counts
    typed_list = meta.get("typed_evidence_refs") or meta.get("linked_evidence_refs")
    if typed_list is not None:
        assert list(typed_list) == list(expected_typed) or set(typed_list) == set(
            expected_typed
        )
    else:
        # counts path
        assert meta.get("typed_evidence_count") == len(expected_typed) or meta.get(
            "linked_evidence_count"
        ) == len(expected_typed)


@pytest.mark.asyncio
async def test_provider_only_appends_evidence_for_selected_episodes(
    episode_store: EpisodeStore,
) -> None:
    """top_k limits selection; non-selected episode linked refs must not appear."""
    selected_id = await _seed_enabled(
        episode_store,
        situation="场景A",
        reflection="反思A",
        confidence=0.9,
        linked_memory_ids=["card:card_selected"],
    )
    other_id = await _seed_enabled(
        episode_store,
        situation="场景B",
        reflection="反思B",
        confidence=0.1,
        linked_memory_ids=["card:card_not_selected"],
    )

    provider = EpisodeProvider(store_getter=lambda: episode_store, top_k=1)
    out = await provider.provide(_ctx())
    assert len(out) == 1
    refs = list(out[0].evidence_refs)
    assert selected_id in refs
    assert "card:card_selected" in refs
    assert other_id not in refs
    assert "card:card_not_selected" not in refs


@pytest.mark.asyncio
async def test_provider_cross_group_recall_unchanged(
    episode_store: EpisodeStore,
) -> None:
    await _seed_enabled(
        episode_store,
        group_id="g1",
        situation="本群",
        linked_memory_ids=["card:card_g1"],
    )
    await _seed_enabled(
        episode_store,
        group_id="g2",
        situation="他群",
        linked_memory_ids=["card:card_g2"],
    )

    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx(group_id="g1"))
    assert len(out) == 1
    assert "本群" in out[0].text
    assert "他群" not in out[0].text
    assert "card:card_g1" in out[0].evidence_refs
    assert "card:card_g2" not in out[0].evidence_refs


@pytest.mark.asyncio
async def test_provider_malformed_linked_refs_dropped_without_breaking_block(
    episode_store: EpisodeStore,
) -> None:
    ep_id = await _seed_enabled(
        episode_store,
        linked_memory_ids=["", "::::", 123, {"bad": True}, "card:ok"],  # type: ignore[list-item]
    )
    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())
    assert len(out) == 1
    assert ep_id in out[0].evidence_refs
    assert "card:ok" in out[0].evidence_refs
    assert out[0].text.startswith("相关历史反思")


@pytest.mark.asyncio
async def test_provider_falls_back_to_linked_memory_refs_when_ids_empty() -> None:
    """Adapter contract: empty linked_memory_ids must fall back to refs.

    Real EpisodeStore keeps ids/refs in sync; this duck-typed episode exercises
    the provider-only path where ids are missing/empty but typed refs remain.
    LinkedMemoryRef objects (or equivalent) must contribute canonical evidence.
    Does not mutate or assert EpisodeStore persistence behavior.
    """
    from services.memory.linked_refs import LinkedMemoryRef, make_linked_ref

    card_ref = make_linked_ref(kind="card", target_id="card_fallback")
    assert isinstance(card_ref, LinkedMemoryRef)
    assert card_ref.canonical == "card:card_fallback"

    ep = type(
        "DuckEpisode",
        (),
        {
            "episode_id": "ep_duck_fallback",
            "situation": "用户问技术问题",
            "action_taken": "直接给结论",
            "outcome_signal": "negative",
            "reflection": "下次先确认",
            "group_id": "g1",
            "confidence": 0.8,
            "state": "enabled_for_prompt",
            # Prefer non-empty ids; empty list must not block refs fallback.
            "linked_memory_ids": [],
            "linked_memory_refs": (card_ref,),
        },
    )()

    class _DuckStore:
        async def list_for_recall(self, *, group_id: str, limit: int = 10):
            assert group_id == "g1"
            return [ep]

        async def update_last_used(self, episode_id: str) -> None:
            return None

    provider = EpisodeProvider(store_getter=lambda: _DuckStore())
    out = await provider.provide(_ctx(group_id="g1"))
    assert len(out) == 1
    block = out[0]

    # Raw episode id + prompt prose remain
    assert "ep_duck_fallback" in block.evidence_refs
    assert block.text.startswith("相关历史反思（从过往同类场景沉淀，仅供参考）：")
    assert "曾经在" in block.text
    assert "card:card_fallback" not in block.text
    assert "card_fallback" not in block.text

    # Fallback evidence from typed refs
    assert "card:card_fallback" in block.evidence_refs
