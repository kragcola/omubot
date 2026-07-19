from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.dream.plugin import (
    LifeReflectionCardDraft,
    LifeReflectionDraft,
    _apply_life_reflection_to_arc,
    _bind_life_reflection_scope,
)
from plugins.qzone_journal.advanced_fiction_context import build_review_provenance
from plugins.qzone_journal.composer import JournalComposer
from plugins.qzone_journal.delivery import DeliveryConfig, DeliveryGateError, JournalDelivery
from plugins.qzone_journal.public_projection import ValidatedPublicProjection
from plugins.qzone_journal.selector import CandidateEvent, JournalSelector
from plugins.qzone_journal.store import JournalStore
from plugins.qzone_journal.transport import WireProfile
from plugins.schedule.generator import ScheduleGenerator, update_story_arc_after_schedule
from plugins.schedule.story_arc import StoryArc
from plugins.schedule.types import Schedule, TimeSlot
from tools import publish_qzone_capture


def _schedule() -> Schedule:
    return Schedule(
        date="2026-07-18",
        theme="合成日程",
        day_narrative="模型生成的一天",
        slots=[
            TimeSlot(
                time="15:30",
                activity="practice",
                description="在虚构排练室重新标站位",
                mood_hint="专注",
            )
        ],
    )


def _candidate(
    *, source: str, subject_kind: str, summary: str = "整理了一段记录"
) -> CandidateEvent:
    return CandidateEvent(
        source=source,
        event_date=date(2026, 7, 18),
        stable_id=f"{source}:2026-07-18",
        subject_kind=subject_kind,
        privacy="public",
        salience=0.9,
        summary=summary,
    )


def _validated_projection(summary: str) -> ValidatedPublicProjection:
    return ValidatedPublicProjection(
        schema_version=1,
        policy_id="qzone-factual-public-v1",
        public_template_id="social_public_event_solo_v1",
        source_event_hash="a" * 64,
        applied_claim_classes=("social_public_event",),
        aliases=(),
        projected_summary=summary,
    )


def _live_profile() -> WireProfile:
    return WireProfile(
        profile_id="qzone-text-v1-live-test",
        endpoint=(
            "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
            "cgi-bin/emotion_cgi_publish_v6"
        ),
        validated=True,
        content_field="con",
        uin_field="hostuin",
    )


def test_synthetic_schedule_classification_follows_arc_scope() -> None:
    fiction_arc = StoryArc(arc_id="fiction-arc", scope="fiction")
    assert update_story_arc_after_schedule(fiction_arc, _schedule()) is True
    fiction_event = fiction_arc.last_events[-1]
    assert fiction_event["subject_kind"] == "fiction"
    assert fiction_event["privacy"] == "public"

    self_arc = StoryArc(arc_id="self-arc", scope="self")
    assert update_story_arc_after_schedule(self_arc, _schedule()) is True
    self_event = self_arc.last_events[-1]
    assert self_event["subject_kind"] == "self"
    assert self_event["privacy"] == "unknown"


def test_dream_reflection_classification_follows_arc_scope() -> None:
    raw = LifeReflectionDraft(
        cards=[
            LifeReflectionCardDraft(
                category="event",
                scope="global",
                scope_id="global",
                content="虚构剧情反思",
            )
        ],
        last_event_summary="虚构排练告一段落。",
    )
    bound = _bind_life_reflection_scope(raw, "global")

    fiction_arc = StoryArc(arc_id="fiction-arc", scope="fiction")
    _apply_life_reflection_to_arc(fiction_arc, bound)
    fiction_event = fiction_arc.last_events[-1]
    assert fiction_event["subject_kind"] == "fiction"
    assert fiction_event["privacy"] == "public"

    self_arc = StoryArc(arc_id="self-arc", scope="self")
    _apply_life_reflection_to_arc(self_arc, bound)
    self_event = self_arc.last_events[-1]
    assert self_event["subject_kind"] == "self"
    assert self_event["privacy"] == "unknown"


