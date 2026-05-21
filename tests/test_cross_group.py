"""Tests for Phase A2 — cross-group visibility migration.

Covers all 5 stores:
  slang, style, learning_normalizer, knowledge_graph (fact + candidate), episodic.
Tests: schema columns, reason + enabled_for_groups params, read path, write guard.
"""

from __future__ import annotations

import pytest

from services.cross_group import cross_group_where
from services.episodic.store import EpisodeStore
from services.knowledge_graph.store import KnowledgeGraphStore
from services.learning_normalizer.store import LearningNormalizerStore
from services.slang.store import SlangStore
from services.style.store import NewStyleExpression, StyleStore


@pytest.fixture
async def slang_store(tmp_path):
    store = SlangStore(db_path=tmp_path / "slang.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def style_store(tmp_path):
    store = StyleStore(db_path=str(tmp_path / "style.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def normalizer_store(tmp_path):
    store = LearningNormalizerStore(db_path=tmp_path / "learning_normalizer.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def kg_store(tmp_path):
    store = KnowledgeGraphStore(db_path=str(tmp_path / "knowledge_graph.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def episode_store(tmp_path):
    store = EpisodeStore(str(tmp_path / "episodic.db"))
    await store.init()
    yield store
    await store.close()


class TestSchema:
    """A2.2: Verify columns exist after init."""

    async def test_slang_has_cross_group_columns(self, slang_store: SlangStore):
        db = slang_store._require_db()
        cursor = await db.execute("PRAGMA table_info(slang_terms)")
        cols = {row["name"] for row in await cursor.fetchall()}
        assert "cross_group_visible" in cols
        assert "cross_group_enabled_by" in cols
        assert "cross_group_enabled_at" in cols

    async def test_style_has_cross_group_columns(self, style_store: StyleStore):
        db = style_store._require_db()
        cursor = await db.execute("PRAGMA table_info(style_expressions)")
        cols = {row["name"] for row in await cursor.fetchall()}
        assert "cross_group_visible" in cols
        assert "cross_group_enabled_by" in cols
        assert "cross_group_enabled_at" in cols

    async def test_slang_default_is_zero(self, slang_store: SlangStore):
        await slang_store.create_term(
            term="testterm", meaning="test meaning",
            group_id="g1", scope="group", source="test",
        )
        db = slang_store._require_db()
        cursor = await db.execute("SELECT cross_group_visible FROM slang_terms LIMIT 1")
        row = await cursor.fetchone()
        assert row["cross_group_visible"] == 0


class TestReadPath:
    """A2.3: cross-group visibility in queries."""

    def test_cross_group_where_sql(self):
        sql = cross_group_where()
        assert "cross_group_visible = 1" in sql
        assert "scope = 'global'" in sql
        assert "group_id = ?" in sql

    async def test_get_injectable_terms_sees_cross_group(self, slang_store: SlangStore):
        term = await slang_store.create_term(
            term="crosstest", meaning="visible across groups",
            group_id="group_a", scope="group", source="test",
        )
        await slang_store.set_status(term.term_id, "approved", actor="test")
        await slang_store.set_cross_group_visibility(term.term_id, visible=True, actor="admin")

        results = await slang_store.get_injectable_terms(
            group_id="group_b", conversation_text="crosstest", max_terms=10,
        )
        assert any(t.term_id == term.term_id for t in results)

    async def test_get_injectable_terms_hides_non_cross_group(self, slang_store: SlangStore):
        term = await slang_store.create_term(
            term="localonly", meaning="not shared",
            group_id="group_a", scope="group", source="test",
        )
        await slang_store.set_status(term.term_id, "approved", actor="test")

        results = await slang_store.get_injectable_terms(
            group_id="group_b", conversation_text="localonly", max_terms=10,
        )
        assert not any(t.term_id == term.term_id for t in results)


class TestWriteGuard:
    """A2.5: write-path protection."""

    async def test_update_term_rejects_cross_group_visible(self, slang_store: SlangStore):
        term = await slang_store.create_term(
            term="guardtest", meaning="test",
            group_id="g1", scope="group", source="test",
        )
        with pytest.raises(ValueError, match="set_cross_group_visibility"):
            await slang_store.update_term(
                term.term_id,
                cross_group_visible=1,
                revision_actor="admin",
            )

    async def test_update_term_rejects_cross_group_from_non_admin(self, slang_store: SlangStore):
        term = await slang_store.create_term(
            term="guardtest2", meaning="test",
            group_id="g1", scope="group", source="test",
        )
        with pytest.raises(ValueError, match="set_cross_group_visibility"):
            await slang_store.update_term(
                term.term_id,
                cross_group_visible=1,
                revision_actor="extractor",
            )

    async def test_set_cross_group_visibility_records_revision(self, slang_store: SlangStore):
        term = await slang_store.create_term(
            term="revtest", meaning="test",
            group_id="g1", scope="group", source="test",
        )
        await slang_store.set_cross_group_visibility(term.term_id, visible=True, actor="admin")
        db = slang_store._require_db()
        cursor = await db.execute(
            "SELECT action FROM slang_term_revisions WHERE term_id = ? ORDER BY created_at DESC LIMIT 1",
            (term.term_id,),
        )
        row = await cursor.fetchone()
        assert row["action"] == "cross_group_enable"

    async def test_style_update_expression_rejects_cross_group(self, style_store: StyleStore):
        expr = await style_store.create_expression(
            NewStyleExpression(situation="test situation", style="test style",
                              scope="group", group_id="g1"),
        )
        with pytest.raises(ValueError, match="set_cross_group_visibility"):
            await style_store.update_expression(
                expr.expression_id,
                cross_group_visible=1,
                actor="admin",
            )

    async def test_style_set_cross_group_visibility(self, style_store: StyleStore):
        expr = await style_store.create_expression(
            NewStyleExpression(situation="shared situation", style="shared style",
                              scope="group", group_id="g1"),
        )
        result = await style_store.set_cross_group_visibility(
            expr.expression_id, visible=True, actor="admin",
        )
        assert result is True
        db = style_store._require_db()
        cursor = await db.execute(
            "SELECT cross_group_visible, cross_group_enabled_by FROM style_expressions WHERE expression_id = ?",
            (expr.expression_id,),
        )
        row = await cursor.fetchone()
        assert row["cross_group_visible"] == 1
        assert row["cross_group_enabled_by"] == "admin"


class TestReasonAndGroups:
    """Verify reason + enabled_for_groups params across all 5 stores."""

    async def test_slang_reason_and_groups(self, slang_store: SlangStore):
        term = await slang_store.create_term(
            term="reasontest", meaning="test", group_id="g1", scope="group", source="test",
        )
        await slang_store.set_cross_group_visibility(
            term.term_id, visible=True, actor="admin",
            reason="shared across test groups",
            enabled_for_groups=["g2", "g3"],
        )
        db = slang_store._require_db()
        cursor = await db.execute(
            "SELECT cross_group_enabled_reason, cross_group_enabled_for_groups "
            "FROM slang_terms WHERE term_id = ?",
            (term.term_id,),
        )
        row = await cursor.fetchone()
        assert row["cross_group_enabled_reason"] == "shared across test groups"
        import json
        groups = json.loads(row["cross_group_enabled_for_groups"])
        assert "g2" in groups and "g3" in groups

    async def test_slang_disable_clears_reason_and_groups(self, slang_store: SlangStore):
        term = await slang_store.create_term(
            term="cleartest", meaning="test", group_id="g1", scope="group", source="test",
        )
        await slang_store.set_cross_group_visibility(
            term.term_id, visible=True, actor="admin",
            reason="some reason", enabled_for_groups=["g5"],
        )
        await slang_store.set_cross_group_visibility(
            term.term_id, visible=False, actor="admin",
        )
        db = slang_store._require_db()
        cursor = await db.execute(
            "SELECT cross_group_enabled_reason, cross_group_enabled_for_groups, "
            "cross_group_enabled_by, cross_group_enabled_at "
            "FROM slang_terms WHERE term_id = ?",
            (term.term_id,),
        )
        row = await cursor.fetchone()
        assert row["cross_group_enabled_reason"] == ""
        assert row["cross_group_enabled_by"] == ""
        assert row["cross_group_enabled_at"] == ""
        import json
        assert json.loads(row["cross_group_enabled_for_groups"]) == []

    async def test_style_reason_and_groups(self, style_store: StyleStore):
        expr = await style_store.create_expression(
            NewStyleExpression(situation="reason test", style="test style",
                              scope="group", group_id="g1"),
        )
        await style_store.set_cross_group_visibility(
            expr.expression_id, visible=True, actor="admin",
            reason="style reason", enabled_for_groups=["g10"],
        )
        db = style_store._require_db()
        cursor = await db.execute(
            "SELECT cross_group_enabled_reason, cross_group_enabled_for_groups "
            "FROM style_expressions WHERE expression_id = ?",
            (expr.expression_id,),
        )
        row = await cursor.fetchone()
        assert row["cross_group_enabled_reason"] == "style reason"
        import json
        assert "g10" in json.loads(row["cross_group_enabled_for_groups"])

    async def test_normalizer_reason_and_groups(self, normalizer_store: LearningNormalizerStore):
        result = await normalizer_store.attach_candidate(
            domain="slang", scope="group", group_id="g1",
            raw_text="normalizer test text",
            source_table="test", source_id="test_001",
        )
        cluster_id = result.cluster_id
        ok = await normalizer_store.set_cross_group_visibility(
            cluster_id, visible=True, actor="admin",
            reason="normalizer reason", enabled_for_groups=["g2"],
        )
        assert ok is True
        cluster = await normalizer_store.get_cluster(cluster_id)
        assert cluster is not None
        assert cluster.cross_group_visible is True
        assert cluster.cross_group_enabled_by == "admin"
        assert cluster.cross_group_enabled_reason == "normalizer reason"
        assert "g2" in cluster.cross_group_enabled_for_groups

    async def test_normalizer_disable_clears(self, normalizer_store: LearningNormalizerStore):
        result = await normalizer_store.attach_candidate(
            domain="slang", scope="group", group_id="g1",
            raw_text="normalizer disable test",
            source_table="test", source_id="test_002",
        )
        await normalizer_store.set_cross_group_visibility(
            result.cluster_id, visible=True, actor="admin", reason="on",
        )
        await normalizer_store.set_cross_group_visibility(
            result.cluster_id, visible=False, actor="admin",
        )
        cluster = await normalizer_store.get_cluster(result.cluster_id)
        assert cluster.cross_group_visible is False
        assert cluster.cross_group_enabled_reason == ""
        assert cluster.cross_group_enabled_for_groups == []

    async def test_kg_fact_reason_and_groups(self, kg_store: KnowledgeGraphStore):
        fact = await kg_store.add_fact(
            subject="Alice", predicate="likes", object="Bob",
            confidence=0.9, source="test",
            evidence={"card_id": "test_card_1", "text": "test"},
        )
        ok = await kg_store.set_fact_cross_group_visibility(
            fact.fact_id, visible=True, actor="admin",
            reason="graph reason", enabled_for_groups=["g7"],
        )
        assert ok is True
        updated = await kg_store.get_fact(fact.fact_id)
        assert updated.cross_group_visible is True
        assert updated.cross_group_enabled_reason == "graph reason"
        assert "g7" in updated.cross_group_enabled_for_groups

    async def test_kg_candidate_reason_and_groups(self, kg_store: KnowledgeGraphStore):
        candidate = await kg_store.add_candidate(
            subject="Cat", predicate="chases", object="Mouse",
            confidence=0.7, source="test",
            evidence={"card_id": "test_card_2", "text": "test"},
        )
        ok = await kg_store.set_candidate_cross_group_visibility(
            candidate.candidate_id, visible=True, actor="admin",
            reason="candidate reason", enabled_for_groups=["g8", "g9"],
        )
        assert ok is True
        updated = await kg_store.get_candidate(candidate.candidate_id)
        assert updated.cross_group_visible is True
        assert updated.cross_group_enabled_reason == "candidate reason"
        assert set(updated.cross_group_enabled_for_groups) == {"g8", "g9"}

    async def test_episode_reason_and_groups(self, episode_store: EpisodeStore):
        ep = await episode_store.create_episode(situation="cg test", group_id="g1")
        ok = await episode_store.set_cross_group_visibility(
            ep.episode_id, visible=True, actor="admin",
            reason="episode reason", enabled_for_groups=["g3"],
        )
        assert ok is True
        updated = await episode_store.get_episode(ep.episode_id)
        assert updated.cross_group_visible is True
        assert updated.cross_group_enabled_by == "admin"
        assert updated.cross_group_enabled_reason == "episode reason"
        assert "g3" in updated.cross_group_enabled_for_groups

    async def test_episode_disable_clears(self, episode_store: EpisodeStore):
        ep = await episode_store.create_episode(situation="disable test", group_id="g1")
        await episode_store.set_cross_group_visibility(
            ep.episode_id, visible=True, actor="admin", reason="on",
        )
        await episode_store.set_cross_group_visibility(
            ep.episode_id, visible=False, actor="admin",
        )
        updated = await episode_store.get_episode(ep.episode_id)
        assert updated.cross_group_visible is False
        assert updated.cross_group_enabled_reason == ""
        assert updated.cross_group_enabled_for_groups == []

    async def test_not_found_returns_false(self, slang_store: SlangStore, episode_store: EpisodeStore):
        assert await slang_store.set_cross_group_visibility("bogus", visible=True, actor="admin") is False
        assert await episode_store.set_cross_group_visibility("bogus", visible=True, actor="admin") is False


class TestSchemaAllStores:
    """Verify cross-group columns exist in normalizer and KG stores."""

    async def test_normalizer_has_cross_group_columns(self, normalizer_store: LearningNormalizerStore):
        db = normalizer_store._require_db()
        cursor = await db.execute("PRAGMA table_info(learning_normalizer_clusters)")
        cols = {row["name"] for row in await cursor.fetchall()}
        for col in ("cross_group_visible", "cross_group_enabled_by", "cross_group_enabled_at",
                     "cross_group_enabled_for_groups", "cross_group_enabled_reason"):
            assert col in cols, f"missing column {col}"

    async def test_kg_facts_has_cross_group_columns(self, kg_store: KnowledgeGraphStore):
        cursor = await kg_store._db.execute("PRAGMA table_info(graph_facts)")
        cols = {row["name"] for row in await cursor.fetchall()}
        for col in ("cross_group_visible", "cross_group_enabled_by", "cross_group_enabled_at",
                     "cross_group_enabled_for_groups", "cross_group_enabled_reason"):
            assert col in cols, f"missing column {col} in graph_facts"

    async def test_kg_candidates_has_cross_group_columns(self, kg_store: KnowledgeGraphStore):
        cursor = await kg_store._db.execute("PRAGMA table_info(extraction_candidates)")
        cols = {row["name"] for row in await cursor.fetchall()}
        for col in ("cross_group_visible", "cross_group_enabled_by", "cross_group_enabled_at",
                     "cross_group_enabled_for_groups", "cross_group_enabled_reason"):
            assert col in cols, f"missing column {col} in extraction_candidates"

    async def test_episode_has_cross_group_columns(self, episode_store: EpisodeStore):
        db = episode_store._require_db()
        cursor = await db.execute("PRAGMA table_info(episodes)")
        cols = {row["name"] for row in await cursor.fetchall()}
        for col in ("cross_group_visible", "cross_group_enabled_by", "cross_group_enabled_at",
                     "cross_group_enabled_for_groups", "cross_group_enabled_reason"):
            assert col in cols, f"missing column {col} in episodes"
