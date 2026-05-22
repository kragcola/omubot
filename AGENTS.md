# AGENTS.md

## Local Workspace Rule

This repository is developed on a macOS external disk formatted as APFS. The
repository checkout itself is the workspace — there is no separate sparseimage
or mount step.

Before running uv or pytest, source the env helper so caches stay inside the
repository:

```bash
source ./scripts/dev/env.sh
./scripts/dev/doctor.sh
```

Expected healthy state:

- filesystem: `apfs` or `hfs`, not `exfat`
- `.venv`: real directory inside the repo, not a symlink
- `UV_CACHE_DIR`: repo-local `.cache/uv`
- `PIP_CACHE_DIR`: repo-local `.cache/pip`
- AppleDouble files (`._*`) should be cleaned before committing

If dependencies need to be rebuilt:

```bash
./scripts/dev/bootstrap.sh
source ./scripts/dev/env.sh
./scripts/dev/doctor.sh
```

## Common Commands

```bash
uv sync
uv run ruff check
uv run pytest
uv run pyright
```

For local development on this machine, run `source ./scripts/dev/env.sh` before
`uv` or `pytest` so caches stay inside the repository.
