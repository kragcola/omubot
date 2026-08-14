# Agent Runtime v2 Migration and Rollback

## Purpose

Upgrade Omubot from an in-process conversational tool loop to a governed Agent
Runtime without replacing PluginBus, SQLite, OneBot, Worldbook, or the existing
domain stores.

## Old to New Ownership

| Current owner | Current responsibility | Target owner |
| --- | --- | --- |
| `kernel.types.Tool` | Tool name/schema/execute | Tool ABI v2 contract, still kernel-owned |
| `services.tools.ToolRegistry` | Registration + direct execution | ToolCatalog only; Policy/Executor own calls |
| `LLMClient.chat` | Model loop + tool execution + reply finalization | LLMClient keeps model transport; AgentRunCoordinator owns loop/checkpoints |
| Plugin manifest/group config | Tool exposure filters | CapabilityPlanner input plus execution-time Policy input |
| Memo/Compaction | Direct CardStore writes | Observation/Candidate producers |
| Card/Episode/Graph stores | Mixed source-of-truth and prompt data | Governed materialized views with evidence refs |
| Schedule legacy StoryArc mutation | Direct state update | Worldbook Event proposal/commit/reducer |
| QZone Journal outbox | QZone-only durable delivery | Retained domain state machine behind generic external-effect adapter |
| ProtocolTrace/Usage | Partial in-memory/aggregate diagnostics | Durable run/model/policy/tool/outbox spans with redaction |

## P0 Compatibility Rules

- Existing Tool subclasses and OpenAI-format tool definitions remain unchanged.
- ABI v2 metadata defaults to `legacy_unclassified`, not a permissive effect.
- The new ledger is not initialized by bot startup and is not read by LLMClient.
- Tests use temporary SQLite paths only.
- No existing storage schema is migrated in place during P0.
- The v1 ledger migration uses a content SHA-256 and verifies required column
  attributes, foreign keys, and index ownership/order on every reopen.
- Terminal Runs reject late ToolCalls. Run cancellation/failure atomically closes
  pre-dispatch calls and marks dispatching calls `unknown`; a Run cannot succeed
  while calls remain open.
- `required` and `provider_supported` idempotency modes require a persisted key
  digest before a ToolCall can be proposed.

## P1 Dark Runtime Rules

- Ledger schema v2 adds a closed-set `concurrency_mode` snapshot and the
  `(concurrency_mode, concurrency_key, status, lease_until)` occupancy index.
  Existing v1 rows upgrade additively to the fail-closed `global_serial`
  default; production storage is still not initialized.
- Claim-time occupancy is checked inside the same `BEGIN IMMEDIATE` transaction
  as the status CAS. `global_serial` shares one global lane, `keyed_serial`
  conflicts only on the same durable key, and `parallel` does not conflict.
- Every non-cancellation transition out of `claimed` or `dispatching` must
  present the current `lease_owner`. A reaped and reclaimed call therefore
  rejects writes from its stale worker.
- Live recovery only reaps expired leases: expired `claimed` becomes `ready`;
  expired `dispatching` becomes terminal `unknown`. Whole-process recovery of
  every incomplete call requires an explicit `exclusive_startup=True` claim.
- Policy is evaluated before queueing and again after the execution slot is
  acquired. Approval expiry and live registry-generation changes therefore
  fail closed before dispatch.
- The coordinator supports dark message/tick/domain-event/recovery Runs,
  approval resume on the same ToolCall, retry resume on the same non-external
  ToolCall, and `waiting_approval|waiting_retry|waiting_external` Run states.

## Planned Runtime State

```text
Run: queued -> running -> waiting_approval|waiting_retry|waiting_external
     -> succeeded|failed|cancelled

ToolCall: proposed -> denied
        | approval_pending -> ready
        | ready -> claimed -> dispatching
        | ready -> failed_retryable
        -> succeeded|failed_retryable|failed_terminal|unknown|cancelled
```

The append-only event stream is authoritative. Snapshot rows accelerate reads
but never erase transitions. An external action that may have left the process
but lacks a verified provider receipt is `unknown`, not retryable.

## P2 QZone Adapter Rules

- The governed tool wraps `JournalDelivery`, never the raw QZone transport.
- A runtime-provided `qzd_[0-9a-f]{24}` allowlist binds the exact durable target
  `qzone:draft:<draft_id>:publish` before a ToolCall is created.
