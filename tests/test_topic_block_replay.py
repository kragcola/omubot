from __future__ import annotations

import json
import re
from pathlib import Path

import tests.topic_block_replay_runner as replay_runner
from services.group.topic_block import TopicBlockTracker
from tests.topic_block_replay_runner import run_replay_fixture

FIXTURE = Path(__file__).parent / "fixtures" / "topic_block_replay" / "v1" / "core.json"


def test_topic_block_replay_fixture_is_deterministic_and_green() -> None:
    first = run_replay_fixture(FIXTURE)
    second = run_replay_fixture(FIXTURE)

    assert first == second
    assert first["schema_version"] == 1
    assert first["case_count"] == 6
    assert first["event_count"] == 13
    assert first["passed"] is True
    assert all(case["passed"] is True for case in first["cases"])


def test_topic_block_replay_fixture_contains_only_case_local_data() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["provenance"] == "synthetic"
    allowed_text = {
        "aaaaaa",
        "zzzzzz",
        "topic_alpha",
        "topic_zeta",
        "reply_alpha",
        "mention_alpha",
        "old_friend_topic",
    }
    for case in payload["cases"]:
        for event in case["events"]:
            actors = [event["actor"], *event.get("mentions", [])]
            if event.get("reply_to"):
                actors.append(event["reply_to"]["actor"])
                assert 0 < int(event["reply_to"]["message_id"]) < 10_000
            assert all(re.fullmatch(r"u[1-9][0-9]?", actor) for actor in actors)
            assert event["synthetic_text"] in allowed_text
            assert 0 < int(event["message_id"]) < 10_000
            assert "group_id" not in event
            assert "text" not in event
            assert "secret" not in event


def test_replay_runner_does_not_advance_tracker_outside_observe(tmp_path, monkeypatch) -> None:
    class GuardedTracker(TopicBlockTracker):
        def __init__(self) -> None:
            super().__init__()
            self._inside_observe = False

        def observe(self, *args, **kwargs):
            self._inside_observe = True
            try:
                return super().observe(*args, **kwargs)
            finally:
                self._inside_observe = False

        def _active(self, group_id: str, now: float):
            if not self._inside_observe:
                raise AssertionError("runner mutated tracker before observe")
            return super()._active(group_id, now)

    fixture = tmp_path / "single.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provenance": "synthetic",
                "cases": [
                    {
                        "case_id": "single",
                        "events": [
                            {
                                "seq": 1,
                                "time_offset_s": 0,
                                "message_id": 1,
                                "actor": "u1",
                                "synthetic_text": "aaaaaa",
                                "expect": {
                                    "block": "b1",
                                    "pool": "active",
                                    "activity_delta": 1,
                                    "indexed_reachable": True,
                                },
                            }
                        ],
                        "expect_final": {"active": ["b1"], "reservoir": []},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(replay_runner, "TopicBlockTracker", GuardedTracker)

    assert run_replay_fixture(fixture)["passed"] is True
