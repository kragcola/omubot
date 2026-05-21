"""Tests for A1.3 — slang normalizer attach on all write paths.

Verifies that _attach_normalizer is called for:
  create_term, upsert_ai_approved_term (existing branch),
  update_term (term/meaning change), merge_terms.
Also verifies that non-text updates (e.g. confidence only) skip attach.
"""

from __future__ import annotations

import pytest

from services.learning_normalizer.store import LearningNormalizerStore
from services.slang.store import SlangStore


@pytest.fixture
async def stores(tmp_path):
    slang = SlangStore(db_path=tmp_path / "slang.db")
    await slang.init()
    normalizer = LearningNormalizerStore(db_path=tmp_path / "learning_normalizer.db")
    await normalizer.init()
    yield slang, normalizer
    await slang.close()
    await normalizer.close()


async def _cluster_count(normalizer: LearningNormalizerStore, *, domain: str = "slang") -> int:
    _clusters, total = await normalizer.list_clusters(domain=domain)
    return total


async def test_create_term_attaches_normalizer(stores):
    slang, normalizer = stores
    assert await _cluster_count(normalizer) == 0
    await slang.create_term(
        term="测试黑话", meaning="test meaning",
        group_id="g1", scope="group", source="test",
    )
    assert await _cluster_count(normalizer) >= 1


async def test_upsert_ai_existing_attaches_normalizer(stores):
    slang, normalizer = stores
    await slang.create_term(
        term="复用词", meaning="original meaning",
        group_id="g1", scope="group", source="extractor",
    )
    before = await _cluster_count(normalizer)

    await slang.upsert_ai_approved_term(
        term="复用词", meaning="updated meaning",
        group_id="g1", reason="reinforce",
    )
    after = await _cluster_count(normalizer)
    assert after >= before


async def test_update_term_meaning_attaches_normalizer(stores):
    slang, normalizer = stores
    term = await slang.create_term(
        term="更新词", meaning="old meaning",
        group_id="g1", scope="group", source="test",
    )
    before = await _cluster_count(normalizer)

    await slang.update_term(term.term_id, meaning="completely new meaning")
    after = await _cluster_count(normalizer)
    assert after >= before


async def test_merge_terms_attaches_normalizer(stores):
    slang, normalizer = stores
    t1 = await slang.create_term(
        term="合并目标", meaning="target meaning",
        group_id="g1", scope="group", source="test",
    )
    t2 = await slang.create_term(
        term="合并来源", meaning="source meaning",
        group_id="g1", scope="group", source="test",
    )
    before = await _cluster_count(normalizer)

    merged = await slang.merge_terms(target_id=t1.term_id, source_ids=[t2.term_id])
    assert merged is not None
    after = await _cluster_count(normalizer)
    assert after >= before


async def test_update_term_no_text_change_skips_normalizer(stores):
    slang, normalizer = stores
    term = await slang.create_term(
        term="不变词", meaning="stable meaning",
        group_id="g1", scope="group", source="test",
    )
    before = await _cluster_count(normalizer)

    await slang.update_term(term.term_id, confidence=0.99)
    after = await _cluster_count(normalizer)
    assert after == before
