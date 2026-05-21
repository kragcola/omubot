#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: scripts/dev/bootstrap.sh [--recreate] [--python <version>] [--no-frozen]

Prepare a repo-local Python environment and repo-local uv/pip caches for macOS
external-disk development.

Options:
  --recreate          Remove the repo-local .venv directory before creating it.
  --python <version>  Python version passed to uv venv. Default: 3.12
  --no-frozen         Use `uv sync` instead of `uv sync --frozen`.
  -h, --help          Show this help text.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_VERSION="${OMUBOT_PYTHON_VERSION:-3.12}"
RECREATE=0
FROZEN=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recreate)
      RECREATE=1
      shift
      ;;
    --python)
      if [[ $# -lt 2 ]]; then
        echo "[bootstrap] --python requires a version argument." >&2
        exit 1
      fi
      PYTHON_VERSION="$2"
      shift 2
      ;;
    --no-frozen)
      FROZEN=0
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "[bootstrap] Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "[bootstrap] uv is required. Install it first: https://github.com/astral-sh/uv" >&2
  exit 1
fi

. "$SCRIPT_DIR/env.sh"
mkdir -p "$UV_CACHE_DIR" "$PIP_CACHE_DIR"

if [[ -L "$ROOT_DIR/.venv" ]]; then
  BACKUP_PATH="$ROOT_DIR/.venv.external-link.bak.$(date +%Y%m%d%H%M%S)"
  mv "$ROOT_DIR/.venv" "$BACKUP_PATH"
  echo "[bootstrap] Backed up external .venv symlink to $BACKUP_PATH"
fi

if [[ "$RECREATE" == "1" && -d "$ROOT_DIR/.venv" ]]; then
  rm -rf "$ROOT_DIR/.venv"
fi

if [[ -e "$ROOT_DIR/.venv" && ! -d "$ROOT_DIR/.venv" ]]; then
  echo "[bootstrap] .venv exists but is not a directory." >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  echo "[bootstrap] Creating repo-local .venv with Python $PYTHON_VERSION"
  uv venv --python "$PYTHON_VERSION" "$ROOT_DIR/.venv"
fi

SYNC_ARGS=(sync)
if [[ "$FROZEN" == "1" ]]; then
  SYNC_ARGS+=(--frozen)
fi

echo "[bootstrap] Using UV_CACHE_DIR=$UV_CACHE_DIR"
echo "[bootstrap] Using PIP_CACHE_DIR=$PIP_CACHE_DIR"
echo "[bootstrap] Running: uv ${SYNC_ARGS[*]}"
(
  cd "$ROOT_DIR"
  uv "${SYNC_ARGS[@]}"
)

"$ROOT_DIR/.venv/bin/python" -V
echo "[bootstrap] Done. Recommended next steps:"
echo "  source \"$ROOT_DIR/scripts/dev/env.sh\""
echo "  ./scripts/dev/doctor.sh"
