"""EpisodeProvider (D.4) tests.

Covers:

- only ``enabled_for_prompt`` episodes surface (other states hidden)
- top_k cap respected
- top_k=0 / disabled / no group_id / no store → empty
- ``last_used_at`` stamped per recalled episode
- evidence_refs carry the episode_id list (BlockTraceBus double-write)
- per-episode char cap protects against runaway reflections
- D2 cancel-path: ``asyncio.wait_for(timeout=0.0)`` leaves DB consistent
"""

from __future__ import annotations

import asyncio

import pytest

from services.block_trace.episode_provider import (
    EpisodeProvider,
    _render_episode_line,
)
from services.block_trace.providers import QueryContext
from services.episodic.store import EpisodeStore


@pytest.fixture
async def episode_store(tmp_path):
    s = EpisodeStore(str(tmp_path / "episodic.db"))
    await s.init()
    yield s
    await s.close()


def _ctx(*, group_id: str = "g1") -> QueryContext:
    return QueryContext(
        request_id="req_test",
        session_id="s",
        user_id="u",
        group_id=group_id,
        conversation_text="用户问技术问题",
    )


async def _seed_enabled_episode(
    store: EpisodeStore,
    *,
    group_id: str = "g1",
    situation: str = "用户问技术问题",
    action_taken: str = "直接给结论没解释",
    outcome_signal: str = "用户给了 negative 反馈",
    reflection: str = "下次先简短确认再展开",
    confidence: float = 0.6,
) -> str:
    """Create an episode and walk it through the legal state machine."""
    ep = await store.create_episode(
        situation=situation,
        action_taken=action_taken,
        outcome_signal=outcome_signal,
        reflection=reflection,
        group_id=group_id,
        confidence=confidence,
    )
    await store.transition_state(ep.episode_id, new_state="candidate")
    await store.transition_state(ep.episode_id, new_state="approved")
    await store.transition_state(ep.episode_id, new_state="enabled_for_prompt")
    return ep.episode_id


@pytest.mark.asyncio
async def test_provide_returns_empty_when_store_missing():
    provider = EpisodeProvider(store_getter=lambda: None)
    out = await provider.provide(_ctx())
    assert out == []


@pytest.mark.asyncio
async def test_provide_returns_empty_when_disabled(episode_store):
    await _seed_enabled_episode(episode_store)
    provider = EpisodeProvider(
        store_getter=lambda: episode_store, enabled=False,
    )
    out = await provider.provide(_ctx())
    assert out == []


@pytest.mark.asyncio
async def test_provide_returns_empty_without_group_id(episode_store):
    await _seed_enabled_episode(episode_store)
    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx(group_id=""))
    assert out == []


@pytest.mark.asyncio
async def test_provide_only_returns_enabled_for_prompt(episode_store):
    # state=approved (not enabled_for_prompt) — must be filtered out
    ep_skip = await episode_store.create_episode(
        situation="should be hidden",
        group_id="g1",
        confidence=0.7,
    )
    await episode_store.transition_state(ep_skip.episode_id, new_state="candidate")
    await episode_store.transition_state(ep_skip.episode_id, new_state="approved")
    # state=enabled_for_prompt — should surface
    ep_id = await _seed_enabled_episode(episode_store)

    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())

    assert len(out) == 1
    block = out[0]
    assert block.source == "episode"
    assert block.provider == "episode_provider"
    assert ep_id in block.evidence_refs
    assert ep_skip.episode_id not in block.evidence_refs
    assert "should be hidden" not in block.text


@pytest.mark.asyncio
async def test_provide_respects_top_k(episode_store):
    ids = []
    for i in range(5):
        ids.append(
            await _seed_enabled_episode(
                episode_store,
                situation=f"场景{i}",
                reflection=f"反思{i}",
                confidence=0.6 + i * 0.05,  # higher i → higher confidence
            )
        )
    provider = EpisodeProvider(store_getter=lambda: episode_store, top_k=2)
    out = await provider.provide(_ctx())

    assert len(out) == 1
    assert len(out[0].evidence_refs) == 2
    # confidence DESC ordering — last two seeded (highest confidence) win
    assert set(out[0].evidence_refs) == {ids[4], ids[3]}


@pytest.mark.asyncio
async def test_provide_top_k_zero_returns_empty(episode_store):
    await _seed_enabled_episode(episode_store)
    provider = EpisodeProvider(store_getter=lambda: episode_store, top_k=0)
    out = await provider.provide(_ctx())
    assert out == []


@pytest.mark.asyncio
async def test_provide_filters_by_group(episode_store):
    await _seed_enabled_episode(
        episode_store, group_id="g1", situation="g1场景",
    )
    await _seed_enabled_episode(
        episode_store, group_id="g2", situation="g2场景",
    )

    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx(group_id="g1"))
    assert len(out) == 1
    assert "g1场景" in out[0].text
    assert "g2场景" not in out[0].text


