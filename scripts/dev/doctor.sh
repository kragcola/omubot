#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: scripts/dev/doctor.sh

Check whether the current repository is using the recommended macOS external-disk
development layout:
  - repo running from an APFS or HFS+ workspace instead of exFAT
  - .venv stored as a real directory inside the repo
  - uv / pip caches redirected into repo-local .cache/
  - no AppleDouble metadata pollution
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOME_DIR="${HOME:-}"

if [[ -f "$SCRIPT_DIR/env.sh" ]]; then
  . "$SCRIPT_DIR/env.sh"
fi

ok() {
  echo "[ok]   $*"
}

warn() {
  echo "[warn] $*"
  WARN_COUNT=$((WARN_COUNT + 1))
}

fail() {
  echo "[fail] $*"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

mount_fs_type() {
  python3 - "$1" <<'PY'
import re
import subprocess
import sys

target = sys.argv[1].rstrip("/") or "/"
best_mount = ""
best_fs = ""
for line in subprocess.run(["mount"], capture_output=True, text=True, check=True).stdout.splitlines():
    match = re.match(r"^.+ on (.+) \(([^,]+),", line)
    if not match:
        continue
    mountpoint, fs_type = match.groups()
    normalized = mountpoint.rstrip("/") or "/"
    if target == normalized or target.startswith(normalized + "/"):
        if len(normalized) > len(best_mount):
            best_mount = normalized
            best_fs = fs_type
print(best_fs)
PY
}

trim_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

WARN_COUNT=0
FAIL_COUNT=0

REPO_FS="$(mount_fs_type "$ROOT_DIR")"
echo "[doctor] repo root: $ROOT_DIR"
echo "[doctor] filesystem: ${REPO_FS:-unknown}"

REPO_FS_LOWER="$(printf '%s' "$REPO_FS" | tr '[:upper:]' '[:lower:]')"
if [[ "$REPO_FS_LOWER" == "apfs" || "$REPO_FS_LOWER" == "hfs" ]]; then
  ok "Repository is running from $REPO_FS."
else
  fail "Repository filesystem is ${REPO_FS:-unknown}; reformat the external disk as APFS (or use an APFS/HFS+ volume)."
fi

if [[ -L "$ROOT_DIR/.venv" ]]; then
  VENV_TARGET="$(trim_path "$ROOT_DIR/.venv")"
  fail ".venv is still a symlink to $VENV_TARGET; rebuild it as a real repo-local directory."
elif [[ -d "$ROOT_DIR/.venv" ]]; then
  ok ".venv is a real directory inside the repo."
elif [[ -e "$ROOT_DIR/.venv" ]]; then
  fail ".venv exists but is not a directory."
else
  warn ".venv does not exist yet; run ./scripts/dev/bootstrap.sh."
fi

EXPECTED_UV_CACHE="$ROOT_DIR/.cache/uv"
EXPECTED_PIP_CACHE="$ROOT_DIR/.cache/pip"
ACTUAL_UV_CACHE="${UV_CACHE_DIR:-}"
if [[ -z "$ACTUAL_UV_CACHE" ]] && command -v uv >/dev/null 2>&1; then
  ACTUAL_UV_CACHE="$(uv cache dir 2>/dev/null || true)"
fi
if [[ -n "$ACTUAL_UV_CACHE" ]]; then
  ACTUAL_UV_CACHE_RESOLVED="$(trim_path "$ACTUAL_UV_CACHE")"
  if [[ "$ACTUAL_UV_CACHE_RESOLVED" == "$EXPECTED_UV_CACHE" ]]; then
    ok "uv cache points to repo-local .cache/uv."
  elif [[ -n "$HOME_DIR" && "$ACTUAL_UV_CACHE_RESOLVED" == "$HOME_DIR"* ]]; then
    fail "uv cache still points outside the repo: $ACTUAL_UV_CACHE_RESOLVED"
  else
    warn "uv cache points to a non-standard path: $ACTUAL_UV_CACHE_RESOLVED"
  fi
else
  warn "Could not determine uv cache directory."
fi

ACTUAL_PIP_CACHE="${PIP_CACHE_DIR:-}"
if [[ -z "$ACTUAL_PIP_CACHE" ]] && command -v python3 >/dev/null 2>&1; then
  ACTUAL_PIP_CACHE="$(python3 -m pip cache dir 2>/dev/null || true)"
fi
if [[ -n "$ACTUAL_PIP_CACHE" ]]; then
  ACTUAL_PIP_CACHE_RESOLVED="$(trim_path "$ACTUAL_PIP_CACHE")"
  if [[ "$ACTUAL_PIP_CACHE_RESOLVED" == "$EXPECTED_PIP_CACHE" ]]; then
    ok "pip cache points to repo-local .cache/pip."
  elif [[ -n "$HOME_DIR" && "$ACTUAL_PIP_CACHE_RESOLVED" == "$HOME_DIR"* ]]; then
    fail "pip cache still points outside the repo: $ACTUAL_PIP_CACHE_RESOLVED"
  else
    warn "pip cache points to a non-standard path: $ACTUAL_PIP_CACHE_RESOLVED"
  fi
else
  warn "Could not determine pip cache directory."
fi

APPLEDOUBLE_COUNT="$(
  find "$ROOT_DIR" \
    \( \
      -path "$ROOT_DIR/.git" \
      -o -path "$ROOT_DIR/.venv" \
      -o -path "$ROOT_DIR/.cache" \
      -o -path "$ROOT_DIR/.pytest_cache" \
      -o -path "$ROOT_DIR/.ruff_cache" \
      -o -path "$ROOT_DIR/admin/frontend/node_modules" \
      -o -path "$ROOT_DIR/napcat/data" \
    \) -prune \
    -o -type f -name '._*' -print | wc -l | tr -d ' '
)"
if [[ "$APPLEDOUBLE_COUNT" == "0" ]]; then
  ok "No AppleDouble metadata files detected."
else
  warn "Found $APPLEDOUBLE_COUNT AppleDouble files. Run ./scripts/cleanup-appledouble.sh before committing."
fi

echo
echo "[doctor] Summary: $FAIL_COUNT fail, $WARN_COUNT warn"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
