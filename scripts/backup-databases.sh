#!/usr/bin/env bash
# Daily backup via BackupService (thin wrapper around Python implementation).
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

# Source env if available (sets UV_CACHE_DIR etc.)
if [ -f "${SCRIPT_DIR}/dev/env.sh" ]; then
  source "${SCRIPT_DIR}/dev/env.sh"
fi

exec uv run python -m services.storage.backup create --host-mode "$@"
