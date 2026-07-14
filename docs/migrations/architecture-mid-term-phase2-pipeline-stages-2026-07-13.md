# Architecture Mid-Term Phase 2 Migration Checklist

> Scope: typed reply pipeline stages and incremental Router / Scheduler / LLMClient responsibility migration.
> Status: completed and deployed on 2026-07-13.

## Old To New Mapping

| Slice | Old owner / entrypoint | New owner / adapter | Preserved owner | Rollback |
| --- | --- | --- | --- | --- |
| Router connection | `kernel.router.setup_routers()` embedded connect/disconnect callbacks | `RuntimeConnectionPipeline`; Router installs `ConnectionPipeline` wrappers | Router keeps NoneBot registration and message/notice protocol conversion | Remove `connection_pipeline` assembly field and restore the embedded callback block |
| Scheduler delivery | `_send_to_group()` performed humanizer, OneBot send, research capture and pair-guard inline | `RuntimeOutboundDelivery.deliver(OutboundDeliveryRequest)` performs one attempt | Scheduler keeps mute checks, retry/backoff, `sent_event`, slow-send logging, pair metric and `ReplyRun` delivery state | Restore the inline single-attempt block and remove `services/scheduler_pipeline/` |
| LLM visible guardrail | `_apply_visible_reply_guardrails()` mixed runtime lookup and guardrail evaluation | `VisibleReplyGuardrailStage.run(VisibleReplyGuardrailInput)` with a runtime adapter | LLMClient keeps drift repair, provider/tool loop, humanization rewrite, usage and successful-commit overshare counting | Restore the synchronous helper calls and remove `reply_guardrail_stage.py` |

## Boundary Inventory

- [x] Inventory Router responsibilities, protocol-only state, and first dispatch seam.
- [x] Inventory Scheduler state-machine responsibilities and first movable non-state stage.
- [x] Inventory LLMClient provider/tool-loop core versus movable stages.
- [x] Freeze stage inputs, outputs, ownership, cancellation, failure, and observability contracts.
- [x] Map every old entrypoint to its temporary adapter and final owner.

## Behavior Preservation

- [x] Preserve NoneBot matcher registration, priority, connect/reconnect, and ingest ordering.
- [x] Preserve group policy, silent_learn, outbound guard, and protocol trace order.
- [x] Preserve coalesce, per-group locks, arbiter decisions, and ReplyRun terminal semantics.
- [x] Preserve provider/tool/streaming/usage behavior and visible reply postprocessing.
- [x] Preserve ContextPlugin, research capture, capabilities, and admin diagnostics.

## Verification

- [x] Each slice has a specific assertion RED before production changes (tool-exhausted was covered by the same LLM integration change and immediately GREEN when its dedicated regression landed).
- [x] Cancellation and partial-failure paths are covered.
- [x] New contracts and changed boundaries pass targeted Pyright with 0 errors.
- [x] Targeted Ruff passes.
- [x] Expanded pipeline regression passes.
- [x] Full pytest matches or exceeds the Phase 1 baseline, except justified skips.
- [x] Independent review found no Critical and all four Important findings were closed with dedicated RED/GREEN evidence; no outstanding Critical/Important remains.

## Deployment And Rollback

- [x] Check stash and dirty worktree before any deployment.
- [x] Deploy only explicit Phase 2 production files, never unrelated dirty files.
- [x] Recreate only qq-bot; never restart/recreate/down NapCat.
- [x] Verify startup, OneBot, research capture, outbound guard, and protocol trace.
- [x] Verify public silent groups have zero send/scheduler/error events.
- [x] Record image/container/restart identities and rollback tag.
