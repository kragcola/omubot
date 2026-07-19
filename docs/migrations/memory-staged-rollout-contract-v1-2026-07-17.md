# Memory staged rollout / rollback contract v1

## Status

- Offline control-plane contract only.
- No production code path, schema, data, deployment, configuration, Docker,
  NapCat, live DB, or credential change.
- Production remains documented at `98887a5`; the July 16–17 memory stack is
  local/uncommitted.

## Problem

The accepted offline stack contains multiple default-enabled behavior changes:
memo write policy; Context temporal trace, query planning, card eligibility,
pack evidence gating and evidence-use instruction; KG provenance/observability;
and joint dual-path telemetry. Several structural slices have no runtime flag.
Shipping worktree defaults in one restart would combine write, retrieval, pack,
prompt, and graph changes without an integrated baseline gate.

## Contract

| Artifact | Role |
| --- | --- |
| `docs/runbooks/memory-system-staged-rollout-v1.json` | Closed cumulative ten-flag phase profile |
| `docs/runbooks/memory-system-staged-rollout-v1.md` | Preflight, stages, metrics, stop, rollback, NO-GO |
| `tests/test_memory_staged_rollout_contract.py` | Profile closure/monotonicity, Stage-0 pure identity, real runtime disabled paths, real config-store/loader binding, runbook acceptance |

Stage 0 turns off all flappable prompt/write/read behavior while keeping
`knowledge_graph.provenance_gate_enabled=true`; setting it false is an unsafe
legacy write escape, not a normal dark switch. Later phases enable observability,
write hygiene, read eligibility, pack observability, evidence instruction,
planner, then TemporalTrace.

Structural slices without kill-switches — archive composition, identity/typed
refs, graph window/hub, Episode v2, atomic promotion — require Stage-0 regression
evidence and a previous bot-image rollback. The profile does not pretend they
are identical to `98887a5`.

The operator dotted namespace is split fail-closed by configuration owner:

- `context.*` and `memo.*` become nested plugin `values` stored at
  `storage/plugins/config/<plugin>.json` through `PluginConfigStore`; the
  canonical wrapper is `schema_version=1`, exact plugin identity, and a
  `values` object. Existing saved operator values are deep-merged before write.
- `knowledge_graph.*` and `block_trace.*` remain main bot configuration roots
  and are validated by `BotConfig`; they are not plugin overrides.
- The contract round-trips the real context/memo manifests and schemas, then
  loads the generated overrides through the runtime `load_plugin_config` path.

## TDD and verification

- Initial RED: rollout profile/runbook missing → `3 failed` with explicit
  contract assertions.
- Initial GREEN: new rollout contract → `3 passed`.
- Post-implementation Grok review session
  `717a68ed-b0a4-47be-a3a6-acc361754e94`: **ACCEPT / 0 Critical / 2 Important /
  4 Minor**. The blocking findings were incomplete Stage-0 runtime coverage and
  a handwritten flag vocabulary not bound to real config loading.
- Remediation RED: four runtime/config assertions passed while the missing
  operator mapping kept the module at **1 failed / 4 passed**.
- Remediation GREEN: contract **5 passed**. Stage-0 now proves TemporalTrace
  zero assembler calls/blocks, memo real legacy add-only, gpo pre-gpo shape
  with seeded data and no quality SQL, and jdt exact disabled zero shape with
  no joint SELECT. Config proof uses real canonical plugin overrides,
  manifest/schema validation, runtime plugin loading, `BotConfig`, and
  preservation of unrelated operator values.
- Rollout-related combined suites: `314 passed`.
- Config-store/loader combination: `37 passed`.
- Full repository: `4790 passed / 17 skipped / 186 warnings`.
- Scoped Ruff: clean.
- Targeted Pyright: `0 errors / 0 warnings`.
- JSON parse and `git diff --check`: clean.

Read-only implementation investigation session
`fec176a0-00d4-4fbe-8145-ddec3d8103ee` completed with `grok-exit: 0`; it
confirmed the split config owners and the no-SQL/no-sidecar runtime gates.
Post-fix review sessions `2c5b4191-affa-4d41-805f-e5bbff2af0d4` and
`00ec71e7-8c70-48bd-b39c-a60d0ef66729` ended in transient 524 before a verdict;
the narrower rebuild `9aedda78-378c-4282-832f-3cc84f255baa` completed with
`grok-exit: 0`: **ACCEPT / 0 Critical / 0 Important / 2 Minor**. Both original
Important findings are closed. The two non-blocking residuals are the explicit
no-canary boundary and `PluginConfigStore.set_values()` replacement semantics,
which the runbook/test mitigate by deep-merging saved values before write.

## Rollback

No runtime rollback is required because this package changes only docs/tests.
Remove the JSON profile, runbook, test module, and this migration entry to remove
the offline contract.

For a future authorized deployment, phase rollback is flag-off plus bot-only
restart/recreate. Structural failure restores the previous bot image. Never
recreate NapCat and never automatically delete additive memory/alias/provenance
data. Keep the graph provenance gate on unless an incident commander explicitly
accepts the unsafe legacy path.

## Residual / NO-GO

- This package does not provide deployed canary metrics or authorization.
- Numeric latency/error thresholds must come from an authorized baseline, not
  synthetic fixtures.
- PPR, GraphRAG communities, full MemGPT/Letta tools, official full benchmark
  CI, legacy row repair, meta index, and big-bang backfill remain deferred under
  the tracker reopening conditions.
