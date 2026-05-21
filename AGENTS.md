# AGENTS.md

## Local Workspace Rule

This repository is developed on a macOS external disk. The original checkout at
`/Volumes/我的电脑/omubot` lives on `exFAT`, which is not the active development
workspace anymore.

Use the mounted image workspace as the primary development root:

```bash
cd "$HOME/OmubotWorkspace/omubot"
source ./scripts/dev/env.sh
./scripts/dev/doctor.sh
```

Expected healthy state:

- filesystem: `hfs` or `apfs`, not `exfat`
- `.venv`: real directory inside the repo, not a symlink
- `UV_CACHE_DIR`: repo-local `.cache/uv`
- `PIP_CACHE_DIR`: repo-local `.cache/pip`
- AppleDouble files (`._*`) should be cleaned before committing

If the workspace image is not mounted, mount it from the external-disk checkout:

```bash
cd '/Volumes/我的电脑/omubot'
OMUBOT_WORKSPACE_FS='JHFS+' \
OMUBOT_WORKSPACE_BUNDLE='/Volumes/我的电脑/omubot/.workspace/OmubotWorkspace.sparseimage' \
OMUBOT_WORKSPACE_MOUNT_POINT="$HOME/OmubotWorkspace" \
./scripts/dev/mount-workspace.sh
```

This machine uses `JHFS+` because APFS sparse images failed to mount reliably
from the `exFAT` external disk. Keep using the HFS+ image unless the disk setup
is intentionally changed.

If dependencies need to be rebuilt:

```bash
cd "$HOME/OmubotWorkspace/omubot"
./scripts/dev/bootstrap.sh
source ./scripts/dev/env.sh
./scripts/dev/doctor.sh
```

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
  "$HOME/OmubotWorkspace/omubot/"
```

Do not use `/Volumes/我的电脑/omubot` for normal local test runs unless the task is
specifically about the external-disk staging checkout.

## Common Commands

```bash
uv sync
uv run ruff check
uv run pytest
uv run pyright
```

For local development on this machine, run `source ./scripts/dev/env.sh` before
`uv` or `pytest` so caches stay inside the repository.
