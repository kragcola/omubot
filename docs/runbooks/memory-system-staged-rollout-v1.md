# Memory system staged rollout / rollback v1

> Planning and offline verification only. This runbook grants **no deployment
> authorization**. Do not touch live SQLite, Docker, NapCat, credentials, or
> production configuration while following the current task. The documented
> production baseline is `98887a5`; July 16–17 memory changes remain local and
> undeployed.

The machine-readable cumulative flag profile is
`docs/runbooks/memory-system-staged-rollout-v1.json`. Its defaults are for a
future first canary, not a request to rewrite checked-in defaults.

## Why this gate exists

The offline worktree contains several independently accepted changes that would
otherwise arrive together: memo write policy, card TTL eligibility, pack-time
evidence gating, evidence-use instructions, query-aware budgets, TemporalTrace,
graph provenance/observability, and joint prompt telemetry. Most checked-in
worktree defaults are enabled. A naive first deploy would change writes,
retrieval, packing, prompt content, and graph governance in one restart.

The objective is therefore to establish a dark baseline, enable one cumulative
phase at a time, and retain a bot-image rollback for behavior that has no flag.
This is a deployment safety contract, not another memory algorithm.

## Preflight

Before any separately authorized deployment window:

1. Record the exact candidate image/commit and confirm the previous bot image
   for `98887a5` remains available. Do not use `git add -A`; enumerate the
   intended memory files explicitly.
2. Record a configuration snapshot and compare it with every flag in the JSON
   profile. Missing keys are unsafe because current code defaults most of them
   to enabled.
3. Run the rollout contract test, the focused memory suites, scoped Ruff and
   Pyright, `git diff --check`, and a full pytest run. No phase advances on a
   failure or unexplained warning-count change.
4. Prepare the existing governed-store backup/restore plan. This runbook does
   not authorize reading or writing live databases.
5. Freeze numeric latency/error budgets from an explicitly authorized baseline
   window. Do not invent production p95 thresholds from synthetic fixtures.
6. Confirm the operation is **bot-only**. Never restart, recreate, or exec into
   NapCat for a memory rollout.

## Operator flag mapping

The dotted paths in the machine profile are an operator-facing namespace, not
a single configuration object. Materialize them by prefix and validate the
result through the real configuration owners. Do not hand-type a partial list:
an omitted key can fall back to the current `true` default.

| Dotted prefix | Configuration owner | Runtime path / model |
| --- | --- | --- |
| `context.*` | context plugin override | `storage/plugins/config/context.json` → `PluginConfigStore` manifest/schema validation → `ContextConfig` |
| `memo.*` | memo plugin override | `storage/plugins/config/memo.json` → `PluginConfigStore` manifest/schema validation → `MemoConfig` |
| `knowledge_graph.*` | main bot configuration | `config/config.json` (preferred) or `config/config.toml` / `BOT_CONFIG_PATH` → `BotConfig.knowledge_graph` |
| `block_trace.*` | main bot configuration | `config/config.json` (preferred) or `config/config.toml` / `BOT_CONFIG_PATH` → `BotConfig.block_trace` |

Plugin overrides use the canonical per-plugin wrapper. The Stage-0 context
payload is:

```json
{
  "schema_version": 1,
  "plugin": "context",
  "values": {
    "temporal_trace": {"enabled": false},
    "query_aware_plan": {"enabled": false},
    "card_eligibility": {"enabled": false},
    "pack_evidence_gate": {"enabled": false},
    "evidence_use_contract": {
      "enabled": false,
      "inject_constrained_instruction": false
    }
  }
}
```

The Stage-0 memo payload is:

```json
{
  "schema_version": 1,
  "plugin": "memo",
  "values": {"write_policy_enabled": false}
}
```

`PluginConfigStore.set_values()` may add store metadata such as `updated_at`,
but `schema_version`, `plugin`, and `values` are the canonical identity and
payload fields. Read the current saved `values`, deep-merge the Stage slice,
then call `set_values()` with the merged result. Do not replace the whole
override with only the rollout keys: that would erase unrelated operator
settings such as context budgets or memo limits. The nested suffix below each
plugin prefix must be preserved; for example,
`context.temporal_trace.enabled` becomes `values.temporal_trace.enabled`, not a
literal dotted JSON key.

The main Stage-0 payload must keep the two non-plugin roots in the bot config:

```json
{
  "knowledge_graph": {
    "provenance_gate_enabled": true,
    "observability_enabled": false
  },
  "block_trace": {
    "joint_dual_path_telemetry_enabled": false
  }
}
```

Before any authorized restart, deep-merge and write plugin overrides through
`PluginConfigStore`, read the canonical files back, validate the effective
values with `ContextConfig` / `MemoConfig`, and validate the main payload with
`BotConfig`. The rollout contract test performs the same mapping and
preservation check against the real manifests, schemas, runtime plugin loader,
and main configuration models.

## Stage 0 — dark behavior profile

Apply the `stage0_dark` profile only in an authorized future window:

- keep `knowledge_graph.provenance_gate_enabled=true`; disabling it is an
  unsafe legacy escape hatch, not a normal dark-mode control;
- keep memo write policy, TemporalTrace, query planner, card eligibility, pack
  evidence gate, evidence-use contract/instruction, graph observability, and
  joint dual-path telemetry disabled;
- prove the flappable read path is identity with
  `tests/test_memory_staged_rollout_contract.py` and existing per-flag tests;
