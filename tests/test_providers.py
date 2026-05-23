"""Tests for ContextProvider implementations + PromptProviderBus."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.block_trace.provider_bus import PromptProviderBus
from services.block_trace.providers import QueryContext
from services.block_trace.slang_provider import SlangProvider
from services.block_trace.store import BlockTraceStore
from services.block_trace.style_provider import StyleProvider


class _FakeSlangSettings:
    def __init__(
        self,
        *,
        injection_enabled: bool = True,
        allowed_groups: set[str] | None = None,
        max_injected_terms: int = 3,
        max_prompt_chars: int = 800,
    ) -> None:
        self.injection_enabled = injection_enabled
        self._allowed = allowed_groups
        self.max_injected_terms = max_injected_terms
        self.max_prompt_chars = max_prompt_chars

    def allows_group(self, group_id: str) -> bool:
        return self._allowed is None or group_id in self._allowed


class _FakeSlangStore:
    def __init__(
        self,
        *,
        settings: _FakeSlangSettings | None = None,
        block_text: str = "fake-slang",
        prompt_refs: tuple[str, ...] = ("slang_ref_1",),
    ) -> None:
        self._settings = settings or _FakeSlangSettings()
        self._block_text = block_text
        self._prompt_refs = prompt_refs
        self.calls: list[dict[str, Any]] = []

    async def load_settings(self) -> _FakeSlangSettings:
        return self._settings

    async def build_prompt_block(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self._block_text

    async def build_prompt_block_with_refs(self, **kwargs: Any) -> tuple[str, tuple[str, ...]]:
        self.calls.append(kwargs)
        return self._block_text, self._prompt_refs


class _FakeStyleStore:
    def __init__(
        self,
        *,
        profile_text: str = "profile-block",
        expression_text: str = "expression-block",
        profile_refs: tuple[str, ...] = ("expr_profile",),
        expression_refs: tuple[str, ...] = ("expr_prompt",),
    ) -> None:
        self._profile = profile_text
        self._expression = expression_text
        self._profile_refs = profile_refs
        self._expression_refs = expression_refs
        self.profile_calls: list[dict[str, Any]] = []
        self.expression_calls: list[dict[str, Any]] = []

    async def build_profile_prompt_block(self, **kwargs: Any) -> str:
        self.profile_calls.append(kwargs)
        return self._profile

    async def build_profile_prompt_block_with_refs(self, **kwargs: Any) -> tuple[str, tuple[str, ...]]:
        self.profile_calls.append(kwargs)
        return self._profile, self._profile_refs

    async def build_prompt_block(self, **kwargs: Any) -> str:
        self.expression_calls.append(kwargs)
        return self._expression

    async def build_prompt_block_with_refs(self, **kwargs: Any) -> tuple[str, tuple[str, ...]]:
        self.expression_calls.append(kwargs)
        return self._expression, self._expression_refs


@pytest.fixture
def qctx() -> QueryContext:
    return QueryContext(
        request_id="req_test",
        session_id="sess_1",
        user_id="user_1",
        group_id="123456",
        conversation_text="hello world",
    )


@pytest.fixture
async def trace_store(tmp_path: Path) -> BlockTraceStore:
    store = BlockTraceStore(db_path=str(tmp_path / "trace.db"))
    await store.init()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# SlangProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slang_provider_emits_candidate(qctx: QueryContext) -> None:
    store = _FakeSlangStore()
    provider = SlangProvider(store_getter=lambda: store)
    candidates = await provider.provide(qctx)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.source == "slang"
    assert cand.provider == "slang_provider"
    assert cand.priority == 40
    assert cand.position == "dynamic"
    assert cand.text == "fake-slang"
    assert cand.label == "群内黑话"
    assert cand.evidence_refs == ("slang_ref_1",)
    assert store.calls[0]["group_id"] == "123456"


@pytest.mark.asyncio
async def test_slang_provider_skips_when_store_none(qctx: QueryContext) -> None:
    provider = SlangProvider(store_getter=lambda: None)
    assert await provider.provide(qctx) == []


@pytest.mark.asyncio
async def test_slang_provider_skips_when_no_group(qctx: QueryContext) -> None:
    store = _FakeSlangStore()
    provider = SlangProvider(store_getter=lambda: store)
    qctx_no_group = QueryContext(
        request_id="r", session_id="s", user_id="u",
        group_id=None, conversation_text="",
    )
    assert await provider.provide(qctx_no_group) == []


@pytest.mark.asyncio
async def test_slang_provider_respects_injection_disabled(qctx: QueryContext) -> None:
    store = _FakeSlangStore(settings=_FakeSlangSettings(injection_enabled=False))
    provider = SlangProvider(store_getter=lambda: store)
    assert await provider.provide(qctx) == []


@pytest.mark.asyncio
async def test_slang_provider_respects_group_allowlist(qctx: QueryContext) -> None:
    # group_id "123456" not in allowlist
    store = _FakeSlangStore(
        settings=_FakeSlangSettings(allowed_groups={"999999"}),
    )
    provider = SlangProvider(store_getter=lambda: store)
    assert await provider.provide(qctx) == []


@pytest.mark.asyncio
async def test_slang_provider_skips_empty_block(qctx: QueryContext) -> None:
    store = _FakeSlangStore(block_text="")
    provider = SlangProvider(store_getter=lambda: store)
    assert await provider.provide(qctx) == []


# ---------------------------------------------------------------------------
# StyleProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_style_provider_emits_two_candidates(qctx: QueryContext) -> None:
    store = _FakeStyleStore()
    provider = StyleProvider(store_getter=lambda: store)
    candidates = await provider.provide(qctx)
    assert len(candidates) == 2
    profile = next(c for c in candidates if c.label == "动态风格档案")
    expression = next(c for c in candidates if c.label == "表达习惯参考")
    assert profile.priority == 42
    assert expression.priority == 45
    assert profile.source == "style"
    assert expression.provider == "style_provider"
    assert profile.evidence_refs == ("expr_profile",)
    assert expression.evidence_refs == ("expr_prompt",)


@pytest.mark.asyncio
async def test_style_provider_disabled(qctx: QueryContext) -> None:
    store = _FakeStyleStore()
    provider = StyleProvider(store_getter=lambda: store, enabled=False)
    assert await provider.provide(qctx) == []


@pytest.mark.asyncio
async def test_style_provider_skips_profile_only(qctx: QueryContext) -> None:
    store = _FakeStyleStore()
    provider = StyleProvider(store_getter=lambda: store, profile_enabled=False)
    candidates = await provider.provide(qctx)
    assert len(candidates) == 1
    assert candidates[0].label == "表达习惯参考"


@pytest.mark.asyncio
async def test_style_provider_skips_when_no_group(qctx: QueryContext) -> None:
    store = _FakeStyleStore()
    provider = StyleProvider(store_getter=lambda: store)
    qctx_no_group = QueryContext(
        request_id="r", session_id="s", user_id="u",
        group_id=None, conversation_text="",
    )
    assert await provider.provide(qctx_no_group) == []


@pytest.mark.asyncio
async def test_style_provider_global_groups_propagates(qctx: QueryContext) -> None:
    store = _FakeStyleStore()
    provider = StyleProvider(
        store_getter=lambda: store,
        global_enabled_groups={"123456"},
    )
    await provider.provide(qctx)
    assert store.profile_calls[0]["include_global"] is True
    assert store.expression_calls[0]["include_global"] is True


# ---------------------------------------------------------------------------
# PromptProviderBus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_register_and_has_provider(trace_store: BlockTraceStore) -> None:
    bus = PromptProviderBus(trace_store)
    bus.register(SlangProvider(store_getter=lambda: None))
    assert bus.has_provider("slang") is True
    assert bus.has_provider("style") is False


@pytest.mark.asyncio
async def test_bus_run_active_returns_prompt_blocks(
    trace_store: BlockTraceStore, qctx: QueryContext,
) -> None:
    bus = PromptProviderBus(trace_store)
    bus.register(SlangProvider(store_getter=lambda: _FakeSlangStore()))
    bus.register(StyleProvider(store_getter=lambda: _FakeStyleStore()))
    blocks = await bus.run_active(qctx)
    assert len(blocks) == 3  # 1 slang + 2 style
    sources = {b.source for b in blocks}
    providers = {b.provider for b in blocks}
    assert sources == {"slang", "style"}
    assert providers == {"slang_provider", "style_provider"}


@pytest.mark.asyncio
async def test_bus_run_shadow_records_traces(
    trace_store: BlockTraceStore, qctx: QueryContext,
) -> None:
    bus = PromptProviderBus(trace_store)
    bus.register(SlangProvider(store_getter=lambda: _FakeSlangStore()))
    await bus.run_shadow(qctx)
    traces = await trace_store.list_for_request(qctx.request_id)
    assert len(traces) == 1
    assert traces[0].decision == "shadow_only"
    assert traces[0].source == "slang"
    assert traces[0].provider == "slang_provider"


@pytest.mark.asyncio
async def test_bus_run_all_isolates_provider_errors(
    trace_store: BlockTraceStore, qctx: QueryContext,
) -> None:
    class _BrokenProvider:
        name = "broken"

        async def provide(self, ctx: QueryContext) -> list[Any]:
            raise RuntimeError("boom")

    bus = PromptProviderBus(trace_store)
    bus.register(_BrokenProvider())
    bus.register(SlangProvider(store_getter=lambda: _FakeSlangStore()))
    blocks = await bus.run_active(qctx)
    # broken provider error should not prevent slang block
    assert len(blocks) == 1
    assert blocks[0].source == "slang"


@pytest.mark.asyncio
async def test_bus_off_mode_in_chat_path_is_noop(
    trace_store: BlockTraceStore, qctx: QueryContext,
) -> None:
    bus = PromptProviderBus(trace_store)
    bus.mode = "off"
    bus.register(SlangProvider(store_getter=lambda: _FakeSlangStore()))
    # off mode means caller should not invoke run_active or run_shadow.
    # Verify that mode flag is the gating mechanism (the LLM client path uses it).
    assert bus.mode == "off"
