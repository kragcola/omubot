from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.block_trace.homophone_provider import HomophoneProvider
from services.block_trace.providers import QueryContext
from services.homophone.interpreter import interpret_homophones


class _FakeSlangStore:
    def __init__(
        self,
        matches_by_group: dict[str, list[SimpleNamespace]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.matches_by_group = matches_by_group or {}
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def find_matching_terms(
        self,
        *,
        group_id: str,
        text: str,
        include_candidates: bool,
    ) -> list[SimpleNamespace]:
        self.calls.append({
            "group_id": group_id,
            "text": text,
            "include_candidates": include_candidates,
        })
        if self.error is not None:
            raise self.error
        return list(self.matches_by_group.get(group_id, ()))


def _ctx(text: str, *, group_id: str | None = "100") -> QueryContext:
    return QueryContext(
        request_id="req-homophone",
        session_id="group_100" if group_id else "private_u1",
        user_id="u1",
        group_id=group_id,
        conversation_text=text,
    )


@pytest.mark.asyncio
async def test_homophone_provider_emits_one_auditable_dynamic_candidate() -> None:
    store = _FakeSlangStore()
    provider = HomophoneProvider(slang_store_getter=lambda: store)

    out = await provider.provide(_ctx("窝讨厌泥"))

    assert provider.name == "homophone"
    assert len(out) == 1
    candidate = out[0]
    assert candidate.source == "homophone"
    assert candidate.provider == "homophone_provider"
    assert candidate.layer == "dynamic"
    assert candidate.position == "dynamic"
    assert candidate.label == "输入理解辅助"
    assert candidate.priority > 40
    assert candidate.metadata["confidence"] >= 0.95
    assert candidate.metadata["rule_ids"]
    assert candidate.evidence_refs == tuple(candidate.metadata["rule_ids"])
    assert "窝讨厌泥" in candidate.text
    assert "我讨厌你" in candidate.text
    assert "仅作理解" in candidate.text
    assert store.calls == [{
        "group_id": "100",
        "text": "窝讨厌泥",
        "include_candidates": False,
    }]


@pytest.mark.asyncio
async def test_homophone_provider_returns_empty_without_interpretation() -> None:
    out = await HomophoneProvider().provide(_ctx("泥土很好"))

    assert out == []


@pytest.mark.asyncio
async def test_homophone_provider_supports_private_conversations() -> None:
    out = await HomophoneProvider().provide(_ctx("窝讨厌泥", group_id=None))

    assert len(out) == 1
    assert out[0].group_id == ""
    assert out[0].scope == "session"


@pytest.mark.parametrize(
    "approved_term",
    [
        SimpleNamespace(term="窝讨厌泥", aliases=[], status="approved"),
        SimpleNamespace(term="群内说法", aliases=["窝讨厌泥"], status="approved"),
    ],
)
@pytest.mark.asyncio
async def test_homophone_provider_suppresses_group_slang_surface_conflicts(
    approved_term: SimpleNamespace,
) -> None:
    store = _FakeSlangStore({"100": [approved_term]})
    provider = HomophoneProvider(slang_store_getter=lambda: store)

    out = await provider.provide(_ctx("窝讨厌泥", group_id="100"))

    assert out == []
    assert store.calls == [{
        "group_id": "100",
        "text": "窝讨厌泥",
        "include_candidates": False,
    }]


@pytest.mark.asyncio
async def test_homophone_provider_keeps_group_conflicts_scoped() -> None:
    approved_term = SimpleNamespace(term="窝讨厌泥", aliases=[], status="approved")
    store = _FakeSlangStore({"100": [approved_term]})
    provider = HomophoneProvider(slang_store_getter=lambda: store)

    out = await provider.provide(_ctx("窝讨厌泥", group_id="200"))

    assert len(out) == 1
    assert store.calls == [{
        "group_id": "200",
        "text": "窝讨厌泥",
        "include_candidates": False,
    }]


@pytest.mark.asyncio
async def test_homophone_provider_fails_closed_when_slang_store_errors() -> None:
    store = _FakeSlangStore(error=RuntimeError("slang store unavailable"))
    provider = HomophoneProvider(slang_store_getter=lambda: store)

    out = await provider.provide(_ctx("窝讨厌泥", group_id="100"))

    assert out == []


@pytest.mark.asyncio
async def test_homophone_provider_does_not_query_group_slang_for_private_chat() -> None:
    store = _FakeSlangStore(error=AssertionError("private chat must not query group slang"))
    provider = HomophoneProvider(slang_store_getter=lambda: store)

    out = await provider.provide(_ctx("窝讨厌泥", group_id=None))

    assert len(out) == 1
    assert store.calls == []


@pytest.mark.parametrize(
    "unavailable_store",
    [
        None,
        SimpleNamespace(),
    ],
)
@pytest.mark.asyncio
async def test_homophone_provider_fails_closed_without_slang_query_capability(
    unavailable_store: object | None,
) -> None:
    provider = HomophoneProvider(slang_store_getter=lambda: unavailable_store)

    out = await provider.provide(_ctx("窝讨厌泥", group_id="100"))

    assert out == []


@pytest.mark.parametrize(
    ("conflict_surface", "kept_source", "kept_interpreted", "removed_interpreted"),
    [
        ("蟹蟹", "窝讨厌泥", "我讨厌你", "谢谢"),
        ("窝讨厌泥", "蟹蟹", "谢谢", "我讨厌你"),
    ],
)
@pytest.mark.asyncio
async def test_homophone_provider_filters_only_conflicting_slang_surface(
    conflict_surface: str,
    kept_source: str,
    kept_interpreted: str,
    removed_interpreted: str,
) -> None:
    text = "蟹蟹，窝讨厌泥"
    approved_term = SimpleNamespace(term=conflict_surface, aliases=[], status="approved")
    store = _FakeSlangStore({"100": [approved_term]})
    provider = HomophoneProvider(slang_store_getter=lambda: store)

    out = await provider.provide(_ctx(text, group_id="100"))

    assert len(out) == 1
    candidate = out[0]
    assert kept_source in candidate.text
    assert kept_interpreted in candidate.text
    assert removed_interpreted not in candidate.text

    full_interpretation = interpret_homophones(text)
    expected_rule_ids = {
        evidence.rule_id
        for evidence in full_interpretation.evidence
        if evidence.source_text == kept_source
    }
    assert expected_rule_ids
    assert set(candidate.metadata["rule_ids"]) == expected_rule_ids
    assert set(candidate.evidence_refs) == expected_rule_ids
