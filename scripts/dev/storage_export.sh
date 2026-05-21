#!/usr/bin/env bash
# Export the live storage volume to a host directory.
#
# After Phase 3 (TASK-20260521-01) the bot's storage lives in a Docker
# named volume (omubot-storage), so the host can no longer cd into
# `./storage` to inspect or back up db files. This wrapper spins up a
# throwaway alpine container that mounts both the named volume and a
# host-side `storage-export/` directory and copies the volume contents
# out. Useful for:
#   - emergency restore (volume → bind-mount snapshot fallback)
#   - manual db inspection (sqlite3, sqlite-utils, etc.)
#   - one-off off-volume backups outside the BackupScheduler cadence
#
# Usage:
#   ./scripts/dev/storage_export.sh                  # default: ./storage-export
#   ./scripts/dev/storage_export.sh /tmp/snapshot    # custom destination
#
# Always run with the bot stopped (`docker compose stop bot`) to avoid
# copying mid-write files. We don't enforce that here — the caller is
# expected to know; _bot_guard.py covers the Python-side write paths.

set -euo pipefail

VOLUME_NAME="${OMUBOT_STORAGE_VOLUME:-omubot-storage}"
DEST="${1:-$PWD/storage-export}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[storage-export] docker CLI not found on host PATH" >&2
  exit 1
fi

if ! docker volume inspect "${VOLUME_NAME}" >/dev/null 2>&1; then
  echo "[storage-export] volume '${VOLUME_NAME}' does not exist; run Phase 3 migration first" >&2
  exit 1
fi

mkdir -p "${DEST}"

echo "[storage-export] copying ${VOLUME_NAME} -> ${DEST}"
docker run --rm \
  -v "${VOLUME_NAME}":/src:ro \
  -v "${DEST}":/dst \
  alpine sh -c "cp -a /src/. /dst/"

echo "[storage-export] done. files:"
ls -lah "${DEST}" | head -20
