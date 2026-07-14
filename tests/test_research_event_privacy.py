"""Privacy contract for research event pseudonymization and configuration."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kernel.config import BotConfig
from tests.test_research_event_capture_wiring import _scheduler

RAW_GROUP_ID = "raw-group-778899"
RAW_ACTOR_ID = "raw-actor-112233"
RAW_AT_USER_IDS = ("raw-at-445566", "raw-at-667788")
SECRET = "test-only-pseudonymization-secret"
OMITTED_ALLOWLIST = object()


def _load_capture_type() -> type[Any]:
    try:
        from services.group.research_event_store import ResearchEventCapture
    except ImportError as exc:
        pytest.fail(
            "missing expected privacy-preserving ResearchEventCapture",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc
    return ResearchEventCapture


class _PersistingRecorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def enqueue(self, event: Any) -> bool:
        self.events.append(event)
        return True


def _new_capture(*, recorder: Any, run_id: str, secret: str) -> Any:
    capture_type = _load_capture_type()
    try:
        return capture_type(
            recorder=recorder,
            run_id=run_id,
            pseudonymization_secret=secret,
            group_allowlist=[RAW_GROUP_ID],
        )
    except TypeError as exc:
        if "pseudonymization_secret" in str(exc):
            pytest.fail(
                "ResearchEventCapture must require pseudonymization_secret",
                pytrace=False,
            )
        raise


@pytest.mark.parametrize(
    ("case", "group_allowlist"),
    [
        ("omitted", OMITTED_ALLOWLIST),
        ("none", None),
        ("empty", []),
    ],
)
def test_capture_rejects_missing_or_empty_group_allowlist(
    case: str,
    group_allowlist: object,
) -> None:
    capture_type = _load_capture_type()
    kwargs: dict[str, Any] = {
        "recorder": _PersistingRecorder(),
        "run_id": f"run-invalid-allowlist-{case}",
        "pseudonymization_secret": SECRET,
    }
    if group_allowlist is not OMITTED_ALLOWLIST:
        kwargs["group_allowlist"] = group_allowlist

    try:
        capture_type(**kwargs)
    except ValueError:
        return
    except TypeError as exc:
        pytest.fail(
            f"{case} group_allowlist must fail closed with ValueError, got TypeError: {exc}",
            pytrace=False,
        )
    pytest.fail(
        f"{case} group_allowlist must fail closed with ValueError",
        pytrace=False,
    )


def _capture_one(*, run_id: str, secret: str = SECRET) -> Any:
    recorder = _PersistingRecorder()
    capture = _new_capture(
        recorder=recorder,
        run_id=run_id,
        secret=secret,
    )
    capture.capture_inbound(
        event_time=datetime(2026, 7, 12, 12, 30, 15, tzinfo=UTC),
        source="live",
        group_id=RAW_GROUP_ID,
        actor_id=RAW_ACTOR_ID,
        message_id=1001,
        reply_to_message_id=None,
        at_targets=RAW_AT_USER_IDS,
        text="privacy test",
        content_type="text",
    )
    assert len(recorder.events) == 1
    return recorder.events[0]


@pytest.mark.parametrize("empty_secret", ["", "   "])
def test_capture_rejects_empty_pseudonymization_secret(empty_secret: str) -> None:
    with pytest.raises(ValueError, match=r"secret|pseudonym", check=lambda exc: bool(str(exc.value))):
        _new_capture(
            recorder=_PersistingRecorder(),
            run_id="run-empty-secret",
            secret=empty_secret,
        )


def test_persisted_event_contains_no_raw_identity_tokens() -> None:
    event = _capture_one(run_id="run-privacy-a")
    persisted_identity_fields = "|".join(
        str(value)
        for value in (
            event.actor_id,
            event.group_id,
            *event.at_user_ids,
            event.event_uid,
        )
    )

    for raw_identifier in (RAW_GROUP_ID, RAW_ACTOR_ID, *RAW_AT_USER_IDS):
        assert raw_identifier not in persisted_identity_fields


def test_same_secret_keeps_actor_and_group_stable_across_capture_runs() -> None:
    first = _capture_one(run_id="run-privacy-a")
    second = _capture_one(run_id="run-privacy-b")

    assert first.run_id == "run-privacy-a"
    assert second.run_id == "run-privacy-b"
    assert first.run_id != second.run_id
    assert first.actor_id == second.actor_id
    assert first.group_id == second.group_id
    assert first.actor_id != RAW_ACTOR_ID
    assert first.group_id != RAW_GROUP_ID


def test_config_exposes_only_pseudonymization_secret_environment_name() -> None:
    policy = BotConfig().research_event_capture
    env_name = getattr(policy, "pseudonymization_salt_env", None)

    assert not hasattr(policy, "actor_id_salt")
    assert isinstance(env_name, str), "config must expose pseudonymization_salt_env"
    assert re.fullmatch(r"[A-Z][A-Z0-9_]*", env_name)


async def test_scheduler_outbound_cq_codes_are_structured_without_raw_id_leakage() -> None:
    raw_at_user_id = "123456789"
    outbound_text = f"[CQ:reply,id=77][CQ:at,qq={raw_at_user_id}]你好"
    recorder = _PersistingRecorder()
    capture_type = _load_capture_type()
    try:
        capture = capture_type(
            recorder=recorder,
            run_id="run-outbound-cq-privacy",
            pseudonymization_secret=SECRET,
            group_allowlist=["100"],
        )
    except TypeError as exc:
        if "group_allowlist" in str(exc):
            pytest.fail(
                "ResearchEventCapture must own the raw group_allowlist",
                pytrace=False,
            )
        if "pseudonymization_secret" in str(exc):
            pytest.fail(
                "ResearchEventCapture must require pseudonymization_secret",
                pytrace=False,
            )
        raise
    bot = SimpleNamespace(
        self_id="999",
        send_group_msg=AsyncMock(return_value={"message_id": 701}),
    )
    scheduler = _scheduler(capture, bot=bot)

    try:
        await scheduler._send_to_group("100", outbound_text)

        assert len(recorder.events) == 1
        event = recorder.events[0]
        privacy_failures: list[str] = []
        if event.reply_to_message_id != 77:
            privacy_failures.append("reply CQ code was not structured as reply_to_message_id=77")
        if len(event.at_user_ids) != 1:
            privacy_failures.append("at CQ code did not produce one pseudonymous at_user_id")
        elif event.at_user_ids[0] == raw_at_user_id or raw_at_user_id in event.at_user_ids[0]:
            privacy_failures.append("at_user_ids leaked the raw QQ identifier")
        if event.text is None or "你好" not in event.text:
            privacy_failures.append("semantic text was not preserved")
        elif raw_at_user_id in event.text:
            privacy_failures.append("research text leaked the raw QQ identifier")
        if event.text is not None and "[CQ:at" in event.text:
            privacy_failures.append("research text retained the raw CQ at transport code")
        if event.text is not None and "[CQ:reply" in event.text:
            privacy_failures.append("research text retained the raw CQ reply transport code")
        assert privacy_failures == [], "; ".join(privacy_failures)
    finally:
        await scheduler.close()
