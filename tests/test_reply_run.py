from kernel.reply_run import ReplyOrigin, ReplyOutcome, ReplyRun, ReplyStage


def test_reply_run_metric_contains_metadata_only() -> None:
    run = ReplyRun.start(
        session_id="group_100",
        group_id="100",
        user_id="u1",
        origin=ReplyOrigin.TRIGGERED,
        trigger_mode="at_mention",
    )
    run.record(ReplyStage.DECISION, force_reply=True)
    run.finish(ReplyOutcome.SKIPPED)

    payload = run.to_metric_metadata()

    assert payload["origin"] == "triggered"
    assert payload["outcome"] == "skipped"
    assert payload["stages"] == ["input", "decision", "skipped"]
    assert not ({"text", "content", "reply", "thought"} & set(payload))