- Publication is `external_irreversible`, always approved, reconcile-only,
  never retried automatically, and serialized by the durable draft target.
- Credential/store/gate failures before claim are terminal pre-dispatch
  failures. A provider call, committed claim, unverified response, or local
  publish finalization failure is ambiguous and must become `unknown`.
- Claim-state inspection and `mark_unknown` cleanup are shielded through
  repeated cancellation. Cancellation is propagated only after the durable
  QZone and Runtime states are safe; cancellation is never erased with
  `uncancel()`.
- Transport exception text is not persisted or returned to the model. Only a
  bounded exception type/reason code crosses the delivery boundary.
- This adapter remains unregistered and unwired in production. The built-in
  wire profile remains `validated=false`; no live publish is enabled by P2.

## P2 Dynamic OneBot Target Rules

- `react_to_message` accepts only canonical runtime-provided refs shaped as
  `onebot:group|user:<id>:message:<message_id>`. The model still selects an
  emoji, but the durable target binds the trusted message ref and normalized
  numeric emoji before ToolCall creation.
- Group reactions call the same live group-policy assertion installed by
  `OutboundGroupAccessGuard` immediately before provider dispatch. A missing
  guard or policy denial is a proven pre-dispatch terminal failure; provider
  exceptions remain `unknown` and are never retried automatically.
- `send_sticker(intent=...)` performs semantic search exactly once during the
  initial binding. The resolved sticker ID and trusted group/user recipient are
  persisted in `ToolCall.target_ref`; approval resume and execute reuse that
  snapshot and never rerun intent search.
- `ToolContext.target_ref` is runtime-owned. The coordinator exposes an existing
  target only while verifying a resumed binding, and the executor copies the
  ledger target into the execution context. Caller target assertions never
  become this durable value.
- Sticker file/segment preparation failures are terminal pre-dispatch failures.
  A OneBot send exception or cancellation after invocation starts is `unknown`;
  no send-count/recent-state success projection is written.
- Reaction and sticker remain dark: production trigger/principal construction,
  registry/LLMClient wiring and reconciliation UI are not enabled by these
  contracts.

## P2 Reconciliation Rules

- `unknown` is an immutable observed ToolCall outcome. Manual resolution never
  rewrites it to `succeeded` or `failed_terminal`; it appends exactly one
  `reconciliation_resolved` event with decision, actor, adapter ID, evidence
  reference, normalized-note SHA-256 and optional external ID.
- Only `reconcile_only` calls in a `waiting_external` Run may be resolved. The
  principal needs `runtime:tool:reconcile` plus the exact durable target.
- Opposite decisions race under `BEGIN IMMEDIATE`; one event wins and the other
  receives a semantic conflict. Exact retry returns the existing record before
  any adapter method is called.
- The Run returns to `running` only when every unknown call in that Run has a
  resolution event. No reconciliation path re-executes or retries the original
  tool automatically.
- Domain adapters must be explicitly idempotent and return a receipt matching
  decision, target and evidence. Authorization, existing-event conflict and
  canonical input validation occur before adapter invocation.
- QZone reconciliation invokes the existing idempotent domain store first:
  confirmed success becomes `published` with an external post ID; confirmed
  not-applied becomes `approved`. Runtime truth is appended only after the
  returned draft ID/status is verified.
- OneBot has no generic provider receipt query. Its adapter records only an
  operator attestation for external OneBot targets owned by group admin,
  interaction or sticker tools. It does not call QQ/NapCat or replay the tool.
- The event table supplies this append-only contract without a schema upgrade;
  Agent Runtime remains SQLite schema v2 and production DB creation stays off.

## Data Migration Strategy

1. Create the Agent Runtime DB only when P1 wiring is explicitly enabled.
2. Register ToolSpec v2 metadata for every tool before permitting it through the
   new executor; unknown effect classes fail closed.
3. Shadow-write observations from ConversationArchive before changing prompt
   retrieval or CardStore writes.
4. Backfill only evidence-backed identity/time/visibility fields. Keep legacy
   records without evidence quarantined.
5. Build Graph and Story projections from authoritative events; never bulk-copy
   a projection into a source-of-truth store.

## P4 Worldbook Governance Rules

- `WorldRefV1` is explicit in the new dark contract only. Existing StoryArc,
  Life, partner, Dream proposal and plugin JSON remain byte/schema compatible.
  A legacy StoryArc snapshot can prove only `omubot.default`; any explicit
  conflicting world fails closed.