@pytest.mark.asyncio
async def test_provide_stamps_last_used_at(episode_store):
    ep_id = await _seed_enabled_episode(episode_store)
    before = await episode_store.get_episode(ep_id)
    assert before is not None
    assert before.last_used_at == ""

    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())
    assert len(out) == 1

    after = await episode_store.get_episode(ep_id)
    assert after is not None
    assert after.last_used_at != ""


@pytest.mark.asyncio
async def test_provide_renders_audit_format(episode_store):
    await _seed_enabled_episode(
        episode_store,
        situation="用户问技术问题",
        action_taken="用了过于俏皮的语气",
        outcome_signal="收到 negative",
        reflection="技术场景下保持简洁，不堆 emoji",
    )
    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())
    text = out[0].text
    # Audit § D.4 mandates this exact phrasing
    assert "曾经在 用户问技术问题 时" in text
    assert "用了过于俏皮的语气" in text
    assert "结果 收到 negative" in text
    assert "下次：技术场景下保持简洁，不堆 emoji" in text


@pytest.mark.asyncio
async def test_provide_skips_empty_lines(episode_store):
    # episode whose every renderable field is blank — produces empty
    # line and must not surface a block at all
    ep = await episode_store.create_episode(
        situation="", action_taken="", outcome_signal="", reflection="",
        group_id="g1", confidence=0.6,
    )
    await episode_store.transition_state(ep.episode_id, new_state="candidate")
    await episode_store.transition_state(ep.episode_id, new_state="approved")
    await episode_store.transition_state(
        ep.episode_id, new_state="enabled_for_prompt",
    )

    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())
    assert out == []


@pytest.mark.asyncio
async def test_provide_priority_lower_than_slang_style(episode_store):
    """Token budget pressure must trim episodes before slang/style.

    Slang priority=40, style profile=42, style expressions=45 (see
    services/block_trace/{slang,style}_provider.py). Episode must be
    strictly higher number → lower-priority slot under the budget
    manager's descending-priority trim order.
    """
    await _seed_enabled_episode(episode_store)
    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())
    assert len(out) == 1
    assert out[0].priority > 45


@pytest.mark.asyncio
async def test_provide_handles_store_exception(episode_store):
    class _BoomStore:
        async def list_for_recall(self, **_kwargs):
            raise RuntimeError("disk gone")

        async def update_last_used(self, *_args, **_kwargs):
            return False

    provider = EpisodeProvider(store_getter=lambda: _BoomStore())
    out = await provider.provide(_ctx())
    assert out == []


def test_render_episode_line_truncates_runaway():
    class _Stub:
        situation = "x"
        action_taken = "a" * 800  # blow past per-episode cap
        outcome_signal = "y"
        reflection = "z"

    line = _render_episode_line(_Stub())  # type: ignore[arg-type]
    assert line.endswith("…")
    assert len(line) <= 280


def test_render_episode_line_partial_fields():
    class _Stub:
        situation = "技术问题"
        action_taken = ""
        outcome_signal = ""
        reflection = "保持简洁"

    line = _render_episode_line(_Stub())  # type: ignore[arg-type]
    assert "曾经在 技术问题 时" in line
    assert "下次：保持简洁" in line


@pytest.mark.asyncio
async def test_provide_cancel_path_leaves_clean_state(episode_store):
    """D2 cancel-path: timed-out provide must not partially update.

    If wait_for cancels mid-stamp, the next provide call must still see
    a coherent EpisodeStore (no half-written rows, no stuck connection).
    The simplest assertion: after a cancel, a re-run returns the same
    episode list, untouched.
    """
    ep_id = await _seed_enabled_episode(episode_store)
    provider = EpisodeProvider(store_getter=lambda: episode_store)

    # Force timeout=0 so the awaited recall (or stamp) gets cancelled.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(provider.provide(_ctx()), timeout=0.0)

    # Store must still be usable; episode must still be queryable
    eps = await episode_store.list_for_recall(group_id="g1", limit=3)
    assert len(eps) == 1
    assert eps[0].episode_id == ep_id


@pytest.mark.asyncio
async def test_evidence_refs_format_for_blocktrace_lookup(episode_store):
    """BlockTraceBus.find_by_source_ref(source='episode', source_id=ep_id)
    relies on evidence_refs being the raw episode_id list, NOT a packed
    JSON string or formatted reference. Lock that contract here so the
    audit's "trace 哪条回复用了哪条反思" path stays usable.
    """
    ep_id = await _seed_enabled_episode(episode_store)
    provider = EpisodeProvider(store_getter=lambda: episode_store)
    out = await provider.provide(_ctx())
    assert len(out) == 1
    refs = out[0].evidence_refs
    assert isinstance(refs, tuple)
    assert ep_id in refs
    # Should be the bare episode_id, not "ep:..." or a dict
    assert all(isinstance(r, str) and r.startswith("ep_") for r in refs)
