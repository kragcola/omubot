# AGENTS.md

## Local Workspace Rule

This repository is developed on a macOS external disk. The active development
workspace on this machine is:

```bash
cd /Volumes/OmubotDisk/omubot
source ./scripts/dev/env.sh
bash ./scripts/dev/doctor.sh
```

Expected healthy state:

- filesystem: `apfs` or `hfs`, not `exfat`
- `.venv`: real directory inside the repo, not a symlink
- `UV_CACHE_DIR`: repo-local `.cache/uv`
- `PIP_CACHE_DIR`: repo-local `.cache/pip`
- AppleDouble files (`._*`) should be cleaned before committing

The old paths `$HOME/OmubotWorkspace/omubot` and `/Volumes/我的电脑/omubot`
are not the active development workspace anymore. Do not use them for normal
local test runs unless the task is specifically about those staging checkouts.

If an agent is sandboxed to `/Volumes/我的电脑/omubot`, it may edit that checkout
as a staging copy, but it must tell the user to sync changes into the active
workspace:

```bash
rsync -a \
  --exclude '.venv' \
  --exclude '.cache' \
  --exclude '.pytest_cache' \
  --exclude '.ruff_cache' \
  --exclude '.workspace' \
  --exclude '.workspace_mount' \
  '/Volumes/我的电脑/omubot/' \
  '/Volumes/OmubotDisk/omubot/'
```

## Common Commands

Run `source ./scripts/dev/env.sh` before `uv` or `pytest` so caches stay inside
the repository.

```bash
uv sync
uv run ruff check
uv run pytest
uv run pyright
```

| Task | Command |
|------|---------|
| Run locally | `docker compose up napcat -d && uv run python bot.py` |
| Run all in Docker | `docker compose up -d` |
| Restart bot for config-only changes | `docker compose restart bot` |
| Rebuild bot for code/dependency changes | `dot_clean . && docker compose build bot && docker compose up -d --no-deps --force-recreate bot` |
| Admin Dashboard | `http://localhost:8081/admin/` |
| Build and deploy | `cd admin/frontend && npm run build && cd ../.. && docker compose build bot && docker compose up -d --no-deps --force-recreate bot` |

## NapCat Red Line

Always use `docker compose restart napcat` for NapCat restarts. Never use
`docker compose down` + `up`, never recreate the NapCat container, and never
change its storage layout unless the task is explicitly about NapCat recovery.
NapCat stores the QQ device fingerprint and login state; recreating it can
trigger Tencent anti-fraud and force re-login.

## Omubot Discipline

- **D1 Same-pattern scan**: when fixing a bug, grep for similar code paths and
  record what was checked in `maintenance-log.md`.
- **D2 Cancel-path tests**: code that may be wrapped by `wait_for` or cancelled
  during shutdown needs a regression test that simulates cancellation and checks
  observable state is not polluted.
- **D3 Migration checklist**: broad refactors need an old-to-new migration
  checklist in `docs/migrations/`.
- **D4 Evidence-based completion**: a fix is not complete without evidence:
  same-pattern scan, externally observable verification, and rollback path.
- **D5 Pytest hygiene**: before full pytest runs, clear orphan pytest workers if
  needed; stale workers can hold SQLite locks.
- **D6 Admin SPA path**: `admin/static` is a bind mount. Frontend-only changes
  need `npm run build`, not a bot rebuild; Python changes need a bot rebuild.
- **D7 Git hygiene**: before deploy/build/merge, check `git stash list`,
  `git status -uno`, and `git ls-files --others --exclude-standard`; never rely
  on `stash apply` exit code alone or let `-uno` hide untracked build inputs.

## Token-Aware Parallel Workflow

- For every non-trivial task, run a lightweight parallel-fit check inline. The
  compact rules here are sufficient for normal fit and dispatch; do not load an
  additional orchestration skill merely to evaluate or run parallel work. Load
  one only when the user explicitly requests that workflow.
