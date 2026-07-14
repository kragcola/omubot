# Architecture Mid-Term M3-M6 Migration Checklist

> Scope: database governance, background task ownership, incremental Admin extraction, and partitioned type gates.
> Status: complete. Runtime deployed 2026-07-13; one historical evidence gap is recorded below and must not be rewritten as a successful pre/post row-count comparison.

## M3 Data Governance

- [x] Add a read-only 21-database catalog before replacing any backup/health registry.
- [x] Preserve existing relative paths and shared database clients.
- [x] Keep legacy `connect_sqlite()` behavior while adding explicit profiles.
- [x] Adopt legacy version-0 schemas only after deterministic fingerprint verification.
- [x] Commit ledger row and `PRAGMA user_version` in the target database transaction.
- [x] Reject checksum drift, partial schema, downgrade, and unknown future versions.
- [x] Create the pre-deploy host-checkout backup `pre-change-20260713-140943`; later classify it correctly as non-production because Docker named-volume `/app/storage` is the live source.
- [x] Immediately create and export the real production named-volume v1 safety backup after adoption (`pre-change-20260713-173655`: schema v2, 20 ok / 1 optional skipped / 0 failed / trusted=true, all current-code restore plans ALLOW); do not present it as a v0 rollback point.
- [x] Expand backup/health/capacity coverage without making optional missing DBs fatal.
- [x] Keep retention disabled by default and owner-managed.
- [x] Validate adoption semantics for types, PK, NOT NULL, defaults, UNIQUE,
  FK, index columns, uniqueness, partial predicates, and WHERE clauses.
- [x] Use `mode=ro` for live/WAL-aware status and include DB/WAL/SHM/journal
  bytes in capacity reporting.
- [x] Reject optional-missing emergency backup, pre-write future versions,
  untrusted/required-failed/skipped/forged-optional restore payloads, and
  governed targets whose semantic schema contract does not match.
- [x] Route Admin prune through the bounded owner retention handler.

## M4 Task Ownership

- [x] Inventory every first-batch task and old owner.
- [x] Map old create/cancel/close semantics to Supervisor metadata and policies.
- [x] Preserve external cancellation and reverse shutdown ordering.
- [x] Keep reply slot state-machine tasks in Scheduler unless ownership truly moves.
- [x] Expose read-only task status and bounded failure history.
- [x] Verify partial startup and shutdown cancellation leave no orphan tasks.
- [x] Register wait=true/false learning extraction under stable
  `learning.extract`, keep Supervisor records bounded, and stop it before
  PluginBus/store shutdown.
- [x] Make BackupScheduler repeated/concurrent start idempotent and finish a
  reload transaction before propagating external cancellation.
- [x] Serialize Admin backup settings read/merge/validate/reload/persist under
  one shielded route transaction so concurrent partial updates remain exact.

## M5 Admin Extraction

- [x] Move plugin toggle transaction without changing ordinary HTTP response/error behavior.
- [x] Move learning extract-all registry/lock/runner without changing polling contracts.
- [x] Preserve current Admin route prefixes and authentication middleware.
- [x] Do not combine unrelated UI restructuring into backend owner migration.
- [x] Preserve ordinary runtime toggle JSON `{ok, plugin}` while keeping
  restart-required pending metadata, reverse cancellation, and fail-closed
  behavior when PluginStateStore is absent.
- [x] Keep ToolRegistry replacement atomic and show persistent target state in
  the restart-required UI.

## M6 Type Gate

- [x] Make `uv run pyright admin` clean.
- [x] Define stable boundary targets for M1-M6.
- [x] Add one executable gate used by local/CI automation.
- [x] Prove the gate fails for an injected boundary type error and passes after restore.
- [x] Keep full-repo legacy debt visible but non-blocking.
- [x] Final gate covers 34 targets with 0 errors and 0 warnings.

## Integration And Deployment

- [x] Run focused RED/GREEN tests per slice.
- [x] Run expanded storage/application/admin regressions.
- [x] Run targeted Ruff and boundary Pyright 0.
- [x] Run full pytest at final integration (`3108 passed / 17 skipped`).
- [x] Complete code/lifecycle independent review with no unresolved
  Critical/Important/Minor findings.
- [x] Run `git stash list`, `git status -uno`, and
  `git ls-files --others --exclude-standard` before deployment.
- [x] Record the governed databases' current v1 / `adopted=1` / semantic-contract state and current domain counts; explicitly record that exact live production pre-migration counts and a pre-adoption named-volume snapshot were not captured.
- [x] Build the frontend into `admin/static` and verify every direct index asset.
- [x] Create M3-M6 rollback tag `omubot-bot:pre-midterm-m3m6-20260713` for image `91e8a36c6713`.
- [x] Record explicit SHA256 manifests for production source and frontend
  assets; exclude dynamic tool DB/WAL/log/pid inputs from Docker context.
- [x] Build and force-recreate only qq-bot.
- [x] Use exactly `docker compose build bot` followed by
  `docker compose up -d --no-deps --force-recreate bot`.
- [x] Verify startup, OneBot, capture, outbound guard, database/task Admin APIs.
- [x] Verify governed DBs are v1 with `adopted=1`, ledger/schema semantics are healthy, and adoption is metadata-only; do not claim exact pre/post row-count equality because the live pre-count snapshot is absent.
- [x] Verify public silent groups have zero successful outbound sends in the fixed post-deployment acceptance window.
- [x] Verify NapCat identity, StartedAt, and restart count are unchanged.
- [x] Record rollback/release image tags and runtime identities.

## Historical Evidence Exception

- The pre-deploy backup `pre-change-20260713-140943` was taken from host `storage/`, not the production Docker named volume. It is retained only as a host-checkout artifact and must not be presented as the production rollback payload.
- Exact live production row counts and a v0 named-volume snapshot immediately before adoption were not captured. This cannot be reconstructed honestly after the fact, so the checklist closes on the stronger evidence that remains available: the adoption migration is metadata-only by implementation and tests; the live databases now report v1, `adopted=1`, and valid semantic contracts; current domain counts are readable; and the post-adoption v1 named-volume backup `pre-change-20260713-173655` passes quick-check and current-code restore preflight. It does not provide a data downgrade to v0.
