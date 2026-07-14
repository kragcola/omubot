# Architecture Mid-Term Phase 1 Migration Checklist

> Scope: independent Composition Root and chat runtime lifecycle ownership only.
> No schema or config migration.

## Process Composition

- [x] Move base `PluginContext` construction from `bot.py` to `bootstrap/application.py` without changing field values.
- [x] Preserve explicit plugin class set and registration order.
- [x] Preserve directory discovery after explicit registration.
- [x] Preserve persisted state application before `kernel.disabled_plugins` overrides.
- [x] Preserve locked-plugin fail-closed behavior and warning paths.
- [x] Preserve optional VisionClient construction and disabled behavior.
- [x] Preserve early runtime-error log sink installation.
- [x] Assign `ctx.bus` and `ctx.command_dispatcher` before route installation.
- [x] Install NoneBot Router exactly once.
- [x] Mount Admin router exactly once and only after service wiring.
- [x] Preserve the current 22-plugin effective startup order, including stable alphabetical discovery ties.
- [x] Keep `bot.py` as the thin executable entrypoint and `nonebot.run()` owner.

## Chat Runtime

- [x] Inventory every resource created by `ChatPlugin.on_startup` and its current owner.
- [x] Move only cross-domain service construction to `bootstrap/chat_runtime.py`.
- [x] Keep ChatPlugin message hooks, commands, plugin metadata and debug behavior unchanged.
- [x] Preserve default context service creation before ContextPlugin takeover.
- [x] Record cleanup ownership only after each startup step succeeds.
- [x] Track and restore previous `PluginContext` values on startup failure/cancellation.
- [x] On startup failure, run acquired cleanups in reverse order and re-raise the original exception.
- [x] On normal shutdown, stop producers and Scheduler before LLMClient and stores.
- [x] Make shutdown idempotent or explicitly reject double shutdown with a tested contract.
- [x] Propagate outer cancellation; do not convert it into ordinary startup/shutdown success.
- [x] Delay non-reversible Usage route registration until successful commit and enforce exactly-once.
- [x] Close CharacterRegistryDB, RecognitionCache and M1/M2 climate metric connections.
- [x] Preserve the same ResearchEventCapture instance across Router and Scheduler.
- [x] Preserve `ctx.outbound_group_access_guard` identity as a borrowed dependency.
- [x] Preserve ContextPlugin takeover during normal Chat shutdown.
- [x] Fix GroupMemoryConfig and runtime-state-bus Affection wiring only in a separate post-migration slice.

## Application Lifecycle

- [x] Keep startup order `PluginBus -> atomic plugin tool merge -> BackupScheduler`.
- [x] Merge plugin tools transactionally without replacing Chat core tools.
- [x] Stop PluginBus tick loop during process shutdown.
- [x] Stop BackupScheduler even when another cleanup fails.
- [x] Fire reverse plugin shutdown even when tick/backup cleanup fails.
- [x] Preserve first-connect one-shot semantics and reconnect behavior.
- [x] Preserve outbound guard installation before protocol trace wrapping.
- [x] Prevent duplicate lifecycle handlers, matchers, middleware and Admin routes on repeated install.

## Verification

- [x] RED/GREEN contract: plugin set and order.
- [x] RED/GREEN contract: state overrides and locked plugins.
- [x] RED/GREEN contract: Router/Admin exactly once.
- [x] RED/GREEN contract: chat startup order.
- [x] RED/GREEN contract: reverse shutdown.
- [x] RED/GREEN contract: partial-startup failure cleanup.
- [x] RED/GREEN contract: atomic plugin tool merge.
- [x] RED/GREEN contract: tick/backup/plugin cleanup error isolation.
- [x] RED/GREEN contract: Chat context publication rollback on failure/cancellation.
- [x] RED/GREEN contract: Coalescer -> Scheduler -> LLM -> stores close order.
- [x] RED/GREEN contract: character/climate resource coverage.
- [x] RED/GREEN contract: research capture/trace/outbound guard identity preservation.
- [x] Regression: ContextPlugin takeover.
- [x] Regression: existing PluginBus lifecycle/partial failure tests.
- [x] Targeted Ruff and Pyright clean for changed files.
- [x] Full pytest matches or exceeds Phase 0 baseline except justified new skips.
- [x] Independent review has no Critical/Important findings.

## Deployment And Rollback

- [x] Check `git stash list` and `git status -uno` before build/deploy.
- [x] Rebuild/recreate only qq-bot for Python code changes.
- [x] Record qq-bot image ID, container ID and restart count.
- [x] Verify startup logs, OneBot connection, capture wiring and outbound guard.
- [x] Verify public `silent_learn` group window has zero send/scheduler/error events.
- [x] Verify NapCat container ID, StartedAt and restart count are unchanged.
- [x] Roll back Phase 1 source/tests only; do not revert unrelated dirty files.
- [x] Never run compose down or recreate NapCat.
