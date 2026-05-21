#!/usr/bin/env bash
# Daily backup via BackupService (thin wrapper around Python implementation).
#
# After Phase 3 (storage moved to docker named volume `omubot-storage`),
# the host no longer has direct read access to /app/storage. This wrapper
# defers to the bot container, which still sees the volume at /app/storage.
#
# Usage:
#   ./scripts/backup-databases.sh                      # daily profile
#   ./scripts/backup-databases.sh --profile migration  # full migration package
#
# Cron (fallback for when bot scheduler is down):
#   30 4 * * * /path/to/omubot/scripts/backup-databases.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

CONTAINER="${OMUBOT_BOT_CONTAINER:-qq-bot}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[backup] docker CLI not found on host PATH; cannot reach the bot container" >&2
  exit 1
fi

# Refuse silently if the container is not running — leaves a clear cron log
# entry without bringing the host into the picture.
if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo "[backup] container '${CONTAINER}' is not running; skipping backup" >&2
  exit 1
fi

exec docker exec "${CONTAINER}" \
  uv run python -m services.storage.backup create --host-mode "$@"
