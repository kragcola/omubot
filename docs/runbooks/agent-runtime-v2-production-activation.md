# Agent Runtime v2 Production Activation

## Scope

This runbook activates at most one governed Agent Runtime v2 worker. It does
not authorize QZone live delivery, change `BUILTIN_WIRE_PROFILE.validated`,
create a source database, replay an `unknown` call, or recreate NapCat.

The production image is allowed to start a worker only when the configured
profile is enabled, the five independent source/backup checks pass, and the
profile-bound `agent_runtime_rollout_attestation.v1` manifest marks every gate
ready. A missing artifact is a hard stop, not a condition to fill with a test
fixture.

## Required Inputs

| Input | Required evidence |
| --- | --- |
| Runtime v2, Memory v1, Worldbook v1, Operator v1, Invocation v2 | Five distinct `storage/` SQLite files; `PRAGMA user_version` values `2, 1, 1, 1, 2`; each file passes `quick_check`. |
| Backups | One frozen backup per source, SHA-256 over the exact payload, and a real restore rehearsal result for each source. |
| Rollback | A real rollback rehearsal that disables the worker, preserves `unknown`/`dispatching` calls for manual reconciliation, and proves the dark image/config restoration path. |
| Operator authority | A named operator, secret credential held outside Git, exact non-wildcard principal scopes and target refs, and store-backed ACL entries. Browser session tokens are not principals. |
| Trusted ingress | A witnessed OneBot trigger-to-persisted-invocation path, exact registry generation, ToolRegistry and LLMClient wiring for one canary target. |
| Worldbook | Authoritative reread, reducer verification, single-world/no-dual-truth witness, tied to the selected source. |
| Provider boundary | Every canary-enabled provider cooperates with `CancelledError` and completes bounded cleanup; the execution fence deliberately serializes provider calls, so `ToolConcurrency.PARALLEL` does not authorize parallel provider execution here. |
| Deployment input | One unique `worker_id`, `max_workers = 1`, one canary target, pinned image input, and an operator-approved maintenance window. |

The source names and expected schemas are fixed:

| Source | Schema |
| --- | --- |
| `runtime` | 2 |
| `memory` | 1 |
| `worldbook` | 1 |
| `operator` | 1 |
| `invocation` | 2 |

## Manifest Gates

The manifest must have the exact profile fingerprint and all of these gates at
`ready`; evidence references stay opaque and are not written to logs.

Activation: `production_principal_construction`, `trusted_invocation_persistence`,
`trusted_invocation_reconstruction`, `operator_identity`,
`operator_authentication`, `production_runtime_source_path`,
`production_memory_source_path`, `llm_client_wiring`, `tool_registry_wiring`,
`bootstrap_wiring`, `single_worker_gate`, `exclusive_recovery_gate`,
`worldbook_governance_source_path`, `worldbook_single_world_gate`,
`worldbook_authoritative_reread`, `worldbook_reducer_verifier`, and
`worldbook_no_dual_truth`.

Rollback: `dark_code_removal`, `production_database_rollback`,
`production_migration_rollback`, and `production_runtime_wiring_rollback`.

## Preflight

Do not edit production configuration merely to make a report green. The named
operator first writes only real, reviewed inputs according to the commented
template in `config.example.toml`.

```bash
source ./scripts/dev/env.sh
PYTHONDONTWRITEBYTECODE=1 uv run python tools/agent_runtime_preflight.py \
  --config config/config.toml \
  --repo-root .
```

Exit `0` is necessary but not sufficient for activation. The command opens
SQLite sources read-only, computes backup hashes, and validates the pinned
manifest. It never initializes a Runtime store, starts a worker, sends an
external request, or prints credentials, source paths, targets, or evidence
references.

Before deployment, record the report, a separate restore rehearsal transcript,
the named operator approval, the exact canary target, and the current rollback
image tag in the active tracker. Re-run the preflight immediately before build
and immediately before enabling the worker.

## Single-Target Canary

Only after the complete artifact set is independently verified:

1. Confirm `git stash list`, `git status -uno`, and untracked release inputs.
   Build only from the isolated release worktree, never from unrelated user WIP.
2. Verify `agent_runtime.enabled = true`, exact `worker_id`, one explicit
   canary target, and the manifest SHA-256 in the reviewed configuration.
3. Build and replace the `bot` service only. Do not run `docker compose down`.

```bash
dot_clean .
docker compose build bot
docker compose up -d --no-deps --force-recreate bot
docker compose ps
```

4. Confirm the process acquired exactly one lease, the governed dispatcher is
   selected, and a trusted canary invocation reaches only the allowed target.
   Check the durable call state without replaying any effect. A lease loss must
   return `worker_not_ready`; no legacy tool fallback is acceptable. Verify the
   provider honors cancellation at its declared timeout: an async provider that
   suppresses cancellation can outlive its lease, which is not an acceptable
   canary contract.
5. Hold the canary until the operator verifies source health, Admin read-only
   visibility, LLM/registry generation, and zero unauthorized outgoing calls.
   Only then consider a separate scope expansion approval.

## Rollback

Stop activation before touching an image. Set `agent_runtime.enabled = false`
in the reviewed configuration, then replace only the bot service:

```bash
docker compose up -d --no-deps --force-recreate --no-build bot
docker compose ps
```

If the code image itself must be reverted, restore
`omubot-bot:pre-agent-runtime-v2-dark-20260814` as the bot image input and use
the same bot-only replacement. Preserve every `dispatching` or `unknown` call
for explicit reconciliation; never retry it automatically. NapCat is neither
restarted nor recreated in any activation or rollback path.