@pytest.mark.parametrize("source", ["schedule_generator", "dream_reflection"])
def test_selector_rejects_stale_or_forged_synthetic_self(source: str) -> None:
    selector = JournalSelector(
        allowed_sources={"event_replan", "dream_reflection", "schedule_generator"},
        salience_threshold=0.7,
    )

    decision = selector.evaluate(_candidate(source=source, subject_kind="self"))

    assert decision.reason == "reject_subject_not_allowed"
    assert decision.accepted is False


@pytest.mark.asyncio
async def test_fiction_composer_has_deterministic_public_fiction_frame() -> None:
    async def llm(_request: Any) -> dict[str, str]:
        return {"text": "今天和司把排练节奏理顺了。"}

    composer = JournalComposer(llm, max_chars=120)
    content = await composer.compose(
        _candidate(source="event_replan", subject_kind="fiction"),
        day_narrative="虚构日程背景",
    )
    recomposed = await composer.recompose(
        subject_kind="fiction",
        source="event_replan",
        verified_summary="和虚构伙伴完成排练",
    )

    assert content.startswith("虚构故事里，")
    assert recomposed.startswith("虚构故事里，")


@pytest.mark.asyncio
async def test_fiction_frame_survives_store_safety_round_trip(tmp_path: Any) -> None:
    async def llm(_request: Any) -> dict[str, str]:
        return {"text": "和虚构伙伴完成了排练。"}

    candidate = _candidate(source="event_replan", subject_kind="fiction")
    content = await JournalComposer(llm).compose(candidate)
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        stored = await store.enqueue(
            dedupe_key="fiction-frame-store-round-trip",
            event_date=candidate.event_date,
            source=candidate.source,
            content=content,
            stable_id=candidate.stable_id,
            subject_kind=candidate.subject_kind,
            privacy=candidate.privacy,
            salience=candidate.salience,
            source_summary=candidate.summary,
        )
    finally:
        await store.close()

    assert stored.content.startswith("虚构故事里，")


@pytest.mark.asyncio
async def test_factual_composer_is_exact_and_never_calls_llm() -> None:
    calls = 0

    async def llm(_request: Any) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"text": "新增了没有证据的细节"}

    summary = "今天和一位朋友一起参加了公开活动"
    projection = _validated_projection(summary)
    candidate = CandidateEvent(
        source="event_replan",
        event_date=date(2026, 7, 18),
        stable_id="factual:2026-07-18",
        subject_kind="factual",
        privacy="public",
        salience=0.9,
        summary=summary,
        public_projection=projection,
    )

    content = await JournalComposer(llm).compose(
        candidate,
        day_narrative="这里包含未经验证的当天叙事",
    )

    assert content == summary
    assert calls == 0


@pytest.mark.asyncio
async def test_self_composer_does_not_receive_synthetic_day_narrative() -> None:
    captured: list[Any] = []

    async def llm(request: Any) -> dict[str, str]:
        captured.append(request)
        return {"text": "整理了一段记录"}

    await JournalComposer(llm).compose(
        _candidate(source="event_replan", subject_kind="self"),
        day_narrative="未经观察的合成日程细节",
    )

    prompt = "\n".join(str(block) for block in captured[0].dynamic_blocks)
    assert "未经观察的合成日程细节" not in prompt


def test_review_provenance_exposes_arc_scope() -> None:
    provenance = build_review_provenance(
        arc=SimpleNamespace(
            arc_id="arc-main",
            scope="fiction",
            stage="rehearsal",
            revision=3,
            partner_states={},
        ),
        advanced_context_included=False,
    )

    assert provenance["arc_scope"] == "fiction"


