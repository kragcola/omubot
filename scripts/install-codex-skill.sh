#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$ROOT_DIR/codex-skills/omubot-admin-console"
DEST_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
DEST_DIR="$DEST_ROOT/omubot-admin-console"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Source skill not found: $SRC_DIR" >&2
  exit 1
fi

mkdir -p "$DEST_ROOT"
rm -rf "$DEST_DIR"
cp -R "$SRC_DIR" "$DEST_DIR"

echo "Installed Codex skill to: $DEST_DIR"
echo "Restart Codex to pick up new skills."