- smoke startup, store ownership, schema adoption, Admin health, and ordinary
  context packing before enabling any later phase.

Stage 0 is not byte-for-byte `98887a5`: structural safety/correctness slices
without flags are present. Their rollback is the previous bot image, not a
configuration toggle.

## Cumulative stages

| Stage | Newly enabled | Required evidence before advancing |
| --- | --- | --- |
| 1 — observe | graph population observability, joint dual-path telemetry | Admin read models return bounded, privacy-safe shapes; no material health/CTE latency regression |
| 2 — write hygiene | memo conflict-aware write policy | add/reinforce/supersede/skip distribution reviewed; cross-scope or unexpected supersede count remains zero |
| 3 — read eligibility | card category TTL eligibility | expired/malformed exclusion matches sampled expectations; no broad empty-memory spike |
| 4 — pack observe | pack evidence gate; evidence-use contract with instruction off | keep/demote/omit and pack-state distributions stable; no mass omit/empty packs |
| 5 — evidence instruction | evidence-use constrained instruction | no instruction storm; normal nonempty packs remain instruction-free |
| 6 — query planner | query-aware type caps and pack budgets | profile mix is plausible; identity remains dominant for ordinary queries; no mode widening |
| 7 — TemporalTrace | current/earlier/trajectory sidecar | false-premise/history cases improve without overshare, cross-scope evidence, or prompt-budget regression |

Each stage is cumulative and requires a separate observation decision. Do not
skip directly from Stage 0 to Stage 7.

## Structural slices without kill-switches

The following cannot be fully returned to `98887a5` with the JSON profile:

- ConversationArchive composition-root ownership and MessageLogPort wiring;
- Entity Identity, alias registry, and Episode typed refs;
- graph scope window / hub control;
- Episode v2 read-time decay and query rerank;
- atomic KG promotion and applied-outcome accounting.

These are additive or correctness/safety-oriented, but they still require
Stage-0 startup/regression evidence. Their emergency rollback is the pinned
previous bot image plus the migration-specific data notes. Do not delete
additive alias, observation, provenance, or episode data automatically.

## Canary metrics

| Signal | Source | Interpretation |
| --- | --- | --- |
| active graph support / invalid evidence buckets | gpo nested observability | distinguish empty population, blocked writes, and unsupported active facts |
| context / temporal / constrained / episode outcomes | jdt snapshot | verify final prompt-budget survival without exposing request IDs |
| keep / demote / omit and pack state | ContextService recent metrics | detect evidence-gate starvation or instruction storms |
| query profile mix | query-aware plan metrics | detect broad-recall/planner over-triggering |
| add / reinforce / supersede / skip | memo write-policy observations | detect conflict-policy drift and unsafe supersede |
| context, graph-health, and jdt latency | authorized runtime measurements | compare with the frozen preflight budget; synthetic timings cannot pass the gate |
| wrong fact, overshare, false-premise handling | manual canary review | user-facing correctness stop signal |

## Stop conditions

Stop the current stage and do not advance when any of the following occurs:

- cross-group/user evidence, raw identifier leakage, or unexpected factual
  overshare;
- unexplained empty-pack, omit, constrained-instruction, supersede, or graph
  gate-reject spike;
- context/graph-health/jdt latency exceeds the numeric budget frozen in
  Preflight;
- startup/schema/catalog/backup verification fails;
- cancellation is swallowed, a store remains open, or ordinary chat behavior
  cannot be reproduced with the current phase disabled.

## Rollback

Prefer phase reversal plus a **bot-only** restart/recreate:

1. disable TemporalTrace;
2. disable query-aware planning;
3. disable evidence-use instruction, then the evidence-use contract;
4. disable the pack evidence gate;
5. disable card eligibility;
6. disable memo write policy;
7. disable jdt and graph observability if their read cost is the issue.

Keep the graph provenance gate enabled unless an incident commander explicitly
accepts the unsafe legacy write path. Turning it off is not a complete rollback.
For structural slices or any unresolved startup/data-contract failure, restore
the pinned previous bot image. Never recreate NapCat. Never bulk-delete or
rewrite live memory data as part of an automatic rollback.

## NO-GO remains unchanged

- PPR / HippoRAG-style diffusion remains blocked until deployed, sanitized
  population, evidence-quality, latency, and prompt-path evidence exist.
- GraphRAG community summaries remain deferred until graph density and an
  isolated offline indexing budget are proven.
- Full MemGPT/Letta memory-tool loops remain deferred until tool rounds,
  write-policy ownership, and dual-write risks are bounded.
- Official full LongMemEval/LoCoMo CI remains a capacity/licensing/judge job,
  not regular CI and not a reason to tune production ranking for scores.
- Historical big-bang backfill remains forbidden; only bounded audited
  re-promote may be reconsidered.

## Offline verification

```bash
cd /Volumes/OmubotDisk/omubot
source ./scripts/dev/env.sh
export PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}

uv run pytest -q tests/test_memory_staged_rollout_contract.py
uv run pytest -q \
  tests/test_context_query_plan.py \
  tests/test_card_eligibility.py \
  tests/test_context_pack_evidence_gate.py \
  tests/test_context_evidence_use_contract.py \
  tests/test_temporal_trace.py \
  tests/test_memo_extractor_write_policy.py \
  tests/test_graph_provenance_gate.py \
  tests/test_graph_population_observability.py \
  tests/test_joint_dual_path_telemetry.py
```

Passing this runbook contract proves only offline configuration and test
coherence. It does not authorize or prove a production canary.