- Delegate only when the user explicitly requests agents/delegation and at least
  two bounded streams can reduce wall-clock time while progressing independently
  without sharing a mutable conflict domain or requiring immediate sequential
  results. Otherwise stay single-agent and use parallel tool calls for independent
  reads or shell checks.
- Model conflicts beyond filenames: symbols/modules, APIs/types/schemas,
  generated outputs/lockfiles/fixtures, databases/ports/caches/test namespaces,
  uncommitted WIP, and ordering dependencies.
- Codex retains planning, architecture, sequencing, decisions, integration,
  acceptance, and the final answer. Count the main agent, Codex workers,
  top-level Grok processes, and Grok children against one global budget. Start
  with at most two delegated workers across that budget by default; exceed that
  default only when the user explicitly requests a larger safe team. Keep the
  main agent on the critical path.
- Give each stream a stable id, one objective, ownership/conflict boundaries,
  dependencies, base revision, isolation mode, allowed/forbidden actions,
  acceptance evidence, and delivery artifact. Default to `fork_turns="none"`
  with a complete prompt; use a small positive fork only when recent conversation
  state is essential, and never use `fork_turns="all"` without explicit user
  authorization.
- Keep one canonical target per stream. Use one writer per conflict domain and
  one owner for generated files, lockfiles, fixtures, migrations, and schemas.
  Concurrent writers use isolated worktrees; shared-workspace workers must be
  read-only or disjoint across every conflict domain. Preserve user WIP: never
  clean, reset, stash, or commit it merely to enable delegation.
- For write-capable, hybrid, multi-step, long-running, or interruption-prone
  runs, keep a persistent ledger and require delivery manifests with outcome,
  base/artifact location, changed files, conflict domains, exact checks, risks,
  and integration notes. Integrate in dependency order, inspect the combined
  diff, and run the smallest meaningful cross-stream verification.
- User requests to avoid delegation always win.

### Grok and recovery

- Use `dispatch-grok` only after explicit Grok authorization. Cost-saving mode
  also requires an explicit cost/token-saving request. Explicit Grok parallelism
  sets `parallel requirement: required`: require a real child id, distinct
  bounded assignment, observable activity, and valid isolation at the first
  natural checkpoint and completion. Zero children is an unmet contract; never
  fabricate a child or silently downgrade to optional.
- Ordinary Grok dispatch defaults Codex subagents to zero. A mixed Codex/Grok
  team requires explicit mixed-team authorization and the shared conflict graph
  and concurrency budget above. Codex independently validates Grok's diff,
  structured evidence, checks, and manifest.
- Preserve delegated scope across interruptions and never silently move required
  scope back to the main agent. Resume the canonical Codex target with
  `followup_task`, or the same Grok process, from its checkpoint. If the target is
  closed, deleted, missing, unknown, or reconnect fails and it is not running,
  visibly mark `rebuilding` and immediately create one replacement with the same
  scope, conflicts, base, isolation, and checkpoint. Only one reconnect or
  replacement may be active for a stream.
- For transient `429`, timeout, or transport failures, use token-light,
  non-blocking backoff from 15 to 60 seconds. Start one continuous outage window
  at the first failure; acknowledgements are not progress, and only concrete
  progress resets the window. Continue recovery until 30 continuous minutes pass
  without concrete progress, then preserve artifacts and mark
  `failed_after_30m`. Stop immediately on explicit quota, billing,
  authentication, policy, cancellation, or permission failures. Keep
  `reconnecting`, `rebuilding`, replacement, and terminal states visible, and
  never finish while a required stream remains active or recovering.

## Local Environment Notes

- **Read-only inspection of live SQLite DBs**: a plain writable open can
  contend for locks, create sidecars, or change connection state. Use
  `sqlite3 'file:storage/<db>.db?mode=ro' '<query>'` so committed WAL state is
  included, and count the main DB plus `-wal`, `-shm`, and `-journal` when
  reporting capacity. Do not add `immutable=1` for a live DB: it bypasses
  normal locking/change detection and can miss active WAL state. Use
  `mode=ro&immutable=1` only for an offline, unchanging backup payload known
  not to require WAL replay. Inspect schema, `PRAGMA user_version`, indexes,
  and `PRAGMA quick_check` before ad hoc SELECTs. Never write while the service
  is running.