- Schedule and Social sources use separate immutable binding contracts. The
  Schedule binding contains canonical date plus summary digest and no evidence.
  The Social binding contains factual experience/group/user/message/source/time
  dimensions but never `user_text`, `bot_reply`, names or offline behavior.
- Event and logical proposal IDs are derived from typed source truth. Producer
  clock changes do not change logical identity; Schedule payload changes do.
- The legacy commit verifier checks exact committed semantics, the real bounded
  reducer history projection, Arc/world/revision and the durable unbounded event
  ID set. Its receipt retains only event/Arc/ID-set proof digests, not mutable
  EventRecord or StoryArc payloads.
- P4 remains pure and dark: no default path, audit DB, plugin callback, runtime
  import, config/gate/ID change, StoryArc mutation or external action. Production
  activation must re-read authoritative Social/Schedule records immediately
  before commit and requires separate authorization.

## P3 Memory Governance Rules

- `ObservationV1` is immutable evidence truth. `CandidateEnvelopeV1` is a
  proposal only and cannot mutate Card/Episode/Style/Slang/Graph stores.
- `ConflictV1` and `PromotionEventV1` use one global append sequence in the
  explicit-path shadow store. Record order decides whether a conflict was known
  at an event; detection time still forbids resolving future conflicts.
- Conflict auxiliary tables are indexes, not truth. Discovery consults both the
  canonical payload and indexes, then requires their exact equality before any
  promotion decision can be appended.
- Store initialization is explicit and single-flight. Close waits for active
  writes; connection setup, rollback and close stay attached through repeated
  cancellation.
- P3 creates no production database, bootstrap hook, prompt projection or
  `LLMClient`/ToolRegistry wiring. Activation requires a separate migration and
  rollout authorization.

## P5 Admin and Rollout Rules

- Runtime, Memory and Worldbook query services accept only caller-owned,
  already-open sources. A missing source reports `source_unavailable`; it never
  discovers a path, creates a directory/database or falls back to `ctx`.
- List APIs use bounded keyset cursors. The Worldbook cursor binds proposal,
  decision and receipt sequence cutoffs so status-filter membership and list
  DTOs remain one snapshot; detail intentionally reports current state.
- Browser POST bodies carry only a server-issued expected token and bounded
  decision evidence. Principal, target, arguments, invocation context and
  Memory conflict IDs are server-owned and cannot be supplied by the browser.
- Memory operator decisions derive the complete current conflict set twice:
  once for the context/token and again in the SQLite write transaction. The
  atomic append outcome distinguishes the first write from an exact retry
  across separate connections without changing the legacy append return type.
- Admin actions append only approval, reconciliation or promotion decisions.
  They never execute a tool, replay an external effect or start a projection.
- Readiness is report-only and attested. Missing evidence is `not_assessed`, not
  a claim that a production resource is absent. A UI status cannot authorize
  activation by itself.

## Production Attestation Contract (Dark Implementation)

- `agent_runtime.attestation.manifest_path` and
  `agent_runtime.attestation.manifest_sha256` are an all-or-nothing pair. Both
  blank preserve only the default `enabled=false` dark state; every enabled
  profile requires the complete digest-pinned pair, and a partial pair is
  invalid. The path must resolve under `storage/`, and the configured SHA-256
  pins the exact JSON bytes before they are parsed.
- The manifest is strict `agent_runtime_rollout_attestation.v1` / schema `1`:
  it contains only `contract_version`, `schema_version`, `profile_fingerprint`,
  `activation`, and `rollback`; `schema_version` must be a JSON integer, not a
  boolean. The fingerprint comes from
  `ProductionActivationProfileV1.attestation_profile_fingerprint()` and binds
  worker identity, principal scopes, exact targets, and all five source paths,
  schema expectations, backup digests, restore references and rollback
  references.
- Every activation and rollback gate must occur exactly once. A `ready` or
  `not_ready` gate carries a bounded reason, a past UTC `evidence_at`, and an
  opaque `evidence_ref`; `not_assessed` carries neither evidence field. The
  reader strips `evidence_ref` before forwarding the result to report-only
  readiness APIs.
- `ProductionRuntimeAssemblyV1.start_worker()` checks dark readiness, fully
  authorized activation readiness, and rollback readiness while holding the
  worker-start lock, then re-runs explicit source/backup preflight immediately
  before acquiring a lease. Missing, malformed, partial, mismatched, cancelled,
  or post-composition-tampered evidence cannot activate the dispatcher or obtain
  a lease. Production composition rejects every external readiness callback;
  only the configured profile-bound manifest can supply activation evidence.
