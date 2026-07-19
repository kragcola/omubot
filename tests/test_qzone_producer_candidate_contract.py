"""QZone v0.6 producer-candidate contract: producer-owned journal event metadata.

Producers must emit subject_kind / privacy / salience; the QZone adapter must not
invent them for legacy rows. Offline only — no credentials, HTTP, Docker, or NapCat.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

_CST = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# JournalEventRecord DTO
# ---------------------------------------------------------------------------


def test_journal_event_record_validates_and_to_dict_stable_keys() -> None:
    from plugins.schedule.story_arc import JournalEventRecord

    record = JournalEventRecord(
        date="2026-07-16",
        source="dream_reflection",
        summary="今天把排练卡住的段落理顺了",
        subject_kind="self",
        privacy="public",
        salience=0.82,
        event_id="dream_reflection:2026-07-16",
    )
    payload = record.to_dict()
    assert payload == {
        "date": "2026-07-16",
        "source": "dream_reflection",
        "summary": "今天把排练卡住的段落理顺了",
        "subject_kind": "self",
        "privacy": "public",
        "salience": 0.82,
        "event_id": "dream_reflection:2026-07-16",
    }
    # Immutable: frozen dataclass
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        record.summary = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs,match",
    [
        pytest.param(
            {
                "date": "not-a-date",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "public",
                "salience": 0.5,
            },
            "date",
            id="bad-date",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "private_message",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "public",
                "salience": 0.5,
            },
            "source",
            id="bad-source",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "   ",
                "subject_kind": "self",
                "privacy": "public",
                "salience": 0.5,
            },
            "summary",
            id="empty-summary",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "alien",
                "privacy": "public",
                "salience": 0.5,
            },
            "subject_kind",
            id="bad-subject",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "factual",
                "privacy": "public",
                "salience": 0.5,
                # bare factual: missing public_projection
            },
            "public_projection",
            id="bare-factual-rejected",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "factual",
                "privacy": "private",
                "salience": 0.5,
                "public_projection": {"schema_version": 1},
            },
            "public privacy",
            id="factual-requires-public-privacy",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "public",
                "salience": 0.5,
                "public_projection": {"schema_version": 1},
            },
            "public_projection",
            id="self-forbids-public-projection",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "fiction",
                "privacy": "public",
                "salience": 0.5,
                "public_projection": {"schema_version": 1},
            },
            "public_projection",
            id="fiction-forbids-public-projection",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "factual",
                "privacy": "public",
                "salience": 0.5,
                "public_projection": "not-a-mapping",
            },
            "public_projection",
            id="factual-projection-not-mapping",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "secret",
                "salience": 0.5,
            },
            "privacy",
            id="bad-privacy",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "public",
                "salience": True,
            },
            "salience",
            id="bool-salience",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "public",
                "salience": float("nan"),
            },
            "salience",
            id="nan-salience",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "public",
                "salience": float("inf"),
            },
            "salience",
            id="inf-salience",
        ),
        pytest.param(
            {
                "date": "2026-07-16",
                "source": "dream_reflection",
                "summary": "ok",
                "subject_kind": "self",
                "privacy": "public",
                "salience": 1.5,
            },
            "salience",
            id="out-of-range-salience",
        ),
    ],
)
def test_journal_event_record_rejects_malformed_fields(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    from plugins.schedule.story_arc import JournalEventRecord

    with pytest.raises(ValueError, match=match):
        JournalEventRecord(**kwargs)


def test_journal_event_record_to_dict_omits_missing_event_id() -> None:
    from plugins.schedule.story_arc import JournalEventRecord

    payload = JournalEventRecord(
        date="2026-07-16",
        source="schedule_generator",
        summary="主题《排练》",
        subject_kind="self",
        privacy="public",
        salience=0.35,
    ).to_dict()
    assert "event_id" not in payload
    assert "public_projection" not in payload
    assert set(payload) == {
        "date",
        "source",
        "summary",
        "subject_kind",
        "privacy",
        "salience",
    }


def test_journal_event_record_carries_projected_factual_only() -> None:
    """Bare factual rejected; projected factual carried; self/fiction forbid projection.

    Deep template/hash validation remains QZone-owned — story_arc only checks
    shallow structure (mapping + public privacy). No reverse import of qzone.
    """
    from plugins.schedule.story_arc import JournalEventRecord

    # Bare factual without public_projection is rejected at producer DTO.
    with pytest.raises(ValueError, match="public_projection"):
        JournalEventRecord(
            date="2026-07-16",
            source="event_replan",
            summary="今天和小明一起完成了公开排练",
            subject_kind="factual",
            privacy="public",
            salience=0.9,
            event_id="factual-bare",
        )

    # Shallow projected factual is accepted and to_dict includes public_projection.
    # Deliberately incomplete for QZone deep validation — carrier only.
    shallow_projection = {
        "schema_version": 1,
        "policy_id": "qzone-factual-public-v1",
        "public_template_id": "social_public_event_solo_v1",
        "claim_classes": ["social_public_event"],
        "source_event_hash": "a" * 64,
        "identities": [
            {
                "internal_ref": {"entity_key": "user:10001", "surface": "小明"},
                "alias_id": "alias-friend-a",
                "public_label": "一位朋友",
                "allowed_claim_classes": ["social_public_event"],
            }
        ],
    }
    record = JournalEventRecord(
        date="2026-07-16",
        source="event_replan",
        summary="今天和小明一起完成了公开排练",
        subject_kind="factual",
        privacy="public",
        salience=0.9,
        event_id="factual-projected",
        public_projection=shallow_projection,
    )
    payload = record.to_dict()
    assert payload["subject_kind"] == "factual"
    assert payload["privacy"] == "public"
    assert payload["public_projection"] == shallow_projection
    assert set(payload) == {
        "date",
        "source",
        "summary",
        "subject_kind",
        "privacy",
        "salience",
        "event_id",
        "public_projection",
    }

    # self/fiction must not carry public_projection.
    for kind in ("self", "fiction"):
        with pytest.raises(ValueError, match="public_projection"):
            JournalEventRecord(
                date="2026-07-16",
                source="event_replan",
                summary="ok",
                subject_kind=kind,  # type: ignore[arg-type]
                privacy="public",
                salience=0.5,
                public_projection={"schema_version": 1},
            )


def test_journal_event_record_public_projection_is_deep_frozen() -> None:
    """Nested mutation after construction must not rewrite a frozen carrier.

    Counterexample (pre-fix): shallow ``dict(public_projection)`` still aliased
    nested ``identities`` / claim lists, so external or to_dict consumers could
    silently change label/hash bindings after JournalEventRecord was built.
    """
    from types import MappingProxyType

    from plugins.schedule.story_arc import JournalEventRecord

    identities = [
        {
            "internal_ref": {"entity_key": "user:10001", "surface": "小明"},
            "alias_id": "alias-friend-a",
            "public_label": "一位朋友",
            "allowed_claim_classes": ["social_public_event"],
        }
    ]
    shallow_projection = {
        "schema_version": 1,
        "policy_id": "qzone-factual-public-v1",
        "public_template_id": "social_public_event_solo_v1",
        "claim_classes": ["social_public_event"],
        "source_event_hash": "a" * 64,
        "identities": identities,
    }
    record = JournalEventRecord(
        date="2026-07-16",
        source="event_replan",
        summary="今天和小明一起完成了公开排练",
        subject_kind="factual",
        privacy="public",
        salience=0.9,
        event_id="factual-deep-freeze",
        public_projection=shallow_projection,
    )
    stored = record.public_projection
    assert isinstance(stored, MappingProxyType)
    assert stored is not shallow_projection
    # Nested sequences are tuples (immutable), not shared lists.
    assert isinstance(stored["identities"], tuple)
    assert stored["identities"] is not identities
    assert isinstance(stored["identities"][0], MappingProxyType)
    assert stored["identities"][0] is not identities[0]

    # External mutation of the caller's objects must not affect the record.
    identities.append({"evil": True})
    identities[0]["public_label"] = "HACKED"
    shallow_projection["source_event_hash"] = "b" * 64
    shallow_projection["claim_classes"].append("milestone")
    assert len(stored["identities"]) == 1
    assert stored["identities"][0]["public_label"] == "一位朋友"
    assert stored["source_event_hash"] == "a" * 64
    assert list(stored["claim_classes"]) == ["social_public_event"]

    # In-place mutation through the record mapping must fail closed.
    with pytest.raises(TypeError):
        stored["source_event_hash"] = "c" * 64  # type: ignore[index]
    with pytest.raises(TypeError):
        stored["identities"][0]["public_label"] = "朋友甲"  # type: ignore[index]
    with pytest.raises(AttributeError):
        stored["claim_classes"].append("attendance")  # type: ignore[attr-defined]

    # to_dict returns a deep plain copy; mutating it must not rewrite the record.
    payload = record.to_dict()
    assert isinstance(payload["public_projection"], dict)
    assert isinstance(payload["public_projection"]["identities"], list)
    assert payload["public_projection"]["identities"] is not stored["identities"]
    payload["public_projection"]["identities"][0]["public_label"] = "朋友乙"
    payload["public_projection"]["claim_classes"].append("milestone")
    assert stored["identities"][0]["public_label"] == "一位朋友"
    assert list(stored["claim_classes"]) == ["social_public_event"]
    # Second to_dict still returns the original carrier snapshot.
    again = record.to_dict()
    assert again["public_projection"]["identities"][0]["public_label"] == "一位朋友"
    assert again["public_projection"]["claim_classes"] == ["social_public_event"]


def test_journal_event_record_projected_factual_reaches_qzone_adapter() -> None:
    """Producer to_dict factual + projection is adapt-ready for QZone deep path."""
    from plugins.qzone_journal.plugin import QZoneJournalPlugin
    from plugins.qzone_journal.public_projection import (
        compute_source_event_hash,
        project_factual_event,
    )
    from plugins.schedule.story_arc import JournalEventRecord

    raw_summary = "今天和小明一起完成了公开排练"
    bindings = [
        (0, "user:10001", "小明", "alias-friend-a", "一位朋友"),
    ]
    digest = compute_source_event_hash(
        raw_summary=raw_summary,
        claim_classes=["social_public_event"],
        public_template_id="social_public_event_solo_v1",
        projection_bindings=bindings,
    )
    projection = {
        "schema_version": 1,
        "policy_id": "qzone-factual-public-v1",
        "public_template_id": "social_public_event_solo_v1",
        "claim_classes": ["social_public_event"],
        "source_event_hash": digest,
        "identities": [
            {
                "internal_ref": {"entity_key": "user:10001", "surface": "小明"},
                "alias_id": "alias-friend-a",
                "public_label": "一位朋友",
                "allowed_claim_classes": [
                    "social_public_event",
                    "attendance",
                    "milestone",
                ],
            }
        ],
    }
    # Producer DTO carries; does not deep-validate (hash may be wrong for other payloads).
    record = JournalEventRecord(
        date="2026-07-15",
        source="event_replan",
        summary=raw_summary,
        subject_kind="factual",
        privacy="public",
        salience=0.9,
        event_id="factual-via-producer",
        public_projection=projection,
    )
    raw = record.to_dict()
    assert "public_projection" in raw

    # QZone adapter is authoritative for deep validation + CandidateEvent.
    plugin = QZoneJournalPlugin()
    today = date(2026, 7, 15)
    arc = SimpleNamespace(arc_id="arc-main", scope="group")
    decision = plugin._adapt_raw_event(raw, arc=arc, today=today)
    assert decision.reason == "accept"
    assert decision.candidate is not None
    assert decision.candidate.subject_kind == "factual"
    assert decision.candidate.summary == "今天和一位朋友一起参加了公开活动"
    assert "小明" not in decision.candidate.summary
    assert decision.candidate.public_projection is not None

    # Deep project path agrees with producer-carried payload.
    validated = project_factual_event(
        raw_summary=raw_summary, projection_input=projection
    )
    assert validated.projected_summary == decision.candidate.summary


# ---------------------------------------------------------------------------
# Producers
# ---------------------------------------------------------------------------


def test_schedule_generator_emits_fiction_public_for_fiction_arc() -> None:
    from plugins.schedule.generator import update_story_arc_after_schedule
    from plugins.schedule.story_arc import StoryArc
    from plugins.schedule.types import Schedule, TimeSlot

    arc = StoryArc(arc_id="weekly_life_20260716", scope="fiction", stage="planning")
    schedule = Schedule(
        date="2026-07-16",
        theme="舞台剧复盘日",
        day_narrative="排练和复习之间找平衡",
        slots=[
            TimeSlot(
                time="09:00",
                activity="practice",
                description="站位复盘",
                mood_hint="专注",
            )
        ],
    )
    assert update_story_arc_after_schedule(arc, schedule) is True
    event = arc.last_events[-1]
    assert event["date"] == "2026-07-16"
    assert event["source"] == "schedule_generator"
    assert event["subject_kind"] == "fiction"
    assert event["privacy"] == "public"
    assert event["salience"] == 0.35
    assert event["event_id"] == "schedule_generator:2026-07-16"
    assert event["theme"] == "舞台剧复盘日"
    assert "排练和复习之间" in event["summary"] or "主题" in event["summary"]


def test_event_replan_emits_fiction_public_on_fiction_arc() -> None:
    from plugins.schedule.plugin import _update_arc_for_event_replan
    from plugins.schedule.story_arc import StoryArc

    arc = StoryArc(arc_id="weekly_life_20260716", scope="fiction", stage="planning")
    now = datetime(2026, 7, 16, 12, 0, tzinfo=_CST)
    _update_arc_for_event_replan(
        arc,
        "纱枝",
        "纱枝作为 fiction 伙伴轻微扭伤，团队把舞台动作临时降难度。",
        "降难度站位约束",
        now=now,
        reason="deadline/exam pressure=0.90",
    )
    event = arc.last_events[-1]
    assert event["date"] == "2026-07-16"
    assert event["source"] == "event_replan"
    assert event["subject_kind"] == "fiction"
    assert event["privacy"] == "public"
    assert event["salience"] == 0.95
    assert event["event_id"] == "event_replan:2026-07-16"
    assert event["reason"] == "deadline/exam pressure=0.90"
    assert "轻微扭伤" in event["summary"]


def test_event_replan_privacy_unknown_when_arc_scope_not_fiction() -> None:
    from plugins.schedule.plugin import _update_arc_for_event_replan
    from plugins.schedule.story_arc import StoryArc

    arc = StoryArc(arc_id="weekly_life_20260716", scope="self", stage="planning")
    now = datetime(2026, 7, 16, 12, 0, tzinfo=_CST)
    _update_arc_for_event_replan(
        arc,
        "伙伴",
        "伙伴轻微扭伤后的降难度排练。",
        "约束",
        now=now,
        reason="tension",
    )
    event = arc.last_events[-1]
    assert event["subject_kind"] == "fiction"
    assert event["privacy"] == "unknown"
    assert event["salience"] == 0.95


def test_dream_global_reflection_emits_fiction_public_salience_on_fiction_arc() -> None:
    from plugins.dream.plugin import (
        LifeReflectionCardDraft,
        LifeReflectionDraft,
        _apply_life_reflection_to_arc,
        _bind_life_reflection_scope,
    )
    from plugins.schedule.story_arc import StoryArc

    raw = LifeReflectionDraft(
        cards=[
            LifeReflectionCardDraft(
                category="event",
                scope="global",
                scope_id="global",
                content="经历洞察：全局反思可公开。",
            )
        ],
        last_event_summary="全局反思：把排练卡住的地方理顺了。",
    )
    bound = _bind_life_reflection_scope(raw, "global")
    assert bound.journal_privacy == "public"

    arc = StoryArc(arc_id="stage_play_week", scope="fiction", stage="planning")
    # Freeze "today" via patching would couple time; apply uses CST today.
    # Assert shape and trusted privacy path regardless of calendar date.
    _apply_life_reflection_to_arc(arc, bound)
    events = [e for e in arc.last_events if e.get("source") == "dream_reflection"]
    assert len(events) == 1
    event = events[0]
    assert event["subject_kind"] == "fiction"
    assert event["privacy"] == "public"
    assert event["salience"] == 0.82
    assert event["summary"] == "全局反思：把排练卡住的地方理顺了。"
    assert event["event_id"] == f"dream_reflection:{event['date']}"
    # ISO date string
    date.fromisoformat(event["date"])


def test_dream_group_reflection_privacy_unknown_even_if_text_looks_safe() -> None:
    from plugins.dream.plugin import (
        LifeReflectionCardDraft,
        LifeReflectionDraft,
        _apply_life_reflection_to_arc,
        _bind_life_reflection_scope,
    )
    from plugins.schedule.story_arc import StoryArc

    raw = LifeReflectionDraft(
        cards=[
            LifeReflectionCardDraft(
                category="event",
                scope="group",
                scope_id="984198159",
                content="经历洞察：群聊里一起练歌，文字看起来安全。",
            )
        ],
        last_event_summary="和群友一起练歌，气氛很好，没有任何敏感内容。",
    )
    bound = _bind_life_reflection_scope(raw, "984198159")
    assert bound.journal_privacy == "unknown"
    # Model-shaped self-authorization must never stick
    assert not hasattr(bound, "privacy") or getattr(bound, "privacy", None) != "public"

    arc = StoryArc(arc_id="stage_play_week", scope="fiction", stage="planning")
    _apply_life_reflection_to_arc(arc, bound)
    event = [e for e in arc.last_events if e.get("source") == "dream_reflection"][-1]
    assert event["subject_kind"] == "fiction"
    assert event["privacy"] == "unknown"
    assert event["salience"] == 0.82


def test_dream_parser_never_self_authorizes_journal_privacy() -> None:
    from plugins.dream.plugin import LifeReflectionDraft, _parse_life_reflection_result

    text = """
    {
      "cards": [{
        "scope": "global",
        "scope_id": "global",
        "category": "event",
        "content": "模型自称可公开"
      }],
      "last_event_summary": "模型想公开",
      "journal_privacy": "public",
      "privacy": "public"
    }
    """
    draft = _parse_life_reflection_result(text)
    assert draft is not None
    assert draft.journal_privacy == "unknown"
    with pytest.raises(TypeError):
        LifeReflectionDraft(journal_privacy="public")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# QZone adapter: no invention
# ---------------------------------------------------------------------------


def _plugin_for_adapter() -> Any:
    from plugins.qzone_journal.plugin import PluginConfig, QZoneJournalPlugin

    return QZoneJournalPlugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
        )
    )


def test_adapter_rejects_legacy_schedule_without_subject_privacy() -> None:
    plugin = _plugin_for_adapter()
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    decision = plugin._adapt_raw_event(
        {
            "date": today.isoformat(),
            "source": "schedule_generator",
            "theme": "复盘",
            "summary": "主题《复盘》",
            # legacy: no subject_kind / privacy / salience
        },
        arc=arc,
        today=today,
    )
    assert decision.reason == "reject_missing_subject_privacy"
    assert decision.candidate is None
    assert decision.source == "schedule_generator"


def test_adapter_rejects_legacy_replan_without_subject_privacy() -> None:
    plugin = _plugin_for_adapter()
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    decision = plugin._adapt_raw_event(
        {
            "date": today.isoformat(),
            "source": "event_replan",
            "summary": "伙伴轻微扭伤",
            "reason": "pressure",
        },
        arc=arc,
        today=today,
    )
    assert decision.reason == "reject_missing_subject_privacy"
    assert decision.candidate is None


def test_adapter_does_not_invent_default_salience() -> None:
    plugin = _plugin_for_adapter()
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    decision = plugin._adapt_raw_event(
        {
            "date": today.isoformat(),
            "source": "event_replan",
            "summary": "伙伴轻微扭伤",
            "subject_kind": "fiction",
            "privacy": "public",
            # missing salience — must not invent 0.95
        },
        arc=arc,
        today=today,
    )
    assert decision.reason == "reject_adapter_unparseable"
    assert decision.candidate is None


@pytest.mark.parametrize(
    "salience",
    [
        pytest.param(True, id="bool-true"),
        pytest.param(False, id="bool-false"),
        pytest.param("not-a-number", id="string"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="inf"),
    ],
)
def test_adapter_rejects_invalid_salience(salience: Any) -> None:
    plugin = _plugin_for_adapter()
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    decision = plugin._adapt_raw_event(
        {
            "date": today.isoformat(),
            "source": "dream_reflection",
            "summary": "反思",
            "subject_kind": "self",
            "privacy": "public",
            "salience": salience,
        },
        arc=arc,
        today=today,
    )
    assert decision.reason == "reject_adapter_unparseable"
    assert decision.candidate is None


def test_adapter_out_of_range_salience_becomes_candidate_for_selector() -> None:
    from plugins.qzone_journal.plugin import PluginConfig, QZoneJournalPlugin
    from plugins.qzone_journal.selector import JournalSelector

    plugin = QZoneJournalPlugin(
        config=PluginConfig(enabled=True, dry_run=True, allow_live_publish=False)
    )
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    decision = plugin._adapt_raw_event(
        {
            "date": today.isoformat(),
            "source": "event_replan",
            "summary": "扭伤",
            "subject_kind": "fiction",
            "privacy": "public",
            "salience": 1.5,
            "event_id": "event_replan:today",
        },
        arc=arc,
        today=today,
    )
    assert decision.reason == "accept"
    assert decision.candidate is not None
    assert decision.candidate.salience == 1.5

    selector = JournalSelector(
        allowed_sources={"event_replan", "dream_reflection", "schedule_generator"},
        salience_threshold=0.7,
    )
    gate = selector.evaluate(decision.candidate, today=today)
    assert gate.reason == "reject_salience_out_of_range"


def test_adapter_accepts_explicit_producer_fields() -> None:
    plugin = _plugin_for_adapter()
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    decision = plugin._adapt_raw_event(
        {
            "date": today.isoformat(),
            "source": "dream_reflection",
            "summary": "全局反思",
            "subject_kind": "self",
            "privacy": "public",
            "salience": 0.82,
            "event_id": f"dream_reflection:{today.isoformat()}",
        },
        arc=arc,
        today=today,
    )
    assert decision.reason == "accept"
    assert decision.candidate is not None
    assert decision.candidate.subject_kind == "self"
    assert decision.candidate.privacy == "public"
    assert decision.candidate.salience == 0.82
    assert decision.candidate.stable_id == f"dream_reflection:{today.isoformat()}"


def test_adapter_legacy_rows_never_crash() -> None:
    plugin = _plugin_for_adapter()
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    for raw in (
        None,
        "string-event",
        42,
        [],
        {"date": today.isoformat()},  # missing source/summary
        {"date": "1999-01-01", "source": "event_replan", "summary": "old"},
    ):
        decision = plugin._adapt_raw_event(raw, arc=arc, today=today)
        assert decision.reason in {
            "reject_adapter_unparseable",
            "reject_missing_subject_privacy",
        }
        assert decision.candidate is None


# ---------------------------------------------------------------------------
# Offline integration: tick path
# ---------------------------------------------------------------------------


class _LLMProbe:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def _call(self, request: Any) -> Any:
        self.calls.append(request)
        return {"text": "今天终于把卡住的排练段落理顺了。"}


class _StoryArcStore:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._arc = SimpleNamespace(
            arc_id="arc-main",
            scope="fiction",
            last_events=[dict(e) for e in events],
        )

    def load_active(self) -> Any:
        return self._arc


def _enabled_plugin() -> Any:
    from plugins.qzone_journal.plugin import PluginConfig, QZoneJournalPlugin

    return QZoneJournalPlugin(
        config=PluginConfig(
            enabled=True,
            dry_run=True,
            allow_live_publish=False,
            manual_review=True,
            salience_threshold=0.7,
        )
    )


def _plugin_context(tmp_path: Path, llm: _LLMProbe, events: list[dict[str, Any]]) -> Any:
    from kernel.types import PluginContext

    today = datetime.now(_CST).date().isoformat()
    schedule = SimpleNamespace(date=today, day_narrative="值得记录的一天", slots=[])
    return PluginContext(
        storage_dir=tmp_path,
        plugin_data_dir=tmp_path / "plugins",
        llm_client=llm,
        schedule_store=SimpleNamespace(current=schedule),
        story_arc_store=_StoryArcStore(events),
    )


@pytest.mark.asyncio
async def test_global_dream_producer_event_reaches_pending_review_offline(
    tmp_path: Path,
) -> None:
    import sqlite3

    from plugins.dream.plugin import (
        LifeReflectionCardDraft,
        LifeReflectionDraft,
        _apply_life_reflection_to_arc,
        _bind_life_reflection_scope,
    )
    from plugins.schedule.story_arc import StoryArc

    today = datetime.now(_CST).date().isoformat()
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    draft = LifeReflectionDraft(
        cards=[LifeReflectionCardDraft(
            category="event",
            scope="global",
            scope_id="global",
            content="经历洞察：把排练卡住的地方理顺了。",
        )],
        last_event_summary="全局反思：把排练卡住的地方理顺了。",
    )
    bound = _bind_life_reflection_scope(draft, "global")
    arc = StoryArc(arc_id="producer-integration", scope="fiction")
    _apply_life_reflection_to_arc(arc, bound)

    assert arc.last_events == [{
        "date": today,
        "source": "dream_reflection",
        "summary": "全局反思：把排练卡住的地方理顺了。",
        "subject_kind": "fiction",
        "privacy": "public",
        "salience": 0.82,
        "event_id": f"dream_reflection:{today}",
    }]

    ctx = _plugin_context(tmp_path, llm, arc.last_events)
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        rows = connection.execute(
            "SELECT source, status FROM qzone_journal_drafts"
        ).fetchall()
    assert rows == [("dream_reflection", "pending_review")]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_group_dream_unknown_rejected_before_llm(tmp_path: Path) -> None:
    import sqlite3

    today = datetime.now(_CST).date().isoformat()
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    events = [
        {
            "date": today,
            "source": "dream_reflection",
            "summary": "和群友一起练歌，文字看起来安全。",
            "subject_kind": "self",
            "privacy": "unknown",
            "salience": 0.82,
            "event_id": f"dream_reflection:{today}",
        }
    ]
    ctx = _plugin_context(tmp_path, llm, events)
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        count = int(
            connection.execute("SELECT COUNT(*) FROM qzone_journal_drafts").fetchone()[0]
        )
    assert count == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_private_and_unknown_privacy_rejected(tmp_path: Path) -> None:
    import sqlite3

    today = datetime.now(_CST).date().isoformat()
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    events = [
        {
            "date": today,
            "source": "event_replan",
            "summary": "私密剧情",
            "subject_kind": "fiction",
            "privacy": "private",
            "salience": 0.95,
            "event_id": "event_replan:private",
        },
        {
            "date": today,
            "source": "event_replan",
            "summary": "未知隐私",
            "subject_kind": "fiction",
            "privacy": "unknown",
            "salience": 0.95,
            "event_id": "event_replan:unknown",
        },
    ]
    ctx = _plugin_context(tmp_path, llm, events)
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        count = int(
            connection.execute("SELECT COUNT(*) FROM qzone_journal_drafts").fetchone()[0]
        )
    assert count == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_bare_factual_injection_still_rejected(tmp_path: Path) -> None:
    """Bare factual (no public_projection) remains reject_public_projection."""
    import sqlite3

    today = datetime.now(_CST).date().isoformat()
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    events = [
        {
            "date": today,
            "source": "event_replan",
            "summary": "真人线下行为注入",
            "subject_kind": "factual",
            "privacy": "public",
            "salience": 0.99,
            "event_id": "factual-injection",
            # intentionally no public_projection
        }
    ]
    ctx = _plugin_context(tmp_path, llm, events)
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        count = int(
            connection.execute("SELECT COUNT(*) FROM qzone_journal_drafts").fetchone()[0]
        )
    assert count == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_projected_factual_via_producer_dict_can_compose(tmp_path: Path) -> None:
    """Projected factual carried through producer-shaped dict is accepted offline."""
    import sqlite3

    from plugins.qzone_journal.public_projection import compute_source_event_hash
    from plugins.schedule.story_arc import JournalEventRecord

    today = datetime.now(_CST).date().isoformat()
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    raw_summary = "今天和小明一起完成了公开排练"
    digest = compute_source_event_hash(
        raw_summary=raw_summary,
        claim_classes=["social_public_event"],
        public_template_id="social_public_event_solo_v1",
        projection_bindings=[
            (0, "user:10001", "小明", "alias-friend-a", "一位朋友"),
        ],
    )
    record = JournalEventRecord(
        date=today,
        source="event_replan",
        summary=raw_summary,
        subject_kind="factual",
        privacy="public",
        salience=0.99,
        event_id="factual-projected-tick",
        public_projection={
            "schema_version": 1,
            "policy_id": "qzone-factual-public-v1",
            "public_template_id": "social_public_event_solo_v1",
            "claim_classes": ["social_public_event"],
            "source_event_hash": digest,
            "identities": [
                {
                    "internal_ref": {"entity_key": "user:10001", "surface": "小明"},
                    "alias_id": "alias-friend-a",
                    "public_label": "一位朋友",
                    "allowed_claim_classes": [
                        "social_public_event",
                        "attendance",
                        "milestone",
                    ],
                }
            ],
        },
    )
    events = [record.to_dict()]
    ctx = _plugin_context(tmp_path, llm, events)
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        rows = connection.execute(
            "SELECT subject_kind, source_summary, status FROM qzone_journal_drafts"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "factual"
    assert rows[0][1] == "今天和一位朋友一起参加了公开活动"
    assert "小明" not in rows[0][1]
    assert rows[0][2] == "pending_review"
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_same_tick_hard_gate_does_not_block_public_fiction_twin(
    tmp_path: Path,
) -> None:
    """Hard-gate failure must not register dedupe; public twin still composes."""
    import sqlite3

    today = datetime.now(_CST).date().isoformat()
    llm = _LLMProbe()
    plugin = _enabled_plugin()
    # Same source/date/stable_id: factual first (gate fail), fiction second (accept).
    events = [
        {
            "date": today,
            "source": "event_replan",
            "summary": "factual twin",
            "subject_kind": "factual",
            "privacy": "public",
            "salience": 0.95,
            "event_id": "shared-stable",
        },
        {
            "date": today,
            "source": "event_replan",
            "summary": "fiction twin public",
            "subject_kind": "fiction",
            "privacy": "public",
            "salience": 0.95,
            "event_id": "shared-stable",
        },
    ]
    ctx = _plugin_context(tmp_path, llm, events)
    await plugin.on_startup(ctx)
    try:
        await plugin.on_tick(ctx)
    finally:
        await plugin.on_shutdown(ctx)

    with sqlite3.connect(tmp_path / "qzone_journal.db") as connection:
        rows = connection.execute(
            "SELECT source, status FROM qzone_journal_drafts"
        ).fetchall()
    assert rows == [("event_replan", "pending_review")]
    assert len(llm.calls) == 1


def test_qzone_version_is_0_8_2() -> None:
    import json
    from pathlib import Path

    from plugins.qzone_journal.plugin import QZoneJournalPlugin

    assert QZoneJournalPlugin.version == "0.8.2"
    manifest = json.loads(
        Path("plugins/qzone_journal/plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "0.8.2"


def test_builtin_wire_profile_remains_unvalidated() -> None:
    from plugins.qzone_journal.delivery import BUILTIN_WIRE_PROFILE

    assert BUILTIN_WIRE_PROFILE.validated is False


def test_adapter_unknown_source_preserves_selector_ownership() -> None:
    """Unknown source with complete fields should reach selector, not invent reject."""
    from plugins.qzone_journal.selector import JournalSelector

    plugin = _plugin_for_adapter()
    today = datetime.now(_CST).date()
    arc = SimpleNamespace(arc_id="arc-a", scope="fiction")
    decision = plugin._adapt_raw_event(
        {
            "date": today.isoformat(),
            "source": "private_message",
            "summary": "私聊内容不得公开",
            "subject_kind": "self",
            "privacy": "public",
            "salience": 0.9,
            "event_id": "pm-1",
        },
        arc=arc,
        today=today,
    )
    assert decision.reason == "accept"
    assert decision.candidate is not None
    selector = JournalSelector(
        allowed_sources={"event_replan", "dream_reflection", "schedule_generator"},
        salience_threshold=0.7,
    )
    gate = selector.evaluate(decision.candidate, today=today)
    assert gate.reason == "reject_source_not_allowed"


def test_journal_event_record_salience_rejects_non_real_types() -> None:
    from plugins.schedule.story_arc import JournalEventRecord

    with pytest.raises(ValueError, match="salience"):
        JournalEventRecord(
            date="2026-07-16",
            source="schedule_generator",
            summary="ok",
            subject_kind="self",
            privacy="public",
            salience="0.35",  # type: ignore[arg-type]
        )
    # Finite boundary values accepted
    for value in (0.0, 1.0, 0.35):
        record = JournalEventRecord(
            date="2026-07-16",
            source="schedule_generator",
            summary="ok",
            subject_kind="self",
            privacy="public",
            salience=value,
        )
        assert record.salience == value
        assert math.isfinite(record.salience)


@pytest.mark.parametrize(
    "event_id",
    [
        pytest.param("p_skey=secret", id="secret-assignment"),
        pytest.param("has space", id="space"),
        pytest.param("中文-id", id="unicode"),
        pytest.param("x" * 181, id="oversized"),
    ],
)
def test_journal_event_record_rejects_unsafe_event_id(event_id: str) -> None:
    from plugins.schedule.story_arc import JournalEventRecord

    with pytest.raises(ValueError, match="event_id"):
        JournalEventRecord(
            date="2026-07-16",
            source="schedule_generator",
            summary="ok",
            subject_kind="self",
            privacy="public",
            salience=0.35,
            event_id=event_id,
        )
