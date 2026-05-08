#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_FILE="$(mktemp)"

cleanup_tmp() {
  rm -f "$TMP_FILE"
}

trap cleanup_tmp EXIT

find "$ROOT_DIR" \
  \( \
    -path "$ROOT_DIR/.git" \
    -o -path "$ROOT_DIR/.venv" \
    -o -path "$ROOT_DIR/.ruff_cache" \
    -o -path "$ROOT_DIR/.pytest_cache" \
    -o -path "$ROOT_DIR/admin/frontend/node_modules" \
    -o -path "$ROOT_DIR/napcat/data" \
  \) -prune \
  -o -type f -name '._*' -print0 > "$TMP_FILE"

COUNT="$(tr -cd '\0' < "$TMP_FILE" | wc -c | tr -d ' ')"

if [ "$COUNT" = "0" ]; then
  echo "[cleanup-appledouble] No AppleDouble files found."
  exit 0
fi

xargs -0 rm -f -- < "$TMP_FILE"

echo "[cleanup-appledouble] Removed $COUNT AppleDouble file(s)."