- Worker lease acquisition and renewal materialize an exact candidate token
  before commit. If cancellation or an error reaches the caller after SQLite may
  have committed, cleanup completes rollback plus an exact-token delete before
  propagating the failure; an unknown lease cannot block another worker for its
  TTL.
- This is a verifier and evidence-binding format, not a production artifact.
  No real source path, backup, restore rehearsal, rollback rehearsal or
  Worldbook authoritative-reread witness has been created by this change.

## Future Production Activation Runbook (Not Authorized)

1. Open a new tracker and obtain separate deployment/activation authorization.
2. Freeze explicit Runtime, Memory and Worldbook source paths plus schema,
   backup, restore and rollback evidence; do not derive paths from Admin state.
3. Implement named/scoped operator authentication and store-backed resource ID
   authorization. Browser context tokens remain assertions, never principals.
4. Persist and reconstruct trusted invocation context from authoritative
   trigger records, including exact targets and registry generation.
5. Attest every activation and rollback gate, including single-worker and
   exclusive-recovery ownership, Worldbook authoritative reread/reducer/no-dual-
   truth, production principal, ToolRegistry, LLMClient and bootstrap wiring.
6. Run dark source integrity/readiness and rollback rehearsal before any wiring.
   Any `not_ready` or `not_assessed` gate is a hard stop.
7. Enable one bounded worker only after the full gate set is ready. Do not enable
   QZone live or change `BUILTIN_WIRE_PROFILE.validated` as part of this runbook.
8. Re-run focused, compatibility, static, storage-isolation and external-effect
   negative tests, then require a new independent current-snapshot review.

This document records the future sequence but grants no authority to perform it.

## Rollback

### P0

Revert the new ABI fields/modules/tests and PluginToggle composition change.
There is no runtime or production-data rollback because the new code is dark.

### P1+

1. Disable the Agent Runtime feature gate and return calls to the legacy loop.
2. Stop new ledger/outbox workers; do not delete `unknown` external calls.
3. Reconcile or manually resolve every `dispatching/unknown` call before retry.
4. Keep observation/promotion ledgers for audit; disable their prompt projections.
5. Roll back the bot image only after schema compatibility is checked. NapCat is
   never recreated as part of this rollback.

The current P1 database exists only in temporary tests. If a dark local P1 DB is
created manually, roll back by stopping its workers and deleting that explicit
test DB; never downgrade a v2 file in place to v1.

For the dark QZone slice, remove `services/tools/qzone_journal.py` and restore
the delivery phase/cancellation changes. There is no production Agent Runtime
database or registry wiring to migrate. Never retry an existing QZone
`dispatching`/`unknown` draft as part of code rollback; reconcile it explicitly.

For reaction/sticker rollback, restore their legacy ToolSpecs, remove the live
message-only group assertion and stop passing `ToolCall.target_ref` into tool
contexts. This affects only dark code and temporary test databases. It must not
be used to replay an existing unknown OneBot call.

For P3 rollback, remove `services/memory/governance_contracts.py`,
`candidate_producers.py`, `governance_store.py` and their tests, then restore the
shared `connect_sqlite` cancellation-cleanup hunk if no other caller depends on
it. No production governance database or projection needs deletion or downgrade.

For P4 rollback, remove `services/worldbook/governance_contracts.py`,
`services/worldbook/governed_adapters.py` and
`tests/test_agent_runtime_worldbook_integration.py`. Existing Worldbook,
Schedule, Social, StoryArc/Life/partner and Dream files require no migration or
rollback because no production module imports the dark contracts and no audit
store exists.

For P5 rollback, remove `services/agent_runtime/admin_query.py`,
`admin_actions.py`, `rollout_readiness.py`, the Memory governance query, the
Worldbook governance store/query, the Agent Runtime Admin route/frontend and
their P5 tests. Remove the aggregate-router arguments only after confirming no
caller supplies them. No production DB, worker, source path or data requires
rollback because P5 remained dark and explicitly uninjected.

## Non-Rollbackable Boundaries

- A verified remote external effect cannot be undone by restoring SQLite.
- An ambiguous external effect must be reconciled, not replayed automatically.
- Canon/runtime truth must not be reconstructed from generated story prose.