@pytest.mark.asyncio
async def test_store_approval_scope_defaults_dry_run_and_can_be_explicit_live(
    tmp_path: Any,
) -> None:
    store = JournalStore(tmp_path / "qzone_journal.db")
    await store.init()
    try:
        async def enqueue(key: str) -> Any:
            return await store.enqueue(
                dedupe_key=key,
                event_date=date(2026, 7, 18),
                source="event_replan",
                content="虚构故事里，完成了一段排练。",
                stable_id=key,
                subject_kind="fiction",
                privacy="public",
                salience=0.9,
                source_summary="完成了一段排练",
                provenance={
                    "schema_version": 1,
                    "arc_id": "arc-main",
                    "arc_revision": 1,
                    "arc_stage": "rehearsal",
                    "advanced_context_included": False,
                    "fiction_partner_entity_ids": [],
                },
            )

        dry = await store.approve((await enqueue("approval-dry")).draft_id)
        live = await store.approve(
            (await enqueue("approval-live")).draft_id,
            approval_scope="live",
        )

        assert dry.approval_scope == "dry_run"
        assert live.approval_scope == "live"
        with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_live_delivery_rejects_dry_run_approval_before_credentials() -> None:
    draft = SimpleNamespace(
        draft_id="qzd_scope",
        content="approved content",
        status="approved",
        approval_scope="dry_run",
    )

    class Store:
        async def get(self, _draft_id: str) -> Any:
            return draft

        async def is_lineage_tip(self, _draft_id: str) -> bool:
            return True

    class Credentials:
        called = False

        async def acquire(self) -> Any:
            self.called = True
            raise AssertionError("credentials must not be read")

    credentials = Credentials()
    delivery = JournalDelivery(
        config=DeliveryConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=("123456789",),
        ),
        store=Store(),
        credential_source=credentials,
        transport=SimpleNamespace(),
        profile=_live_profile(),
    )

    with pytest.raises(DeliveryGateError, match="live approval scope"):
        await delivery.deliver(draft.draft_id)
    assert credentials.called is False


@pytest.mark.asyncio
async def test_live_delivery_rejects_legacy_synthetic_self_before_credentials() -> None:
    draft = SimpleNamespace(
        draft_id="qzd_legacy_synthetic_self",
        content="今天按计划去排练了。",
        status="approved",
        approval_scope="live",
        source="schedule_generator",
        subject_kind="self",
        privacy="public",
        source_summary="今天按计划去排练了。",
    )

    class Store:
        async def get(self, _draft_id: str) -> Any:
            return draft

        async def is_lineage_tip(self, _draft_id: str) -> bool:
            return True

    class Credentials:
        called = False

        async def acquire(self) -> Any:
            self.called = True
            raise AssertionError("credentials must not be read")

    credentials = Credentials()
    delivery = JournalDelivery(
        config=DeliveryConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=("123456789",),
        ),
        store=Store(),
        credential_source=credentials,
        transport=SimpleNamespace(),
        profile=_live_profile(),
    )

    with pytest.raises(DeliveryGateError, match="authenticity"):
        await delivery.deliver(draft.draft_id)
    assert credentials.called is False


@pytest.mark.asyncio
async def test_live_delivery_rejects_unframed_fiction_before_credentials() -> None:
    draft = SimpleNamespace(
        draft_id="qzd_unframed_fiction",
        content="今天和虚构伙伴完成了排练。",
        status="approved",
        approval_scope="live",
        source="event_replan",
        subject_kind="fiction",
        privacy="public",
        source_summary="和虚构伙伴完成了排练",
    )

    class Store:
        async def get(self, _draft_id: str) -> Any:
            return draft

        async def is_lineage_tip(self, _draft_id: str) -> bool:
            return True

    class Credentials:
        called = False

        async def acquire(self) -> Any:
            self.called = True
            raise AssertionError("credentials must not be read")

    credentials = Credentials()
    delivery = JournalDelivery(
        config=DeliveryConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=("123456789",),
        ),
        store=Store(),
        credential_source=credentials,
        transport=SimpleNamespace(),
        profile=_live_profile(),
    )

    with pytest.raises(DeliveryGateError, match="authenticity"):
        await delivery.deliver(draft.draft_id)
    assert credentials.called is False