- **Process probing in the macOS sandbox**: `pgrep` and some `ps` calls fail
  with `sysmond service not found` or permission errors. Prefer
  `docker compose ps`, container logs, pidfiles, or `lsof -nP -iTCP:<port>`.

## Atomic Writes + Self-Verification

Dependent git/write operations occasionally lose an intermediate step when split
across separate tool calls (e.g. a `git add` that does not take effect, leaving
the index empty so the following `git commit` exits with `no changes added` and
HEAD never moves). Do not trust a tool's "success" wording — verify external
state.

- **Chain dependent git/write ops into one bash call**:
  `git add X Y && git commit -m '…' && git log --oneline -1`. Do not split them
  into separate tool calls.
- **Print verification evidence in the same command**: `git log --oneline -1`
  after commit, `ls -l` / `wc -l` after writing a file, `git diff --stat`. Read
  the real result from that output, not from "tool succeeded".
- **Before claiming "committed" / "written", require external evidence**: HEAD
  hash actually changed, file actually on disk, index matches expectation (D4).
- **Stop after two failures of the same local atomic action** — switch approach
  (atomic bash) or report. Delegated transient `429`, timeout, and transport
  recovery follows the continuous 30-minute policy above.

## Skill Trigger

For Omubot-specific work involving admin/frontend, admin routes, docs/wiki,
maintenance notes, services, plugins, or incremental project changes, use the
`omubot-admin-console` skill first. Follow `docs/agent-ui-guidelines.md` and
`docs/admin-ui-style-guide.md` for admin UI work.

Use `omubot-deep-delivery` when any of these are true:

- the user criticizes reasoning depth, initiative, or verification quality
- the task asks to search the web or depends on current/upstream facts
- the task enrolls/builds datasets or character packs
- the task touches agent prompts, skills, hooks, or cross-agent workflow rules
- the task is production-facing and needs research, dry-runs, runtime checks,
  collision checks, and rollback notes

For these tasks, do not finish with only static checks. Include structural,
semantic, runtime, and collision/negative verification where relevant.

The skill bodies are mirrored in:

- `.agents/skills/omubot-admin-console/`
- `.agents/skills/omubot-deep-delivery/`
- `.agents/skills/omubot-continuity/`
- `.claude/skills/omubot-admin-console/`
- `.claude/skills/omubot-deep-delivery/`
- `.claude/skills/omubot-continuity/`

## Continuity Rule

For long-running, resumed, or compaction-sensitive work, use the
`omubot-continuity` skill. First read `.workspace/agent-session-state.md` if it
exists, then `docs/tracking/ACTIVE.md`, then the active tracker named there, then
`git status --short`. Continue from the tracker `next_step` instead of
rediscovering the repository from scratch.

Create or update an active tracker for work that spans sessions, touches 3+
files, involves production/runtime/skills/hooks/prompts, or requires a bug test
ledger. Keep `maintenance-log.md` for durable completed changes, not live todo.
An active tracker may double as the persistent parallel run ledger only when it
records the run/workstream ids, accepted base and dirty baseline, global budget,
conflict/ownership/isolation boundaries, status/checkpoints, evidence, and final
delivery manifests. Otherwise use `$CODEX_HOME/state/parallel-runs` and
cross-link it from the tracker; never maintain two competing ledgers.

## Maintenance Log

Update `maintenance-log.md` in the same turn when a task creates a durable
project change, including:

- deployment, runtime, config, routing, API, or storage behavior changes
- admin/frontend milestone progress
- process, docs, skill, hook, or workflow changes future agents depend on

Entries should be Chinese, reverse chronological, and include change type,
content, impact scope, and handoff or rollback notes when relevant.
