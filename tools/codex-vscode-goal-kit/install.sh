#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_SRC="$SCRIPT_DIR/plugins/goal-kit"
TARGET_PLUGIN_DIR="$HOME/plugins/goal-kit"
TARGET_MARKETPLACE="$HOME/.agents/plugins/marketplace.json"

find_codex_bin() {
  if [[ -n "${CODEX_BIN:-}" && -x "${CODEX_BIN:-}" ]]; then
    printf '%s\n' "$CODEX_BIN"
    return 0
  fi

  local ext_dir
  ext_dir="$(find "$HOME/.vscode/extensions" -maxdepth 1 -type d -name 'openai.chatgpt-*' | sort | tail -n 1 || true)"
  if [[ -n "$ext_dir" && -x "$ext_dir/bin/macos-aarch64/codex" ]]; then
    printf '%s\n' "$ext_dir/bin/macos-aarch64/codex"
    return 0
  fi

  if command -v codex >/dev/null 2>&1; then
    command -v codex
    return 0
  fi

  return 1
}

merge_marketplace() {
  TARGET_MARKETPLACE="$TARGET_MARKETPLACE" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["TARGET_MARKETPLACE"]).expanduser()
path.parent.mkdir(parents=True, exist_ok=True)

if path.exists():
    payload = json.loads(path.read_text())
else:
    payload = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [],
    }

plugins = payload.setdefault("plugins", [])
entry = {
    "name": "goal-kit",
    "source": {"source": "local", "path": "./plugins/goal-kit"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity",
}

for index, existing in enumerate(plugins):
    if isinstance(existing, dict) and existing.get("name") == "goal-kit":
        plugins[index] = entry
        break
else:
    plugins.append(entry)

path.write_text(json.dumps(payload, indent=2) + "\n")
PY
}

CODEX_BIN="$(find_codex_bin || true)"
if [[ -z "${CODEX_BIN:-}" ]]; then
  echo "Could not find a Codex binary. Install the VS Code Codex extension first." >&2
  exit 1
fi

mkdir -p "$HOME/plugins"
rm -rf "$TARGET_PLUGIN_DIR"
cp -R "$PLUGIN_SRC" "$TARGET_PLUGIN_DIR"
merge_marketplace

"$CODEX_BIN" features enable goals
"$CODEX_BIN" plugin remove goal-kit --marketplace personal >/dev/null 2>&1 || true
"$CODEX_BIN" plugin add goal-kit --marketplace personal

echo
echo "Installed Goal Kit into:"
echo "  $TARGET_PLUGIN_DIR"
echo
echo "Enabled built-in Codex goals."
echo "If VS Code is already open, reload the window or reopen the Codex panel."
echo
echo "Try these commands in Codex:"
echo "  /goal"
echo "  /goal-kit:draft"
echo "  /goal-kit:from-file docs/goal.md"
echo "  /goal-kit:doctor"