@pytest.mark.asyncio
async def test_live_delivery_rejects_missing_review_metadata_before_credentials() -> None:
    draft = SimpleNamespace(
        draft_id="qzd_missing_review_metadata",
        content="缺少审核元数据的旧草稿",
        status="approved",
        approval_scope="live",
    )

    class Store:
        async def get(self, _draft_id: str) -> Any:
            return draft

        async def is_lineage_tip(self, _draft_id: str) -> bool:
            return True

    class Credentials:
        called = False

        async def acquire(self) -> Any:
            self.called = True
            raise AssertionError("credentials must not be read")

    credentials = Credentials()
    delivery = JournalDelivery(
        config=DeliveryConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=("123456789",),
        ),
        store=Store(),
        credential_source=credentials,
        transport=SimpleNamespace(),
        profile=_live_profile(),
    )

    with pytest.raises(DeliveryGateError, match="authenticity"):
        await delivery.deliver(draft.draft_id)
    assert credentials.called is False


@pytest.mark.asyncio
async def test_live_delivery_rejects_rewritten_factual_before_credentials() -> None:
    draft = SimpleNamespace(
        draft_id="qzd_rewritten_factual",
        content="今天和一位朋友参加公开活动，后来又去了别处。",
        status="approved",
        approval_scope="live",
        source="event_replan",
        subject_kind="factual",
        privacy="public",
        source_summary="今天和一位朋友参加公开活动",
    )

    class Store:
        async def get(self, _draft_id: str) -> Any:
            return draft

        async def is_lineage_tip(self, _draft_id: str) -> bool:
            return True

    class Credentials:
        called = False

        async def acquire(self) -> Any:
            self.called = True
            raise AssertionError("credentials must not be read")

    credentials = Credentials()
    delivery = JournalDelivery(
        config=DeliveryConfig(
            enabled=True,
            dry_run=False,
            allow_live_publish=True,
            allowed_live_uins=("123456789",),
        ),
        store=Store(),
        credential_source=credentials,
        transport=SimpleNamespace(),
        profile=_live_profile(),
    )

    with pytest.raises(DeliveryGateError, match="authenticity"):
        await delivery.deliver(draft.draft_id)
    assert credentials.called is False


@pytest.mark.asyncio
async def test_live_capture_rejects_dry_run_approval_before_credentials() -> None:
    content = "approved content"
    draft = SimpleNamespace(
        draft_id="qzd_scope",
        content=content,
        status="approved",
        approval_scope="dry_run",
    )

    class Store:
        async def get(self, _draft_id: str) -> Any:
            return draft

        async def list(self, **_kwargs: Any) -> list[Any]:
            return [draft]

    class Credentials:
        called = False

        async def acquire(self) -> Any:
            self.called = True
            raise AssertionError("credentials must not be read")

    credentials = Credentials()
    request = publish_qzone_capture.CaptureRequest(
        draft_id=draft.draft_id,
        expected_content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        expected_uin_sha256=hashlib.sha256(b"123456789").hexdigest(),
        profile_id="qzone-text-v1-live-test",
    )

    with pytest.raises(
        publish_qzone_capture.CapturePreflightError,
        match="live approval scope",
    ):
        await publish_qzone_capture.publish_once(
            request=request,
            store=Store(),
            credential_source=credentials,
            transport=SimpleNamespace(),
        )
    assert credentials.called is False


@pytest.mark.asyncio
async def test_schedule_excludes_dream_reflection_cards_from_all_generation_context() -> None:
    cards = [
        SimpleNamespace(source="dream_reflection", content="合成经历洞察"),
        SimpleNamespace(source="manual", content="人工确认的普通记忆"),
    ]

    class MemoryStore:
        async def search_cards(
            self,
            _query: str,
            *,
            scope: str,
            limit: int,
        ) -> list[Any]:
            assert scope == "global"
            return cards[:limit]

    generator = object.__new__(ScheduleGenerator)
    generator._memory_card_store = MemoryStore()

    recent = await generator._load_recent_memory_cards()
    reflection = await generator._load_recent_reflection_insight_cards()

    assert [card.source for card in recent] == ["manual"]
    assert reflection == []
