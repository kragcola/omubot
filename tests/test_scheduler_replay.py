from __future__ import annotations

from services.scheduler_replay import (
    ReplayJudgement,
    ReplaySample,
    ReplayStore,
    judgement_to_dict,
    make_counterfactual_sample,
    summarize_judgements,
)


def _sample() -> ReplaySample:
    return ReplaySample(
        group_id="100",
        message_id=1,
        created_at=10.0,
        actual_decision="reply",
        counterfactual_decision="skip",
        context="hello",
    )


def test_counterfactual_sample_flips_reply_and_skip() -> None:
    reply = make_counterfactual_sample(
        group_id="100",
        message_id=1,
        created_at=10,
        actual_decision="reply",
        context="ctx",
    )
    skip = make_counterfactual_sample(
        group_id="100",
        message_id=1,
        created_at=10,
        actual_decision="skip",
        context="ctx",
    )

    assert reply.counterfactual_decision == "skip"
    assert skip.counterfactual_decision == "reply"


def test_replay_store_records_summary(tmp_path) -> None:
    store = ReplayStore(tmp_path / "replay.db")
    judgements = [
        ReplayJudgement(_sample(), "real_better"),
        ReplayJudgement(_sample(), "counterfactual_better"),
        ReplayJudgement(_sample(), "indistinguishable"),
    ]

    summary = store.record_run(run_id="run-1", group_id="100", judgements=judgements)
    runs = store.list_runs()

    assert summary == summarize_judgements(judgements)
    assert runs[0]["run_id"] == "run-1"
    run_summary = runs[0]["summary"]
    assert isinstance(run_summary, dict)
    assert run_summary["counterfactual_better"] == 1
    assert judgement_to_dict(judgements[0])["label"] == "real_better"
