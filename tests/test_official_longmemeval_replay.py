from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from services.context import official_replay
from services.context.official_replay import (
    build_replay_turns,
    load_longmemeval_cases,
    run_longmemeval_case,
    run_longmemeval_dataset,
    score_ranked_refs,
)
from tools import run_longmemeval_replay as replay_cli


def _official_row() -> dict[str, object]:
    return {
        "question_id": "q_single",
        "question_type": "single-session-user",
        "question": "Which drink does the user prefer?",
        "answer": "oolong tea",
        "question_date": "2026-07-17",
        "haystack_session_ids": ["session_1", "session_2"],
        "haystack_dates": ["2026-07-01", "2026-07-02"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I prefer oolong tea.", "has_answer": True},
                {"role": "assistant", "content": "Noted."},
            ],
            [{"role": "user", "content": "The weather is warm."}],
        ],
        "answer_session_ids": ["session_1"],
    }


def test_load_longmemeval_cases_maps_official_sessions_and_turn_labels() -> None:
    cases = load_longmemeval_cases([_official_row()])

    assert len(cases) == 1
    case = cases[0]
    assert case.question_id == "q_single"
    assert case.question_type == "single-session-user"
    assert case.answer_session_ids == ("session_1",)
    assert [session.session_id for session in case.sessions] == ["session_1", "session_2"]
    assert case.sessions[0].turns[0].has_answer is True
    assert case.sessions[0].turns[1].has_answer is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("answer", None),
        ("answer", "   "),
        ("question_date", None),
        ("question_date", "   "),
    ],
)
def test_load_longmemeval_cases_requires_official_case_fields(
    field: str,
    value: object,
) -> None:
    row = _official_row()
    row[field] = value

    with pytest.raises((TypeError, ValueError), match=field):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_unknown_question_type() -> None:
    row = _official_row()
    row["question_type"] = "custom-memory-task"

    with pytest.raises(ValueError, match="question_type"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_non_conversation_role() -> None:
    row = _official_row()
    sessions = row["haystack_sessions"]
    assert isinstance(sessions, list)
    first_session = sessions[0]
    assert isinstance(first_session, list)
    first_turn = first_session[0]
    assert isinstance(first_turn, dict)
    first_turn["role"] = "system"

    with pytest.raises(ValueError, match="role"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_empty_haystack() -> None:
    row = _official_row()
    row["haystack_session_ids"] = []
    row["haystack_dates"] = []
    row["haystack_sessions"] = []
    row["answer_session_ids"] = []

    with pytest.raises(ValueError, match="haystack"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_empty_session_turns() -> None:
    row = _official_row()
    sessions = row["haystack_sessions"]
    assert isinstance(sessions, list)
    sessions[0] = []

    with pytest.raises(ValueError, match="turn"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_non_boolean_answer_label() -> None:
    row = _official_row()
    sessions = row["haystack_sessions"]
    assert isinstance(sessions, list)
    first_session = sessions[0]
    assert isinstance(first_session, list)
    first_turn = first_session[0]
    assert isinstance(first_turn, dict)
    first_turn["has_answer"] = "true"

    with pytest.raises(TypeError, match="has_answer"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_unknown_answer_session() -> None:
    row = _official_row()
    row["answer_session_ids"] = ["session_missing"]

    with pytest.raises(ValueError, match="answer_session_ids"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_non_string_answer_session() -> None:
    row = _official_row()
    row["answer_session_ids"] = [1]

    with pytest.raises(TypeError, match="answer_session_ids"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_duplicate_answer_sessions() -> None:
    row = _official_row()
    row["answer_session_ids"] = ["session_1", "session_1"]

    with pytest.raises(ValueError, match="duplicate answer_session_ids"):
        load_longmemeval_cases([row])


def test_load_longmemeval_cases_rejects_duplicate_question_ids() -> None:
    first = _official_row()
    second = _official_row()

    with pytest.raises(ValueError, match="duplicate question_id"):
        load_longmemeval_cases([first, second])


def test_load_longmemeval_cases_rejects_duplicate_session_ids() -> None:
    row = _official_row()
    row["haystack_session_ids"] = ["session_1", "session_1"]

    with pytest.raises(ValueError, match="duplicate haystack session_id"):
        load_longmemeval_cases([row])


@pytest.mark.parametrize(
    "field",
    ["haystack_session_ids", "haystack_dates", "haystack_sessions"],
)
def test_load_longmemeval_cases_rejects_misaligned_session_arrays(field: str) -> None:
    row = _official_row()
    values = row[field]
    assert isinstance(values, list)
    values.pop()

    with pytest.raises(ValueError, match="haystack arrays must have equal lengths"):
        load_longmemeval_cases([row])


@pytest.mark.parametrize(
    ("field", "value"),
    [("role", ""), ("content", "   ")],
)
def test_load_longmemeval_cases_rejects_empty_turn_fields(
    field: str,
    value: str,
) -> None:
    row = _official_row()
    sessions = row["haystack_sessions"]
    assert isinstance(sessions, list)
    first_session = sessions[0]
    assert isinstance(first_session, list)
    first_turn = first_session[0]
    assert isinstance(first_turn, dict)
    first_turn[field] = value

    with pytest.raises(ValueError, match=field):
        load_longmemeval_cases([row])


@pytest.mark.parametrize(
    ("field", "value"),
    [("question_id", ""), ("question_type", "   "), ("question", None)],
)
def test_load_longmemeval_cases_rejects_invalid_question_fields(
    field: str,
    value: object,
) -> None:
    row = _official_row()
    row[field] = value

    with pytest.raises((TypeError, ValueError), match=field):
        load_longmemeval_cases([row])


@pytest.mark.parametrize(
    ("field", "value"),
    [("haystack_session_ids", ""), ("haystack_dates", "   ")],
)
def test_load_longmemeval_cases_rejects_empty_session_fields(
    field: str,
    value: str,
) -> None:
    row = _official_row()
    values = row[field]
    assert isinstance(values, list)
    values[0] = value

    with pytest.raises(ValueError, match=field):
        load_longmemeval_cases([row])


def test_score_ranked_refs_matches_official_recall_and_ndcg() -> None:
    metrics = score_ranked_refs(
        ["session_noise", "session_a", "session_b"],
        ["session_a", "session_b"],
        k=2,
    )

    assert metrics.recall_any == 1.0
    assert metrics.recall_all == 0.0
    assert metrics.ndcg == pytest.approx(0.5)


def test_score_ranked_refs_rejects_duplicate_ranked_references() -> None:
    with pytest.raises(ValueError, match="duplicate ranked"):
        score_ranked_refs(["session_a", "session_a"], ["session_a"], k=2)


def test_build_replay_turns_preserves_session_and_answer_provenance() -> None:
    case = load_longmemeval_cases([_official_row()])[0]

    turns = build_replay_turns(case)

    assert [turn.turn_ref for turn in turns] == [
        "session_1_0",
        "session_1_1",
        "session_2_0",
    ]
    assert [turn.session_id for turn in turns] == ["session_1", "session_1", "session_2"]
    assert [turn.has_answer for turn in turns] == [True, False, False]
    assert turns[0].content == "[2026-07-01] user: I prefer oolong tea."
    assert turns[0].search_content == "[2026-07-01] I prefer oolong tea."
    assert turns[0].source_message_id.startswith("longmemeval:")
    assert len({turn.source_message_id for turn in turns}) == len(turns)


@pytest.mark.asyncio
async def test_run_longmemeval_case_uses_real_context_stack(tmp_path) -> None:
    row = _official_row()
    row["question"] = "oolong tea preference"
    case = load_longmemeval_cases([row])[0]

    result = await run_longmemeval_case(
        case,
        db_path=tmp_path / "longmemeval-case.db",
        top_k=3,
        max_chars=2000,
    )

    assert result.indexed_turn_count == 3
    assert result.packed_hit_count >= 1
    assert result.ranked_session_ids[0] == "session_1"
    assert result.ranked_turn_refs[0] == "session_1_0"
    assert result.session_metrics is not None
    assert result.session_metrics.recall_any == 1.0
    assert result.session_metrics.recall_all == 1.0
    assert result.turn_metrics is not None
    assert result.turn_metrics.recall_any == 1.0


@pytest.mark.asyncio
async def test_run_longmemeval_case_reports_generic_keyword_ties_without_reordering(
    tmp_path,
) -> None:
    row = _official_row()
    row["question"] = "What does the user prefer about oolong tea?"
    case = load_longmemeval_cases([row])[0]

    result = await run_longmemeval_case(
        case,
        db_path=tmp_path / "generic-keyword-tie.db",
        top_k=3,
        max_chars=2000,
    )

    assert result.packed_hit_count == 2
    assert set(result.ranked_session_ids) == {"session_1", "session_2"}
    assert result.session_metrics is not None
    assert result.session_metrics.recall_any == 1.0
    assert result.session_metrics.recall_all == 1.0


@pytest.mark.asyncio
async def test_run_longmemeval_case_scores_sessions_without_turn_labels(tmp_path) -> None:
    row = _official_row()
    row["question"] = "What does the user prefer about oolong tea?"
    sessions = row["haystack_sessions"]
    assert isinstance(sessions, list)
    for session in sessions:
        assert isinstance(session, list)
        for turn in session:
            assert isinstance(turn, dict)
            turn.pop("has_answer", None)
    case = load_longmemeval_cases([row])[0]

    result = await run_longmemeval_case(
        case,
        db_path=tmp_path / "session-label-only.db",
        top_k=3,
        max_chars=2000,
    )

    assert result.is_abstention is False
    assert result.session_metrics is not None
    assert result.session_metrics.recall_any == 1.0
    assert result.turn_metrics is None


@pytest.mark.asyncio
async def test_run_longmemeval_case_keeps_abstention_unscored(tmp_path) -> None:
    row = _official_row()
    row["question_id"] = "q_unanswerable_abs"
    row["question"] = "Which unavailable code mentions warm weather?"
    row["answer_session_ids"] = []
    sessions = row["haystack_sessions"]
    assert isinstance(sessions, list)
    for session in sessions:
        assert isinstance(session, list)
        for turn in session:
            assert isinstance(turn, dict)
            turn.pop("has_answer", None)
    case = load_longmemeval_cases([row])[0]

    result = await run_longmemeval_case(
        case,
        db_path=tmp_path / "abstention.db",
        top_k=3,
        max_chars=2000,
    )

    assert result.is_abstention is True
    assert result.packed_hit_count >= 1
    assert result.session_metrics is None
    assert result.turn_metrics is None


@pytest.mark.asyncio
async def test_run_longmemeval_dataset_reports_scope_without_raw_content(tmp_path) -> None:
    regular = _official_row()
    regular["question"] = "What does the user prefer about oolong tea?"
    abstention = _official_row()
    abstention["question_id"] = "q_unknown_abs"
    abstention["question"] = "What is the unavailable launch code?"
    abstention["answer_session_ids"] = []
    sessions = abstention["haystack_sessions"]
    assert isinstance(sessions, list)
    for session in sessions:
        assert isinstance(session, list)
        for turn in session:
            assert isinstance(turn, dict)
            turn.pop("has_answer", None)
    dataset_path = tmp_path / "longmemeval_s_cleaned.json"
    dataset_path.write_text(json.dumps([regular, abstention]), encoding="utf-8")

    report = await run_longmemeval_dataset(
        dataset_path,
        top_k=3,
        max_chars=2000,
    )
    payload = report.to_dict()

    assert payload["version"] == "longmemeval_raw_turn_replay_v1"
    assert payload["upstream_commit"] == "9e0b455f4ef0e2ab8f2e582289761153549043fc"
    assert payload["total_cases"] == 2
    assert payload["selected_cases"] == 2
    assert payload["benchmark_parity"] is False
    assert payload["deterministic_provenance"] is True
    assert payload["ranking_tie_break_deterministic"] is False
    assert payload["memory_extraction_scored"] is False
    assert payload["answer_generation_scored"] is False
    assert payload["upstream_turn_to_session_equivalent"] is False
    assert payload["session_ranking_scope"] == "packed_unique_sessions_at_turn_top_k"
    assert payload["aggregates"]["session_scored_cases"] == 1
    assert payload["aggregates"]["abstention_cases"] == 1
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "Which drink" not in serialized
    assert "oolong tea" not in serialized
    assert "unavailable launch code" not in serialized


@pytest.mark.asyncio
async def test_run_longmemeval_dataset_opaqueizes_external_identifiers(tmp_path) -> None:
    row = _official_row()
    row["question_id"] = "case-alice@example.com"
    row["question"] = "oolong tea preference"
    row["haystack_session_ids"] = ["group_123456", "private_654321"]
    row["answer_session_ids"] = ["group_123456"]
    dataset_path = tmp_path / "privacy.json"
    dataset_path.write_text(json.dumps([row]), encoding="utf-8")

    report = await run_longmemeval_dataset(dataset_path, top_k=3, max_chars=2000)
    first_payload = report.to_dict()
    second_payload = report.to_dict()
    serialized = json.dumps(first_payload, ensure_ascii=False)

    assert "case-alice@example.com" not in serialized
    assert "group_123456" not in serialized
    assert "private_654321" not in serialized
    assert first_payload["results"] == second_payload["results"]


@pytest.mark.asyncio
async def test_run_longmemeval_dataset_rejects_non_list_root(tmp_path) -> None:
    dataset_path = tmp_path / "invalid-root.json"
    dataset_path.write_text(json.dumps({"cases": [_official_row()]}), encoding="utf-8")

    with pytest.raises(TypeError, match="dataset must be a JSON list"):
        await run_longmemeval_dataset(dataset_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("top_k", "max_chars", "error"),
    [(0, 2400, "top_k"), (10, -1, "max_chars")],
)
async def test_run_longmemeval_dataset_validates_options_without_selected_cases(
    tmp_path,
    top_k: int,
    max_chars: int,
    error: str,
) -> None:
    dataset_path = tmp_path / "empty.json"
    dataset_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        await run_longmemeval_dataset(
            dataset_path,
            top_k=top_k,
            max_chars=max_chars,
        )


@pytest.mark.asyncio
async def test_run_longmemeval_dataset_cancel_cleans_temporary_databases(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "cancel.json"
    dataset_path.write_text(json.dumps([_official_row()]), encoding="utf-8")
    created_dirs: list[Path] = []
    search_started = asyncio.Event()
    never_finish = asyncio.Event()
    real_temporary_directory = official_replay.tempfile.TemporaryDirectory

    def tracking_temporary_directory(*args: Any, **kwargs: Any) -> Any:
        kwargs["dir"] = tmp_path
        directory = real_temporary_directory(*args, **kwargs)
        created_dirs.append(Path(directory.name))
        return directory

    async def blocked_search(*args: Any, **kwargs: Any) -> list[Any]:
        search_started.set()
        await never_finish.wait()
        return []

    monkeypatch.setattr(
        official_replay.tempfile,
        "TemporaryDirectory",
        tracking_temporary_directory,
    )
    monkeypatch.setattr(official_replay.MemoryContextSource, "search", blocked_search)

    task = asyncio.create_task(run_longmemeval_dataset(dataset_path))
    await search_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert created_dirs
    assert all(not path.exists() for path in created_dirs)


@pytest.mark.asyncio
async def test_run_longmemeval_case_cancel_during_init_closes_partial_store(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = load_longmemeval_cases([_official_row()])[0]
    init_started = asyncio.Event()
    never_finish = asyncio.Event()
    opened_stores: list[Any] = []
    real_init = official_replay.CardStore.init

    async def blocked_init(store: Any, *args: Any, **kwargs: Any) -> None:
        await real_init(store, *args, **kwargs)
        opened_stores.append(store)
        init_started.set()
        await never_finish.wait()

    monkeypatch.setattr(official_replay.CardStore, "init", blocked_init)
    task = asyncio.create_task(
        run_longmemeval_case(
            case,
            db_path=tmp_path / "cancel-init.db",
        )
    )
    await init_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert opened_stores
    try:
        assert opened_stores[0]._db is None
    finally:
        await opened_stores[0].close()


def test_load_longmemeval_cases_rejects_non_object_case() -> None:
    with pytest.raises(TypeError, match="case must be an object"):
        load_longmemeval_cases(["not-an-object"])  # type: ignore[list-item]


def test_longmemeval_replay_cli_prints_sanitized_report(tmp_path, capsys) -> None:
    row = _official_row()
    row["question"] = "What does the user prefer about SECRET-QUESTION-oolong tea?"
    dataset_path = tmp_path / "longmemeval.json"
    dataset_path.write_text(json.dumps([row]), encoding="utf-8")

    debug_messages: list[str] = []
    sink_id = logger.add(lambda message: debug_messages.append(str(message)), level="DEBUG")
    try:
        exit_code = replay_cli.main(
            [
                str(dataset_path),
                "--limit",
                "1",
                "--top-k",
                "3",
                "--max-chars",
                "2000",
                "--indent",
                "0",
            ]
        )
    finally:
        logger.remove(sink_id)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["version"] == "longmemeval_raw_turn_replay_v1"
    assert payload["selected_cases"] == 1
    assert payload["benchmark_parity"] is False
    assert "oolong tea" not in json.dumps(payload, ensure_ascii=False)
    assert "SECRET-QUESTION" not in captured.err
    assert "SECRET-QUESTION" not in "".join(debug_messages)


def test_longmemeval_replay_cli_sanitizes_schema_failures(tmp_path, capsys) -> None:
    row = _official_row()
    sessions = row["haystack_sessions"]
    assert isinstance(sessions, list)
    first_session = sessions[0]
    assert isinstance(first_session, list)
    first_turn = first_session[0]
    assert isinstance(first_turn, dict)
    first_turn["has_answer"] = "TOP SECRET RAW CONTENT"
    dataset_path = tmp_path / "invalid.json"
    dataset_path.write_text(json.dumps([row]), encoding="utf-8")

    exit_code = replay_cli.main([str(dataset_path), "--indent", "0"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 2
    assert payload == {"error": "replay_failed:TypeError", "ok": False}
    assert "TOP SECRET RAW CONTENT" not in output
