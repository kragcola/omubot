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
| Rebuild bot for code/dependency changes | `dot_clean . && docker compose up bot -d --build` |
| Admin Dashboard | `http://localhost:8081/admin/` |
| Build and deploy | `./scripts/deploy.sh` |

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
- **D7 Git hygiene**: before deploy/build/merge, check `git stash list` and
  `git status -uno`; never rely on `stash apply` exit code alone.

## Skill Trigger

For Omubot-specific work involving admin/frontend, admin routes, docs/wiki,
maintenance notes, services, plugins, or incremental project changes, use the
`omubot-admin-console` skill first. Follow `docs/agent-ui-guidelines.md` and
`docs/admin-ui-style-guide.md` for admin UI work.

The skill bodies are mirrored in:

- `.agents/skills/omubot-admin-console/`
- `.claude/skills/omubot-admin-console/`

## Maintenance Log

Update `maintenance-log.md` in the same turn when a task creates a durable
project change, including:

- deployment, runtime, config, routing, API, or storage behavior changes
- admin/frontend milestone progress
- process, docs, skill, hook, or workflow changes future agents depend on

Entries should be Chinese, reverse chronological, and include change type,
content, impact scope, and handoff or rollback notes when relevant.
