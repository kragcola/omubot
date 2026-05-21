#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: scripts/dev/mount-workspace.sh [--create] [--print-env]

Mount a macOS sparseimage workspace used to avoid exFAT development issues.

Options:
  --create     Create the sparseimage if it does not exist yet.
  --print-env  Print the resolved bundle, mount point, and target repo path.
  -h, --help   Show this help text.

Environment overrides:
  OMUBOT_WORKSPACE_BUNDLE       Full path to the sparseimage file.
  OMUBOT_WORKSPACE_FS           Filesystem for new images. Default: APFS
  OMUBOT_WORKSPACE_VOLUME_NAME  Mounted volume name. Default: OmubotWorkspace
  OMUBOT_WORKSPACE_MOUNT_POINT  Mounted path. Default: $HOME/OmubotWorkspace
  OMUBOT_WORKSPACE_SIZE         Size passed to hdiutil create. Default: 128g
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[mount-workspace] This helper only supports macOS." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_NAME="$(basename "$ROOT_DIR")"
DEFAULT_PARENT="$(dirname "$ROOT_DIR")"
VOLUME_NAME="${OMUBOT_WORKSPACE_VOLUME_NAME:-OmubotWorkspace}"
BUNDLE_PATH="${OMUBOT_WORKSPACE_BUNDLE:-$DEFAULT_PARENT/${VOLUME_NAME}.sparseimage}"
WORKSPACE_FS="${OMUBOT_WORKSPACE_FS:-APFS}"
DEFAULT_MOUNT_POINT="${HOME:-$ROOT_DIR}/${VOLUME_NAME}"
MOUNT_POINT="${OMUBOT_WORKSPACE_MOUNT_POINT:-$DEFAULT_MOUNT_POINT}"
WORKSPACE_SIZE="${OMUBOT_WORKSPACE_SIZE:-128g}"
TARGET_REPO_PATH="${MOUNT_POINT}/${REPO_NAME}"

CREATE_IF_MISSING=0
PRINT_ENV=0

for arg in "$@"; do
  case "$arg" in
    --create)
      CREATE_IF_MISSING=1
      ;;
    --print-env)
      PRINT_ENV=1
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "[mount-workspace] Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

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

exact_mount_fs_type() {
  python3 - "$1" <<'PY'
import re
import subprocess
import sys

target = sys.argv[1].rstrip("/") or "/"
for line in subprocess.run(["mount"], capture_output=True, text=True, check=True).stdout.splitlines():
    match = re.match(r"^.+ on (.+) \(([^,]+),", line)
    if not match:
        continue
    mountpoint, fs_type = match.groups()
    normalized = mountpoint.rstrip("/") or "/"
    if normalized == target:
        print(fs_type)
        break
PY
}

dir_has_entries() {
  find "$1" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .
}

has_appledouble() {
  local path="$1"
  if [[ -d "$path" ]]; then
    find "$path" -name '._*' -print -quit 2>/dev/null | grep -q .
    return $?
  fi
  return 1
}

if [[ "$PRINT_ENV" == "1" ]]; then
  cat <<EOF
OMUBOT_WORKSPACE_BUNDLE=$BUNDLE_PATH
OMUBOT_WORKSPACE_FS=$WORKSPACE_FS
OMUBOT_WORKSPACE_MOUNT_POINT=$MOUNT_POINT
OMUBOT_WORKSPACE_VOLUME_NAME=$VOLUME_NAME
OMUBOT_WORKSPACE_TARGET_REPO=$TARGET_REPO_PATH
EOF
fi

if ! command -v hdiutil >/dev/null 2>&1; then
  echo "[mount-workspace] hdiutil is required on macOS." >&2
  exit 1
fi

if [[ ! -e "$BUNDLE_PATH" ]]; then
  if [[ "$CREATE_IF_MISSING" != "1" ]]; then
    echo "[mount-workspace] Workspace image not found: $BUNDLE_PATH" >&2
    echo "[mount-workspace] Re-run with --create to create it on the external disk." >&2
    exit 1
  fi

  mkdir -p "$(dirname "$BUNDLE_PATH")"
  echo "[mount-workspace] Creating $WORKSPACE_FS sparseimage at $BUNDLE_PATH ($WORKSPACE_SIZE)"
  hdiutil create \
    -size "$WORKSPACE_SIZE" \
    -type SPARSE \
    -layout GPTSPUD \
    -fs "$WORKSPACE_FS" \
    -volname "$VOLUME_NAME" \
    "$BUNDLE_PATH"
fi

if has_appledouble "$BUNDLE_PATH"; then
  echo "[mount-workspace] Directory-style workspace image contains AppleDouble metadata (._*), which often breaks mounting on exFAT." >&2
  echo "[mount-workspace] Delete the image and recreate it as a sparseimage with --create." >&2
  exit 1
fi

if ! mkdir -p "$MOUNT_POINT" 2>/dev/null; then
  echo "[mount-workspace] Cannot create mount point: $MOUNT_POINT" >&2
  echo "[mount-workspace] Use a writable path, for example:" >&2
  echo "  OMUBOT_WORKSPACE_MOUNT_POINT=\"\$HOME/OmubotWorkspace\" ./scripts/dev/mount-workspace.sh" >&2
  exit 1
fi
CURRENT_EXACT_FS="$(exact_mount_fs_type "$MOUNT_POINT")"
if [[ -z "$CURRENT_EXACT_FS" ]]; then
  if dir_has_entries "$MOUNT_POINT"; then
    echo "[mount-workspace] Mount point exists but is not mounted: $MOUNT_POINT" >&2
    echo "[mount-workspace] It also contains files. Move or delete them before attaching the workspace image." >&2
    exit 1
  fi
  echo "[mount-workspace] Attaching $BUNDLE_PATH to $MOUNT_POINT"
  hdiutil attach "$BUNDLE_PATH" -mountpoint "$MOUNT_POINT" -nobrowse
  CURRENT_EXACT_FS="$(exact_mount_fs_type "$MOUNT_POINT")"
fi

CURRENT_FS_LOWER="$(printf '%s' "$CURRENT_EXACT_FS" | tr '[:upper:]' '[:lower:]')"
if [[ "$CURRENT_FS_LOWER" != "apfs" && "$CURRENT_FS_LOWER" != "hfs" ]]; then
  PARENT_FS="$(mount_fs_type "$MOUNT_POINT")"
  echo "[mount-workspace] Expected APFS/HFS mount at $MOUNT_POINT, got: ${CURRENT_EXACT_FS:-$PARENT_FS}" >&2
  exit 1
fi

echo "[mount-workspace] Workspace ready:"
echo "  bundle     $BUNDLE_PATH"
echo "  mount      $MOUNT_POINT"
echo "  repo path  $TARGET_REPO_PATH"

if [[ ! -d "$TARGET_REPO_PATH/.git" ]]; then
  cat <<EOF
[mount-workspace] Next step:
  mkdir -p "$TARGET_REPO_PATH"
  rsync -a --exclude '.venv' --exclude '.cache' --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '.workspace' --exclude '.workspace_mount' "$ROOT_DIR/" "$TARGET_REPO_PATH/"
  cd "$TARGET_REPO_PATH"
  ./scripts/dev/bootstrap.sh
EOF
fi
